"""MCP tool implementations for abacus-proxy.

Modeled after model-proxy's MCP surface, but with a single upstream:
- POST {ROUTELLM_API_BASE_URL}/chat/completions
- model is forced to ROUTELLM_MODEL_ID (default: "route-llm")
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
from loguru import logger

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
