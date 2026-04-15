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
    # Wire-level payload logging (debugging Roo ↔ abacus-proxy ↔ upstream)
    #
    # SECURITY:
    # - Payload logging is redacted best-effort, but still risky (PII may exist).
    # - Keep disabled by default. Prefer summaries unless debugging.
    # ---------------------------------------------------------------------
    enable_wire_logging: bool = Field(
        default=False,
        description="Enable wire-level request/response logging (summaries + correlation IDs)",
    )
    wire_log_include_payloads: bool = Field(
        default=False,
        description="Include truncated redacted JSON payload blobs in logs (very noisy)",
    )
    wire_log_max_chars: int = Field(
        default=8000,
        ge=500,
        le=200_000,
        description="Max chars for any logged payload JSON blob",
    )
    wire_log_max_list_items: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Max list items to include when logging/redacting payloads",
    )

    # Legacy debug flag used in some code paths; treat as an alias for enable_wire_logging.
    enable_raw_response_logging: bool = Field(
        default=False,
        description="Legacy alias for enable_wire_logging (kept for compatibility)",
    )

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
    # Upstream vLLM (alternative routing target)
    #
    # For local vLM deployments, this should be the full OpenAI-compatible
    # API base (including /v1), e.g.:
    #   http://localhost:8005/v1
    # ---------------------------------------------------------------------
    vllm_api_base_url: str = Field(default="http://localhost:8005/v1")
    vllm_model_id: str = Field(default="route-vllm")
    vllm_target_model: str = Field(default="gemma-4-26b-a4b-it", description="Actual model name to send to vLLM")
    vllm_force_stream: bool = Field(
        default=True,
        description="Force streaming mode for vLLM requests (recommended for compatibility)"
    )

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

    # ---------------------------------------------------------------------
    # Conversation completion contract (OpenAI-style tools)
    #
    # Purpose:
    # - Encourage upstream models to call `attempt_completion` only when the
    #   user's request is truly complete.
    # - Avoid forcing task completion by proxy-side response rewriting.
    # ---------------------------------------------------------------------
    completion_contract_enabled: bool = Field(
        default=True,
        description=(
            "Append a small instruction to the system prompt describing when to call "
            "attempt_completion vs reply normally"
        ),
    )

    # NOTE: We intentionally do NOT support proxy-side forced completion.
    # Upstream models should call `attempt_completion` explicitly when done.

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

    # ---------------------------------------------------------------------
    # Intelligent Model Routing (when model="route-llm")
    #
    # When enabled, abacus-proxy analyzes incoming requests and automatically
    # selects the best model from RouteLLM's catalog based on:
    # - Use case detection (coding, architecting, condensation)
    # - Model value index (capability/cost ratio)
    # - Capability gates (context length, max tokens, thinking support)
    # ---------------------------------------------------------------------
    enable_intelligent_routing: bool = Field(
        default=True,
        description="Enable intelligent model selection when model='route-llm'"
    )

    # Force upstream connections to use a specific local bind address.
    # This is useful when IPv6 connectivity is broken (e.g., upstream has AAAA records
    # but outbound IPv6 cannot reach :443). Setting this to "0.0.0.0" forces IPv4.
    upstream_local_address: str | None = Field(default="0.0.0.0")

    # Backward-compatible legacy fields (still used by /v1/models endpoint until migrated)
    routellm_base_url: str = Field(default="https://routellm.abacus.ai")
    abacus_api_key: str | None = Field(default=None, description="ABACUS_API_KEY")

    # ---------------------------------------------------------------------
    # Intelligent Tool Selection
    #
    # Analyzes request context and selectively includes only relevant tools:
    # - Always includes "core" tools (read_file, write_to_file, attempt_completion)
    # - Intelligently selects additional tools based on:
    #   * Request content keywords/semantics
    #   * Recent tool usage patterns
    #   * Mem0 insights (if enabled)
    #
    # Reduces token overhead by 50-80% (15K-25K tokens → 3K-5K tokens)
    # ---------------------------------------------------------------------
    enable_intelligent_tool_selection: bool = Field(
        default=True,
        description="Enable intelligent tool selection based on request context"
    )
    
    intelligent_tool_use_mem0: bool = Field(
        default=True,
        description="Use Mem0 to suggest tools based on historical usage patterns"
    )
    
    intelligent_tool_min_keyword_matches: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Minimum keyword matches to include a tool category"
    )
    
    intelligent_tool_recent_usage_window: int = Field(
        default=10,
        ge=3,
        le=50,
        description="Number of recent messages to analyze for tool usage patterns"
    )
    
    # Core tools that are ALWAYS included (customizable via CORE_TOOL_NAMES env var)
    core_tool_names: set[str] = Field(
        default_factory=lambda: {
            "read_file", "write_to_file", "list_files", "search_files",
            "attempt_completion", "ask_followup_question", "execute_command",
            "get_product_context", "get_active_context", "get_decisions",
            "search_decisions_fts", "get_recent_activity_summary",
        },
        description="Tool names that are always included in intelligent selection"
    )

    # ---------------------------------------------------------------------
    # MCP Tool Inclusion (legacy system prompt optimization)
    #
    # NOTE: Superseded by intelligent_tool_selection (above).
    # Kept for backward compatibility. Use intelligent_tool_selection instead.
    # ---------------------------------------------------------------------
    enable_tool_inclusion: bool = Field(
        default=False,
        description="[LEGACY] Enable MCP tool inclusion filtering (use intelligent_tool_selection instead)"
    )
    
    tool_include_categories: list[str] = Field(
        default_factory=list,
        description="[LEGACY] MCP server categories to include"
    )
    
    tool_include_servers: list[str] = Field(
        default_factory=list,
        description="[LEGACY] Specific MCP servers to include"
    )

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

    # Enhanced context management features
    enable_dynamic_threshold: bool = Field(
        default=True,
        description="Dynamically adjust threshold based on request characteristics"
    )
    enable_importance_scoring: bool = Field(
        default=True,
        description="Use importance scoring for message retention (considers tool calls, errors, recency)"
    )
    enable_system_compression: bool = Field(
        default=True,
        description="Compress system messages when they exceed budget"
    )
    enable_adaptive_summary: bool = Field(
        default=True,
        description="Adapt summary retrieval limit based on available budget"
    )

    # Advanced compression strategies
    enable_code_compression: bool = Field(
        default=True,
        description="Compress code blocks before storing in Mem0 (2-5K tokens saved)"
    )
    enable_differential_compression: bool = Field(
        default=True,
        description="Store only diffs for similar consecutive messages (1-3K tokens saved)"
    )
    enable_aggressive_system_dedup: bool = Field(
        default=True,
        description="Hash system message sections separately for better deduplication"
    )

    # Compression thresholds
    code_compression_min_lines: int = Field(
        default=20,
        ge=5,
        le=500,
        description="Minimum code block lines to trigger compression"
    )
    differential_similarity_threshold: float = Field(
        default=0.85,
        ge=0.5,
        le=0.99,
        description="Similarity threshold for differential compression (0.0-1.0)"
    )

    # Default max context length (used for token budget calculations)
    default_max_context_length: int = Field(
        default=65470,
        ge=1000,
        le=200000,
        description="Default maximum context length in tokens"
    )

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
    # Tool Schema Registry (context-aware tool filtering/injection)
    # ---------------------------------------------------------------------
    enable_tool_registry: bool = Field(default=False)
    tool_registry_path: str = Field(default="./tool_schema_registry.json")
    
    # Server-side tool injection (when client omits tools)
    enable_tool_injection: bool = Field(default=False)
    tool_injection_max_tools: int = Field(default=20, ge=1, le=100)
    tool_injection_max_tokens: int = Field(default=5000, ge=100, le=50_000)
    
    # Client-side tool filtering (when client provides tools)
    enable_tool_filtering: bool = Field(default=False)
    tool_filtering_max_tools: int = Field(default=50, ge=1, le=200)

    # ---------------------------------------------------------------------
    # Request Tracking (Supabase telemetry)
    #
    # Logs API requests/responses to Supabase for analyzing:
    # - Intelligent routing effectiveness (value-index, use case detection)
    # - Context condensation effectiveness (tokens saved)
    # - Cost optimization and trends
    # - Request payload transformations (debugging)
    # ---------------------------------------------------------------------
    enable_request_tracking: bool = Field(
        default=False,
        description="Enable request/response logging to Supabase"
    )
    
    # Supabase connection (falls back to env vars if not provided)
    supabase_url: str | None = Field(default=None)
    supabase_service_role_key: str | None = Field(default=None)
    
    # Privacy controls
    store_request_payloads: bool = Field(
        default=False,
        description="Store full request payloads (privacy sensitive)"
    )
    store_response_payloads: bool = Field(
        default=False,
        description="Store full response payloads (privacy sensitive)"
    )
    store_context_text: bool = Field(
        default=False,
        description="Store raw/condensed context text (privacy sensitive)"
    )
    
    # HTTP timeout for telemetry writes (best-effort, never blocks requests)
    request_tracking_timeout_seconds: float = Field(default=2.5, ge=0.5, le=10.0)

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
