"""MCP WebSocket server for abacus-proxy.

Modeled after model-proxy's MCP server:
- initialize
- notifications/initialized
- tools/list
- tools/call

This implementation delegates tool logic to [`src/mcp/tools.py`](src/mcp/tools.py:1),
which (for this service) routes all chat completion requests to a single upstream
RouteLLM endpoint using model="route-llm".
"""

from __future__ import annotations

import json
from contextlib import suppress
from http import HTTPStatus
from typing import Any

import websockets
from loguru import logger
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from src.mcp.errors import MCPToolError
from src.mcp.tools import (
    get_model_proxy_url,
    list_available_models,
    mcp_call,
    mcp_list_servers,
    mcp_list_tools,
    send_chat_request,
    stream_chat_request,
)


class MCPServer:
    """Model Context Protocol server for AI agent integration with abacus-proxy."""

    def __init__(
        self,
        host: str = "0.0.0.0",  # nosec B104
        port: int = 9002,
        debug: bool = False,
        app_resources: dict[str, Any] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.debug = debug
        self.connections: dict[str, Any] = {}
        self.app_resources = app_resources or {}
        self.tools = self._register_tools()
        self._bridged_tools_cache: list[dict[str, Any]] | None = None

    def _register_tools(self) -> dict[str, Any]:
        """Register available MCP tools."""
        return {
            "send_chat_request": {
                "name": "send_chat_request",
                "description": (
                    "Send a chat completion request "
                    "(routes to RouteLLM with model='route-llm')"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": (
                                "Client-requested model id (accepted for compatibility)"
                            ),
                        },
                        "messages": {
                            "type": "array",
                            "description": "Array of message objects",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "role": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["role", "content"],
                                "additionalProperties": False,
                            },
                        },
                        "temperature": {
                            "type": "number",
                            "description": "Sampling temperature (0-2)",
                            "default": 1.0,
                        },
                        "max_tokens": {
                            "type": "integer",
                            "description": "Maximum tokens to generate",
                        },
                    },
                    "required": ["model", "messages"],
                    "additionalProperties": False,
                },
            },
            "stream_chat_request": {
                "name": "stream_chat_request",
                "description": (
                    "Send a streaming chat completion request "
                    "(info-only; MCP does not stream)"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": (
                                "Client-requested model id (accepted for compatibility)"
                            ),
                        },
                        "messages": {
                            "type": "array",
                            "description": "Array of message objects",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "role": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["role", "content"],
                                "additionalProperties": False,
                            },
                        },
                        "temperature": {"type": "number", "default": 1.0},
                        "max_tokens": {"type": "integer"},
                    },
                    "required": ["model", "messages"],
                    "additionalProperties": False,
                },
            },
            "list_available_models": {
                "name": "list_available_models",
                "description": "List models available from the upstream RouteLLM service",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            "get_model_proxy_url": {
                "name": "get_model_proxy_url",
                "description": "Get the current ABACUS_PROXY_URL (or MODEL_PROXY_URL fallback)",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            "mcp_list_servers": {
                "name": "mcp_list_servers",
                "description": "List MCP servers configured/allowlisted for the MCP bridge (no secrets)",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            "mcp_list_tools": {
                "name": "mcp_list_tools",
                "description": "List tools available on a configured external MCP server (bridge discovery)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server": {
                            "type": "string",
                            "description": "MCP server name",
                        },
                    },
                    "required": ["server"],
                    "additionalProperties": False,
                },
            },
            "mcp_call": {
                "name": "mcp_call",
                "description": "Call a tool on a configured external MCP server (generic bridge)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "server": {
                            "type": "string",
                            "description": "MCP server name (required if tool_ref not provided)",
                        },
                        "tool": {
                            "type": "string",
                            "description": "Tool name on the target MCP server (required if tool_ref not provided)",
                        },
                        "tool_ref": {
                            "type": "string",
                            "description": "Optional shorthand: 'server/tool' (if provided, server/tool can be omitted)",
                        },
                        "arguments": {
                            "type": "object",
                            "description": "Tool arguments payload (JSON object)",
                            "default": {},
                        },
                    },
                    "required": ["arguments"],
                    "additionalProperties": False,
                },
            },
        }

    def _cfg_int(self, key: str, default: int) -> int:
        cfg = self.app_resources.get("config")
        try:
            v = getattr(cfg, key)
        except Exception:
            return default
        try:
            return int(v)
        except Exception:
            return default

    async def _load_bridged_tools(self) -> list[dict[str, Any]]:
        """Load and cache all bridged tools from configured MCP servers."""
        if self._bridged_tools_cache is not None:
            return self._bridged_tools_cache

        bridged_tools: list[dict[str, Any]] = []

        try:
            # Get list of configured servers
            servers_result = await mcp_list_servers(self.app_resources, {})
            if servers_result.get("status") != "success":
                logger.warning("[MCP] Failed to list servers for bridged tools")
                self._bridged_tools_cache = []
                return []

            servers = servers_result.get("servers", [])
            for server_info in servers:
                if not isinstance(server_info, dict):
                    continue

                server_name = server_info.get("name")
                if not isinstance(server_name, str) or not server_name.strip():
                    continue

                # Skip if server is not enabled
                if not server_info.get("bridge_enabled") or not server_info.get("server_enabled"):
                    continue

                try:
                    # Get tools for this server
                    tools_result = await mcp_list_tools(self.app_resources, {"server": server_name})
                    if tools_result.get("status") != "success":
                        continue

                    tools = tools_result.get("tools", [])
                    for tool in tools:
                        if not isinstance(tool, dict):
                            continue

                        tool_name = tool.get("name")
                        if not isinstance(tool_name, str):
                            continue

                        # Create a namespaced tool name to avoid collisions
                        namespaced_name = f"{server_name}__{tool_name}"

                        # Extract schema and description
                        input_schema = tool.get("inputSchema", {})
                        description = tool.get("description", f"Bridged tool: {server_name}/{tool_name}")

                        bridged_tools.append({
                            "name": namespaced_name,
                            "description": f"[{server_name}] {description}",
                            "inputSchema": input_schema,
                            "_bridge_server": server_name,
                            "_bridge_tool": tool_name,
                        })

                except Exception as e:
                    logger.warning(f"[MCP] Failed to load tools from server {server_name}: {e}")
                    continue

            self._bridged_tools_cache = bridged_tools
            logger.info(f"[MCP] Loaded {len(bridged_tools)} bridged tools from {len(servers)} servers")

        except Exception as e:
            logger.warning(f"[MCP] Failed to load bridged tools: {e}")
            self._bridged_tools_cache = []

        return self._bridged_tools_cache

    async def process_request(self, connection: Any, request: Request) -> Response | None:
        """Return HTTP 426 for non-WebSocket requests (avoids noisy InvalidUpgrade logs)."""
        connection_header = request.headers.get("Connection", "")
        if not connection_header or "upgrade" not in connection_header.lower():
            body = (
                b"WebSocket Upgrade Required\nThis endpoint only accepts WebSocket connections.\n"
            )
            headers = Headers(
                [
                    ("Content-Type", "text/plain"),
                    ("Content-Length", str(len(body))),
                    ("Upgrade", "websocket"),
                ]
            )
            return Response(
                status_code=HTTPStatus.UPGRADE_REQUIRED,
                reason_phrase="Upgrade Required",
                headers=headers,
                body=body,
            )
        return None

    async def handle_connection(self, websocket: Any, path: str = "") -> None:
        """Handle incoming WebSocket connection."""
        _ = path
        try:
            remote = websocket.remote_address
            connection_id = f"{remote[0]}:{remote[1]}"
        except Exception:
            connection_id = "unknown"

        self.connections[connection_id] = websocket
        logger.info(f"[MCP] New connection: {connection_id}")

        try:
            async for message in websocket:
                await self._handle_message(websocket, message, connection_id)
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"[MCP] Connection closed: {connection_id}")
        except Exception as e:
            logger.opt(exception=True).error(
                f"[MCP] Error handling connection {connection_id}: {e}"
            )
        finally:
            self.connections.pop(connection_id, None)

    async def _handle_message(self, websocket: Any, message: str, connection_id: str) -> None:
        """Handle incoming JSON-RPC message."""
        data: dict[str, Any] | None = None
        try:
            raw = json.loads(message)
            if not isinstance(raw, dict):
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request"},
                }
                await websocket.send(json.dumps(response, default=str))
                return

            data = raw

            if self.debug:
                logger.debug(f"[MCP] recv {connection_id}: {data}")

            if "method" in data:
                response = await self._handle_method_call(data, connection_id)
            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": data.get("id"),
                    "error": {"code": -32600, "message": "Invalid Request"},
                }

            await websocket.send(json.dumps(response, default=str))

        except json.JSONDecodeError:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            await websocket.send(json.dumps(error_response))
        except Exception as e:
            logger.opt(exception=True).error(f"[MCP] Error processing message {connection_id}: {e}")
            error_response = {
                "jsonrpc": "2.0",
                "id": data.get("id") if data is not None else None,
                "error": {"code": -32603, "message": "Internal error"},
            }
            with suppress(Exception):
                await websocket.send(json.dumps(error_response))

    async def _handle_method_call(self, data: dict[str, Any], connection_id: str) -> dict[str, Any]:
        """Handle JSON-RPC method calls."""
        _ = connection_id
        method = data.get("method")
        params = data.get("params", {}) or {}
        request_id = data.get("id")

        try:
            if method == "tools/list":
                # Combine native tools with bridged tools
                native_tools = list(self.tools.values())
                bridged_tools = await self._load_bridged_tools()
                all_tools = native_tools + bridged_tools

                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": all_tools},
                }

            if method == "tools/call":
                return await self._handle_tool_call(params, request_id)

            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "abacus-proxy-mcp", "version": "0.1.0"},
                    },
                }

            if method == "notifications/initialized":
                # Notification; respond with an empty result if client sent an id (rare).
                if request_id is None:
                    return {"jsonrpc": "2.0", "id": request_id, "result": {}}

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        except Exception as e:
            logger.opt(exception=True).error(f"[MCP] Error handling method {method}: {e}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "Internal error", "data": str(e)},
            }

    async def _handle_tool_call(self, params: dict[str, Any], request_id: Any) -> dict[str, Any]:
        """Handle tool execution calls."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {}) or {}

        # Check if this is a bridged tool (namespaced with server__tool)
        if "__" in str(tool_name):
            bridged_tools = await self._load_bridged_tools()
            for bridged_tool in bridged_tools:
                if bridged_tool.get("name") == tool_name:
                    # Extract server and tool names
                    server = bridged_tool.get("_bridge_server")
                    tool = bridged_tool.get("_bridge_tool")

                    if server and tool:
                        # Call the bridged tool via mcp_call
                        try:
                            result = await mcp_call(
                                self.app_resources,
                                {"server": server, "tool": tool, "arguments": arguments}
                            )
                            
                            # Extract the actual content from the mcp_call result
                            # mcp_call returns a dict with status, content, result, etc.
                            # We need to extract the actual tool result for MCP protocol
                            if isinstance(result, dict):
                                # If result has 'content' key (normalized MCP result), use it
                                if "content" in result:
                                    content_data = result["content"]
                                    # If content is already in MCP format, use it directly
                                    if isinstance(content_data, dict) and content_data.get("type") == "mcp_content":
                                        mcp_content = content_data.get("content", [])
                                    else:
                                        # Otherwise wrap the content
                                        mcp_content = [{"type": "text", "text": json.dumps(content_data, default=str)}]
                                # If result has 'result' key (raw MCP tool result), extract content from it
                                elif "result" in result:
                                    raw_result = result["result"]
                                    if isinstance(raw_result, dict) and "content" in raw_result:
                                        mcp_content = raw_result["content"]
                                    else:
                                        mcp_content = [{"type": "text", "text": json.dumps(raw_result, default=str)}]
                                else:
                                    # Fallback: wrap entire result
                                    mcp_content = [{"type": "text", "text": json.dumps(result, default=str)}]
                            else:
                                mcp_content = [{"type": "text", "text": json.dumps(result, default=str)}]
                            
                            return {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "result": {"content": mcp_content},
                            }
                        except MCPToolError as e:
                            logger.warning(f"[MCP] Bridged tool error {server}/{tool}: {e.message}")
                            return {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "error": {"code": e.code, "message": e.message, "data": e.data},
                            }

        # Handle native tools
        if tool_name not in self.tools:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"},
            }

        try:
            if tool_name == "send_chat_request":
                result = await send_chat_request(self.app_resources, arguments)
            elif tool_name == "stream_chat_request":
                result = await stream_chat_request(self.app_resources, arguments)
            elif tool_name == "list_available_models":
                result = await list_available_models(self.app_resources, arguments)
            elif tool_name == "get_model_proxy_url":
                result = await get_model_proxy_url(self.app_resources, arguments)
            elif tool_name == "mcp_list_servers":
                result = await mcp_list_servers(self.app_resources, arguments)
            elif tool_name == "mcp_list_tools":
                result = await mcp_list_tools(self.app_resources, arguments)
            elif tool_name == "mcp_call":
                result = await mcp_call(self.app_resources, arguments)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Tool not implemented: {tool_name}"},
                }

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, default=str)}]},
            }

        except MCPToolError as e:
            logger.warning(f"[MCP] Tool error {tool_name}: {e.message}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": e.code, "message": e.message, "data": e.data},
            }
        except Exception as e:
            logger.opt(exception=True).error(f"[MCP] Unexpected error in {tool_name}: {e}")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "Internal error", "data": str(e)},
            }

    async def start(self) -> None:
        """Start the MCP server."""
        max_size = self._cfg_int("mcp_max_message_size", 10 * 1024 * 1024)
        ping_interval = self._cfg_int("mcp_ping_interval", 20)
        ping_timeout = self._cfg_int("mcp_ping_timeout", 60)
        close_timeout = self._cfg_int("mcp_close_timeout", 10)

        logger.info(f"[MCP] Starting server on ws://{self.host}:{self.port}")

        async with websockets.serve(
            self.handle_connection,
            self.host,
            self.port,
            process_request=self.process_request,
            compression=None,
            max_size=max_size,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
            close_timeout=close_timeout,
        ) as server:
            logger.info(f"[MCP] Server started on ws://{self.host}:{self.port}")
            await server.serve_forever()

    async def stop(self) -> None:
        """Stop the MCP server."""
        logger.info("[MCP] Stopping server (closing connections)")
        connections_copy = list(self.connections.items())
        for _connection_id, websocket in connections_copy:
            with suppress(Exception):
                await websocket.close()
        self.connections.clear()
