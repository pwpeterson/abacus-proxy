"""Intelligent tool selection based on request context.

Analyzes incoming requests and selects relevant MCP servers and tools
based on the prompt, context, and conversation history.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from src.utils.dynamic_mcp_loader import get_mcp_loader


class IntelligentToolSelector:
    """Select relevant tools based on request analysis."""
    
    def __init__(self):
        self.mcp_loader = get_mcp_loader()
        
        # Keywords that indicate specific tool categories
        self.category_keywords = {
            "git": [
                "git", "commit", "branch", "merge", "push", "pull", "clone",
                "repository", "repo", "version control", "github", "gitlab"
            ],
            "browser": [
                "browser", "chrome", "firefox", "webpage", "screenshot",
                "navigate", "click", "scrape", "automation", "puppeteer"
            ],
            "search": [
                "search", "google", "find online", "look up", "web search",
                "brave search", "query", "internet"
            ],
            "filesystem": [
                "file", "directory", "folder", "read", "write", "create",
                "delete", "move", "copy", "path", "filesystem"
            ],
            "memory": [
                "remember", "recall", "memory", "context", "history",
                "previous", "store", "retrieve", "mem0", "conport"
            ],
            "database": [
                "database", "sql", "query", "table", "sqlite", "postgres",
                "insert", "select", "update", "delete"
            ],
            "web": [
                "fetch", "http", "api", "request", "download", "crawl",
                "url", "website", "endpoint"
            ],
            "ai": [
                "model", "llm", "generate", "analyze", "thinking",
                "reasoning", "inference", "completion"
            ],
        }
        
        # Always include these core servers
        self.core_servers = [
            "filesystem",  # File operations are fundamental
            "mem0-mcp",    # Memory for context
        ]
    
    def select_tools_for_request(
        self,
        prompt: str,
        context: str | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
        max_servers: int = 10,
    ) -> list[str]:
        """Select relevant MCP servers based on request analysis.
        
        Args:
            prompt: User's prompt/request
            context: Additional context (optional)
            conversation_history: Previous messages (optional)
            max_servers: Maximum number of servers to include
            
        Returns:
            List of server names to include
        """
        # Combine all text for analysis
        analysis_text = prompt.lower()
        if context:
            analysis_text += " " + context.lower()
        if conversation_history:
            for msg in conversation_history[-5:]:  # Last 5 messages
                if isinstance(msg, dict) and "content" in msg:
                    analysis_text += " " + str(msg["content"]).lower()
        
        # Detect relevant categories
        relevant_categories = self._detect_categories(analysis_text)
        logger.debug(f"Detected categories: {relevant_categories}")
        
        # Get servers for each category
        selected_servers = set(self.core_servers)
        
        for category in relevant_categories:
            category_servers = self.mcp_loader.get_servers_by_category(category)
            selected_servers.update(category_servers)
        
        # Add project-specific servers if mentioned
        if "conport" in analysis_text or "context portal" in analysis_text:
            selected_servers.add("conport")
        
        if "mindscape" in analysis_text:
            selected_servers.add("mindscape-ai")
        
        # Filter to only available servers
        available_servers = [
            server for server in selected_servers
            if self.mcp_loader.is_server_available(server)
        ]
        
        # Limit to max_servers (keep core servers first)
        if len(available_servers) > max_servers:
            core_available = [s for s in self.core_servers if s in available_servers]
            other_available = [s for s in available_servers if s not in self.core_servers]
            available_servers = core_available + other_available[:max_servers - len(core_available)]
        
        logger.info(f"Selected {len(available_servers)} MCP servers for request: {', '.join(available_servers)}")
        return available_servers
    
    def _detect_categories(self, text: str) -> list[str]:
        """Detect relevant categories from text.
        
        Args:
            text: Text to analyze (should be lowercase)
            
        Returns:
            List of detected category names
        """
        detected = []
        
        for category, keywords in self.category_keywords.items():
            # Check if any keyword appears in text
            if any(keyword in text for keyword in keywords):
                detected.append(category)
        
        return detected
    
    def get_all_available_tools(self) -> list[str]:
        """Get all available MCP server names.
        
        Returns:
            List of all configured server names
        """
        return self.mcp_loader.get_all_server_names()
    
    def select_tools_for_mode(self, mode: str) -> list[str]:
        """Select tools appropriate for a specific mode.
        
        Args:
            mode: Mode name (e.g., 'code', 'architect', 'debug')
            
        Returns:
            List of server names appropriate for the mode
        """
        mode_servers = {
            "code": [
                "filesystem", "mcp-git-tools", "mem0-mcp", "conport",
                "sqlite", "model-proxy"
            ],
            "architect": [
                "filesystem", "mem0-mcp", "conport", "mindscape-ai",
                "sequentialthinking", "model-proxy"
            ],
            "debug": [
                "filesystem", "mcp-git-tools", "mem0-mcp", "conport",
                "browser-tools-mcp", "chrome-devtools", "model-proxy"
            ],
            "ask": [
                "mem0-mcp", "conport", "brave-search", "fetch-mcp",
                "context7", "model-proxy"
            ],
        }
        
        servers = mode_servers.get(mode.lower(), self.core_servers)
        
        # Filter to only available servers
        available = [
            server for server in servers
            if self.mcp_loader.is_server_available(server)
        ]
        
        logger.info(f"Selected {len(available)} servers for mode '{mode}': {', '.join(available)}")
        return available


# Global instance
_selector: IntelligentToolSelector | None = None


def get_tool_selector() -> IntelligentToolSelector:
    """Get or create the global tool selector instance."""
    global _selector
    if _selector is None:
        _selector = IntelligentToolSelector()
    return _selector
