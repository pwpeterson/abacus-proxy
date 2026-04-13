"""FastAPI application for abacus-proxy.

Initial goal: container bring-up parity with model-proxy:
- GET /health for quadlet health checks
- (Optional) GET /v1/models placeholder for OpenAI-compatible clients
- Lifespan hooks to start/stop MCP server
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import asyncio
import json
import time
import uuid

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from src.models.config import AppConfig
from src.models.memory import MemoryConfig
from src.utils.code_jit_prompt_injector import inject_code_jit_context
from src.utils.context_manager import ContextManager
from src.utils.memory_service import MemoryService
from src.utils.prompt_guard import guard_chat_messages
from src.utils.request_logger import AbacusRequestLogger
from src.utils.rules_prompt_injector import inject_retrieved_rules
from src.utils.startup_indexer import auto_index_rules_on_startup
from src.utils.performance_middleware import create_performance_middleware

StartupHook = Callable[[dict[str, Any]], Awaitable[None]]
ShutdownHook = Callable[[], Awaitable[None]]


def create_app(
    config: AppConfig,
    on_startup: StartupHook | None = None,
    on_shutdown: ShutdownHook | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        logger.info("🚀 Starting abacus-proxy")

        # Initialize Mem0-backed services (best-effort; do not prevent startup).
        memory_service: MemoryService | None = None
        context_manager: ContextManager | None = None
        try:
            mem_cfg = MemoryConfig(
                enable_memory=bool(getattr(config, "enable_memory", True)),
                vector_store_type=str(getattr(config, "memory_vector_store", "qdrant") or "qdrant"),
                vector_store_path=str(
                    getattr(config, "memory_vector_store_path", "./memory_data") or "./memory_data"
                ),
                embedding_model=str(
                    getattr(config, "memory_embedding_model", "text-embedding-3-small")
                    or "text-embedding-3-small"
                ),
                collection_name=str(
                    getattr(config, "memory_collection_name", "abacus_proxy_memory")
                    or "abacus_proxy_memory"
                ),
                qdrant_url=getattr(config, "qdrant_url", None),
                qdrant_api_key=getattr(config, "qdrant_api_key", None),
            )
            memory_service = MemoryService(mem_cfg)
            context_manager = ContextManager(
                memory_service,
                context_threshold=float(getattr(config, "context_threshold", 0.7)),
                keep_recent_ratio=float(getattr(config, "keep_recent_ratio", 0.2)),
                min_keep_messages=int(getattr(config, "min_keep_messages", 5)),
                summary_limit=int(getattr(config, "summary_limit", 3)),
                enable_dynamic_threshold=bool(getattr(config, "enable_dynamic_threshold", True)),
                enable_importance_scoring=bool(getattr(config, "enable_importance_scoring", True)),
                enable_system_compression=bool(getattr(config, "enable_system_compression", True)),
                enable_adaptive_summary=bool(getattr(config, "enable_adaptive_summary", True)),
            )
            app.state.memory_service = memory_service
            app.state.context_manager = context_manager

            logger.bind(memory_enabled=memory_service.is_enabled).info(
                "Mem0 services initialized"
            )

            # Optional: auto-index `.roo` rules into Mem0 on startup.
            if bool(getattr(config, "auto_index_rules_on_startup", False)):
                # Offload to a thread: indexing may do I/O and embeddings.
                idx_result = await asyncio.to_thread(
                    auto_index_rules_on_startup,
                    memory_service,
                )
                logger.bind(**{f"rules_index_{k}": v for k, v in idx_result.items()}).info(
                    "Rules auto-index completed"
                )

        except Exception as e:
            logger.warning(f"Mem0 services disabled (init failed): {e}")
            app.state.memory_service = None
            app.state.context_manager = None
        # Initialize request tracking (best-effort; do not prevent startup)
        request_logger = AbacusRequestLogger(
            enabled=bool(getattr(config, "enable_request_tracking", False)),
            supabase_url=getattr(config, "supabase_url", None),
            supabase_service_role_key=getattr(config, "supabase_service_role_key", None),
            http_timeout_seconds=float(
                getattr(config, "request_tracking_timeout_seconds", 2.5)
            ),
        )
        app.state.request_logger = request_logger
        logger.bind(request_tracking_enabled=request_logger.enabled).info(
            "Request tracking initialized"
        )

        # Tool discovery removed - Roo Code provides tools directly
        app.state.discovered_tools = {}
        app.state.tool_map = {}

        if on_startup:
            await on_startup(
                {
                    "config": config,
                    "memory_service": getattr(app.state, "memory_service", None),
                    "context_manager": getattr(app.state, "context_manager", None),
                }
            )
        yield
        # Shutdown
        if on_shutdown:
            await on_shutdown()
        logger.info("👋 Shutting down abacus-proxy")

    app = FastAPI(
        title="Abacus Proxy API",
        description="Abacus/RouteLLM proxy with MCP integration (initial skeleton)",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.config = config
    # Cache for upstream `/v1/models` to avoid hammering the upstream endpoint.
    # Only successful responses are cached.
    app.state.models_cache = {"ts": 0.0, "data": None}
    
    # Add performance logging middleware
    import os
    create_performance_middleware(
        app,
        enabled=os.getenv("ENABLE_PERFORMANCE_LOGGING", "true").lower() == "true",
    )

    @app.get("/health")
    async def health() -> JSONResponse:  # type: ignore[reportUnusedFunction]
        return JSONResponse(
            status_code=200,
            content={
                "status": "healthy",
                "service": "abacus-proxy",
                "mcp_enabled": bool(config.enable_mcp),
            },
        )

    def _get_upstream_headers(*, request: Request | None = None) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}

        # Prefer the server-side configured key; otherwise forward the inbound auth header.
        if config.abacus_api_key:
            headers["Authorization"] = f"Bearer {config.abacus_api_key}"
        elif request is not None:
            auth = request.headers.get("authorization")
            if auth:
                headers["Authorization"] = auth

        return headers

    def _assert_not_recursive(*, upstream_url: str, request: Request) -> None:
        """Fail fast if upstream points back at this proxy (prevents infinite recursion)."""
        try:
            up = httpx.URL(upstream_url)
            base = httpx.URL(str(request.base_url))
        except Exception:
            return

        if up.scheme == base.scheme and up.host == base.host and up.port == base.port:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Misconfiguration: ROUTELLM_API_BASE_URL points to this proxy "
                    f"({request.base_url}). This would cause a recursive request loop."
                ),
            )

    def _get_upstream_transport() -> httpx.AsyncHTTPTransport | None:
        """Return an httpx transport configured for upstream connectivity.

        Default behavior is to bind to IPv4 (0.0.0.0) to avoid broken IPv6 egress paths.

        Notes:
        - httpx only supports HTTP/2 when the `h2` dependency is installed.
        - If `h2` is missing, enabling http2=True will crash at runtime.
        """
        la = getattr(config, "upstream_local_address", None)
        if not (isinstance(la, str) and la.strip()):
            return None

        http2_enabled = False
        try:
            import h2  # noqa: F401
        except Exception:
            http2_enabled = False
        else:
            http2_enabled = True

        return httpx.AsyncHTTPTransport(local_address=la.strip(), http2=http2_enabled)

    def _normalize_api_base_url(raw: str) -> str:
        """Normalize upstream base URL.

        We defensively unescape common JSON-escaped forms (e.g. `https:\\/\\/host\\/v1`).
        Logs show some deployments accidentally pass `\\/` sequences via env vars.
        """
        s = (raw or "").strip()
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            s = s[1:-1]

        # Some env loaders/systemd quoting can leave literal backslashes in URLs
        # (e.g. `https:\/\/host\/v1`). Strip them aggressively.
        s = s.replace("\\", "")

        return s.rstrip("/")

    def _rewrite_text_only_to_attempt_completion(data: Any) -> Any:
        """Rewrite text-only assistant replies into an OpenAI tool call.

        If upstream returns an OpenAI-style chat completion with a text-only message:
          choices[0].message.content = "<text>"
          choices[0].message.tool_calls is missing/empty

        then rewrite to:
          choices[0].message.content = null
          choices[0].message.tool_calls[0].function.name = "attempt_completion"
          choices[0].message.tool_calls[0].function.arguments = "{\"result\": \"<text>\"}"
          choices[0].finish_reason = "tool_calls"

        If `tool_calls` already exist, do not modify the response.
        """
        if not isinstance(data, dict):
            return data

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return data

        choice0 = choices[0]
        if not isinstance(choice0, dict):
            return data

        msg = choice0.get("message")
        if not isinstance(msg, dict):
            return data

        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return data

        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            return data

        msg["content"] = None
        msg["tool_calls"] = [
            {
                "id": "call_attempt_completion_0",
                "type": "function",
                "function": {
                    "name": "attempt_completion",
                    "arguments": json.dumps({"result": content}, ensure_ascii=False),
                },
            }
        ]
        choice0["finish_reason"] = "tool_calls"
        return data

    def _apply_prompt_guard(payload: dict[str, Any]) -> None:
        """Trim chat message history to avoid upstream context-window failures."""
        if not getattr(config, "prompt_guard_enabled", True):
            return

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return

        guarded, stats = guard_chat_messages(
            messages,
            max_input_chars=int(getattr(config, "prompt_max_input_chars", 0) or 0),
            max_single_message_chars=int(getattr(config, "prompt_max_single_message_chars", 0) or 0),
            keep_last_messages=int(getattr(config, "prompt_keep_last_messages", 0) or 0),
            preserve_system_messages=bool(getattr(config, "prompt_preserve_system_messages", True)),
        )

        payload["messages"] = guarded

        # Safety: prompt_guard may drop tool outputs while leaving tool calls (or vice versa),
        # which makes upstream OpenAI-compatible validators reject the request.
        # If we detect dangling tool history, remove the orphaned side.
        def _sanitize_dangling_tool_history(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            call_ids: set[str] = set()
            output_ids: set[str] = set()

            for m in msgs:
                if not isinstance(m, dict):
                    continue

                tc = m.get("tool_calls")
                if isinstance(tc, list):
                    for item in tc:
                        if isinstance(item, dict):
                            tid = item.get("id")
                            if isinstance(tid, str) and tid:
                                call_ids.add(tid)

                if m.get("role") == "tool":
                    tid = m.get("tool_call_id")
                    if isinstance(tid, str) and tid:
                        output_ids.add(tid)

            missing_outputs = call_ids - output_ids
            missing_calls = output_ids - call_ids
            if not missing_outputs and not missing_calls:
                return msgs

            sanitized: list[dict[str, Any]] = []
            dropped_tool_msgs = 0
            stripped_tool_calls = 0
            dropped_empty_assistant = 0

            for m in msgs:
                if not isinstance(m, dict):
                    continue

                if m.get("role") == "tool":
                    tid = m.get("tool_call_id")
                    if isinstance(tid, str) and tid in missing_calls:
                        dropped_tool_msgs += 1
                        continue

                tc = m.get("tool_calls")
                if isinstance(tc, list) and tc:
                    new_tc = [
                        item
                        for item in tc
                        if isinstance(item, dict)
                        and isinstance(item.get("id"), str)
                        and item.get("id") not in missing_outputs
                    ]
                    if len(new_tc) != len(tc):
                        stripped_tool_calls += (len(tc) - len(new_tc))
                        m = dict(m)
                        if new_tc:
                            m["tool_calls"] = new_tc
                        else:
                            m.pop("tool_calls", None)
                            content = m.get("content")
                            if content is None or (isinstance(content, str) and not content.strip()):
                                dropped_empty_assistant += 1
                                continue

                sanitized.append(m)

            logger.warning(
                "Prompt guard produced dangling tool history; sanitized "
                f"missing_outputs={sorted(list(missing_outputs))[:5]} missing_calls={sorted(list(missing_calls))[:5]} "
                f"dropped_tool_msgs={dropped_tool_msgs} stripped_tool_calls={stripped_tool_calls} "
                f"dropped_empty_assistant={dropped_empty_assistant}"
            )
            return sanitized

        payload["messages"] = _sanitize_dangling_tool_history(payload["messages"])

        if stats.dropped_messages or stats.truncated_messages:
            logger.info(
                "Prompt guard applied "
                f"original_chars={stats.original_chars} final_chars={stats.final_chars} "
                f"dropped_messages={stats.dropped_messages} truncated_messages={stats.truncated_messages}"
            )

    def _get_identity(
        request: Request, payload: dict[str, Any]
    ) -> tuple[str | None, str | None, str | None]:
        """Best-effort identity extraction for Mem0 scoping."""
        user_id = request.headers.get("x-user-id") or request.headers.get("x-mem0-user-id")
        agent_id = request.headers.get("x-agent-id")
        project_id = request.headers.get("x-project-id") or request.headers.get("x-mem0-project-id")

        # Allow payload overrides (clients may include these fields).
        pu = payload.get("user_id")
        pa = payload.get("agent_id")
        pp = payload.get("project_id")
        if isinstance(pu, str) and pu.strip():
            user_id = pu.strip()
        if isinstance(pa, str) and pa.strip():
            agent_id = pa.strip()
        if isinstance(pp, str) and pp.strip():
            project_id = pp.strip()

        return user_id, agent_id, project_id

    async def _apply_mem0_enhancements(payload: dict[str, Any], request: Request) -> None:
        """Inject rules/code context and apply Mem0-backed condensation (best-effort).

        This mutates `payload["messages"]` in place when applicable.
        """
        ms: MemoryService | None = getattr(app.state, "memory_service", None)
        cm: ContextManager | None = getattr(app.state, "context_manager", None)

        if ms is None or cm is None or not getattr(ms, "is_enabled", False):
            return

        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return

        requested_model = payload.get("model")
        model_id = (
            requested_model
            if isinstance(requested_model, str) and requested_model.strip()
            else str(getattr(config, "route_default_target_model", "gpt-5.2") or "gpt-5.2")
        )

        user_id, agent_id, project_id = _get_identity(request, payload)

        # 1) Optional rules retrieval/injection
        if bool(getattr(config, "enable_rules_memory", False)):
            messages, rules_stats = await inject_retrieved_rules(
                messages=messages,
                memory_service=ms,
                model_id=model_id,
                enable=True,
                user_id=str(getattr(config, "rules_memory_user_id", "system") or "system"),
                project_id=str(getattr(config, "rules_memory_project_id", "roo_rules") or "roo_rules"),
                max_shards=int(getattr(config, "rules_memory_max_shards", 8)),
                max_tokens=int(getattr(config, "rules_memory_max_tokens", 900)),
                trivial_bypass=bool(getattr(config, "rules_memory_trivial_bypass", True)),
            )
            if rules_stats.get("injected"):
                logger.info(
                    "Injected rules from Mem0 "
                    f"retrieved={rules_stats.get('retrieved')} tokens~{rules_stats.get('tokens_used')}"
                )

        # 2) Optional code JIT retrieval/injection
        if bool(getattr(config, "enable_code_jit_context", False)):
            messages, code_stats = await inject_code_jit_context(
                messages=messages,
                memory_service=ms,
                model_id=model_id,
                enable=True,
                user_id=user_id,
                project_id=project_id,
                max_chunks=int(getattr(config, "code_jit_max_chunks", 6)),
                max_tokens=int(getattr(config, "code_jit_max_tokens", 2000)),
                trivial_bypass=bool(getattr(config, "code_jit_trivial_bypass", True)),
            )
            if code_stats.get("injected"):
                logger.info(
                    "Injected code JIT context "
                    f"retrieved={code_stats.get('retrieved')} tokens~{code_stats.get('tokens_used')}"
                )

        # 3) Context management / condensation (Mem0-backed)
        enable_ctx = bool(getattr(config, "enable_context_management", True))
        if payload.get("enable_context_management") is False:
            enable_ctx = False

        if enable_ctx:
            session_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4()}"
            max_ctx = int(getattr(config, "default_max_context_length", 65470) or 65470)

            tools = payload.get("tools")
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
                    "Context condensed via Mem0 "
                    f"stored={ctx_stats.get('messages_stored')} saved={ctx_stats.get('tokens_saved')}"
                )

        payload["messages"] = messages

    @app.get("/v1/models")
    async def list_models(request: Request) -> JSONResponse:  # type: ignore[reportUnusedFunction]
        """List models by proxying the upstream RouteLLM model catalog.

        This endpoint exposes the upstream Abacus model catalog (e.g.
        `https://routellm.abacus.ai/v1/models`) at the local OpenAI-compatible
        URL (e.g. `http://localhost:8003/v1/models`).

        Optional enrichment:
        - Query: `?enrich=value_index`
        - Requires server config: ENABLE_MODEL_VALUE_INDEX=true
        - Adds per-model `abacus_proxy.use_cases.*.index` and explanations

        Caching:
        - Successful responses are cached in-memory for a short TTL to reduce load.
        - Error responses are not cached.

        Auth:
        - If ABACUS_API_KEY is configured on the server, use it.
        - Otherwise, forward the inbound Authorization header (if present).
        """
        enrich = request.query_params.get("enrich")

        cache_ttl_s = 60.0
        cache = getattr(app.state, "models_cache", {"ts": 0.0, "data": None})
        now = time.monotonic()

        if cache.get("data") is not None and (now - float(cache.get("ts", 0.0))) < cache_ttl_s:
            data = cache["data"]
            # Enrich on-demand (do not cache enriched variant; keep cache as raw upstream).
            if (
                enrich == "value_index"
                and bool(getattr(config, "enable_model_value_index", False))
                and isinstance(data, dict)
            ):
                from src.utils.model_value_index import (
                    ValueIndexConfig,
                    enrich_abacus_models,
                )

                vi_cfg = ValueIndexConfig(
                    family_quality_priors=dict(getattr(config, "model_value_index_family_priors", {}) or {}),
                    missing_thinking_penalty=float(
                        getattr(config, "model_value_index_missing_thinking_penalty", 0.10) or 0.10
                    ),
                )
                enriched = enrich_abacus_models(
                    data,
                    cfg=vi_cfg,
                    exclude_router_models=bool(
                        getattr(config, "model_value_index_exclude_router_models", True)
                    ),
                )
                return JSONResponse(status_code=200, content=enriched)

            return JSONResponse(status_code=200, content=data)

        api_base = _normalize_api_base_url(config.routellm_api_base_url)
        url = f"{api_base}/models"
        headers = _get_upstream_headers(request=request)

        try:
            transport = _get_upstream_transport()
            async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
                r = await client.get(url, headers=headers)

            # Best-effort JSON parse.
            try:
                data = r.json()
            except Exception:
                data = {"raw_text": r.text}

            # If upstream errors, fall back to minimal list (do not cache).
            if r.status_code >= 400:
                return JSONResponse(
                    status_code=200,
                    content={
                        "object": "list",
                        "data": [{"id": config.routellm_model_id, "object": "model"}],
                        "upstream_error": {"status_code": r.status_code, "body": data},
                    },
                )

            # Cache only successful results (raw upstream data).
            cache["ts"] = now
            cache["data"] = data
            app.state.models_cache = cache

            if (
                enrich == "value_index"
                and bool(getattr(config, "enable_model_value_index", False))
                and isinstance(data, dict)
            ):
                from src.utils.model_value_index import ValueIndexConfig, enrich_abacus_models

                vi_cfg = ValueIndexConfig(
                    family_quality_priors=dict(getattr(config, "model_value_index_family_priors", {}) or {}),
                    missing_thinking_penalty=float(
                        getattr(config, "model_value_index_missing_thinking_penalty", 0.10) or 0.10
                    ),
                )
                enriched = enrich_abacus_models(
                    data,
                    cfg=vi_cfg,
                    exclude_router_models=bool(
                        getattr(config, "model_value_index_exclude_router_models", True)
                    ),
                )
                return JSONResponse(status_code=r.status_code, content=enriched)

            return JSONResponse(status_code=r.status_code, content=data)

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch models from upstream RouteLLM: {e}")
            return JSONResponse(
                status_code=200,
                content={
                    "object": "list",
                    "data": [{"id": config.routellm_model_id, "object": "model"}],
                    "upstream_error": {"exception": str(e)},
                },
            )

    @app.post("/v1/completions")
    async def completions_legacy(request: Request):  # type: ignore[reportUnusedFunction]
        """OpenAI-compatible *legacy* completions endpoint.

        Some clients (including provider "test" flows) still call `/v1/completions`.
        RouteLLM/Abacus primarily supports chat completions, so we translate the
        legacy prompt format into a chat request and forward through the same
        proxy path as [`chat_completions()`](src/app_fastapi.py:266).

        Supported inputs (best-effort):
        - `prompt`: string | list[string]
        - `max_tokens`, `temperature`, `top_p`, `stop`, `stream`, `model`

        Output:
        - Returns a *chat.completion*-shaped response (not text completion), but
          this is sufficient for Roo's connectivity tests and most clients that
          only need a successful response.
        """
        try:
            payload = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}") from e

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        prompt = payload.get("prompt", "")
        if isinstance(prompt, list):
            prompt_text = "\n".join(str(p) for p in prompt)
        else:
            prompt_text = str(prompt)

        chat_payload: dict[str, Any] = {
            "model": payload.get("model", config.routellm_model_id),
            "messages": [{"role": "user", "content": prompt_text}],
        }

        await _apply_mem0_enhancements(chat_payload, request)

        # Prompt guard last (after Mem0 enhancements).
        _apply_prompt_guard(chat_payload)

        # Pass-through a few commonly used sampling params.
        for k in ("max_tokens", "temperature", "top_p", "stop", "stream", "presence_penalty", "frequency_penalty"):
            if k in payload:
                chat_payload[k] = payload[k]

        # Forward via the existing handler logic by calling upstream directly here
        # using the same routing/sanitization behaviors as chat_completions.
        headers = _get_upstream_headers(request=request)

        # Reuse the model routing/aliasing logic from chat_completions.
        requested_model = chat_payload.get("model")
        if not isinstance(requested_model, str) or not requested_model.strip():
            requested_model = config.routellm_model_id
        requested_model = requested_model.strip()

        # Determine which upstream API to use based on requested model
        use_vllm = requested_model == config.vllm_model_id
        if use_vllm:
            api_base = _normalize_api_base_url(config.vllm_api_base_url)
            upstream_model = requested_model
        else:
            api_base = _normalize_api_base_url(config.routellm_api_base_url)
            
            aliases = getattr(config, "model_aliases", {}) or {}
            upstream_model = aliases.get(requested_model, requested_model)
            if upstream_model == "route-llm":
                upstream_model = getattr(config, "route_default_target_model", "gpt-5.2") or "gpt-5.2"

        upstream_url = f"{api_base}/chat/completions"
        _assert_not_recursive(upstream_url=upstream_url, request=request)
        
        chat_payload["model"] = upstream_model

        stream = bool(chat_payload.get("stream", False))
        if stream:
            headers.setdefault("Accept", "text/event-stream")

        try:
            timeout_s = float(getattr(config, "upstream_timeout_seconds", 120.0))
            timeout = httpx.Timeout(timeout_s, connect=10.0)

            transport = _get_upstream_transport()
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                r = await client.post(upstream_url, headers=headers, json=chat_payload)

            try:
                data = r.json()
                return JSONResponse(status_code=r.status_code, content=data)
            except Exception:
                return JSONResponse(status_code=r.status_code, content={"raw_text": r.text})

        except httpx.ConnectError as e:
            raise HTTPException(status_code=502, detail="Failed to connect to upstream") from e
        except httpx.TimeoutException as e:
            raise HTTPException(status_code=504, detail="Timeout talking to upstream") from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Upstream error: {e}") from e

    async def _select_best_model(
        payload: dict[str, Any],
        *,
        config: AppConfig,
    ) -> str:
        """Select best model from RouteLLM catalog based on request characteristics.
        
        This function implements intelligent model routing by:
        1. Fetching the enriched models catalog from upstream
        2. Detecting the use case from request characteristics
        3. Filtering eligible models for that use case
        4. Selecting the model with the highest value index
        
        Returns:
            Model ID to use for the upstream request
        """
        from src.utils.model_value_index import (
            ValueIndexConfig,
            enrich_abacus_models,
        )
        
        # Fetch models catalog (use cache if available)
        cache = getattr(app.state, "models_cache", {"ts": 0.0, "data": None})
        cache_ttl_s = 60.0
        now = time.monotonic()
        
        models_data = None
        if cache.get("data") is not None and (now - float(cache.get("ts", 0.0))) < cache_ttl_s:
            models_data = cache["data"]
        else:
            # Fetch from upstream
            api_base = _normalize_api_base_url(config.routellm_api_base_url)
            url = f"{api_base}/models"
            headers = _get_upstream_headers()
            
            try:
                transport = _get_upstream_transport()
                async with httpx.AsyncClient(timeout=20.0, transport=transport) as client:
                    r = await client.get(url, headers=headers)
                
                if r.status_code == 200:
                    models_data = r.json()
                    cache["ts"] = now
                    cache["data"] = models_data
                    app.state.models_cache = cache
            except Exception as e:
                logger.warning(f"Failed to fetch models for intelligent routing: {e}")
        
        if not models_data or not isinstance(models_data, dict):
            # Fallback to default if catalog unavailable
            fallback = getattr(config, "route_default_target_model", "gpt-5.2") or "gpt-5.2"
            logger.warning(f"Models catalog unavailable, using fallback: {fallback}")
            return fallback
        
        # Enrich models with value indexes
        vi_cfg = ValueIndexConfig(
            family_quality_priors=dict(getattr(config, "model_value_index_family_priors", {}) or {}),
            missing_thinking_penalty=float(
                getattr(config, "model_value_index_missing_thinking_penalty", 0.10) or 0.10
            ),
        )
        enriched = enrich_abacus_models(
            models_data,
            cfg=vi_cfg,
            exclude_router_models=True,
        )
        
        # Detect use case from request
        use_case = _detect_use_case(payload)
        
        # Filter eligible models for this use case
        models = enriched.get("data", [])
        eligible_models = []
        
        for m in models:
            if not isinstance(m, dict):
                continue
            
            ap = m.get("abacus_proxy")
            if not isinstance(ap, dict):
                continue
            
            ucs = ap.get("use_cases", {})
            if not isinstance(ucs, dict):
                continue
            
            uc_data = ucs.get(use_case)
            if not isinstance(uc_data, dict):
                continue
            
            if uc_data.get("eligible"):
                eligible_models.append({
                    "id": m.get("id"),
                    "index": float(uc_data.get("index", 0.0)),
                    "capability": float(uc_data.get("capability", 0.0)),
                    "job_cost": float(uc_data.get("job_cost", 0.0)),
                })
        
        if not eligible_models:
            fallback = getattr(config, "route_default_target_model", "gpt-5.2") or "gpt-5.2"
            logger.warning(
                f"No eligible models for use_case={use_case}, using fallback: {fallback}"
            )
            return fallback
        
        # Sort by value index (highest first)
        eligible_models.sort(key=lambda m: m["index"], reverse=True)
        
        selected = eligible_models[0]
        logger.info(
            f"Intelligent routing: use_case={use_case} selected={selected['id']} "
            f"index={selected['index']:.1f} capability={selected['capability']:.3f} "
            f"cost=${selected['job_cost']:.6f}"
        )
        
        return str(selected["id"])
    
    def _detect_use_case(payload: dict[str, Any]) -> str:
        """Detect use case from request characteristics.
        
        Returns:
            One of: "coding", "architecting", "condensation"
        """
        messages = payload.get("messages", [])
        tools = payload.get("tools", [])
        
        # Calculate total input size
        total_chars = 0
        for m in messages:
            if isinstance(m, dict):
                content = m.get("content", "")
                if isinstance(content, str):
                    total_chars += len(content)
                elif isinstance(content, list):
                    # Handle multi-modal content
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            total_chars += len(str(item.get("text", "")))
        
        # Large context = condensation use case
        if total_chars > 30000:
            return "condensation"
        
        # Check for coding indicators
        coding_keywords = [
            "code", "implement", "function", "class", "debug", "refactor",
            "bug", "error", "fix", "test", "api", "endpoint", "database",
            "query", "algorithm", "data structure", "variable", "method",
            "import", "export", "module", "package", "library", "framework",
        ]
        
        # Tools present suggests coding/implementation work
        if tools and isinstance(tools, list) and len(tools) > 0:
            return "coding"
        
        # Check message content for coding keywords
        content_lower = ""
        for m in messages:
            if isinstance(m, dict):
                content = m.get("content", "")
                if isinstance(content, str):
                    content_lower += content.lower() + " "
        
        coding_score = sum(1 for kw in coding_keywords if kw in content_lower)
        
        # High coding keyword density = coding use case
        if coding_score >= 3:
            return "coding"
        
        # Check for architecture/planning keywords
        arch_keywords = [
            "design", "architecture", "plan", "strategy", "approach",
            "system", "component", "module", "structure", "pattern",
            "workflow", "process", "integration", "scalability",
        ]
        
        arch_score = sum(1 for kw in arch_keywords if kw in content_lower)
        
        if arch_score >= 2:
            return "architecting"
        
        # Default to coding for ambiguous cases (most common in Roo)
        return "coding"
    
    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):  # type: ignore[reportUnusedFunction]
        """OpenAI-compatible chat completions endpoint.

        Roo (and most OpenAI-compatible clients) will call this endpoint.
        This proxy forwards the request to the configured RouteLLM upstream.

        Important:
        - When model="route-llm", intelligent model selection is performed
        - When model="route-vllm", requests are routed to local vLLM instance
        - Streaming is supported via pass-through when `stream=true`.
        """
        headers = _get_upstream_headers(request=request)

        try:
            payload = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}") from e

        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        await _apply_mem0_enhancements(payload, request)

        # Prompt guard last (it may drop old messages; should run after condensation store).
        _apply_prompt_guard(payload)

        # Tool injection/filtering removed - Roo Code provides tools directly
        # Tools are passed through from client without modification


        # ------------------------------------------------------------------
        # Model routing / aliasing
        #
        # The upstream `/v1/models` may include "router" models (like `route-llm`)
        # that redirect internally. For deterministic round-trip testing, we
        # allow the client to request `route-llm` locally, while we forward to a
        # concrete upstream model (default: gpt-5.2).
        #
        # route-vllm routes directly to local vLLM instance at configured URL.
        #
        # This is controlled by:
        # - AppConfig.model_aliases (env: MODEL_ALIASES_JSON)
        # - AppConfig.route_default_target_model (env: ROUTE_DEFAULT_TARGET_MODEL)
        # - AppConfig.vllm_api_base_url (env: VLLM_API_BASE_URL)
        # ------------------------------------------------------------------
        requested_model = payload.get("model")
        if not isinstance(requested_model, str) or not requested_model.strip():
            requested_model = config.routellm_model_id
        requested_model = requested_model.strip()

        # Determine which upstream API to use based on requested model
        use_vllm = requested_model == config.vllm_model_id
        if use_vllm:
            api_base = _normalize_api_base_url(config.vllm_api_base_url)
            upstream_model = requested_model
        else:
            api_base = _normalize_api_base_url(config.routellm_api_base_url)
            
            aliases = getattr(config, "model_aliases", {}) or {}
            upstream_model = aliases.get(requested_model, requested_model)

            # Intelligent model selection when route-llm is requested
            if upstream_model == "route-llm":
                if bool(getattr(config, "enable_intelligent_routing", True)):
                    upstream_model = await _select_best_model(payload, config=config)
                else:
                    # Fallback to configured default
                    upstream_model = getattr(config, "route_default_target_model", "gpt-5.2") or "gpt-5.2"

            if upstream_model != requested_model:
                logger.info(f"Model alias: requested={requested_model} -> upstream={upstream_model}")

        # Set upstream URL after determining API base
        upstream_url = f"{api_base}/chat/completions"
        _assert_not_recursive(upstream_url=upstream_url, request=request)

        # Overwrite model for upstream call. Everything else is passed through
        # (gpt-5.2 is standard OpenAI protocol per requirements).
        payload["model"] = upstream_model

        stream = bool(payload.get("stream", False))
        if stream:
            # Hint SSE for OpenAI-compatible streaming.
            headers.setdefault("Accept", "text/event-stream")

        try:
            timeout_s = float(getattr(config, "upstream_timeout_seconds", 120.0))
            # Connect failures can otherwise hang for a long time; keep this short.
            timeout = httpx.Timeout(timeout_s, connect=10.0)

            t0 = time.monotonic()
            logger.info(
                f"Proxying /v1/chat/completions to upstream={upstream_url} stream={stream}"
            )

            transport = _get_upstream_transport()
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                if stream:
                    # Upstream streaming has proven unreliable in this environment (ReadError after 200).
                    # Instead, fetch a normal (non-stream) completion and translate it into SSE chunks.
                    payload_nostream = dict(payload)
                    payload_nostream["stream"] = False

                    r = await client.post(upstream_url, headers=headers, json=payload_nostream)

                    logger.info(
                        "Upstream non-stream responded (for local SSE) "
                        f"status={r.status_code} elapsed_s={time.monotonic() - t0:.3f}"
                    )

                    # Return upstream errors as JSON.
                    try:
                        upstream_json = r.json()
                    except Exception:
                        upstream_json = {"raw_text": r.text}

                    if r.status_code >= 400:
                        return JSONResponse(status_code=r.status_code, content=upstream_json)

                    upstream_json = _rewrite_text_only_to_attempt_completion(upstream_json)

                    def _sse_line(obj: dict[str, Any] | str) -> bytes:
                        import json as _json

                        if isinstance(obj, str):
                            return f"data: {obj}\n\n".encode("utf-8")
                        return ("data: " + _json.dumps(obj, ensure_ascii=False) + "\n\n").encode(
                            "utf-8"
                        )

                    def _completion_to_sse_events(data: dict[str, Any]):
                        # Convert OpenAI chat.completion -> chat.completion.chunk stream.
                        comp_id = data.get("id", "chatcmpl-local")
                        created = data.get("created")
                        model = data.get("model")
                        choices = data.get("choices") or []

                        for choice in choices:
                            idx = choice.get("index", 0)
                            msg = (choice.get("message") or {}) if isinstance(choice, dict) else {}
                            role = msg.get("role")
                            content = msg.get("content")
                            tool_calls = msg.get("tool_calls")
                            finish_reason = choice.get("finish_reason")

                            # Role chunk
                            if role:
                                yield {
                                    "id": comp_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model,
                                    "choices": [{"index": idx, "delta": {"role": role}, "finish_reason": None}],
                                }

                            # Tool calls chunk (if present)
                            if tool_calls is not None:
                                yield {
                                    "id": comp_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model,
                                    "choices": [
                                        {
                                            "index": idx,
                                            "delta": {"tool_calls": tool_calls},
                                            "finish_reason": None,
                                        }
                                    ],
                                }

                            # Content chunk
                            if content:
                                yield {
                                    "id": comp_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model,
                                    "choices": [
                                        {"index": idx, "delta": {"content": content}, "finish_reason": None}
                                    ],
                                }

                            # Finish chunk
                            yield {
                                "id": comp_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [{"index": idx, "delta": {}, "finish_reason": finish_reason}],
                            }

                    async def _iter_bytes():
                        # Emit translated chunks then DONE.
                        try:
                            for ev in _completion_to_sse_events(upstream_json):
                                yield _sse_line(ev)
                        finally:
                            yield _sse_line("[DONE]")

                    return StreamingResponse(
                        _iter_bytes(),
                        status_code=200,
                        media_type="text/event-stream",
                    )

                r = await client.post(upstream_url, headers=headers, json=payload)

            logger.info(
                "Upstream /chat/completions responded "
                f"status={r.status_code} elapsed_s={time.monotonic() - t0:.3f}"
            )

            # Best-effort JSON parse.
            try:
                data = r.json()
                data = _rewrite_text_only_to_attempt_completion(data)
                
                # Log request (best-effort, never blocks)
                try:
                    request_logger_inst = getattr(app.state, "request_logger", None)
                    if request_logger_inst and request_logger_inst.enabled:
                        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4()}"
                        
                        # Determine routing metadata
                        routing_method = "intelligent" if upstream_model != requested_model and requested_model == "route-llm" else "direct"
                        use_case = _detect_use_case(payload) if routing_method == "intelligent" else None
                        
                        request_logger_inst.log_request(
                            request_id=request_id,
                            request_payload=payload if config.store_request_payloads else {},
                            response_payload=data if config.store_response_payloads else {},
                            status_code=r.status_code,
                            routing_method=routing_method,
                            requested_model=requested_model,
                            selected_model=upstream_model,
                            use_case_detected=use_case,
                        )
                except Exception as log_err:
                    logger.warning(f"Request tracking failed (ignored): {log_err}")
                
                return JSONResponse(status_code=r.status_code, content=data)
            except Exception:
                return JSONResponse(
                    status_code=r.status_code,
                    content={"raw_text": r.text},
                )

        except httpx.ConnectError as e:
            raise HTTPException(status_code=502, detail="Failed to connect to upstream") from e
        except httpx.TimeoutException as e:
            raise HTTPException(status_code=504, detail="Timeout talking to upstream") from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Upstream error: {e}") from e

    return app
