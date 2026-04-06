"""Configuration models for abacus-proxy.

Keep this intentionally small for the initial container bring-up:
- HTTP server settings
- Logging settings
- RouteLLM upstream settings
- MCP server settings
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    # HTTP server
    host: str = Field(default="0.0.0.0")  # nosec B104
    port: int = Field(default=8003)

    # Logging
    log_level: str = Field(default="INFO")
    log_dir: Path = Field(default=Path("logs"))

    # ---------------------------------------------------------------------
    # Upstream RouteLLM (single target)
    #
    # For local deployments, this should be the full OpenAI-compatible API base
    # (including /v1), e.g.:
    #   http://localhost:8003/v1
    # ---------------------------------------------------------------------
    routellm_api_base_url: str = Field(default="http://localhost:8003/v1")
    routellm_model_id: str = Field(default="route-llm")
    upstream_timeout_seconds: float = Field(default=120.0)

    # ---------------------------------------------------------------------
    # Prompt/context-size guard (heuristic char budgets)
    #
    # Purpose: prevent upstream "input exceeds context window" failures by
    # trimming chat message histories before proxying.
    #
    # Notes:
    # - These are character budgets (not tokens) to avoid tokenizer deps.
    # - Set prompt_guard_enabled=false to disable.
    # ---------------------------------------------------------------------
    prompt_guard_enabled: bool = Field(default=True)
    prompt_max_input_chars: int = Field(
        default=200_000, description="Max total chars across messages.content (0 disables)"
    )
    prompt_max_single_message_chars: int = Field(
        default=50_000, description="Max chars per message.content (0 disables)"
    )
    prompt_keep_last_messages: int = Field(
        default=80, description="Keep last N non-system messages (0 disables)"
    )
    prompt_preserve_system_messages: bool = Field(
        default=True, description="Preserve system messages when trimming history"
    )

    # Default model alias target used when a client requests a "router" model
    # (e.g. route-llm) but we want deterministic round-trip testing against a
    # concrete model.
    #
    # Example:
    #   requested model: "route-llm"  -> upstream model: "gpt-5.2"
    route_default_target_model: str = Field(default="gpt-5.2")

    # JSON-configurable alias mapping (loaded from env).
    # Keys and values are model ids from upstream `/v1/models`.
    model_aliases: dict[str, str] = Field(default_factory=lambda: {"route-llm": "gpt-5.2"})

    # ---------------------------------------------------------------------
    # Model value-index enrichment (optional)
    #
    # When enabled and requested via query param, `/v1/models` can return an
    # enriched catalog with per-use-case cost/value indexes plus explanation.
    # ---------------------------------------------------------------------
    enable_model_value_index: bool = Field(default=False)

    # Exclude router models (e.g. `route-llm`) from scoring by default.
    model_value_index_exclude_router_models: bool = Field(default=True)

    # Penalize models missing an explicit `thinking` flag (0..0.8).
    model_value_index_missing_thinking_penalty: float = Field(default=0.10, ge=0.0, le=0.8)

    # Heuristic "quality priors" by provider family (openai/anthropic/google/...).
    # Keep empty for defaults; override via env JSON parsing in config_loader.
    model_value_index_family_priors: dict[str, float] = Field(default_factory=dict)

    # Force upstream connections to use a specific local bind address.
    # This is useful when IPv6 connectivity is broken (e.g., upstream has AAAA records
    # but outbound IPv6 cannot reach :443). Setting this to "0.0.0.0" forces IPv4.
    upstream_local_address: str | None = Field(default="0.0.0.0")

    # Backward-compatible legacy fields (still used by /v1/models endpoint until migrated)
    routellm_base_url: str = Field(default="https://routellm.abacus.ai")
    abacus_api_key: str | None = Field(default=None, description="ABACUS_API_KEY")

    # ---------------------------------------------------------------------
    # Mem0-backed memory + context management (condensation)
    # ---------------------------------------------------------------------
    enable_memory: bool = Field(default=True)

    memory_vector_store: str = Field(default="qdrant", description="mem0 vector store backend")
    memory_vector_store_path: str = Field(
        default="./memory_data", description="Local vector store path (when not using centralized Qdrant)"
    )
    memory_embedding_model: str = Field(default="text-embedding-3-small")
    memory_collection_name: str = Field(default="abacus_proxy_memory")

    # Centralized Qdrant (optional)
    qdrant_url: str | None = Field(default=None)
    qdrant_api_key: str | None = Field(default=None)

    # Context management (conversation condensation; Mem0-backed)
    enable_context_management: bool = Field(default=True)
    context_threshold: float = Field(
        default=0.7, ge=0.1, le=0.95, description="Condense when prompt tokens exceed this fraction of context"
    )
    keep_recent_ratio: float = Field(
        default=0.2, ge=0.0, le=1.0, description="Keep this fraction of most recent messages when condensing"
    )
    min_keep_messages: int = Field(default=5, ge=1, le=200, description="Always keep at least N messages")
    summary_limit: int = Field(default=3, ge=0, le=25, description="Retrieve up to N memories for summary context")

    # ---------------------------------------------------------------------
    # Mem0-backed `.roo` rules retrieval + injection (optional)
    # ---------------------------------------------------------------------
    enable_rules_memory: bool = Field(default=False)
    rules_memory_user_id: str = Field(default="system")
    rules_memory_project_id: str = Field(default="roo_rules")
    rules_memory_max_shards: int = Field(default=8, ge=0, le=50)
    rules_memory_max_tokens: int = Field(default=900, ge=0, le=10_000)
    rules_memory_trivial_bypass: bool = Field(default=True)

    # Auto-index `.roo/rules*` into Mem0 on startup (optional)
    auto_index_rules_on_startup: bool = Field(default=False)
    auto_index_interval_hours: int = Field(default=24, ge=1, le=720)
    auto_index_state_dir: str | None = Field(default=None)
    auto_index_max_chars: int = Field(default=1200, ge=200, le=20_000)
    auto_index_overlap_chars: int = Field(default=150, ge=0, le=5_000)

    # ---------------------------------------------------------------------
    # Codebase JIT semantic context (Mem0-backed; optional)
    # ---------------------------------------------------------------------
    enable_code_jit_context: bool = Field(default=False)
    code_jit_max_chunks: int = Field(default=6, ge=0, le=50)
    code_jit_max_tokens: int = Field(default=2000, ge=0, le=50_000)
    code_jit_trivial_bypass: bool = Field(default=True)

    # ---------------------------------------------------------------------
    # MCP server
    # ---------------------------------------------------------------------
    enable_mcp: bool = Field(default=True)
    mcp_host: str = Field(default="0.0.0.0")  # nosec B104
    mcp_port: int = Field(default=9002)
    mcp_debug: bool = Field(default=False)

    mcp_max_message_size: int = Field(default=10 * 1024 * 1024)
    mcp_ping_interval: int = Field(default=20)
    mcp_ping_timeout: int = Field(default=60)
    mcp_close_timeout: int = Field(default=10)
