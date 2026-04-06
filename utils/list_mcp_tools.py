#!/usr/bin/env python3
"""
MCP Server Tool Discovery Script v6
Fixed asyncio.timeout usage for SSE servers.
"""

import asyncio
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

try:
    import websockets

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False


class TransportType(Enum):
    STDIO = "stdio"
    SSE = "sse"
    WEBSOCKET = "websocket"


@dataclass
class ServerResult:
    name: str
    transport: TransportType
    command: str
    tools: list[dict]
    error: str | None = None
    disabled: bool = False


def get_mcp_config_paths() -> list[Path]:
    """Get possible MCP configuration file paths."""
    home = Path.home()

    paths = [
        # Roo Code global settings
        home / "srv/apps/model-proxy/.roo/mcp_settings.json",
        # Project-level configs (check common locations)
        home / "srv/apps/model-proxy/.roo/mcp.json",
    ]
    return [p for p in paths if p.exists()]


def detect_transport(server_config: dict[str, Any]) -> TransportType:
    if "transport" in server_config:
        transport = server_config["transport"].lower()
        if transport == "sse":
            return TransportType.SSE
        elif transport in ("ws", "websocket"):
            return TransportType.WEBSOCKET

    if "url" in server_config:
        url = server_config["url"].lower()
        if "sse" in url or url.startswith("http"):
            return TransportType.SSE
        elif url.startswith("ws"):
            return TransportType.WEBSOCKET

    if server_config.get("command") == "websocat":
        return TransportType.WEBSOCKET

    return TransportType.STDIO


def load_mcp_config(config_path: Path) -> dict[str, Any]:
    with open(config_path) as f:
        config = json.load(f)

    if "mcpServers" in config:
        return config["mcpServers"]
    elif "servers" in config:
        return config["servers"]
    return config


async def read_json_line(stream, timeout: float = 30.0) -> dict | None:
    """Read lines until we get valid JSON, skipping non-JSON output."""
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            line = await asyncio.wait_for(stream.readline(), timeout=min(remaining, 5.0))
            if not line:
                return None

            line_str = line.decode().strip()
            if not line_str:
                continue

            try:
                return json.loads(line_str)
            except json.JSONDecodeError:
                continue

        except asyncio.TimeoutError:
            continue

    return None


async def query_stdio_server(name: str, server_config: dict[str, Any]) -> ServerResult:
    command = server_config.get("command")
    args = server_config.get("args", [])
    env_additions = server_config.get("env", {})

    env = os.environ.copy()
    for key, value in env_additions.items():
        if isinstance(value, str):
            value = value.replace("${workspaceFolder}", str(Path.cwd()))
            value = value.replace("${workspaceFolderBasename}", Path.cwd().name)
            for env_key, env_val in os.environ.items():
                value = value.replace(f"${{{env_key}}}", env_val)
        env[key] = value

    expanded_args = []
    for arg in args:
        if isinstance(arg, str):
            arg = arg.replace("${workspaceFolder}", str(Path.cwd()))
            arg = arg.replace("${workspaceFolderBasename}", Path.cwd().name)
        expanded_args.append(arg)

    cmd_str = f"{command} {' '.join(expanded_args)}"

    if not command:
        return ServerResult(
            name=name,
            transport=TransportType.STDIO,
            command="",
            tools=[],
            error="No command specified",
        )

    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            command,
            *expanded_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        if process.stdin is None or process.stdout is None:
            return ServerResult(
                name=name,
                transport=TransportType.STDIO,
                command=cmd_str,
                tools=[],
                error="Subprocess missing stdin/stdout",
            )

        stdin = process.stdin
        stdout = process.stdout

        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-tool-lister", "version": "1.0.0"},
            },
        }

        stdin.write((json.dumps(init_request) + "\n").encode())
        await stdin.drain()

        init_response = await read_json_line(stdout, timeout=30.0)
        if not init_response:
            return ServerResult(
                name=name,
                transport=TransportType.STDIO,
                command=cmd_str,
                tools=[],
                error="No valid JSON response (timeout or non-JSON output)",
            )

        initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        stdin.write((json.dumps(initialized) + "\n").encode())
        await stdin.drain()

        tools_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        stdin.write((json.dumps(tools_request) + "\n").encode())
        await stdin.drain()

        tools_response = await read_json_line(process.stdout, timeout=15.0)
        if not tools_response:
            return ServerResult(
                name=name,
                transport=TransportType.STDIO,
                command=cmd_str,
                tools=[],
                error="No tools response",
            )

        tools = tools_response.get("result", {}).get("tools", [])
        return ServerResult(name=name, transport=TransportType.STDIO, command=cmd_str, tools=tools)

    except FileNotFoundError:
        return ServerResult(
            name=name,
            transport=TransportType.STDIO,
            command=cmd_str,
            tools=[],
            error=f"Command not found: {command}",
        )
    except Exception as e:
        return ServerResult(
            name=name, transport=TransportType.STDIO, command=cmd_str, tools=[], error=str(e)
        )
    finally:
        if process:
            with suppress(BaseException):
                process.terminate()
            with suppress(BaseException):
                await asyncio.wait_for(process.wait(), timeout=2.0)
            with suppress(BaseException):
                process.kill()


async def read_sse_event(lines_iter, timeout: float = 10.0) -> tuple[str | None, str | None]:
    """Read next SSE event from async line iterator with timeout."""
    event_type = None
    data_lines = []

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
        return await asyncio.wait_for(_read(), timeout=timeout)
    except asyncio.TimeoutError:
        return None, None


async def query_sse_server(name: str, server_config: dict[str, Any]) -> ServerResult:
    """
    MCP SSE Protocol:
    1. GET /sse -> receive endpoint event with message URL
    2. POST to message URL -> 202 accepted
    3. Read response from SSE stream as message event
    """

    url = server_config.get("url", "")

    if not HTTPX_AVAILABLE:
        return ServerResult(
            name=name,
            transport=TransportType.SSE,
            command=url,
            tools=[],
            error="httpx not installed (pip install httpx)",
        )

    if not url:
        return ServerResult(
            name=name, transport=TransportType.SSE, command="", tools=[], error="No URL specified"
        )

    # Parse base URL
    if "/sse" in url:
        base_url = url.rsplit("/sse", 1)[0]
        sse_url = url
    else:
        base_url = url.rstrip("/")
        sse_url = f"{base_url}/sse"

    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client,
            client.stream("GET", sse_url) as resp,
        ):
            if resp.status_code != 200:
                return ServerResult(
                    name=name,
                    transport=TransportType.SSE,
                    command=url,
                    tools=[],
                    error=f"SSE connection failed: {resp.status_code}",
                )

            lines_iter = resp.aiter_lines()

            # Step 1: Get message endpoint from endpoint event
            evt, data = await read_sse_event(lines_iter, timeout=10.0)
            if not data:
                return ServerResult(
                    name=name,
                    transport=TransportType.SSE,
                    command=url,
                    tools=[],
                    error="Timeout waiting for endpoint",
                )

            msg_url = f"{base_url}{data}"

            # Step 2: Send initialize request
            r = await client.post(
                msg_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "mcp-tool-lister", "version": "1.0.0"},
                    },
                },
            )
            if r.status_code not in (200, 202, 204):
                return ServerResult(
                    name=name,
                    transport=TransportType.SSE,
                    command=url,
                    tools=[],
                    error=f"Initialize POST failed: {r.status_code}",
                )

            # Step 3: Read initialize response from SSE
            evt, data = await read_sse_event(lines_iter, timeout=15.0)
            if evt != "message":
                return ServerResult(
                    name=name,
                    transport=TransportType.SSE,
                    command=url,
                    tools=[],
                    error=f"Expected message event, got: {evt}",
                )

            # Step 4: Send initialized notification
            await client.post(
                msg_url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
            )

            # Step 5: Send tools/list request
            await client.post(
                msg_url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            )

            # Step 6: Read tools response from SSE
            evt, data = await read_sse_event(lines_iter, timeout=15.0)
            if evt == "message" and data:
                try:
                    msg = json.loads(data)
                    tools = msg.get("result", {}).get("tools", [])
                    return ServerResult(
                        name=name, transport=TransportType.SSE, command=url, tools=tools
                    )
                except json.JSONDecodeError as e:
                    return ServerResult(
                        name=name,
                        transport=TransportType.SSE,
                        command=url,
                        tools=[],
                        error=f"Invalid JSON: {e}",
                    )

            return ServerResult(
                name=name,
                transport=TransportType.SSE,
                command=url,
                tools=[],
                error="No tools response received",
            )

    except httpx.ConnectError:
        return ServerResult(
            name=name,
            transport=TransportType.SSE,
            command=url,
            tools=[],
            error="Connection refused - server not running?",
        )
    except Exception as e:
        return ServerResult(
            name=name, transport=TransportType.SSE, command=url, tools=[], error=str(e)
        )


async def query_websocket_server(name: str, server_config: dict[str, Any]) -> ServerResult:
    command = server_config.get("command", "")
    args = server_config.get("args", [])

    url = server_config.get("url", "")
    if not url and args:
        for arg in args:
            if arg.startswith("ws://") or arg.startswith("wss://"):
                url = arg
                break

    if not WEBSOCKETS_AVAILABLE:
        return ServerResult(
            name=name,
            transport=TransportType.WEBSOCKET,
            command=url or f"{command} {' '.join(args)}",
            tools=[],
            error="websockets not installed (pip install websockets)",
        )

    if not url:
        return ServerResult(
            name=name,
            transport=TransportType.WEBSOCKET,
            command=f"{command} {' '.join(args)}",
            tools=[],
            error="No WebSocket URL found",
        )

    try:
        async with websockets.connect(url, close_timeout=5) as ws:
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "mcp-tool-lister", "version": "1.0.0"},
                },
            }

            await ws.send(json.dumps(init_request))
            await asyncio.wait_for(ws.recv(), timeout=10.0)

            await ws.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

            tools_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            await ws.send(json.dumps(tools_request))

            tools_response = await asyncio.wait_for(ws.recv(), timeout=10.0)
            response_data = json.loads(tools_response)
            tools = response_data.get("result", {}).get("tools", [])

            return ServerResult(
                name=name, transport=TransportType.WEBSOCKET, command=url, tools=tools
            )

    except asyncio.TimeoutError:
        return ServerResult(
            name=name, transport=TransportType.WEBSOCKET, command=url, tools=[], error="Timeout"
        )
    except ConnectionRefusedError:
        return ServerResult(
            name=name,
            transport=TransportType.WEBSOCKET,
            command=url,
            tools=[],
            error="Connection refused - server not running?",
        )
    except Exception as e:
        return ServerResult(
            name=name, transport=TransportType.WEBSOCKET, command=url, tools=[], error=str(e)
        )


async def query_server(name: str, server_config: dict[str, Any]) -> ServerResult:
    if server_config.get("disabled", False):
        transport = detect_transport(server_config)
        return ServerResult(name=name, transport=transport, command="", tools=[], disabled=True)

    transport = detect_transport(server_config)

    if transport == TransportType.SSE:
        return await query_sse_server(name, server_config)
    elif transport == TransportType.WEBSOCKET:
        return await query_websocket_server(name, server_config)
    else:
        return await query_stdio_server(name, server_config)


def print_server_result(result: ServerResult) -> None:
    transport_icon = {
        TransportType.STDIO: "📟",
        TransportType.SSE: "🌐",
        TransportType.WEBSOCKET: "🔌",
    }

    print(f"\n{'='*70}")
    print(f"{transport_icon.get(result.transport, '❓')} {result.name} [{result.transport.value}]")
    print(f"{'='*70}")

    if result.disabled:
        print("   ⏸️  Disabled")
        return

    if result.error:
        print(f"   ❌ Error: {result.error}")
        return

    tools = result.tools
    if not tools:
        print("   ℹ️  No tools reported")
        return

    print(f"   📦 {len(tools)} tool(s):\n")

    for tool in sorted(tools, key=lambda t: t.get("name", "")):
        tname = tool.get("name", "unnamed")
        desc = tool.get("description", "")
        if len(desc) > 80:
            desc = desc[:77] + "..."

        print(f"   • {tname}")
        if desc:
            print(f"     {desc}")

        schema = tool.get("inputSchema", {})
        props = schema.get("properties", {})
        required = schema.get("required", [])

        if props:
            params = []
            for pname, pinfo in list(props.items())[:5]:
                ptype = pinfo.get("type", "any")
                req = "*" if pname in required else ""
                params.append(f"{pname}{req}:{ptype}")
            if len(props) > 5:
                params.append(f"... +{len(props)-5} more")
            print(f"     Args: {', '.join(params)}")
        print()


def print_summary(results: list[ServerResult]) -> None:
    total_tools = 0
    by_transport = {t: {"count": 0, "tools": 0, "errors": 0, "disabled": 0} for t in TransportType}

    for r in results:
        by_transport[r.transport]["count"] += 1
        if r.disabled:
            by_transport[r.transport]["disabled"] += 1
        elif r.error:
            by_transport[r.transport]["errors"] += 1
        else:
            by_transport[r.transport]["tools"] += len(r.tools)
            total_tools += len(r.tools)

    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)

    successful = sum(1 for r in results if not r.error and not r.disabled)
    failed = sum(1 for r in results if r.error)
    disabled = sum(1 for r in results if r.disabled)

    print(
        f"\n   Servers: {len(results)} total ({successful} ok, {failed} failed, {disabled} disabled)"
    )
    print(f"   Tools discovered: {total_tools}")

    print("\n   By transport:")
    for transport, stats in by_transport.items():
        if stats["count"] > 0:
            icon = {"stdio": "📟", "sse": "🌐", "websocket": "🔌"}.get(transport.value, "❓")
            print(f"   {icon} {transport.value}: {stats['count']} servers, {stats['tools']} tools")

    all_tools = []
    for r in results:
        if not r.error and not r.disabled:
            for t in r.tools:
                all_tools.append((t.get("name", "?"), r.name))

    if all_tools:
        print(f"\n   All {len(all_tools)} tools:")
        for tool_name, server_name in sorted(all_tools):
            print(f"   • {tool_name} ({server_name})")


async def main():
    print("🔍 MCP Server Tool Discovery v6")
    print("=" * 70)

    if not HTTPX_AVAILABLE:
        print("⚠️  httpx not installed - SSE servers won't work")
    if not WEBSOCKETS_AVAILABLE:
        print("⚠️  websockets not installed - WebSocket servers won't work")

    config_paths = get_mcp_config_paths()

    if not config_paths:
        print("\n❌ No MCP configuration files found!")
        return

    print("\n📁 Config files:")
    for p in config_paths:
        print(f"   • {p}")

    all_servers = {}
    for config_path in config_paths:
        try:
            servers = load_mcp_config(config_path)
            print(f"   → {len(servers)} server(s) from {config_path.name}")
            all_servers.update(servers)
        except Exception as e:
            print(f"   ❌ Error loading {config_path}: {e}")

    if not all_servers:
        print("\n❌ No MCP servers configured!")
        return

    print(f"\n🚀 Querying {len(all_servers)} server(s)...\n")

    tasks = [query_server(name, config) for name, config in all_servers.items()]
    results = await asyncio.gather(*tasks)

    for result in sorted(results, key=lambda r: r.name.lower()):
        print_server_result(result)

    print_summary(results)


if __name__ == "__main__":
    asyncio.run(main())
