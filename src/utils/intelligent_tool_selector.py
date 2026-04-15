"""Intelligent tool selection based on request context and Mem0 insights.

This module reduces token overhead by selecting only relevant tools for each request:
- Always includes "core" tools (file ops, task completion, ConPort read ops, etc.)
- Intelligently selects additional tools based on:
  * User request content (keyword/semantic analysis)
  * Recent tool usage from conversation history
  * Mem0 patterns (if available)
  
This runs BEFORE condensation to ensure token estimates are accurate.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService


# Default core tools that are ALWAYS included (can be overridden via config)
DEFAULT_CORE_TOOL_NAMES = {
    # File operations
    "read_file",
    "write_to_file",
    "list_files",
    "search_files",
    
    # Task completion
    "attempt_completion",
    "ask_followup_question",
    
    # Command execution (essential for many workflows)
    "execute_command",
    
    # ConPort read operations (project memory context checking)
    "get_product_context",
    "get_active_context",
    "get_decisions",
    "search_decisions_fts",
    "get_recent_activity_summary",
}

# Tool categories with keyword triggers
TOOL_CATEGORIES = {
    "git": {
        "keywords": [
            "commit", "push", "pull", "branch", "merge", "git", "repository",
            "version control", "diff", "log", "status", "clone", "checkout"
        ],
        "tool_patterns": [r"^mcp.*git.*", r"^git_.*"],
    },
    "database": {
        "keywords": [
            "database", "sql", "query", "table", "sqlite", "postgres", "mysql",
            "insert", "select", "update", "delete", "schema"
        ],
        "tool_patterns": [r"^mcp.*sqlite.*", r"^mcp.*postgres.*", r"^.*_query$"],
    },
    "web": {
        "keywords": [
            "web", "scrape", "crawl", "fetch", "http", "api", "request",
            "browser", "url", "website", "html", "download"
        ],
        "tool_patterns": [
            r"^mcp.*fetch.*", r"^mcp.*puppeteer.*", r"^mcp.*brave.*",
            r"^mcp.*browser.*", r"^.*_search$"
        ],
    },
    "memory": {
        "keywords": [
            "remember", "memory", "recall", "store", "save note",
            "mem0", "log decision", "track progress", "pattern"
        ],
        "tool_patterns": [
            r"^mcp.*mem0.*", 
            r"^.*log_decision$", r"^.*log_progress$", r"^.*log_system_pattern$",
            r"^.*add_memory$", r"^.*update_.*context$"
        ],
    },
    "documentation": {
        "keywords": [
            "documentation", "docs", "library", "api reference", "context7",
            "example", "tutorial", "guide", "readme"
        ],
        "tool_patterns": [r"^mcp.*context7.*", r"^.*_docs$"],
    },
    "code_analysis": {
        "keywords": [
            "analyze", "review", "refactor", "optimize", "performance",
            "security", "quality", "lint", "test", "debug"
        ],
        "tool_patterns": [r"^.*_review$", r"^.*_analysis$", r"^.*_audit$"],
    },
    "devtools": {
        "keywords": [
            "chrome", "devtools", "inspect", "console", "network",
            "screenshot", "lighthouse", "performance", "accessibility"
        ],
        "tool_patterns": [r"^mcp.*chrome.*", r"^mcp.*devtools.*"],
    },
}


class IntelligentToolSelector:
    """Intelligently select relevant tools based on request context."""

    def __init__(
        self,
        *,
        memory_service: MemoryService | None = None,
        enable_mem0_insights: bool = True,
        min_keyword_matches: int = 1,
        recent_usage_window: int = 10,
        core_tool_names: set[str] | None = None,
    ) -> None:
        """Initialize the intelligent tool selector.
        
        Args:
            memory_service: Optional Mem0 service for usage pattern insights
            enable_mem0_insights: Whether to use Mem0 for tool selection
            min_keyword_matches: Minimum keyword matches to include a category
            recent_usage_window: Number of recent messages to analyze for tool usage
            core_tool_names: Set of tool names to always include (defaults to DEFAULT_CORE_TOOL_NAMES)
        """
        self.memory_service = memory_service
        self.enable_mem0_insights = enable_mem0_insights
        self.min_keyword_matches = min_keyword_matches
        self.recent_usage_window = recent_usage_window
        self.core_tool_names = core_tool_names or DEFAULT_CORE_TOOL_NAMES

    def select_tools(
        self,
        *,
        all_tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Select relevant tools based on request context.
        
        Args:
            all_tools: Complete list of available tools
            messages: Conversation messages (for context analysis)
            user_id: Optional user ID for Mem0 queries
            project_id: Optional project ID for Mem0 queries
            
        Returns:
            Tuple of (selected_tools, selection_stats)
        """
        if not all_tools:
            return [], {"total_tools": 0, "selected_tools": 0, "strategy": "empty_input"}

        # Extract user request content
        user_content = self._extract_user_request(messages)
        
        # Analyze request for category relevance
        relevant_categories = self._detect_relevant_categories(user_content)
        
        # Analyze recent tool usage from conversation
        recently_used_tools = self._extract_recent_tool_usage(messages)
        
        # Get Mem0 insights (if enabled and available)
        mem0_suggested_tools: set[str] = set()
        if self.enable_mem0_insights and self.memory_service and self.memory_service.is_enabled:
            mem0_suggested_tools = self._get_mem0_tool_suggestions(
                user_content=user_content,
                user_id=user_id,
                project_id=project_id,
            )
        
        # Select tools
        selected_tools: list[dict[str, Any]] = []
        core_count = 0
        category_count = 0
        usage_count = 0
        mem0_count = 0
        
        for tool in all_tools:
            if not isinstance(tool, dict):
                continue
            
            tool_func = tool.get("function", {})
            if not isinstance(tool_func, dict):
                continue
            
            tool_name = tool_func.get("name", "")
            if not isinstance(tool_name, str):
                continue
            
            # Always include core tools
            if self._is_core_tool(tool_name):
                selected_tools.append(tool)
                core_count += 1
                continue
            
            # Include if recently used in conversation
            if tool_name in recently_used_tools:
                selected_tools.append(tool)
                usage_count += 1
                continue
            
            # Include if Mem0 suggests it
            if tool_name in mem0_suggested_tools:
                selected_tools.append(tool)
                mem0_count += 1
                continue
            
            # Include if matches relevant category
            if self._matches_category(tool_name, relevant_categories):
                selected_tools.append(tool)
                category_count += 1
                continue
        
        stats = {
            "total_tools": len(all_tools),
            "selected_tools": len(selected_tools),
            "core_tools": core_count,
            "category_tools": category_count,
            "usage_tools": usage_count,
            "mem0_tools": mem0_count,
            "relevant_categories": list(relevant_categories),
            "strategy": "intelligent_selection",
        }
        
        logger.info(
            f"Tool selection: {len(all_tools)} → {len(selected_tools)} tools "
            f"(core={core_count}, category={category_count}, usage={usage_count}, mem0={mem0_count})"
        )
        
        return selected_tools, stats

    def _is_core_tool(self, tool_name: str) -> bool:
        """Check if a tool is a core tool that should always be included."""
        # Direct match
        if tool_name in self.core_tool_names:
            return True

        # Common Roo MCP bridge format: mcp--server--tool
        # Example: mcp--conport--get_active_context
        if tool_name.startswith("mcp--") and "--" in tool_name:
            base = tool_name.split("--")[-1]
            if base in self.core_tool_names:
                return True
        
        # Strip MCP prefix and check (e.g., mcp_conport_get_decisions → get_decisions)
        for core_name in self.core_tool_names:
            if tool_name.endswith(f"_{core_name}") or tool_name == f"mcp_{core_name}":
                return True

        return False

    def _extract_user_request(self, messages: list[dict[str, Any]]) -> str:
        """Extract the most recent user request content."""
        if not messages:
            return ""
        
        # Get last user message
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content.lower()
        
        return ""

    def _detect_relevant_categories(self, user_content: str) -> set[str]:
        """Detect which tool categories are relevant based on user request keywords."""
        if not user_content:
            return set()
        
        relevant = set()
        
        for category, config in TOOL_CATEGORIES.items():
            keywords = config.get("keywords", [])
            matches = sum(1 for kw in keywords if kw in user_content)
            
            if matches >= self.min_keyword_matches:
                relevant.add(category)
        
        return relevant

    def _matches_category(self, tool_name: str, relevant_categories: set[str]) -> bool:
        """Check if a tool matches any of the relevant categories."""
        if not relevant_categories:
            return False
        
        for category in relevant_categories:
            config = TOOL_CATEGORIES.get(category, {})
            patterns = config.get("tool_patterns", [])
            
            for pattern in patterns:
                if re.search(pattern, tool_name, re.IGNORECASE):
                    return True
        
        return False

    def _extract_recent_tool_usage(self, messages: list[dict[str, Any]]) -> set[str]:
        """Extract tool names that were recently used in the conversation."""
        if not messages:
            return set()
        
        recently_used = set()
        
        # Look at last N messages
        recent_messages = messages[-self.recent_usage_window:]
        
        for msg in recent_messages:
            if not isinstance(msg, dict):
                continue
            
            # Extract from assistant tool calls
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        func = tc.get("function", {})
                        if isinstance(func, dict):
                            name = func.get("name")
                            if isinstance(name, str):
                                recently_used.add(name)
        
        return recently_used

    def _get_mem0_tool_suggestions(
        self,
        *,
        user_content: str,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> set[str]:
        """Query Mem0 for tool usage patterns relevant to the current request."""
        if not self.memory_service or not user_content:
            return set()
        
        try:
            # Search for relevant memories about tool usage
            query = f"tool usage {user_content[:200]}"
            
            result = self.memory_service.search_memory(
                query=query,
                user_id=user_id or "default",
                project_id=project_id,
                limit=5,
            )
            
            suggested_tools = set()
            
            # Extract tool names from memory content
            for item in result.results:
                memory_text = item.memory.lower()
                
                # Look for common patterns like "used read_file" or "calling git_commit"
                tool_pattern = r'\b([a-z_]+_[a-z_]+)\b'
                matches = re.findall(tool_pattern, memory_text)
                
                for match in matches:
                    # Validate it looks like a tool name
                    if len(match) > 4 and match.count("_") > 0:
                        suggested_tools.add(match)
            
            if suggested_tools:
                logger.debug(f"Mem0 suggested tools: {suggested_tools}")
            
            return suggested_tools
            
        except Exception as e:
            logger.debug(f"Mem0 tool suggestion failed: {e}")
            return set()


def filter_system_message_tools(
    system_content: str,
    selected_tool_names: set[str],
) -> str:
    """Filter MCP tool descriptions from system message to match selected tools.
    
    This removes descriptions of tools that weren't selected, reducing system message size.
    
    Args:
        system_content: Original system message content
        selected_tool_names: Set of tool names that were selected
        
    Returns:
        Filtered system message content with only selected tool descriptions
    """
    if not system_content or not selected_tool_names:
        return system_content
    
    # Split into lines for processing
    lines = system_content.split('\n')
    filtered_lines = []
    
    # State tracking
    in_mcp_section = False
    in_server_block = False
    current_server_lines = []
    server_has_selected_tools = False
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Detect start of MCP servers section
        if '# Connected MCP Servers' in line or '## Connected MCP Servers' in line:
            in_mcp_section = True
            filtered_lines.append(line)
            i += 1
            continue
        
        # Detect end of MCP servers section (next major heading)
        if in_mcp_section and line.strip().startswith('#') and 'MCP' not in line and line.strip() != '#':
            # Flush any pending server block
            if in_server_block and server_has_selected_tools:
                filtered_lines.extend(current_server_lines)
            
            in_mcp_section = False
            in_server_block = False
            current_server_lines = []
            filtered_lines.append(line)
            i += 1
            continue
        
        # Process MCP section
        if in_mcp_section:
            # Detect server block start (e.g., "## filesystem (`npx...`)" or "### filesystem")
            if line.strip().startswith('##') and ('(`' in line or line.strip().endswith(':')):
                # Flush previous server block if it has selected tools
                if in_server_block and server_has_selected_tools:
                    filtered_lines.extend(current_server_lines)
                
                # Start new server block
                in_server_block = True
                current_server_lines = [line]
                server_has_selected_tools = False
                i += 1
                continue
            
            # Process tool description lines within server block
            if in_server_block:
                # Check if this line describes a function/tool
                # Format: "- tool_name" or "**tool_name**" or similar
                is_tool_line = False
                tool_name_in_line = None
                
                # Pattern 1: "- tool_name:" or "- tool_name ("
                match = re.match(r'^[\s\-\*]*([a-z_][a-z0-9_]*)\s*[:(\[]', line, re.IGNORECASE)
                if match:
                    is_tool_line = True
                    tool_name_in_line = match.group(1)
                
                # Pattern 2: Function calls in descriptions
                if not is_tool_line:
                    match = re.search(r'\b([a-z_][a-z0-9_]+)\(', line, re.IGNORECASE)
                    if match:
                        potential_tool = match.group(1)
                        if potential_tool in selected_tool_names:
                            tool_name_in_line = potential_tool
                            is_tool_line = True
                
                if is_tool_line and tool_name_in_line:
                    # Check if this tool was selected
                    if tool_name_in_line in selected_tool_names:
                        current_server_lines.append(line)
                        server_has_selected_tools = True
                    # Skip this line if tool not selected
                else:
                    # Keep non-tool lines (headers, descriptions, etc.)
                    current_server_lines.append(line)
                
                i += 1
                continue
        
        # Keep all non-MCP lines
        if not in_mcp_section:
            filtered_lines.append(line)
        
        i += 1
    
    # Flush final server block if needed
    if in_server_block and server_has_selected_tools:
        filtered_lines.extend(current_server_lines)
    
    filtered_content = '\n'.join(filtered_lines)
    
    # Log the reduction
    original_len = len(system_content)
    filtered_len = len(filtered_content)
    if filtered_len < original_len:
        saved_chars = original_len - filtered_len
        logger.info(
            f"System message tool descriptions filtered: {original_len} → {filtered_len} chars "
            f"(saved {saved_chars} chars, ~{saved_chars // 4} tokens)"
        )
    
    return filtered_content


def filter_tools_intelligently(
    *,
    all_tools: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    memory_service: MemoryService | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    enable_mem0_insights: bool = True,
    core_tool_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Convenience function for intelligent tool filtering.
    
    Args:
        all_tools: Complete list of available tools
        messages: Conversation messages
        memory_service: Optional Mem0 service
        user_id: Optional user ID
        project_id: Optional project ID
        enable_mem0_insights: Whether to use Mem0 for suggestions
        core_tool_names: Set of tool names to always include (defaults to DEFAULT_CORE_TOOL_NAMES)
        
    Returns:
        Tuple of (filtered_tools, selection_stats)
    """
    selector = IntelligentToolSelector(
        memory_service=memory_service,
        enable_mem0_insights=enable_mem0_insights,
        core_tool_names=core_tool_names,
    )
    
    return selector.select_tools(
        all_tools=all_tools,
        messages=messages,
        user_id=user_id,
        project_id=project_id,
    )
