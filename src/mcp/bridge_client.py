"""MCP bridge client runtime (stdio + SSE) for model-proxy.

This module implements the runtime requested by the MCP Bridge plan:
- Connect to configured external MCP servers (stdio and SSE).
- Execute tools/list and tools/call.
- Enforce per-server concurrency limits (best-effort).
- Apply timeouts (connect + per-call).
- Cache tools/list responses (TTL) to reduce discovery traffic.

Security/policy enforcement is performed by the caller (see src/mcp/tools.py).
This module focuses on transport mechanics and safe defaults.

NOTE:
- For stdio servers, we serialize requests over a single subprocess instance
  to avoid multiplexing complexity on a single stdout stream.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.mcp.errors import MCPToolError

try:
    import httpx

    _HTTPX_AVAILABLE = True
except Exception:  # pragma: no cover
    httpx = None  # type: ignore[assignment]
    _HTTPX_AVAILABLE = False


_JSON = dict[str, Any]


def _now_monotonic() -> float:
    return time.monotonic()


def _expand_vars(value: str) -> str:
    """Expand ${workspaceFolder}, ${workspaceFolderBasename}, and ${ENV} in config strings."""
    workspace = Path.cwd()
    out = value.replace("${workspaceFolder}", str(workspace))
    out = out.replace("${workspaceFolderBasename}", workspace.name)
    # Expand environment variables in ${NAME} form.
    for k, v in os.environ.items():
        out = out.replace(f"${{{k}}}", v)
    return out


def _load_mcp_config() -> dict[str, Any]:
    """Load MCP configuration with dynamic loader fallback.
    
    Priority:
    1. Manual config file (mcp_abacus_proxy_config.json) - DEPRECATED
    2. Dynamic loader from global + project settings
    """
    cfg_path = os.getenv("MCP_ABACUS_PROXY_CONFIG_PATH", "mcp_abacus_proxy_config.json")
    p = Path(cfg_path)
    
    # Try manual config first (deprecated but still supported)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise MCPToolError(
                    -32603,
                    f"Invalid MCP config (expected object): {cfg_path}",
                    {"error_type": "config_error", "path": str(cfg_path)},
                )
            return data
        except Exception as e:
            raise MCPToolError(
                -32603,
                f"Failed to parse MCP config: {cfg_path}",
                {"error_type": "config_error", "path": str(cfg_path), "detail": str(e)},
            ) from e
    
    # Use dynamic loader (loads from global + project settings)
    try:
        from src.utils.dynamic_mcp_loader import get_mcp_loader
        
        loader = get_mcp_loader()
        all_servers = loader.load_all_servers()
        
        if all_servers:
            # Convert to bridge config format
            return {
                "mcp_bridge": {
                    "server_allowlist": list(all_servers.keys())
                },
                **all_servers  # Add all server configs at root level
            }
        
    except Exception as e:
        # Dynamic loader failed
        try:
            from loguru import logger
            logger.warning(f"MCP dynamic loader failed: {e}")
        except Exception:
            pass
    
    # No config available
    raise MCPToolError(
        -32603,
        f"MCP config not found: {cfg_path} (dynamic loader also failed)",
        {"error_type": "config_error", "path": str(cfg_path)},
    )


def _get_server_config(cfg: dict[str, Any], server: str) -> dict[str, Any]:
    raw = cfg.get(server)
    if not isinstance(raw, dict):
        return {}
    return raw


def _get_bridge_config(cfg: dict[str, Any]) -> dict[str, Any]:
    bridge = cfg.get("mcp_bridge")
    return bridge if isinstance(bridge, dict) else {}


def _merge_policy(bridge: dict[str, Any], server: str) -> dict[str, Any]:
    defaults = bridge.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}

    servers = bridge.get("servers")
    if not isinstance(servers, dict):
        servers = {}

    per = servers.get(server)
    if not isinstance(per, dict):
        per = {}

    merged = dict(defaults)
    merged.update(per)
    return merged


def _get_int(d: dict[str, Any], key: str, default: int) -> int:
    v = d.get(key, default)
    try:
        return int(v)
    except Exception:
        return default


@dataclass(frozen=True)
class ToolListCacheEntry:
    expires_at: float
    tools: list[dict[str, Any]]


class _ServerGate:
    """Per-server gate for max concurrency + stdio serialization."""

    def __init__(self) -> None:
        self.semaphore_by_server: dict[str, asyncio.Semaphore] = {}
        self.stdio_lock_by_server: dict[str, asyncio.Lock] = {}

    def get_semaphore(self, server: str, max_concurrent: int) -> asyncio.Semaphore:
        max_concurrent = max(1, int(max_concurrent))
        sem = self.semaphore_by_server.get(server)
        if sem is None or sem._value + len(getattr(sem, "_waiters", []) or []) != max_concurrent:  # type: ignore[attr-defined] # noqa: SLF001
            # (Re)create if missing or if max_concurrent changed.
            sem = asyncio.Semaphore(max_concurrent)
            self.semaphore_by_server[server] = sem
        return sem

    def get_stdio_lock(self, server: str) -> asyncio.Lock:
        lock = self.stdio_lock_by_server.get(server)
        if lock is None:
            lock = asyncio.Lock()
            self.stdio_lock_by_server[server] = lock
        return lock


_GATES = _ServerGate()

# tools/list cache: server -> entry
_TOOL_LIST_CACHE: dict[str, ToolListCacheEntry] = {}
_TOOL_LIST_CACHE_LOCK = asyncio.Lock()


async def _read_json_line(stream: asyncio.StreamReader, timeout_s: float) -> dict[str, Any] | None:
    """Read stdout lines until we get valid JSON, skipping non-JSON output."""
    deadline = _now_monotonic() + timeout_s
    while _now_monotonic() < deadline:
        remaining = max(0.0, deadline - _now_monotonic())
        try:
            line = await asyncio.wait_for(stream.readline(), timeout=min(remaining, 5.0))
            if not line:
                return None
            line_str = line.decode(errors="replace").strip()
            if not line_str:
                continue
            try:
                obj = json.loads(line_str)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
        except asyncio.TimeoutError:
            continue
    return None


class StdioMCPClient:
    """A minimal stdio MCP JSON-RPC client with a persistent subprocess."""

    def __init__(self, server: str, server_cfg: dict[str, Any], connect_timeout_s: float) -> None:
        self.server = server
        self.server_cfg = server_cfg
        self.connect_timeout_s = connect_timeout_s

        self._proc: asyncio.subprocess.Process | None = None
        self._initialized = False
        self._next_id = 1

    async def _ensure_process(self) -> asyncio.subprocess.Process:
        if self._proc and self._proc.returncode is None:
            return self._proc

        command = self.server_cfg.get("command")
        args = self.server_cfg.get("args", [])
        env_additions = self.server_cfg.get("env", {})

        if not isinstance(command, str) or not command.strip():
            raise MCPToolError(
                -32603,
                f"MCP server '{self.server}' is missing 'command'",
                {"error_type": "config_error", "server": self.server},
            )
        if not isinstance(args, list):
            args = []

        expanded_args: list[str] = []
        for a in args:
            if isinstance(a, str):
                expanded_args.append(_expand_vars(a))
            else:
                expanded_args.append(str(a))

        env = os.environ.copy()
        if isinstance(env_additions, dict):
            for k, v in env_additions.items():
                if isinstance(k, str) and isinstance(v, str):
                    env[k] = _expand_vars(v)

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    command,
                    *expanded_args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                ),
                timeout=self.connect_timeout_s,
            )
        except asyncio.TimeoutError as e:
            raise MCPToolError(
                -32603,
                "MCP stdio connect timeout",
                {"error_type": "timeout", "server": self.server},
            ) from e
        except FileNotFoundError as e:
            raise MCPToolError(
                -32603,
                f"Command not found: {command}",
                {"error_type": "connection_error", "server": self.server},
            ) from e
        except Exception as e:
            raise MCPToolError(
                -32603,
                f"Failed to start MCP stdio server: {e}",
                {"error_type": "connection_error", "server": self.server},
            ) from e

        self._proc = proc
        self._initialized = False
        self._next_id = 1
        return proc

    async def _send(self, msg: dict[str, Any]) -> None:
        proc = await self._ensure_process()
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def _request(
        self, method: str, params: dict[str, Any], timeout_s: float
    ) -> dict[str, Any]:
        proc = await self._ensure_process()
        assert proc.stdout is not None

        req_id = self._next_id
        self._next_id += 1

        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})

        resp = await _read_json_line(proc.stdout, timeout_s=timeout_s)
        if not resp:
            raise MCPToolError(
                -32603,
                "No JSON response from MCP stdio server",
                {"error_type": "timeout", "server": self.server, "method": method},
            )

        # Best-effort: ensure it's the response for our request id.
        # (We serialize requests per process so this should hold.)
        if resp.get("id") != req_id and "error" not in resp and "result" not in resp:
            # Keep reading a bit; server may emit non-response notifications.
            more = await _read_json_line(proc.stdout, timeout_s=timeout_s)
            if more:
                resp = more

        if "error" in resp and isinstance(resp["error"], dict):
            err = resp["error"]
            err_code = err.get("code")
            code = int(err_code) if isinstance(err_code, int) else -32603
            msg = err.get("message")
            message = str(msg) if isinstance(msg, str) and msg.strip() else "MCP tool error"

            error_type = "tool_error"
            if code == -32601 and "tool not found" in message.lower():
                error_type = "tool_not_found"
            elif code == -32602:
                error_type = "invalid_arguments"

            # Keep a small, safe subset of the remote error payload (truncate large strings).
            remote_error: dict[str, Any] = {}
            for k in ("code", "message", "data"):
                if k in err:
                    remote_error[k] = err.get(k)

            if isinstance(remote_error.get("message"), str) and len(remote_error["message"]) > 2000:
                remote_error["message"] = remote_error["message"][:2000] + "…"
            if isinstance(remote_error.get("data"), str) and len(remote_error["data"]) > 2000:
                remote_error["data"] = remote_error["data"][:2000] + "…"

            raise MCPToolError(
                code,
                message,
                {
                    "error_type": error_type,
                    "server": self.server,
                    "method": method,
                    "remote_error": remote_error,
                },
            )
        return resp

    async def ensure_initialized(self, timeout_s: float) -> None:
        if self._initialized:
            return

        # initialize
        init_params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "abacus-proxy-mcp-bridge", "version": "1.0.0"},
        }
        _ = await self._request("initialize", init_params, timeout_s=timeout_s)

        # notifications/initialized
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        self._initialized = True

    async def list_tools(self, timeout_s: float) -> list[dict[str, Any]]:
        await self.ensure_initialized(timeout_s=timeout_s)
        resp = await self._request("tools/list", {}, timeout_s=timeout_s)
        tools = resp.get("result", {}).get("tools", [])
        return tools if isinstance(tools, list) else []

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], timeout_s: float
    ) -> dict[str, Any]:
        await self.ensure_initialized(timeout_s=timeout_s)
        resp = await self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout_s=timeout_s,
        )
        result = resp.get("result")
        return result if isinstance(result, dict) else {"raw": result}

    async def close(self) -> None:
        if not self._proc:
            return
        proc = self._proc
        self._proc = None
        self._initialized = False
        with contextlib.suppress(BaseException):
            proc.terminate()
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        with contextlib.suppress(BaseException):
            proc.kill()


def _parse_sse_base(url: str) -> tuple[str, str]:
    """Return (base_url, sse_url)."""
    url = url.strip()
    if "/sse" in url:
        base_url = url.rsplit("/sse", 1)[0]
        sse_url = url
    else:
        base_url = url.rstrip("/")
        sse_url = f"{base_url}/sse"
    return base_url, sse_url


async def _read_sse_event(lines_iter, timeout_s: float) -> tuple[str | None, str | None]:
    """Read next SSE event from async line iterator with timeout."""
    event_type: str | None = None
    data_lines: list[str] = []

    async def _read():
        nonlocal event_type, data_lines
        async for line in lines_iter:
            line = line.strip()
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line == "" and (event_type or data_lines):
                return event_type, "\n".join(data_lines)
        return None, None

    try:
        return await asyncio.wait_for(_read(), timeout=timeout_s)
    except asyncio.TimeoutError:
        return None, None


class SseMCPClient:
    """A minimal MCP SSE client based on the same approach as utils/list_mcp_tools.py."""

    def __init__(self, server: str, url: str, connect_timeout_s: float) -> None:
        self.server = server
        self.url = url
        self.connect_timeout_s = connect_timeout_s

    async def list_tools(self, timeout_s: float) -> list[dict[str, Any]]:
        if not _HTTPX_AVAILABLE or httpx is None:
            raise MCPToolError(
                -32603,
                "httpx is required for SSE MCP servers",
                {"error_type": "dependency_error", "server": self.server},
            )

        base_url, sse_url = _parse_sse_base(self.url)

        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout_s, connect=self.connect_timeout_s)
                ) as client,
                client.stream("GET", sse_url) as resp,
            ):
                if resp.status_code != 200:
                    raise MCPToolError(
                        -32603,
                        f"SSE connection failed: {resp.status_code}",
                        {"error_type": "connection_error", "server": self.server, "url": sse_url},
                    )

                lines_iter = resp.aiter_lines()

                # Step 1: get message endpoint
                evt, data = await _read_sse_event(lines_iter, timeout_s=min(10.0, timeout_s))
                if not data:
                    raise MCPToolError(
                        -32603,
                        "Timeout waiting for SSE endpoint event",
                        {"error_type": "timeout", "server": self.server},
                    )

                msg_url = f"{base_url}{data}"

                # Step 2: initialize
                r = await client.post(
                    msg_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "abacus-proxy-mcp-bridge", "version": "1.0.0"},
                        },
                    },
                )
                if r.status_code not in (200, 202, 204):
                    raise MCPToolError(
                        -32603,
                        f"Initialize POST failed: {r.status_code}",
                        {"error_type": "connection_error", "server": self.server},
                    )

                # Step 3: read initialize response
                evt, data = await _read_sse_event(lines_iter, timeout_s=min(15.0, timeout_s))
                if evt != "message" or not data:
                    raise MCPToolError(
                        -32603,
                        f"Expected message event, got: {evt}",
                        {"error_type": "connection_error", "server": self.server},
                    )

                # Step 4: initialized notification
                await client.post(
                    msg_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
                )

                # Step 5: tools/list request
                await client.post(
                    msg_url,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                )

                # Step 6: read tools response
                evt, data = await _read_sse_event(lines_iter, timeout_s=min(15.0, timeout_s))
                if evt != "message" or not data:
                    raise MCPToolError(
                        -32603,
                        "No tools response received",
                        {"error_type": "timeout", "server": self.server},
                    )

                msg = json.loads(data)
                tools = msg.get("result", {}).get("tools", [])
                return tools if isinstance(tools, list) else []

        except MCPToolError:
            raise
        except httpx.ConnectError as e:
            raise MCPToolError(
                -32603,
                "Connection refused - SSE server not running?",
                {"error_type": "connection_error", "server": self.server},
            ) from e
        except asyncio.TimeoutError as e:
            raise MCPToolError(
                -32603,
                "Timeout talking to SSE server",
                {"error_type": "timeout", "server": self.server},
            ) from e
        except Exception as e:
            raise MCPToolError(
                -32603,
                f"SSE MCP error: {e}",
                {"error_type": "connection_error", "server": self.server},
            ) from e

    async def call_tool(
        self, *, tool_name: str, arguments: dict[str, Any], timeout_s: float
    ) -> dict[str, Any]:
        """
        Call an MCP tool over SSE.

        This uses the same session flow as list_tools:
        - open SSE stream
        - receive endpoint message path
        - POST initialize + notifications/initialized
        - POST tools/call
        - read result from SSE message event
        """
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise MCPToolError(
                -32602,
                "Missing required parameter: tool_name",
                {"error_type": "invalid_arguments", "server": self.server},
            )
        if not isinstance(arguments, dict):
            raise MCPToolError(
                -32602,
                "Parameter 'arguments' must be an object",
                {"error_type": "invalid_arguments", "server": self.server, "tool": tool_name},
            )

        if not _HTTPX_AVAILABLE or httpx is None:
            raise MCPToolError(
                -32603,
                "httpx is required for SSE MCP servers",
                {"error_type": "dependency_error", "server": self.server},
            )

        base_url, sse_url = _parse_sse_base(self.url)

        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout_s, connect=self.connect_timeout_s)
                ) as client,
                client.stream("GET", sse_url) as resp,
            ):
                if resp.status_code != 200:
                    raise MCPToolError(
                        -32603,
                        f"SSE connection failed: {resp.status_code}",
                        {"error_type": "connection_error", "server": self.server, "url": sse_url},
                    )

                lines_iter = resp.aiter_lines()

                # Step 1: get message endpoint
                evt, data = await _read_sse_event(lines_iter, timeout_s=min(10.0, timeout_s))
                if not data:
                    raise MCPToolError(
                        -32603,
                        "Timeout waiting for SSE endpoint event",
                        {"error_type": "timeout", "server": self.server},
                    )

                msg_url = f"{base_url}{data}"

                # Step 2: initialize
                r = await client.post(
                    msg_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "abacus-proxy-mcp-bridge", "version": "1.0.0"},
                        },
                    },
                )
                if r.status_code not in (200, 202, 204):
                    raise MCPToolError(
                        -32603,
                        f"Initialize POST failed: {r.status_code}",
                        {"error_type": "connection_error", "server": self.server},
                    )

                # Step 3: read initialize response
                evt, data = await _read_sse_event(lines_iter, timeout_s=min(15.0, timeout_s))
                if evt != "message" or not data:
                    raise MCPToolError(
                        -32603,
                        f"Expected message event, got: {evt}",
                        {"error_type": "connection_error", "server": self.server},
                    )

                # Step 4: initialized notification
                await client.post(
                    msg_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
                )

                # Step 5: tools/call request
                await client.post(
                    msg_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments},
                    },
                )

                # Step 6: read tools/call response
                evt, data = await _read_sse_event(lines_iter, timeout_s=min(30.0, timeout_s))
                if evt != "message" or not data:
                    raise MCPToolError(
                        -32603,
                        "No tools/call response received",
                        {"error_type": "timeout", "server": self.server, "tool": tool_name},
                    )

                msg = json.loads(data)
                if isinstance(msg, dict) and isinstance(msg.get("error"), dict):
                    err = msg["error"]
                    err_code = err.get("code")
                    code = int(err_code) if isinstance(err_code, int) else -32603
                    msg_txt = err.get("message")
                    message = (
                        str(msg_txt)
                        if isinstance(msg_txt, str) and msg_txt.strip()
                        else "MCP tool error"
                    )

                    error_type = "tool_error"
                    if code == -32601 and "tool not found" in message.lower():
                        error_type = "tool_not_found"
                    elif code == -32602:
                        error_type = "invalid_arguments"

                    remote_error: dict[str, Any] = {}
                    for k in ("code", "message", "data"):
                        if k in err:
                            remote_error[k] = err.get(k)

                    if (
                        isinstance(remote_error.get("message"), str)
                        and len(remote_error["message"]) > 2000
                    ):
                        remote_error["message"] = remote_error["message"][:2000] + "…"
                    if (
                        isinstance(remote_error.get("data"), str)
                        and len(remote_error["data"]) > 2000
                    ):
                        remote_error["data"] = remote_error["data"][:2000] + "…"

                    raise MCPToolError(
                        code,
                        message,
                        {
                            "error_type": error_type,
                            "server": self.server,
                            "tool": tool_name,
                            "remote_error": remote_error,
                        },
                    )

                result = msg.get("result")
                return result if isinstance(result, dict) else {"raw": result}

        except MCPToolError:
            raise
        except httpx.ConnectError as e:
            raise MCPToolError(
                -32603,
                "Connection refused - SSE server not running?",
                {"error_type": "connection_error", "server": self.server},
            ) from e
        except asyncio.TimeoutError as e:
            raise MCPToolError(
                -32603,
                "Timeout talking to SSE server",
                {"error_type": "timeout", "server": self.server},
            ) from e
        except Exception as e:
            raise MCPToolError(
                -32603,
                f"SSE MCP error: {e}",
                {"error_type": "connection_error", "server": self.server},
            ) from e


# Persistent stdio client instances: server -> client
_STDIO_CLIENTS: dict[str, StdioMCPClient] = {}
_STDIO_CLIENTS_LOCK = asyncio.Lock()


def _get_stdio_client(
    server: str, server_cfg: dict[str, Any], connect_timeout_s: float
) -> StdioMCPClient:
    cli = _STDIO_CLIENTS.get(server)
    if cli is None:
        cli = StdioMCPClient(
            server=server, server_cfg=server_cfg, connect_timeout_s=connect_timeout_s
        )
        _STDIO_CLIENTS[server] = cli
    return cli


async def close_stdio_client(server: str) -> None:
    """Close a persistent stdio client for a server (best-effort)."""
    async with _STDIO_CLIENTS_LOCK:
        cli = _STDIO_CLIENTS.pop(server, None)

    if cli is not None:
        await cli.close()


async def close_all_stdio_clients() -> None:
    """Close all persistent stdio clients (best-effort)."""
    async with _STDIO_CLIENTS_LOCK:
        clients = list(_STDIO_CLIENTS.items())
        _STDIO_CLIENTS.clear()

    for _, cli in clients:
        with contextlib.suppress(BaseException):
            await cli.close()


def _atexit_terminate_stdio_clients() -> None:
    """
    Best-effort cleanup if the process is exiting without an async shutdown.

    This avoids leaving orphaned MCP subprocesses around during local scripts/tests.
    """
    for cli in list(_STDIO_CLIENTS.values()):
        proc = getattr(cli, "_proc", None)
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int) and pid > 0:
            with contextlib.suppress(BaseException):
                os.kill(pid, signal.SIGTERM)


atexit.register(_atexit_terminate_stdio_clients)


def _get_transport(server_cfg: dict[str, Any]) -> str:
    t = server_cfg.get("type") or server_cfg.get("transport") or ""
    return str(t).lower().strip()


async def list_tools_for_server(server: str) -> list[dict[str, Any]]:
    """
    List tools for a configured MCP server, using caching and policy-configured timeouts.
    """
    cfg = _load_mcp_config()
    server_cfg = _get_server_config(cfg, server)
    if not server_cfg:
        raise MCPToolError(
            -32603,
            f"Unknown MCP server: {server}",
            {"error_type": "config_error", "server": server},
        )

    bridge = _get_bridge_config(cfg)
    policy = _merge_policy(bridge, server)

    timeout_s = _get_int(policy, "timeout_ms", 60000) / 1000.0
    connect_timeout_s = _get_int(policy, "connect_timeout_ms", 5000) / 1000.0
    max_concurrent = _get_int(policy, "max_concurrent", 1)

    schema_validation = bridge.get("schema_validation")
    ttl_s = 0
    if isinstance(schema_validation, dict):
        ttl_s = _get_int(schema_validation, "live_cache_ttl_seconds", 0)

    # Cache check
    if ttl_s > 0:
        async with _TOOL_LIST_CACHE_LOCK:
            entry = _TOOL_LIST_CACHE.get(server)
            if entry and entry.expires_at > _now_monotonic():
                return entry.tools

    sem = _GATES.get_semaphore(server, max_concurrent=max_concurrent)
    async with sem:
        transport = _get_transport(server_cfg)

        if transport in ("stdio", ""):
            # Serialize stdio requests on a single process.
            lock = _GATES.get_stdio_lock(server)
            async with lock:
                cli = _get_stdio_client(server, server_cfg, connect_timeout_s=connect_timeout_s)
                tools = await cli.list_tools(timeout_s=timeout_s)
        elif transport in ("sse", "http", "https"):
            url = server_cfg.get("url")
            if not isinstance(url, str) or not url.strip():
                raise MCPToolError(
                    -32603,
                    f"MCP SSE server '{server}' missing url",
                    {"error_type": "config_error", "server": server},
                )
            cli = SseMCPClient(server=server, url=url, connect_timeout_s=connect_timeout_s)
            tools = await cli.list_tools(timeout_s=timeout_s)
        else:
            raise MCPToolError(
                -32603,
                f"Unsupported MCP transport: {transport}",
                {"error_type": "config_error", "server": server, "transport": transport},
            )

    # Cache store
    if ttl_s > 0:
        async with _TOOL_LIST_CACHE_LOCK:
            _TOOL_LIST_CACHE[server] = ToolListCacheEntry(
                expires_at=_now_monotonic() + float(ttl_s),
                tools=tools,
            )

    return tools


async def call_tool_on_server(server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Call a tool on a configured MCP server.

    Returns the MCP tool 'result' object (dict with content, etc).
    """
    cfg = _load_mcp_config()
    server_cfg = _get_server_config(cfg, server)
    if not server_cfg:
        raise MCPToolError(
            -32603,
            f"Unknown MCP server: {server}",
            {"error_type": "config_error", "server": server},
        )

    bridge = _get_bridge_config(cfg)
    policy = _merge_policy(bridge, server)

    timeout_s = _get_int(policy, "timeout_ms", 60000) / 1000.0
    connect_timeout_s = _get_int(policy, "connect_timeout_ms", 5000) / 1000.0
    max_concurrent = _get_int(policy, "max_concurrent", 1)

    sem = _GATES.get_semaphore(server, max_concurrent=max_concurrent)
    async with sem:
        transport = _get_transport(server_cfg)

        if transport in ("stdio", ""):
            lock = _GATES.get_stdio_lock(server)
            async with lock:
                cli = _get_stdio_client(server, server_cfg, connect_timeout_s=connect_timeout_s)
                return await cli.call_tool(tool_name=tool, arguments=arguments, timeout_s=timeout_s)

        if transport in ("sse", "http", "https"):
            url = server_cfg.get("url")
            if not isinstance(url, str) or not url.strip():
                raise MCPToolError(
                    -32603,
                    f"MCP SSE server '{server}' missing url",
                    {"error_type": "config_error", "server": server},
                )
            cli = SseMCPClient(server=server, url=url, connect_timeout_s=connect_timeout_s)
            return await cli.call_tool(tool_name=tool, arguments=arguments, timeout_s=timeout_s)

        raise MCPToolError(
            -32603,
            f"Unsupported MCP transport: {transport}",
            {"error_type": "config_error", "server": server, "transport": transport},
        )
