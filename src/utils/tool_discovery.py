"""Dynamic tool discovery from MCP servers.

Discovery tools from configured MCP servers and Roo Code's built-in tools.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from src.mcp.bridge_client import list_tools_for_server
from src.utils.dynamic_mcp_loader import get_mcp_loader


# Cache for discovered tools (refreshed periodically)
_TOOL_CACHE: dict[str, list[dict[str, Any]]] = {}
_CACHE_VERSION: int = 0


def _get_roo_code_tools() -> list[dict[str, Any]]:
    """Get Roo Code's built-in tools.
    
    These are the core tools that Roo Code provides for file operations,
    git operations, search, etc.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the contents of one or more files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "File paths to read (max 5 files)"
                        }
                    },
                    "required": ["paths"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_to_file",
                "description": "Write content to a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                        "content": {"type": "string", "description": "File content"}
                    },
                    "required": ["path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "apply_diff",
                "description": "Apply precise modifications to a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path"},
                        "diff": {"type": "string", "description": "Search/replace diff"}
                    },
                    "required": ["path", "diff"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_files",
                "description": "Search files using regex",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path"},
                        "regex": {"type": "string", "description": "Regex pattern"},
                        "file_pattern": {"type": "string", "description": "File glob pattern"}
                    },
                    "required": ["path", "regex"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in a directory",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path"},
                        "recursive": {"type": "boolean", "description": "Recursive listing"}
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Execute a CLI command",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Command to execute"},
                        "cwd": {"type": "string", "description": "Working directory"}
                    },
                    "required": ["command"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "ask_followup_question",
                "description": "Ask the user a clarifying question",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "Question to ask"},
                        "suggestions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Suggested answers"
                        }
                    },
                    "required": ["question"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "attempt_completion",
                "description": "Signal task completion",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "result": {"type": "string", "description": "Task result summary"}
                    },
                    "required": ["result"]
                }
            }
        }
    ]


async def discover_all_tools(
    force_refresh: bool = False,
    selected_servers: list[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Discover tools from configured MCP servers and Roo Code.
    
    Args:
        force_refresh: Force refresh of cached tools
        selected_servers: Optional list of specific servers to discover (None = all)
        
    Returns:
        Dictionary mapping server names to their tool lists
        Special key "roo_code" contains Roo Code's built-in tools
    """
    global _TOOL_CACHE, _CACHE_VERSION
    
    # Return cached tools if available and not forcing refresh
    if _TOOL_CACHE and not force_refresh and selected_servers is None:
        logger.debug(f"Using cached tools (version {_CACHE_VERSION})")
        return _TOOL_CACHE
    
    logger.info("Discovering tools from MCP servers...")
    discovered_tools: dict[str, list[dict[str, Any]]] = {}
    
    # Add Roo Code's built-in tools
    discovered_tools["roo_code"] = _get_roo_code_tools()
    logger.info(f"Added {len(discovered_tools['roo_code'])} Roo Code built-in tools")
    
    # Load MCP server configurations dynamically
    mcp_loader = get_mcp_loader()
    
    # Get list of servers to discover
    if selected_servers is None:
        # Discover all available servers
        server_list = mcp_loader.get_all_server_names()
        logger.info(f"Discovering tools from all {len(server_list)} configured servers")
    else:
        # Only discover selected servers
        server_list = selected_servers
        logger.info(f"Discovering tools from {len(server_list)} selected servers")
    
    # Discover tools from each MCP server
    for server_name in server_list:
        server_config = mcp_loader.get_server_config(server_name)
        if not server_config:
            logger.warning(f"No config found for server: {server_name}")
            continue
            
        try:
            logger.debug(f"Discovering tools from server: {server_name}")
            tools = await list_tools_for_server(server_name)
            
            if tools:
                # Convert MCP tool format to OpenAI function calling format
                converted_tools = _convert_mcp_tools_to_openai(tools, server_name)
                discovered_tools[server_name] = converted_tools
                logger.info(f"Discovered {len(converted_tools)} tools from {server_name}")
            else:
                logger.warning(f"No tools found for server: {server_name}")
                
        except Exception as e:
            logger.error(f"Failed to discover tools from {server_name}: {e}")
            continue
    
    # Update cache only if discovering all servers
    if selected_servers is None:
        _TOOL_CACHE = discovered_tools
        _CACHE_VERSION += 1
    
    total_tools = sum(len(tools) for tools in discovered_tools.values())
    logger.info(f"Tool discovery complete: {total_tools} tools from {len(discovered_tools)} sources")
    
    return discovered_tools


def _convert_mcp_tools_to_openai(
    mcp_tools: list[dict[str, Any]],
    server_name: str
) -> list[dict[str, Any]]:
    """Convert MCP tool format to OpenAI function calling format.
    
    Args:
        mcp_tools: List of tools in MCP format
        server_name: Name of the MCP server (for routing)
        
    Returns:
        List of tools in OpenAI function calling format
    """
    openai_tools = []
    
    for tool in mcp_tools:
        try:
            openai_tool = {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema", {}),
                },
                "_mcp_server": server_name  # Internal metadata for routing
            }
            openai_tools.append(openai_tool)
        except Exception as e:
            logger.warning(f"Failed to convert tool {tool.get('name', 'unknown')}: {e}")
            continue
    
    return openai_tools


def build_tool_map(discovered_tools: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """Build a mapping of tool names to their server names.
    
    Args:
        discovered_tools: Dictionary of server names to tool lists
        
    Returns:
        Dictionary mapping tool names to server names
    """
    tool_map = {}
    
    for server_name, tools in discovered_tools.items():
        for tool in tools:
            tool_name = tool["function"]["name"]
            if tool_name in tool_map:
                logger.warning(
                    f"Tool name collision: {tool_name} exists in both "
                    f"{tool_map[tool_name]} and {server_name}"
                )
            tool_map[tool_name] = server_name
    
    return tool_map


def get_all_tools_flat(discovered_tools: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Flatten all discovered tools into a single list.
    
    Args:
        discovered_tools: Dictionary of server names to tool lists
        
    Returns:
        Flattened list of all tools
    """
    all_tools = []
    for tools in discovered_tools.values():
        all_tools.extend(tools)
    return all_tools
