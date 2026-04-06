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
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": list(self.tools.values())},
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
                    return {"jsonrpc": "2.0", "id": None, "result": {}}
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
