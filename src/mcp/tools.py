"""MCP tool implementations for abacus-proxy.

Modeled after model-proxy's MCP surface, but with a single upstream:
- POST {ROUTELLM_API_BASE_URL}/chat/completions
- model is forced to ROUTELLM_MODEL_ID (default: "route-llm")
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from src.mcp.bridge_client import call_tool_on_server, list_tools_for_server
from src.mcp.errors import MCPToolError
from src.utils.code_jit_prompt_injector import inject_code_jit_context
from src.utils.context_manager import ContextManager
from src.utils.memory_service import MemoryService
from src.utils.prompt_guard import guard_chat_messages
from src.utils.rules_prompt_injector import inject_retrieved_rules


def _get_config(app_resources: dict[str, Any]) -> Any:
    cfg = app_resources.get("config")
    if cfg is None:
        raise MCPToolError(-32603, "App config not available")
    return cfg


def _get_upstream_headers(*, config: Any) -> dict[str, str]:
    # For local upstream, auth is typically not required; if provided, send it.
    headers: dict[str, str] = {"Content-Type": "application/json"}

    token = getattr(config, "abacus_api_key", None)
    if isinstance(token, str) and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"

    return headers


def _get_upstream_transport(config: Any) -> httpx.AsyncHTTPTransport | None:
    """Prefer IPv4 when outbound IPv6 is broken.

    In this environment, `routellm.abacus.ai` publishes AAAA records but IPv6 :443
    connectivity fails (curl -6). Binding to 0.0.0.0 forces IPv4.
    """
    la = getattr(config, "upstream_local_address", None)
    if isinstance(la, str) and la.strip():
        return httpx.AsyncHTTPTransport(local_address=la.strip())
    return None


def _get_api_base_url(config: Any) -> str:
    base = getattr(config, "routellm_api_base_url", None)
    if isinstance(base, str) and base.strip():
        return base.strip()

    # Backward-compatible fallback (older config used routellm_base_url without /v1)
    base2 = getattr(config, "routellm_base_url", None)
    if isinstance(base2, str) and base2.strip():
        return base2.strip().rstrip("/") + "/v1"

    return "http://localhost:8003/v1"


def _get_route_model_id(config: Any) -> str:
    mid = getattr(config, "routellm_model_id", None)
    if isinstance(mid, str) and mid.strip():
        return mid.strip()
    return "route-llm"


async def send_chat_request(
    app_resources: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Send a chat completion request via RouteLLM (single upstream model=route-llm).

    Arguments schema mirrors model-proxy's MCP tool:
      - model: string (accepted for compatibility, but upstream model is forced)
      - messages: [{role, content}, ...]
      - temperature: number
      - max_tokens: integer (optional)
    """
    cfg = _get_config(app_resources)

    requested_model = arguments.get("model")
    messages = arguments.get("messages", [])
    temperature = arguments.get("temperature", 1.0)
    max_tokens = arguments.get("max_tokens")

    if not requested_model or not isinstance(requested_model, str):
        raise MCPToolError(-32602, "Missing required parameter: model")
    if not isinstance(messages, list) or not messages:
        raise MCPToolError(-32602, "Missing required parameter: messages")

    api_base = _get_api_base_url(cfg).rstrip("/")
    url = f"{api_base}/chat/completions"

    upstream_model = _get_route_model_id(cfg)

    # ------------------------------------------------------------------
    # Mem0 enhancements (best-effort): rules injection, code JIT injection,
    # and Mem0-backed condensation.
    # ------------------------------------------------------------------
    model_id = str(getattr(cfg, "route_default_target_model", "gpt-5.2") or "gpt-5.2")

    user_id = arguments.get("user_id") if isinstance(arguments.get("user_id"), str) else None
    agent_id = arguments.get("agent_id") if isinstance(arguments.get("agent_id"), str) else None
    project_id = arguments.get("project_id") if isinstance(arguments.get("project_id"), str) else None

    sid = arguments.get("session_id")
    if isinstance(sid, str) and sid.strip():
        session_id = sid.strip()
    else:
        session_id = f"mcp_{uuid.uuid4()}"

    ms = app_resources.get("memory_service")
    cm = app_resources.get("context_manager")

    if (
        isinstance(ms, MemoryService)
        and isinstance(cm, ContextManager)
        and getattr(ms, "is_enabled", False)
        and isinstance(messages, list)
        and messages
    ):
        # 1) Optional `.roo` rules retrieval/injection
        if bool(getattr(cfg, "enable_rules_memory", False)):
            messages, rules_stats = await inject_retrieved_rules(
                messages=messages,
                memory_service=ms,
                model_id=model_id,
                enable=True,
                user_id=str(getattr(cfg, "rules_memory_user_id", "system") or "system"),
                project_id=str(getattr(cfg, "rules_memory_project_id", "roo_rules") or "roo_rules"),
                max_shards=int(getattr(cfg, "rules_memory_max_shards", 8)),
                max_tokens=int(getattr(cfg, "rules_memory_max_tokens", 900)),
                trivial_bypass=bool(getattr(cfg, "rules_memory_trivial_bypass", True)),
            )
            if rules_stats.get("injected"):
                logger.info(
                    "[MCP] Injected rules from Mem0 "
                    f"retrieved={rules_stats.get('retrieved')} tokens~{rules_stats.get('tokens_used')}"
                )

        # 2) Optional code JIT retrieval/injection
        if bool(getattr(cfg, "enable_code_jit_context", False)):
            messages, code_stats = await inject_code_jit_context(
                messages=messages,
                memory_service=ms,
                model_id=model_id,
                enable=True,
                user_id=user_id,
                project_id=project_id,
                max_chunks=int(getattr(cfg, "code_jit_max_chunks", 6)),
                max_tokens=int(getattr(cfg, "code_jit_max_tokens", 2000)),
                trivial_bypass=bool(getattr(cfg, "code_jit_trivial_bypass", True)),
            )
            if code_stats.get("injected"):
                logger.info(
                    "[MCP] Injected code JIT context "
                    f"retrieved={code_stats.get('retrieved')} tokens~{code_stats.get('tokens_used')}"
                )

        # 3) Context management / condensation (Mem0-backed)
        enable_ctx = bool(getattr(cfg, "enable_context_management", True))
        if arguments.get("enable_context_management") is False:
            enable_ctx = False

        if enable_ctx:
            max_ctx = int(getattr(cfg, "default_max_context_length", 65470) or 65470)

            tools = arguments.get("tools")
            tools_for_estimation = tools if isinstance(tools, list) else None

            messages, ctx_stats = cm.manage_context(
                messages=messages,
                model_id=model_id,
                max_context_length=max_ctx,
                user_id=user_id,
                agent_id=agent_id,
                project_id=project_id,
                session_id=str(session_id),
                enable_condensation=True,
                tools=tools_for_estimation,
            )
            if ctx_stats.get("condensed"):
                logger.info(
                    "[MCP] Context condensed via Mem0 "
                    f"stored={ctx_stats.get('messages_stored')} saved={ctx_stats.get('tokens_saved')}"
                )

    # Guard oversized prompts to avoid upstream context-window failures.
    # Run this *after* Mem0 enhancements so condensation/rewrite can happen first.
    if bool(getattr(cfg, "prompt_guard_enabled", True)):
        guarded, stats = guard_chat_messages(
            messages,
            max_input_chars=int(getattr(cfg, "prompt_max_input_chars", 0) or 0),
            max_single_message_chars=int(getattr(cfg, "prompt_max_single_message_chars", 0) or 0),
            keep_last_messages=int(getattr(cfg, "prompt_keep_last_messages", 0) or 0),
            preserve_system_messages=bool(getattr(cfg, "prompt_preserve_system_messages", True)),
        )
        messages = guarded
        if stats.dropped_messages or stats.truncated_messages:
            logger.info(
                "[MCP] Prompt guard applied "
                f"original_chars={stats.original_chars} final_chars={stats.final_chars} "
                f"dropped_messages={stats.dropped_messages} truncated_messages={stats.truncated_messages}"
            )

    payload: dict[str, Any] = {
        "model": upstream_model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    # RouteLLM API only supports a subset of Chat Completions params.
    # Only forward allowlisted keys.
    allowed_keys = {
        "model",
        "messages",
        "max_tokens",
        "stream",
        "temperature",
        "stop",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "logprobs",
        "response_format",
    }
    for k in list(arguments.keys()):
        if k in allowed_keys and k not in payload:
            payload[k] = arguments[k]

    headers = _get_upstream_headers(config=cfg)

    timeout_s = float(getattr(cfg, "upstream_timeout_seconds", 120.0))

    try:
        transport = _get_upstream_transport(cfg)
        async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
            r = await client.post(url, headers=headers, json=payload)

        # Best-effort JSON parse. RouteLLM/OpenAI-compatible should return JSON.
        try:
            data = r.json()
        except Exception:
            data = {"raw_text": r.text}

        if r.status_code >= 400:
            raise MCPToolError(
                -32603,
                f"Upstream chat completion failed: HTTP {r.status_code}",
                {
                    "error_type": "upstream_error",
                    "status_code": r.status_code,
                    "url": url,
                    "response": data,
                },
            )

        return {
            "status": "success",
            "requested_model": requested_model,
            "upstream_model": upstream_model,
            "response": data,
        }

    except MCPToolError:
        raise
    except httpx.ConnectError as e:
        raise MCPToolError(
            -32603,
            "Failed to connect to upstream RouteLLM service",
            {"error_type": "connection_error", "url": url},
        ) from e
    except httpx.TimeoutException as e:
        raise MCPToolError(
            -32603,
            "Timeout talking to upstream RouteLLM service",
            {"error_type": "timeout", "url": url, "timeout_seconds": timeout_s},
        ) from e
    except Exception as e:
        raise MCPToolError(-32603, f"Chat request failed: {e}") from e


async def stream_chat_request(
    app_resources: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    """Streaming via MCP is not supported; provide guidance."""
    cfg = _get_config(app_resources)
    api_base = _get_api_base_url(cfg).rstrip("/")
    upstream_model = _get_route_model_id(cfg)

    requested_model = arguments.get("model")
    if not requested_model or not isinstance(requested_model, str):
        raise MCPToolError(-32602, "Missing required parameter: model")

    return {
        "status": "info",
        "message": (
            "Streaming is available via REST API (POST /v1/chat/completions with "
            "stream=true). MCP does not stream."
        ),
        "requested_model": requested_model,
        "upstream_model": upstream_model,
        "endpoint": f"{api_base}/chat/completions",
    }


async def list_available_models(
    app_resources: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    """List models by asking the upstream /v1/models if available, else return route-llm only."""
    _ = arguments
    cfg = _get_config(app_resources)

    api_base = _get_api_base_url(cfg).rstrip("/")
    url = f"{api_base}/models"
    headers = _get_upstream_headers(config=cfg)
    timeout_s = float(getattr(cfg, "upstream_timeout_seconds", 30.0))

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(url, headers=headers)

        try:
            data = r.json()
        except Exception:
            data = {"raw_text": r.text}

        if r.status_code >= 400:
            # Fall back to minimal, stable response
            upstream_model = _get_route_model_id(cfg)
            return {
                "status": "partial",
                "message": (
                    f"Upstream /models returned HTTP {r.status_code}; returning minimal "
                    "model list"
                ),
                "models": [{"id": upstream_model}],
                "upstream_error": {"status_code": r.status_code, "response": data},
            }

        return {"status": "success", "response": data}

    except Exception as e:
        upstream_model = _get_route_model_id(cfg)
        logger.warning(f"Failed to fetch upstream models list: {e}")
        return {
            "status": "partial",
            "message": "Failed to reach upstream /models; returning minimal model list",
            "models": [{"id": upstream_model}],
            "error": str(e),
        }


async def get_model_proxy_url(
    app_resources: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Return this service's public base URL if configured (for clients/tools)."""
    _ = app_resources
    _ = arguments
    return {
        "status": "success",
        "abacus_proxy_url": os.getenv(
            "ABACUS_PROXY_URL",
            os.getenv("MODEL_PROXY_URL", "http://localhost:8003"),
        ),
    }


# ---------------------------------------------------------------------------
# MCP Bridge Tools (from model-proxy)
# ---------------------------------------------------------------------------

from src.mcp.bridge_client import call_tool_on_server, list_tools_for_server

def _load_abacus_proxy_mcp_config() -> dict[str, Any]:
    """
    Load the model-proxy MCP configuration file.

    Default path: mcp_abacus_proxy_config.json
    Optional override: MCP_ABACUS_PROXY_CONFIG_PATH env var
    """
    cfg_path = os.getenv("MCP_ABACUS_PROXY_CONFIG_PATH", "mcp_abacus_proxy_config.json")
    p = Path(cfg_path)
    if not p.exists():
        raise MCPToolError(
            -32603,
            f"MCP config not found: {cfg_path}",
            {"error_type": "config_error", "path": str(cfg_path)},
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise MCPToolError(
            -32603,
            f"Failed to parse MCP config: {cfg_path}",
            {"error_type": "config_error", "path": str(cfg_path), "detail": str(e)},
        ) from e

    if not isinstance(data, dict):
        raise MCPToolError(
            -32603,
            f"Invalid MCP config (expected object): {cfg_path}",
            {"error_type": "config_error", "path": str(cfg_path)},
        )

    return data


def _get_bridge_block(cfg: dict[str, Any]) -> dict[str, Any]:
    bridge = cfg.get("mcp_bridge")
    return bridge if isinstance(bridge, dict) else {}


def _merge_server_policy(bridge: dict[str, Any], server: str) -> dict[str, Any]:
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


def _tool_matches_any(tool: str, patterns: list[Any]) -> bool:
    # Supports exact match and "*" wildcard.
    for p in patterns:
        if p == "*":
            return True
        if isinstance(p, str) and p == tool:
            return True
    return False


def _is_tool_allowed(bridge: dict[str, Any], server: str, tool: str) -> tuple[bool, str]:
    """
    Decide allow/deny based on mcp_bridge policy.

    Return: (allowed, reason)
    """
    allowlist = bridge.get("server_allowlist")
    if not isinstance(allowlist, list) or server not in allowlist:
        return False, "server_not_allowlisted"

    merged = _merge_server_policy(bridge, server)

    if merged.get("enabled") is False:
        return False, "server_disabled"

    deny_by_default = bool(merged.get("deny_by_default", True))

    allowed_tools = merged.get("allowed_tools")
    if not isinstance(allowed_tools, list):
        allowed_tools = []

    denied_tools = merged.get("denied_tools")
    if not isinstance(denied_tools, list):
        denied_tools = []

    # Deny list takes precedence
    if _tool_matches_any(tool, denied_tools):
        return False, "tool_denied"

    # Allow list
    if _tool_matches_any(tool, allowed_tools):
        return True, "tool_allowed"

    if deny_by_default:
        return False, "deny_by_default"

    return True, "default_allow"


def _parse_tool_ref(tool_ref: str) -> tuple[str, str]:
    if "/" not in tool_ref:
        raise MCPToolError(
            -32602,
            "Invalid tool_ref: expected 'server/tool'",
            {"error_type": "invalid_arguments", "tool_ref": tool_ref},
        )
    server, tool = tool_ref.split("/", 1)
    server = server.strip()
    tool = tool.strip()
    if not server or not tool:
        raise MCPToolError(
            -32602,
            "Invalid tool_ref: expected 'server/tool'",
            {"error_type": "invalid_arguments", "tool_ref": tool_ref},
        )
    return server, tool


def _get_schema_validation_block(bridge: dict[str, Any]) -> dict[str, Any]:
    raw = bridge.get("schema_validation")
    return raw if isinstance(raw, dict) else {}


def _extract_mcp_input_schema(
    *, tools: list[dict[str, Any]], tool_name: str
) -> dict[str, Any] | None:
    """Extract JSON schema for MCP tool arguments from tools/list response."""
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("name") != tool_name:
            continue
        schema = t.get("inputSchema")
        return schema if isinstance(schema, dict) else None
    return None


def _extract_openai_parameters_schema(tool_schema: dict[str, Any]) -> dict[str, Any] | None:
    """Extract JSON schema for arguments from an OpenAI tool schema dict."""
    if not isinstance(tool_schema, dict):
        return None
    if tool_schema.get("type") != "function":
        return None
    fn = tool_schema.get("function")
    if not isinstance(fn, dict):
        return None
    params = fn.get("parameters")
    return params if isinstance(params, dict) else None


def _ensure_openai_strict_object_schemas(schema: Any) -> Any:
    """Post-process a JSON schema to satisfy OpenAI strict validation.

    OpenAI (gpt-5.2+) requires that any schema with `"type": "object"` explicitly sets
    `"additionalProperties": false` (not omitted, not true).

    We apply this recursively to:
    - the schema itself
    - anyOf/oneOf/allOf branches
    - properties values
    - items (arrays)
    - $defs/definitions

    This is a *schema normalization* step only; it does not validate or coerce tool arguments.
    """
    if isinstance(schema, list):
        return [_ensure_openai_strict_object_schemas(x) for x in schema]

    if not isinstance(schema, dict):
        return schema

    # Recurse first (so we don't miss nested objects)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema and isinstance(schema[key], list):
            schema[key] = [_ensure_openai_strict_object_schemas(x) for x in schema[key]]

    if "properties" in schema and isinstance(schema["properties"], dict):
        schema["properties"] = {
            k: _ensure_openai_strict_object_schemas(v) for k, v in schema["properties"].items()
        }

    if "items" in schema:
        schema["items"] = _ensure_openai_strict_object_schemas(schema["items"])

    for defs_key in ("$defs", "definitions"):
        if defs_key in schema and isinstance(schema[defs_key], dict):
            schema[defs_key] = {
                k: _ensure_openai_strict_object_schemas(v) for k, v in schema[defs_key].items()
            }

    # Enforce OpenAI strictness for object schemas.
    #
    # IMPORTANT:
    # Some MCP servers legitimately use `additionalProperties: true` (or omit it) to accept
    # arbitrary argument objects (e.g. an "echo" tool for testing). Forcing `false` would
    # break schema validation and reject valid calls.
    #
    # OpenAI strictness is only required when sending tool schemas *to OpenAI*; it should
    # not be applied to MCP tool argument validation.
    if schema.get("type") == "object" and "additionalProperties" not in schema:
        schema["additionalProperties"] = False

    return schema


def _resolve_registry_schema_if_unambiguous(
    *, server: str, tool_name: str
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Resolve schema from tool_schema_registry.json only when it can be proven to belong
    to the requested MCP server (avoid cross-server collisions).
    """
    registry_path = os.getenv("TOOL_SCHEMA_REGISTRY_PATH", "tool_schema_registry.json")
    try:
        registry = load_tool_schema_registry(Path(str(registry_path)))
    except Exception:
        return None, None

    sources = registry.sources or {}
    src = sources.get(tool_name)
    if not isinstance(src, dict):
        return None, None
    if src.get("server") != server:
        return None, None

    openai_tool = get_registry_tool_schema(registry=registry, tool_name=tool_name)
    if not openai_tool:
        return None, None

    params_schema = _extract_openai_parameters_schema(openai_tool)
    if not params_schema:
        return None, None

    # Do NOT apply OpenAI strict schema normalization here.
    # This schema is used for validating arguments for an MCP tool call, not for sending
    # tool schemas to OpenAI.
    return params_schema, "registry_if_unambiguous"


async def _resolve_argument_schema(
    *, server: str, tool_name: str, source_precedence: list[str]
) -> tuple[dict[str, Any] | None, str | None]:
    for source in source_precedence:
        if source == "live_tools_list_cache":
            tools = await list_tools_for_server(server)
            schema = _extract_mcp_input_schema(tools=tools, tool_name=tool_name)
            if schema:
                # Do NOT apply OpenAI strict schema normalization here.
                # This schema is used for validating arguments against the MCP server's own
                # declared inputSchema. Mutating additionalProperties would change semantics.
                return schema, "live_tools_list_cache"

        if source == "registry_if_unambiguous":
            schema, src_name = _resolve_registry_schema_if_unambiguous(
                server=server, tool_name=tool_name
            )
            if schema and src_name:
                return schema, src_name

    return None, None


def _validate_arguments_against_schema(
    *,
    schema: dict[str, Any],
    arguments: dict[str, Any],
    server: str,
    tool_name: str,
    schema_source: str,
) -> None:
    if not _JSONSCHEMA_AVAILABLE or _jsonschema_validators is None:
        raise MCPToolError(
            -32603,
            "Schema validation is enabled but jsonschema is not available",
            {"error_type": "dependency_error", "dependency": "jsonschema"},
        )

    validator_cls = _jsonschema_validators.validator_for(schema)
    validator = validator_cls(schema)

    errors: list[dict[str, Any]] = []
    for err in validator.iter_errors(arguments):
        # JSON Pointer-ish string; keep it simple and readable.
        path = "$." + ".".join(str(p) for p in err.path) if err.path else "$"
        errors.append({"path": path, "message": err.message})
        if len(errors) >= 10:
            break

    if errors:
        raise MCPToolError(
            -32602,
            "Invalid tool arguments",
            {
                "error_type": "invalid_arguments",
                "server": server,
                "tool": tool_name,
                "schema_source": schema_source,
                "errors": errors,
            },
        )


async def mcp_list_servers(
    app_resources: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    """
    List configured/allowlisted MCP servers for the bridge.

    This is safe to implement without the external MCP runtime because it only
    reads local config and returns non-sensitive metadata.
    """
    _ = app_resources  # reserved for future: injected config/cache
    _ = arguments

    cfg = _load_abacus_proxy_mcp_config()
    bridge = _get_bridge_block(cfg)

    allowlist = bridge.get("server_allowlist")
    if not isinstance(allowlist, list):
        allowlist = []

    servers: list[dict[str, Any]] = []
    for name in allowlist:
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()

        raw_server_cfg = cfg.get(name)
        server_cfg = raw_server_cfg if isinstance(raw_server_cfg, dict) else {}

        merged_policy = _merge_server_policy(bridge, name)

        # Return minimal safe metadata; never leak command-lines/env.
        servers.append(
            {
                "name": name,
                "configured": bool(server_cfg),
                "transport": server_cfg.get("type") or server_cfg.get("transport") or "unknown",
                "url": server_cfg.get("url"),
                "bridge_enabled": bool(bridge.get("enabled", False)),
                "server_enabled": bool(merged_policy.get("enabled", True)),
            }
        )

    return {
        "status": "success",
        "bridge_enabled": bool(bridge.get("enabled", False)),
        "server_allowlist": allowlist,
        "servers": servers,
    }


async def mcp_list_tools(
    app_resources: dict[str, Any], arguments: dict[str, Any]
) -> dict[str, Any]:
    """
    List tools available on a configured external MCP server.

    Policy enforcement happens here (bridge enabled + server allowlist), then the
    transport runtime is delegated to src/mcp/bridge_client.py.
    """
    _ = app_resources  # reserved for future: shared caches/resources

    server = arguments.get("server")
    if not isinstance(server, str) or not server.strip():
        raise MCPToolError(
            -32602,
            "Missing required parameter: server",
            {"error_type": "invalid_arguments"},
        )
    server = server.strip()

    cfg = _load_abacus_proxy_mcp_config()
    bridge = _get_bridge_block(cfg)

    if not bool(bridge.get("enabled", False)):
        raise MCPToolError(
            -32603,
            "MCP bridge is disabled",
            {"error_type": "policy_denied", "reason": "bridge_disabled"},
        )

    allowlist = bridge.get("server_allowlist")
    if not isinstance(allowlist, list) or server not in allowlist:
        raise MCPToolError(
            -32603,
            "MCP server is not allowlisted",
            {"error_type": "policy_denied", "reason": "server_not_allowlisted", "server": server},
        )

    tools = await list_tools_for_server(server)
    return {
        "status": "success",
        "server": server,
        "tools": tools,
        "total_tools": len(tools),
    }


# ---------------------------------------------------------------------------
# MCP Bridge guardrails (Task 85)
# ---------------------------------------------------------------------------

# NOTE: These counters are process-local (best-effort) and intended for quick observability,
# not billing-grade metrics.
_MCP_BRIDGE_METRICS: Counter[str] = Counter()


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _new_call_id() -> str:
    # Short, sortable-ish call id for correlation in logs.
    return f"mcpb_{_now_ms()}_{secrets.token_hex(4)}"


def _get_hygiene_block(bridge: dict[str, Any]) -> dict[str, Any]:
    raw = bridge.get("hygiene")
    return raw if isinstance(raw, dict) else {}


def _get_server_transport_from_cfg(cfg: dict[str, Any], server: str) -> str:
    server_cfg = cfg.get(server)
    if not isinstance(server_cfg, dict):
        return "unknown"
    t = server_cfg.get("type") or server_cfg.get("transport") or "unknown"
    return str(t).lower().strip() or "unknown"


def _get_allowed_transports(bridge: dict[str, Any], server: str) -> set[str] | None:
    merged = _merge_server_policy(bridge, server)
    raw = merged.get("allowed_transports")
    if not isinstance(raw, list):
        return None
    out: set[str] = set()
    for v in raw:
        if isinstance(v, str) and v.strip():
            out.add(v.lower().strip())
    return out or None


_PROMPT_INJECTION_RE = re.compile(
    r"(?is)\b(ignore|disregard|override)\b.{0,40}\b(previous|prior|all)\b.{0,40}\b(instruction|instructions|system)\b"
)


def _sanitize_tool_output_text(*, text: str, max_chars: int) -> tuple[str, list[str]]:
    """
    Sanitize untrusted tool output text for safer *display and downstream prompting*.

    We do NOT mutate the raw MCP tool result. Instead we provide a sanitized view and warnings.
    """
    warnings: list[str] = []

    if not isinstance(text, str):
        text = str(text)

    # Size limit
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars] + "…"
        warnings.append("output_truncated")

    # Detect prompt-injection style phrases
    if _PROMPT_INJECTION_RE.search(text):
        warnings.append("prompt_injection_pattern_detected")

    # If tool output contains XML-ish tool tags, provide an escaped view so strict runners
    # don't mistakenly interpret it as a tool call.
    if re.search(r"<\s*[A-Za-z_][A-Za-z0-9_-]*\b", text):
        warnings.append("tool_tag_like_output_detected")
        # Minimal XML escaping
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    return text, warnings


def _apply_hygiene_to_content(
    *, bridge: dict[str, Any], content: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    hygiene = _get_hygiene_block(bridge)
    if not bool(hygiene.get("enabled", True)):
        return content, []

    # Default: keep small enough to avoid blowing up prompts/logs.
    max_chars = hygiene.get("max_output_chars", 20000)
    try:
        max_chars_int = int(max_chars)
    except Exception:
        max_chars_int = 20000

    warnings: list[str] = []
    out = dict(content)

    if isinstance(out.get("text"), str):
        sanitized, w = _sanitize_tool_output_text(text=out["text"], max_chars=max_chars_int)
        out["text_sanitized"] = sanitized
        warnings.extend(w)

    return out, warnings


def _record_bridge_metric(*, key: str) -> None:
    if isinstance(key, str) and key:
        _MCP_BRIDGE_METRICS[key] += 1


def _audit_log(
    *,
    call_id: str,
    server: str,
    tool: str,
    ok: bool,
    duration_ms: int,
    error_type: str | None = None,
    reason: str | None = None,
    schema_source: str | None = None,
    arguments_redacted: dict[str, Any] | None = None,
) -> None:
    # Audit is config-driven under mcp_bridge.audit; we assume caller already decided whether
    # to include arguments_redacted.
    #
    # IMPORTANT: Include critical correlation fields in the *message string* too, because many
    # log sinks/handlers show only the message by default (bind() fields may not appear).
    try:
        msg = (
            "mcp_bridge_call "
            f"call_id={call_id} server={server} tool={tool} ok={ok} duration_ms={duration_ms}"
        )
        if error_type:
            msg += f" error_type={error_type}"
        if reason:
            msg += f" reason={reason}"
        if schema_source:
            msg += f" schema_source={schema_source}"

        logger.bind(
            event="mcp_bridge_call",
            call_id=call_id,
            mcp_server=server,
            mcp_tool=tool,
            ok=ok,
            duration_ms=duration_ms,
            error_type=error_type,
            reason=reason,
            schema_source=schema_source,
            arguments_redacted=arguments_redacted,
        ).info(msg)
    except Exception:
        # Never fail the tool call due to logging issues.
        return


def _get_audit_block(bridge: dict[str, Any]) -> dict[str, Any]:
    raw = bridge.get("audit")
    return raw if isinstance(raw, dict) else {}


def _truncate_str(s: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "…"


def _redact_for_audit(value: Any, *, redact_keys: set[str], max_depth: int = 6) -> Any:
    """Redact sensitive keys recursively for audit/debug payloads."""
    if max_depth <= 0:
        return "<max_depth>"

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            ks = str(k)
            if ks.lower() in redact_keys:
                out[ks] = "<redacted>"
            else:
                out[ks] = _redact_for_audit(v, redact_keys=redact_keys, max_depth=max_depth - 1)
        return out

    if isinstance(value, list):
        # Keep list length bounded to avoid huge error payloads
        return [
            _redact_for_audit(v, redact_keys=redact_keys, max_depth=max_depth - 1)
            for v in value[:50]
        ]

    if isinstance(value, str):
        return value

    return value


def _safe_arguments_snapshot(
    *, bridge: dict[str, Any], args_payload: dict[str, Any]
) -> dict[str, Any] | None:
    """
    Return a redacted + truncated snapshot of arguments for safe debugging, controlled by config.
    """
    audit = _get_audit_block(bridge)
    if not bool(audit.get("enabled", False)):
        return None
    if not bool(audit.get("log_arguments", False)):
        return None

    redact_keys_raw = audit.get("redact_keys", [])
    redact_keys: set[str] = set()
    if isinstance(redact_keys_raw, list):
        for k in redact_keys_raw:
            if isinstance(k, str) and k.strip():
                redact_keys.add(k.strip().lower())

    max_arg_chars = audit.get("max_arg_chars", 2000)
    try:
        max_arg_chars_int = int(max_arg_chars)
    except Exception:
        max_arg_chars_int = 2000

    redacted = _redact_for_audit(args_payload, redact_keys=redact_keys)

    try:
        rendered = json.dumps(redacted, ensure_ascii=False, default=str)
    except Exception:
        rendered = str(redacted)

    rendered = _truncate_str(rendered, max_arg_chars_int)

    # Return both structured + rendered forms so callers can choose.
    return {"redacted": redacted, "rendered": rendered, "max_arg_chars": max_arg_chars_int}


def _normalize_mcp_result_content(*, result: dict[str, Any]) -> dict[str, Any]:
    """
    Create a stable, proxy-friendly view of an MCP tool `result` object.

    - If result contains MCP `content` blocks, return them with a convenience `text`
      field that concatenates text blocks.
    - Otherwise return a `raw` wrapper.
    """
    content_blocks = result.get("content")
    if not isinstance(content_blocks, list):
        return {"type": "raw", "value": result}

    # Bound the number of content blocks we surface to avoid accidental prompt/log bloat.
    content_blocks = [b for b in content_blocks if isinstance(b, dict)][:50]

    texts: list[str] = []
    for block in content_blocks:
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])

    out: dict[str, Any] = {"type": "mcp_content", "content": content_blocks}
    if texts:
        out["text"] = "\n".join(texts)
    return out


async def mcp_call(app_resources: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Call a tool on a configured external MCP server.

    Guardrails (Task 85):
    - policy enforcement (server allowlist, tool allow/deny, transport allowlist)
    - correlation IDs
    - audit logging + lightweight metrics (best-effort)
    - output hygiene (sanitize tool-tag-like output and truncate large outputs)
    """
    _ = app_resources  # reserved for future: shared caches/resources

    started_ms = _now_ms()

    tool_ref = arguments.get("tool_ref")
    server = arguments.get("server")
    tool = arguments.get("tool")

    call_id = arguments.get("call_id")
    if not isinstance(call_id, str) or not call_id.strip():
        call_id = _new_call_id()
    call_id = call_id.strip()

    # Parse tool ref / parameters
    if isinstance(tool_ref, str) and tool_ref.strip():
        server, tool = _parse_tool_ref(tool_ref.strip())
    else:
        if not isinstance(server, str) or not server.strip():
            raise MCPToolError(
                -32602,
                "Missing required parameter: server (or provide tool_ref)",
                {"error_type": "invalid_arguments", "call_id": call_id},
            )
        if not isinstance(tool, str) or not tool.strip():
            raise MCPToolError(
                -32602,
                "Missing required parameter: tool (or provide tool_ref)",
                {"error_type": "invalid_arguments", "call_id": call_id},
            )
        server = server.strip()
        tool = tool.strip()

    args_payload = arguments.get("arguments", {})
    if args_payload is None:
        args_payload = {}
    if not isinstance(args_payload, dict):
        raise MCPToolError(
            -32602,
            "Parameter 'arguments' must be an object",
            {"error_type": "invalid_arguments", "server": server, "tool": tool, "call_id": call_id},
        )

    cfg = _load_abacus_proxy_mcp_config()
    bridge = _get_bridge_block(cfg)

    args_snapshot = _safe_arguments_snapshot(bridge=bridge, args_payload=args_payload)

    schema_source: str | None = None
    policy_reason: str | None = None

    try:
        # Base bridge enable gate
        if not bool(bridge.get("enabled", False)):
            policy_reason = "bridge_disabled"
            raise MCPToolError(
                -32603,
                "MCP bridge is disabled",
                {"error_type": "policy_denied", "reason": policy_reason},
            )

        # Ensure server exists in config to avoid accidentally proxying unknown endpoints.
        if not isinstance(cfg.get(server), dict):
            policy_reason = "server_not_configured"
            raise MCPToolError(
                -32603,
                "MCP server is not configured",
                {"error_type": "policy_denied", "reason": policy_reason, "server": server},
            )

        # Tool allow/deny policy
        allowed, reason = _is_tool_allowed(bridge, server, tool)
        if not allowed:
            policy_reason = reason
            raise MCPToolError(
                -32603,
                "MCP tool call denied by policy",
                {"error_type": "policy_denied", "server": server, "tool": tool, "reason": reason},
            )

        # Transport policy (optional): enforce allowed_transports if configured
        allowed_transports = _get_allowed_transports(bridge, server)
        if allowed_transports is not None:
            transport = _get_server_transport_from_cfg(cfg, server)
            if transport not in allowed_transports:
                policy_reason = "transport_not_allowed"
                raise MCPToolError(
                    -32603,
                    "MCP transport denied by policy",
                    {
                        "error_type": "policy_denied",
                        "server": server,
                        "tool": tool,
                        "reason": policy_reason,
                        "transport": transport,
                        "allowed_transports": sorted(allowed_transports),
                    },
                )

        # Optional argument schema validation (task 83)
        schema_block = _get_schema_validation_block(bridge)
        if bool(schema_block.get("enabled", False)):
            precedence = schema_block.get("source_precedence")
            if not isinstance(precedence, list):
                precedence = ["live_tools_list_cache", "registry_if_unambiguous"]
            precedence = [str(x) for x in precedence if isinstance(x, str) and str(x).strip()]

            schema, schema_source = await _resolve_argument_schema(
                server=server, tool_name=tool, source_precedence=precedence
            )
            if schema and schema_source:
                _validate_arguments_against_schema(
                    schema=schema,
                    arguments=args_payload,
                    server=server,
                    tool_name=tool,
                    schema_source=schema_source,
                )

        result = await call_tool_on_server(server, tool, args_payload)

        duration_ms = max(0, _now_ms() - started_ms)
        content = _normalize_mcp_result_content(result=result)
        content, hygiene_warnings = _apply_hygiene_to_content(bridge=bridge, content=content)

        _record_bridge_metric(key="mcp_bridge.ok")
        _record_bridge_metric(key=f"mcp_bridge.ok.server:{server}")
        _record_bridge_metric(key=f"mcp_bridge.ok.server:{server}.tool:{tool}")

        audit = _get_audit_block(bridge)
        if bool(audit.get("enabled", False)):
            _audit_log(
                call_id=call_id,
                server=server,
                tool=tool,
                ok=True,
                duration_ms=duration_ms,
                schema_source=schema_source,
                arguments_redacted=args_snapshot,
            )

        return {
            "status": "success",
            "ok": True,
            "call_id": call_id,
            "server": server,
            "tool": tool,
            "duration_ms": duration_ms,
            "warnings": hygiene_warnings,
            "content": content,
            "result": result,
        }

    except MCPToolError as e:
        duration_ms = max(0, _now_ms() - started_ms)

        data = e.data if isinstance(e.data, dict) else {}
        error_type = data.get("error_type")

        # Normalize/derive error_type if missing or inconsistent.
        if not isinstance(error_type, str) or not error_type:
            if e.code == -32601:
                error_type = "tool_not_found"
            elif e.code == -32602:
                error_type = "invalid_arguments"
            else:
                error_type = "tool_error"

        normalized_data = dict(data)
        normalized_data.update(
            {
                "error_type": error_type,
                "server": server,
                "tool": tool,
                "call_id": call_id,
                "duration_ms": duration_ms,
            }
        )
        if schema_source and "schema_source" not in normalized_data:
            normalized_data["schema_source"] = schema_source
        if args_snapshot is not None and "arguments_redacted" not in normalized_data:
            normalized_data["arguments_redacted"] = args_snapshot

        # Metrics + audit (best-effort)
        _record_bridge_metric(key="mcp_bridge.error")
        _record_bridge_metric(key=f"mcp_bridge.error.type:{error_type}")
        _record_bridge_metric(key=f"mcp_bridge.error.server:{server}")
        _record_bridge_metric(key=f"mcp_bridge.error.server:{server}.tool:{tool}")

        audit = _get_audit_block(bridge)
        if bool(audit.get("enabled", False)):
            _audit_log(
                call_id=call_id,
                server=server,
                tool=tool,
                ok=False,
                duration_ms=duration_ms,
                error_type=error_type,
                reason=policy_reason or normalized_data.get("reason"),
                schema_source=schema_source,
                arguments_redacted=args_snapshot,
            )

        raise MCPToolError(e.code, e.message, normalized_data) from e
    except Exception as e:
        duration_ms = max(0, _now_ms() - started_ms)

        _record_bridge_metric(key="mcp_bridge.error")
        _record_bridge_metric(key="mcp_bridge.error.type:internal_error")
        _record_bridge_metric(key=f"mcp_bridge.error.server:{server}")
        _record_bridge_metric(key=f"mcp_bridge.error.server:{server}.tool:{tool}")

        audit = _get_audit_block(bridge)
        if bool(audit.get("enabled", False)):
            _audit_log(
                call_id=call_id,
                server=server,
                tool=tool,
                ok=False,
                duration_ms=duration_ms,
                error_type="internal_error",
                reason=policy_reason,
                schema_source=schema_source,
                arguments_redacted=args_snapshot,
            )

        raise MCPToolError(
            -32603,
            "Internal error",
            {
                "error_type": "internal_error",
                "server": server,
                "tool": tool,
                "call_id": call_id,
                "duration_ms": duration_ms,
                "arguments_redacted": args_snapshot,
            },
        ) from e
