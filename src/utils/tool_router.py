"""Route tool calls to appropriate MCP servers.

Handles routing of tool calls from the model to the correct MCP server.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from src.mcp.bridge_client import call_tool_on_server
from src.mcp.errors import MCPToolError


async def route_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    tool_map: dict[str, str],
    bridge_config: dict[str, Any]
) -> dict[str, Any]:
    """Route a tool call to the appropriate MCP server.
    
    Args:
        tool_name: Name of the tool to call
        arguments: Tool arguments
        tool_map: Mapping of tool names to server names
        bridge_config: MCP bridge configuration
        
    Returns:
        Tool execution result
        
    Raises:
        MCPToolError: If tool routing or execution fails
    """
    # Look up which server owns this tool
    server_name = tool_map.get(tool_name)
    
    if not server_name:
        raise MCPToolError(
            -32601,
            f"Tool not found: {tool_name}",
            {"tool_name": tool_name, "available_servers": list(tool_map.keys())}
        )
    
    # Special handling for Roo Code tools (these are handled by Roo Code itself)
    if server_name == "roo_code":
        logger.debug(f"Tool {tool_name} is a Roo Code built-in tool, returning placeholder")
        return {
            "success": True,
            "message": f"Tool {tool_name} will be handled by Roo Code",
            "tool_name": tool_name,
            "arguments": arguments
        }
    
    # Route to external MCP server via bridge
    logger.info(f"Routing tool call: {tool_name} -> {server_name}")
    
    try:
        result = await call_tool_on_server(
            server_name=server_name,
            tool_name=tool_name,
            arguments=arguments,
            bridge_config=bridge_config
        )
        
        logger.debug(f"Tool call successful: {tool_name} on {server_name}")
        return result
        
    except MCPToolError as e:
        logger.error(f"Tool call failed: {tool_name} on {server_name}: {e.message}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error routing tool call: {tool_name}: {e}")
        raise MCPToolError(
            -32603,
            f"Internal error routing tool call: {str(e)}",
            {"tool_name": tool_name, "server_name": server_name}
        ) from e


async def route_multiple_tool_calls(
    tool_calls: list[dict[str, Any]],
    tool_map: dict[str, str],
    bridge_config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Route multiple tool calls in parallel.
    
    Args:
        tool_calls: List of tool calls to route
        tool_map: Mapping of tool names to server names
        bridge_config: MCP bridge configuration
        
    Returns:
        List of tool execution results
    """
    results = []
    
    for tool_call in tool_calls:
        try:
            tool_name = tool_call.get("function", {}).get("name")
            arguments_str = tool_call.get("function", {}).get("arguments", "{}")
            
            # Parse arguments if they're a string
            if isinstance(arguments_str, str):
                arguments = json.loads(arguments_str)
            else:
                arguments = arguments_str
            
            result = await route_tool_call(
                tool_name=tool_name,
                arguments=arguments,
                tool_map=tool_map,
                bridge_config=bridge_config
            )
            
            results.append({
                "tool_call_id": tool_call.get("id"),
                "role": "tool",
                "name": tool_name,
                "content": json.dumps(result)
            })
            
        except Exception as e:
            logger.error(f"Failed to route tool call: {e}")
            results.append({
                "tool_call_id": tool_call.get("id"),
                "role": "tool",
                "name": tool_call.get("function", {}).get("name", "unknown"),
                "content": json.dumps({
                    "error": str(e),
                    "error_type": type(e).__name__
                })
            })
    
    return results
