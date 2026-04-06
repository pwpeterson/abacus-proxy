#!/usr/bin/env python3
"""
Sync MCP tool schemas into tool_schema_registry.json using utils/list_mcp_tools.py discovery logic.

Why this exists:
- utils/list_mcp_tools.py has robust MCP discovery across transports (stdio/sse/websocket)
  and, in your environment, discovers more tools (e.g. 234) than the older registry sync script.
- This script keeps the "merge into OpenAI tool schema registry + provenance metadata" behavior
  from utils/sync_mcp_tools_to_registry.py.

Usage:
  # dry run (prints summary only)
  uv run python utils/sync_registry_mcp_tools.py

  # write tool_schema_registry.json
  uv run python utils/sync_registry_mcp_tools.py --write

Options:
  --registry PATH                 Output registry path (default: tool_schema_registry.json)
  --max-servers N                 Only query first N servers (0 = no limit)
  --include-non-openai-safe       Include tool names that don't match OpenAI tool naming constraints
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Import the discovery implementation (stdio/sse/websocket) and config parsing.
# This module is a script, but its functions/classes are import-safe.
from utils import list_mcp_tools

_OPENAI_TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_excluded(
    *,
    excluded: list[dict[str, Any]],
    excluded_counts: dict[str, int],
    max_samples: int,
    server: str,
    transport: str,
    command: str,
    tool_name: str | None,
    reason: str,
    detail: str | None = None,
) -> None:
    """Record an excluded tool with a reason and keep a capped sample list."""
    excluded_counts[reason] = excluded_counts.get(reason, 0) + 1

    if len(excluded) >= max_samples:
        return

    entry: dict[str, Any] = {
        "server": server,
        "transport": transport,
        "command": command,
        "tool": tool_name,
        "reason": reason,
    }
    if detail:
        entry["detail"] = detail
    excluded.append(entry)


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "description": "Tool schema registry for model-proxy server-side tool injection when client omits `tools`.",
            "tools": [],
        }

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {
            "version": 1,
            "description": "Tool schema registry for model-proxy server-side tool injection when client omits `tools`.",
            "tools": [],
        }

    # Ensure minimal required keys exist
    data.setdefault("version", 1)
    data.setdefault(
        "description",
        "Tool schema registry for model-proxy server-side tool injection when client omits `tools`.",
    )
    data.setdefault("tools", [])
    return data


def _write_registry(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _mcp_tool_to_openai_schema(tool: dict[str, Any]) -> dict[str, Any] | None:
    """
    Convert an MCP tool schema (from tools/list) into an OpenAI Chat Completions tool schema.
    Expected MCP keys:
      - name: str
      - description: str (optional)
      - inputSchema: JSON schema dict
    """
    if not isinstance(tool, dict):
        return None

    name = tool.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    input_schema = tool.get("inputSchema")
    if not isinstance(input_schema, dict):
        return None

    description = tool.get("description")
    if not isinstance(description, str):
        description = ""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema,
        },
    }


def _index_registry_tools(tools: list[Any]) -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") != "function":
            continue
        fn = t.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if isinstance(name, str) and name.strip():
            by_name[name] = t
    return by_name


async def _discover_all_servers(
    *,
    max_servers: int,
) -> tuple[list[list_mcp_tools.ServerResult], dict[str, dict[str, Any]]]:
    """
    Returns:
      - server results list (one per server)
      - merged server config dict (name -> config)
    """
    config_paths = list_mcp_tools.get_mcp_config_paths()
    if not config_paths:
        raise RuntimeError(
            "No MCP configuration files found via list_mcp_tools.get_mcp_config_paths()"
        )

    merged_servers: dict[str, dict[str, Any]] = {}
    for config_path in config_paths:
        servers = list_mcp_tools.load_mcp_config(config_path)
        if not isinstance(servers, dict):
            continue
        merged_servers.update(servers)

    items = sorted(merged_servers.items(), key=lambda kv: kv[0].lower())
    if max_servers and max_servers > 0:
        items = items[:max_servers]

    tasks = [list_mcp_tools.query_server(name, cfg) for name, cfg in items]
    results = await asyncio.gather(*tasks)
    return results, dict(items)


def _summarize_results(results: list[list_mcp_tools.ServerResult]) -> dict[str, Any]:
    servers_total = len(results)
    servers_disabled = len([r for r in results if getattr(r, "disabled", False)])
    servers_failed = [r.name for r in results if (not r.disabled and bool(r.error))]
    servers_ok = len([r for r in results if (not r.disabled and not r.error)])

    tools_total = 0
    for r in results:
        if r.disabled or r.error:
            continue
        tools_total += len(r.tools or [])

    return {
        "servers_total": servers_total,
        "servers_ok": servers_ok,
        "servers_failed": servers_failed,
        "servers_disabled": servers_disabled,
        "mcp_tools_total": tools_total,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", default="tool_schema_registry.json")
    parser.add_argument(
        "--max-servers",
        type=int,
        default=0,
        help="Only query first N servers (0 = no limit)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write updates to tool_schema_registry.json",
    )
    parser.add_argument(
        "--include-non-openai-safe",
        action="store_true",
        help="Include tool names that don't match OpenAI constraints (may break some providers)",
    )
    parser.add_argument(
        "--excluded-sample-limit",
        type=int,
        default=200,
        help="Max number of excluded tool entries to include in output (default: 200)",
    )
    args = parser.parse_args()

    results, servers_used = await _discover_all_servers(max_servers=int(args.max_servers or 0))

    registry_path = Path(args.registry)
    registry = _load_registry(registry_path)

    existing_tools_raw = registry.get("tools")
    existing_tools: list[Any] = existing_tools_raw if isinstance(existing_tools_raw, list) else []
    tool_by_name = _index_registry_tools(existing_tools)

    sources_raw = registry.get("sources")
    sources: dict[str, Any] = sources_raw if isinstance(sources_raw, dict) else {}

    skipped_non_openai_safe = 0
    discovered_mcp_tools = 0
    merged_or_updated = 0

    excluded: list[dict[str, Any]] = []
    excluded_counts: dict[str, int] = {}

    for res in results:
        if res.disabled or res.error:
            continue

        discovered_at = _utc_now_iso()
        server_entry_cfg = servers_used.get(res.name) if isinstance(servers_used, dict) else None

        # Best-effort transport name: use enum value if available
        transport_val = getattr(res.transport, "value", None)
        transport = str(transport_val) if isinstance(transport_val, str) else str(res.transport)

        for tool in res.tools or []:
            if not isinstance(tool, dict):
                _record_excluded(
                    excluded=excluded,
                    excluded_counts=excluded_counts,
                    max_samples=int(args.excluded_sample_limit or 200),
                    server=res.name,
                    transport=transport,
                    command=res.command,
                    tool_name=None,
                    reason="invalid_tool_object",
                    detail=f"type={type(tool).__name__}",
                )
                continue

            discovered_mcp_tools += 1

            name = tool.get("name")
            if not isinstance(name, str):
                _record_excluded(
                    excluded=excluded,
                    excluded_counts=excluded_counts,
                    max_samples=int(args.excluded_sample_limit or 200),
                    server=res.name,
                    transport=transport,
                    command=res.command,
                    tool_name=None,
                    reason="missing_or_invalid_name",
                    detail=f"type={type(name).__name__}",
                )
                continue
            if not name.strip():
                _record_excluded(
                    excluded=excluded,
                    excluded_counts=excluded_counts,
                    max_samples=int(args.excluded_sample_limit or 200),
                    server=res.name,
                    transport=transport,
                    command=res.command,
                    tool_name=name,
                    reason="empty_name",
                )
                continue

            # OpenAI naming constraints (default)
            if not args.include_non_openai_safe and not _OPENAI_TOOL_NAME_RE.match(name):
                skipped_non_openai_safe += 1
                _record_excluded(
                    excluded=excluded,
                    excluded_counts=excluded_counts,
                    max_samples=int(args.excluded_sample_limit or 200),
                    server=res.name,
                    transport=transport,
                    command=res.command,
                    tool_name=name,
                    reason="non_openai_safe_name",
                )
                continue

            input_schema = tool.get("inputSchema")
            if not isinstance(input_schema, dict):
                _record_excluded(
                    excluded=excluded,
                    excluded_counts=excluded_counts,
                    max_samples=int(args.excluded_sample_limit or 200),
                    server=res.name,
                    transport=transport,
                    command=res.command,
                    tool_name=name,
                    reason="missing_or_invalid_inputSchema",
                    detail=f"type={type(input_schema).__name__}",
                )
                continue

            schema = _mcp_tool_to_openai_schema(tool)
            if schema is None:
                _record_excluded(
                    excluded=excluded,
                    excluded_counts=excluded_counts,
                    max_samples=int(args.excluded_sample_limit or 200),
                    server=res.name,
                    transport=transport,
                    command=res.command,
                    tool_name=name,
                    reason="conversion_failed",
                )
                continue

            tool_name = schema.get("function", {}).get("name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                _record_excluded(
                    excluded=excluded,
                    excluded_counts=excluded_counts,
                    max_samples=int(args.excluded_sample_limit or 200),
                    server=res.name,
                    transport=transport,
                    command=res.command,
                    tool_name=name,
                    reason="conversion_missing_function_name",
                )
                continue

            tool_by_name[tool_name] = schema
            merged_or_updated += 1

            sources[tool_name] = {
                "server": res.name,
                "transport": transport,
                "command": res.command,
                "discovered_at": discovered_at,
                "config": server_entry_cfg,
            }

    merged_tools = sorted(
        tool_by_name.values(),
        key=lambda t: str(t.get("function", {}).get("name", "")).lower(),
    )

    registry["version"] = int(registry.get("version") or 1)
    registry["updated_at"] = _utc_now_iso()
    registry["tools"] = merged_tools
    registry["sources"] = sources
    registry["discovery_summary"] = {
        **_summarize_results(results),
        "registry_tools_total": len(merged_tools),
        "tools_skipped_non_openai_safe": skipped_non_openai_safe,
        "tools_merged_or_updated": merged_or_updated,
        "tools_excluded_total": int(sum(excluded_counts.values())),
        "tools_excluded_by_reason": dict(
            sorted(excluded_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        # Capped sample list to keep registry size under control
        "tools_excluded_sample": excluded,
    }

    print(json.dumps(registry["discovery_summary"], indent=2))

    if args.write:
        _write_registry(registry_path, registry)
        print(f"Wrote updated registry to {registry_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
