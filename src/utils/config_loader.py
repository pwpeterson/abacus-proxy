"""Configuration loading utilities for abacus-proxy."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from src.models.config import AppConfig


def _load_dotenv_files() -> None:
    """Best-effort load of env files.

    Precedence (later overrides earlier):
    1) abacus-proxy.env (repo defaults; non-secrets)
    2) model-proxy.env (kept for compatibility with existing quadlets)
    3) supabase.env (if present)
    4) .env (host-specific overrides / secrets)
    """
    if os.getenv("_ABACUS_PROXY_DOTENV_LOADED", "").lower() in ("1", "true", "yes", "on"):
        return

    project_root = Path(__file__).resolve().parent.parent.parent

    for filename, override in (
        ("abacus-proxy.env", False),
        ("model-proxy.env", False),
        ("supabase.env", False),
        (".env", True),
    ):
        p = project_root / filename
        if p.exists():
            load_dotenv(p, override=override)

    os.environ["_ABACUS_PROXY_DOTENV_LOADED"] = "1"


def _parse_model_aliases(*, default_target: str) -> dict[str, str]:
    """Parse MODEL_ALIASES_JSON (a JSON object) into a mapping of model->model.

    Example:
      MODEL_ALIASES_JSON='{"route-llm":"gpt-5.2","cheap-router":"gemini-3.1-flash"}'
    """
    raw = (os.getenv("MODEL_ALIASES_JSON") or "").strip()
    if not raw:
        return {"route-llm": default_target}

    try:
        data = json.loads(raw)
    except Exception:
        # Fall back to safe default if env var is malformed
        return {"route-llm": default_target}

    if not isinstance(data, dict):
        return {"route-llm": default_target}

    out: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            out[k.strip()] = v.strip()

    return out or {"route-llm": default_target}


def _parse_family_quality_priors(*, default: dict[str, float] | None = None) -> dict[str, float]:
    """Parse MODEL_VALUE_INDEX_FAMILY_PRIORS_JSON into {family: prior_float}.

    Example:
      MODEL_VALUE_INDEX_FAMILY_PRIORS_JSON='{"openai":1.05,"anthropic":1.1,"meta":0.9}'
    """
    raw = (os.getenv("MODEL_VALUE_INDEX_FAMILY_PRIORS_JSON") or "").strip()
    if not raw:
        return dict(default or {})

    try:
        data = json.loads(raw)
    except Exception:
        return dict(default or {})

    if not isinstance(data, dict):
        return dict(default or {})

    out: dict[str, float] = {}
    for k, v in data.items():
        if not (isinstance(k, str) and k.strip()):
            continue
        try:
            fv = float(v)
        except Exception:
            continue
        if fv <= 0:
            continue
        out[k.strip()] = fv

    return out or dict(default or {})


def _parse_float(*, env_name: str, default: float) -> float:
    raw = (os.getenv(env_name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _get_first_env(*names: str) -> str | None:
    """Return the first non-empty env var among names, stripped.

    Supports deployments that historically used different var names/casing.
    """
    for name in names:
        v = os.getenv(name)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def load_app_config() -> AppConfig:
    """Load AppConfig from environment variables."""
    _load_dotenv_files()

    route_default_target_model = os.getenv("ROUTE_DEFAULT_TARGET_MODEL", "gpt-5.2").strip() or "gpt-5.2"
    model_aliases = _parse_model_aliases(default_target=route_default_target_model)

    # Model value-index enrichment parsing
    enable_model_value_index = os.getenv("ENABLE_MODEL_VALUE_INDEX", "false").lower() == "true"
    model_value_index_exclude_router_models = (
        os.getenv("MODEL_VALUE_INDEX_EXCLUDE_ROUTER_MODELS", "true").lower() == "true"
    )
    model_value_index_missing_thinking_penalty = _parse_float(
        env_name="MODEL_VALUE_INDEX_MISSING_THINKING_PENALTY",
        default=0.10,
    )
    model_value_index_family_priors = _parse_family_quality_priors(default=None)

    enable_wire_logging = os.getenv("ENABLE_WIRE_LOGGING", "false").lower() == "true"
    wire_log_include_payloads = os.getenv("WIRE_LOG_INCLUDE_PAYLOADS", "false").lower() == "true"
    wire_log_max_chars = int(os.getenv("WIRE_LOG_MAX_CHARS", "8000"))
    wire_log_max_list_items = int(os.getenv("WIRE_LOG_MAX_LIST_ITEMS", "50"))

    # Legacy alias: some deployments used ENABLE_RAW_RESPONSE_LOGGING as a debug switch.
    legacy_raw_logging = os.getenv("ENABLE_RAW_RESPONSE_LOGGING", "").lower() == "true"

    return AppConfig(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8003")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_dir=Path(os.getenv("LOG_DIR", "logs")),
        # Wire logging
        enable_wire_logging=enable_wire_logging or legacy_raw_logging,
        wire_log_include_payloads=wire_log_include_payloads,
        wire_log_max_chars=wire_log_max_chars,
        wire_log_max_list_items=wire_log_max_list_items,
        enable_raw_response_logging=enable_wire_logging or legacy_raw_logging,
        # Upstream RouteLLM (single target)
        routellm_api_base_url=os.getenv("ROUTELLM_API_BASE_URL", "http://localhost:8003/v1"),
        routellm_model_id=os.getenv("ROUTELLM_MODEL_ID", "route-llm"),
        upstream_timeout_seconds=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "120.0")),
        # Prompt/context-size guard (heuristic char budgets)
        prompt_guard_enabled=os.getenv("PROMPT_GUARD_ENABLED", "true").lower() == "true",
        prompt_max_input_chars=int(os.getenv("PROMPT_MAX_INPUT_CHARS", "200000")),
        prompt_max_single_message_chars=int(os.getenv("PROMPT_MAX_SINGLE_MESSAGE_CHARS", "50000")),
        prompt_keep_last_messages=int(os.getenv("PROMPT_KEEP_LAST_MESSAGES", "80")),
        prompt_preserve_system_messages=os.getenv(
            "PROMPT_PRESERVE_SYSTEM_MESSAGES", "true"
        ).lower()
        == "true",
        # Mem0-backed memory
        enable_memory=os.getenv("ENABLE_MEMORY", "true").lower() == "true",
        memory_vector_store=os.getenv("MEMORY_VECTOR_STORE", "qdrant"),
        memory_vector_store_path=os.getenv("MEMORY_VECTOR_STORE_PATH", "./memory_data"),
        memory_embedding_model=os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small"),
        memory_collection_name=os.getenv("MEMORY_COLLECTION_NAME", "abacus_proxy_memory"),
        qdrant_url=os.getenv("QDRANT_URL") or None,
        qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
        # Context management (condensation)
        enable_context_management=os.getenv("ENABLE_CONTEXT_MANAGEMENT", "true").lower() == "true",
        context_threshold=float(os.getenv("CONTEXT_THRESHOLD", "0.7")),
        keep_recent_ratio=float(os.getenv("KEEP_RECENT_RATIO", "0.2")),
        min_keep_messages=int(os.getenv("MIN_KEEP_MESSAGES", "5")),
        summary_limit=int(os.getenv("SUMMARY_LIMIT", "3")),
        # Rules memory (Mem0-backed `.roo` rules)
        enable_rules_memory=os.getenv("ENABLE_RULES_MEMORY", "false").lower() == "true",
        rules_memory_user_id=os.getenv("RULES_MEMORY_USER_ID", "system"),
        rules_memory_project_id=os.getenv("RULES_MEMORY_PROJECT_ID", "roo_rules"),
        rules_memory_max_shards=int(os.getenv("RULES_MEMORY_MAX_SHARDS", "8")),
        rules_memory_max_tokens=int(os.getenv("RULES_MEMORY_MAX_TOKENS", "900")),
        rules_memory_trivial_bypass=os.getenv("RULES_MEMORY_TRIVIAL_BYPASS", "true").lower()
        == "true",
        auto_index_rules_on_startup=os.getenv("AUTO_INDEX_RULES_ON_STARTUP", "false").lower()
        == "true",
        auto_index_interval_hours=int(os.getenv("AUTO_INDEX_INTERVAL_HOURS", "24")),
        auto_index_state_dir=os.getenv("AUTO_INDEX_STATE_DIR") or None,
        auto_index_max_chars=int(os.getenv("AUTO_INDEX_MAX_CHARS", "1200")),
        auto_index_overlap_chars=int(os.getenv("AUTO_INDEX_OVERLAP_CHARS", "150")),
        # Code JIT context (Mem0-backed semantic code retrieval)
        enable_code_jit_context=os.getenv("ENABLE_CODE_JIT_CONTEXT", "false").lower() == "true",
        code_jit_max_chunks=int(os.getenv("CODE_JIT_MAX_CHUNKS", "6")),
        code_jit_max_tokens=int(os.getenv("CODE_JIT_MAX_TOKENS", "2000")),
        code_jit_trivial_bypass=os.getenv("CODE_JIT_TRIVIAL_BYPASS", "true").lower() == "true",
        # Routing/aliasing
        route_default_target_model=route_default_target_model,
        model_aliases=model_aliases,
        # Model value-index enrichment
        enable_model_value_index=enable_model_value_index,
        model_value_index_exclude_router_models=model_value_index_exclude_router_models,
        model_value_index_missing_thinking_penalty=model_value_index_missing_thinking_penalty,
        model_value_index_family_priors=model_value_index_family_priors,
        upstream_local_address=os.getenv("UPSTREAM_LOCAL_ADDRESS", "0.0.0.0"),
        # Backward-compatible legacy fields
        routellm_base_url=os.getenv("ROUTELLM_BASE_URL", "https://routellm.abacus.ai"),
        abacus_api_key=_get_first_env(
            "ABACUS_API_KEY",
            "ROUTELLM_API_KEY",
            "RouteLLM_API_KEY",
            "ROUTELLM_KEY",
            "RouteLLM_KEY",
        ),
        # Tool Schema Registry
        enable_tool_registry=os.getenv("ENABLE_TOOL_REGISTRY", "false").lower() == "true",
        tool_registry_path=os.getenv("TOOL_REGISTRY_PATH", "./tool_schema_registry.json"),
        enable_tool_injection=os.getenv("ENABLE_TOOL_INJECTION", "false").lower() == "true",
        tool_injection_max_tools=int(os.getenv("TOOL_INJECTION_MAX_TOOLS", "20")),
        tool_injection_max_tokens=int(os.getenv("TOOL_INJECTION_MAX_TOKENS", "5000")),
        enable_tool_filtering=os.getenv("ENABLE_TOOL_FILTERING", "false").lower() == "true",
        tool_filtering_max_tools=int(os.getenv("TOOL_FILTERING_MAX_TOOLS", "50")),
        # Request Tracking (Supabase telemetry)
        enable_request_tracking=os.getenv("ENABLE_REQUEST_TRACKING", "false").lower() == "true",
        supabase_url=os.getenv("SUPABASE_URL") or None,
        supabase_service_role_key=_get_first_env(
            "SUPABASE_SERVICE_ROLE_KEY",
            "SERVICE_ROLE_KEY",
            "SUPABASE_SERVICE_KEY",
            "SUPABASE_KEY",
        ),
        store_request_payloads=os.getenv("STORE_REQUEST_PAYLOADS", "false").lower() == "true",
        store_response_payloads=os.getenv("STORE_RESPONSE_PAYLOADS", "false").lower() == "true",
        store_context_text=os.getenv("STORE_CONTEXT_TEXT", "false").lower() == "true",
        request_tracking_timeout_seconds=float(os.getenv("REQUEST_TRACKING_TIMEOUT_SECONDS", "2.5")),
        # MCP server
        enable_mcp=os.getenv("ENABLE_MCP", "true").lower() == "true",
        mcp_host=os.getenv("MCP_HOST", "0.0.0.0"),
        mcp_port=int(os.getenv("MCP_PORT", "9002")),
        mcp_debug=os.getenv("MCP_DEBUG", "false").lower() == "true",
        mcp_max_message_size=int(os.getenv("MCP_MAX_MESSAGE_SIZE", str(10 * 1024 * 1024))),
        mcp_ping_interval=int(os.getenv("MCP_PING_INTERVAL", "20")),
        mcp_ping_timeout=int(os.getenv("MCP_PING_TIMEOUT", "60")),
        mcp_close_timeout=int(os.getenv("MCP_CLOSE_TIMEOUT", "10")),
    )
