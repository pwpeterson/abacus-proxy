"""Tool filtering for context-aware MCP tool selection.

Reduces system prompt bloat by only including relevant tools for each request.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


# Roo Code built-in tools (always available, no external MCP servers)
# Used as fallback when core_tool_names not provided
BUILTIN_TOOLS = {
    "read_file",
    "read_text_file",
    "read_media_file",
    "read_multiple_files",
    "write_to_file",
    "edit_file",
    "create_directory",
    "list_files",
    "list_directory",
    "search_files",
    "execute_command",
    "browser_action",
    "ask_followup_question",
    "attempt_completion",
    "switch_mode",
    "new_task",
}

# MCP server categories
MCP_SERVER_CATEGORIES = {
    "core": {
        "filesystem",
        "execute_command",
    },
    "version_control": {
        "mcp-git-tools",
        "git-safe",
    },
    "memory": {
        "conport",
        "mem0-mcp",
        "mindscape-ai",
    },
    "code_quality": {
        "mcp-toolbox",
    },
    "web": {
        "brave-search",
        "fetch-mcp",
        "puppeteer",
        "chrome-devtools",
        "browser-tools-mcp",
    },
    "documentation": {
        "context7",
        "mcp-crawl4ai-rag",
    },
    "data": {
        "sqlite",
    },
}


def filter_tools_array_for_vllm(
    tools: list[dict[str, Any]],
    core_tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter request payload tools array to core tools only.
    
    vLLM should only receive core tools (configurable via CORE_TOOL_NAMES).
    
    Args:
        tools: Request payload tools array
        core_tool_names: Set of core tool names to include (uses BUILTIN_TOOLS if None)
        
    Returns:
        Filtered tools array with only core tools
    """
    if not tools or not isinstance(tools, list):
        return tools
    
    # Use provided core_tool_names or fall back to BUILTIN_TOOLS
    allowed_tools = core_tool_names if core_tool_names else BUILTIN_TOOLS

    def _is_allowed_tool_name(name: str) -> bool:
        """Return True if `name` maps to a tool in allowed_tools.

        vLLM may receive tool names in several formats depending on the client/runtime:
        - plain: "get_product_context"
        - bridged MCP (Roo Code): "mcp--conport--get_product_context"
        - bridged MCP (alt): "mcp_conport_get_product_context"

        CORE_TOOL_NAMES is configured using the *base* tool names (no prefixes).
        """
        if name in allowed_tools:
            return True

        # Common Roo MCP bridge format: mcp--server--tool
        if name.startswith("mcp--") and "--" in name:
            base = name.split("--")[-1]
            if base in allowed_tools:
                return True

        # Common underscore format: mcp_server_tool
        # We match by suffix to avoid guessing server names.
        for core in allowed_tools:
            if name.endswith(f"_{core}"):
                return True

        return False
    
    filtered_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        
        # Get tool name from function.name
        func = tool.get("function", {})
        if not isinstance(func, dict):
            continue
        
        name = func.get("name")
        if not isinstance(name, str):
            continue
        
        # Keep only core tools
        if _is_allowed_tool_name(name):
            filtered_tools.append(tool)
    
    original_count = len(tools)
    filtered_count = len(filtered_tools)
    reduction_pct = 100 * (original_count - filtered_count) / original_count if original_count > 0 else 0
    
    logger.info(
        f"[TOOL FILTER] vLLM tools array filtering: "
        f"{original_count} → {filtered_count} tools ({reduction_pct:.1f}% reduction, core tools only)"
    )
    
    return filtered_tools


def extract_mcp_servers_section(system_content: str) -> tuple[str, str, str]:
    """Extract MCP SERVERS section from system message.
    
    Returns:
        (before_mcp, mcp_section, after_mcp)
    """
    # Find the MCP SERVERS section
    mcp_start = system_content.find("\nMCP SERVERS\n")
    if mcp_start == -1:
        return system_content, "", ""
    
    # Find the next major section (starts with \n====)
    after_mcp_start = system_content.find("\n\n====", mcp_start + 1)
    if after_mcp_start == -1:
        # MCP section goes to end
        return system_content[:mcp_start], system_content[mcp_start:], ""
    
    before = system_content[:mcp_start]
    mcp_section = system_content[mcp_start:after_mcp_start]
    after = system_content[after_mcp_start:]
    
    return before, mcp_section, after


def parse_mcp_servers(mcp_section: str) -> dict[str, str]:
    """Parse MCP SERVERS section into server_name -> content dict.
    
    Returns:
        {"server_name": "## server_name\n...\n"}
    """
    servers = {}
    
    # Split by ## headers (server names)
    lines = mcp_section.split("\n")
    current_server = None
    current_content = []
    
    for line in lines:
        if line.startswith("## ") and not line.startswith("## Available Tools"):
            # Save previous server
            if current_server:
                servers[current_server] = "\n".join(current_content)
            
            # Start new server
            current_server = line[3:].split("(")[0].strip()
            current_content = [line]
        elif current_server:
            current_content.append(line)
    
    # Save last server
    if current_server:
        servers[current_server] = "\n".join(current_content)
    
    return servers


def filter_mcp_tools(
    system_content: str,
    categories: list[str] | None = None,
    servers: list[str] | None = None,
) -> str:
    """Filter MCP SERVERS section by categories or explicit server list.
    
    Args:
        system_content: Full system message content
        categories: Categories to include (e.g., ['core', 'memory'])
        servers: Specific server names to include (e.g., ['conport', 'filesystem'])
        
    Returns:
        Filtered system message
        
    Notes:
        - If both categories and servers are provided, servers takes precedence
        - If neither are provided, returns original content (no filtering)
    """
    before, mcp_section, after = extract_mcp_servers_section(system_content)
    
    if not mcp_section:
        logger.debug("[TOOL FILTER] No MCP SERVERS section found in system message")
        return system_content
    
    parsed_servers = parse_mcp_servers(mcp_section)
    
    # Determine which servers to keep
    if servers:
        # Explicit server list takes precedence
        allowed_servers = set(servers)
        filter_type = f"servers={servers}"
    elif categories:
        # Build allowed servers from categories
        allowed_servers = set()
        for cat in categories:
            allowed_servers.update(MCP_SERVER_CATEGORIES.get(cat, set()))
        filter_type = f"categories={categories}"
    else:
        # No filtering requested
        return system_content
    
    # Filter servers
    filtered_servers = {k: v for k, v in parsed_servers.items() if k in allowed_servers}
    
    if not filtered_servers:
        logger.info(f"[TOOL FILTER] No servers match filter ({filter_type}), removing MCP section")
        return before + after
    
    # Reconstruct MCP section with filtered servers
    filtered_mcp = "\nMCP SERVERS\n\n"
    filtered_mcp += "The Model Context Protocol (MCP) enables communication between the system and MCP servers.\n\n"
    filtered_mcp += "# Connected MCP Servers\n\n"
    
    for server_name, content in filtered_servers.items():
        filtered_mcp += content + "\n\n"
    
    original_chars = len(mcp_section)
    filtered_chars = len(filtered_mcp)
    reduction_pct = 100 * (original_chars - filtered_chars) / original_chars if original_chars > 0 else 0
    
    logger.info(
        f"[TOOL FILTER] MCP tool filtering ({filter_type}): "
        f"{len(parsed_servers)} → {len(filtered_servers)} servers, "
        f"{original_chars} → {filtered_chars} chars ({reduction_pct:.1f}% reduction)"
    )
    
    return before + filtered_mcp + after


def filter_tools_for_vllm(system_content: str) -> str:
    """Filter system message to only include built-in tools for vLLM.
    
    Args:
        system_content: Full system message content
        
    Returns:
        Filtered system message with only built-in tools
    """
    # For vLLM, keep only core filesystem server (provides built-in tools)
    return filter_mcp_tools(system_content, servers=["filesystem"])


def filter_tools_by_category(
    system_content: str,
    categories: list[str],
) -> str:
    """Filter tools to only include specified categories.
    
    Args:
        system_content: Full system message content
        categories: Categories to include (e.g., ['core', 'version_control'])
        
    Returns:
        Filtered system message
    """
    return filter_mcp_tools(system_content, categories=categories)
