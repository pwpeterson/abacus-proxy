"""Supabase-backed request tracker for abacus-proxy.

Tracks API requests/responses for analyzing intelligent routing effectiveness,
context condensation, and cost optimization.

Based on model-proxy's ProxyApiLogger with enhancements for routing intelligence.

Tables:
- public.abacus_proxy_requests
- public.abacus_proxy_request_contexts
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


@dataclass(frozen=True)
class SupabaseConfig:
    """Supabase connection configuration for server-side telemetry."""

    url: str
    service_role_key: str


class AbacusRequestLogger:
    """Logs API requests/responses into abacus_proxy_* telemetry tables."""

    def __init__(
        self,
        *,
        enabled: bool,
        supabase_url: str | None = None,
        supabase_service_role_key: str | None = None,
        http_timeout_seconds: float = 2.5,
    ) -> None:
        """Initialize request logger.

        Args:
            enabled: Whether to enable request logging
            supabase_url: Supabase URL (optional, falls back to env vars)
            supabase_service_role_key: Supabase service role key (optional, falls back to env vars)
            http_timeout_seconds: HTTP timeout for Supabase requests
        """
        logger.info(
            f"[AbacusRequestLogger.__init__] enabled={enabled}, url={supabase_url}, "
            f"key_present={bool(supabase_service_role_key)}"
        )
        self.enabled = bool(enabled)

        # Prefer SUPABASE_URL for server-side telemetry
        url = (
            supabase_url or os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL") or ""
        ).strip()

        # Accept multiple common env var names
        key = (
            supabase_service_role_key
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or os.getenv("SERVICE_ROLE_KEY")
            or os.getenv("SUPABASE_KEY")
            or ""
        ).strip()

        self._http_timeout_seconds = float(http_timeout_seconds)

        self._cfg: SupabaseConfig | None = None
        if self.enabled:
            if not url or not key:
                logger.warning(
                    "Request tracking enabled but Supabase is not configured (missing url/key); "
                    f"disabling. url_present={bool(url)} key_present={bool(key)}"
                )
                self.enabled = False
            else:
                self._cfg = SupabaseConfig(url=url.rstrip("/"), service_role_key=key)

        # Lazy init for tiktoken encoding cache
        self._encoding_cache: dict[str, Any] = {}

    # -------------------------
    # Token counting utilities
    # -------------------------

    def _get_encoding(self, model: str):
        """Get tiktoken encoding for model."""
        import tiktoken

        if model not in self._encoding_cache:
            try:
                self._encoding_cache[model] = tiktoken.encoding_for_model(model)
            except KeyError:
                self._encoding_cache[model] = tiktoken.get_encoding("cl100k_base")
        return self._encoding_cache[model]

    def count_text_tokens(self, text: str, *, model: str) -> int:
        """Count tokens in text."""
        enc = self._get_encoding(model)
        return len(enc.encode(text))

    def count_messages_tokens(self, messages: list[dict[str, Any]], *, model: str) -> int:
        """Approximate token counting compatible with OpenAI chat formatting."""
        enc = self._get_encoding(model)
        tokens = 0

        for message in messages:
            tokens += 4  # <im_start>{role/name}\n{content}<im_end>\n
            if isinstance(message, dict):
                for key, value in message.items():
                    if isinstance(value, str):
                        tokens += len(enc.encode(value))
                    elif key == "tool_calls" and isinstance(value, list):
                        for tool_call in value:
                            if isinstance(tool_call, dict):
                                tokens += len(enc.encode(json.dumps(tool_call, ensure_ascii=False)))

                    if key == "name":
                        tokens -= 1

        tokens += 2  # every reply is primed with <im_start>assistant
        return max(tokens, 0)

    # -------------------------
    # Supabase HTTP helpers
    # -------------------------

    def _headers(self) -> dict[str, str]:
        """Get Supabase request headers."""
        assert self._cfg is not None
        key = self._cfg.service_role_key
        return {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }

    def _postgrest_table_url(self, table: str) -> str:
        """Get PostgREST table URL."""
        assert self._cfg is not None
        return f"{self._cfg.url}/rest/v1/{table}"

    def _safe_json(self, obj: Any) -> JsonValue:
        """Best-effort conversion to JSON-serializable structures."""
        return json.loads(json.dumps(obj, default=str))

    def _coerce_str(self, value: Any) -> str | None:
        """Coerce a value into a compact string identifier."""
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            v = value.strip()
            return v if v else None
        if isinstance(value, int):
            return str(value)
        return None

    def _insert_one(self, *, table: str, record: dict[str, Any]) -> None:
        """Insert a single record into Supabase table (best-effort)."""
        if not self.enabled:
            return
        try:
            url = self._postgrest_table_url(table)
            logger.debug(f"[_insert_one] Inserting to table={table}, url={url}")
            with httpx.Client(
                timeout=httpx.Timeout(
                    connect=1.0, read=self._http_timeout_seconds, write=5.0, pool=5.0
                )
            ) as c:
                r = c.post(url, headers=self._headers(), json=record)
                if r.status_code >= 400:
                    logger.warning(
                        f"Supabase insert failed: table={table} http={r.status_code} "
                        f"body={(r.text or '')[:500]}"
                    )
                else:
                    logger.debug(
                        f"[_insert_one] Successfully inserted to table={table}, "
                        f"status={r.status_code}"
                    )
        except Exception as e:
            logger.warning(f"Supabase insert error (ignored): table={table} err={e}")

    # -------------------------
    # Public API
    # -------------------------

    def log_request(
        self,
        *,
        request_id: str,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any] | None,
        status_code: int,
        error_message: str | None = None,
        app_id: Any = None,
        request_time: datetime | None = None,
        response_time: datetime | None = None,
        duration_ms: int | None = None,
        provider_name: str | None = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
        prompt_char_count: int | None = None,
        # Intelligent routing metadata (NEW for abacus-proxy)
        routing_method: str | None = None,
        requested_model: str | None = None,
        selected_model: str | None = None,
        value_index_score: int | None = None,
        use_case_detected: str | None = None,
        # Context condensation analysis
        raw_request_context: str | None = None,
        condensed_request_context: str | None = None,
        condensation_method: str | None = None,
        condensation_version: str | None = None,
        condensation_trigger: str | None = None,
        raw_context_token_count: int | None = None,
        condensed_context_token_count: int | None = None,
    ) -> str | None:
        """Log a request to Supabase.

        Returns:
            The request_id, or None if disabled.
        """
        logger.debug(f"[AbacusRequestLogger.log_request] ENTRY: enabled={self.enabled}")
        if not self.enabled:
            return None

        # Default timestamps
        req_dt = request_time or datetime.now(timezone.utc)
        resp_dt = response_time

        # Normalize payloads
        request_payload_json = self._safe_json(request_payload)
        request_payload_safe: dict[str, Any] = (
            request_payload_json
            if isinstance(request_payload_json, dict)
            else {"_value": request_payload_json}
        )

        response_payload_json = (
            self._safe_json(response_payload) if response_payload is not None else None
        )
        response_payload_safe: dict[str, Any] | None = (
            response_payload_json if isinstance(response_payload_json, dict) else None
        )

        # Determine model/provider
        model_final = model
        if not model_final:
            mv = request_payload_safe.get("model")
            model_final = mv if isinstance(mv, str) and mv else "unknown"

        provider_final = provider_name or "routellm"

        # Count request tokens from messages if present
        request_token_count: int | None = None
        try:
            usage_obj = (
                response_payload_safe.get("usage")
                if isinstance(response_payload_safe, dict)
                else None
            )
            if isinstance(usage_obj, dict):
                pt = usage_obj.get("prompt_tokens")
                if isinstance(pt, (int, float)) and int(pt) >= 0:
                    request_token_count = int(pt)
        except Exception:
            request_token_count = None

        if request_token_count is None:
            try:
                messages_any = request_payload_safe.get("messages")
                messages: list[dict[str, Any]] = (
                    [m for m in messages_any if isinstance(m, dict)]
                    if isinstance(messages_any, list)
                    else []
                )
                if messages:
                    request_token_count = self.count_messages_tokens(messages, model=model_final)
            except Exception:
                request_token_count = None

        # Count response tokens
        response_token_count: int | None = None
        try:
            usage_obj = (
                response_payload_safe.get("usage")
                if isinstance(response_payload_safe, dict)
                else None
            )
            if isinstance(usage_obj, dict):
                ct = usage_obj.get("completion_tokens")
                if isinstance(ct, (int, float)) and int(ct) >= 0:
                    response_token_count = int(ct)
        except Exception:
            response_token_count = None

        if response_token_count is None:
            try:
                if isinstance(response_payload_safe, dict):
                    choices = response_payload_safe.get("choices")
                    if isinstance(choices, list) and choices:
                        first = choices[0] if isinstance(choices[0], dict) else {}
                        message_value = first.get("message")
                        msg = message_value if isinstance(message_value, dict) else {}
                        content = msg.get("content")
                        if isinstance(content, str) and content:
                            response_token_count = self.count_text_tokens(
                                content, model=model_final
                            )
            except Exception:
                response_token_count = None

        # Derive duration if possible
        if duration_ms is None and resp_dt is not None:
            try:
                duration_ms = int((resp_dt - req_dt).total_seconds() * 1000)
            except Exception:
                duration_ms = None

        # Calculate estimated cost (simplified - you may want to use a cost calculator)
        estimated_cost: float | None = None
        try:
            if request_token_count is not None:
                # Placeholder cost calculation - replace with actual model pricing
                input_cost_per_1k = 0.01  # $0.01 per 1K input tokens
                output_cost_per_1k = 0.03  # $0.03 per 1K output tokens
                estimated_cost = (
                    (request_token_count / 1000) * input_cost_per_1k
                    + ((response_token_count or 0) / 1000) * output_cost_per_1k
                )
                logger.debug(
                    f"[AbacusRequestLogger.log_request] Calculated cost: ${estimated_cost:.6f} "
                    f"(input={request_token_count}, output={response_token_count or 0})"
                )
        except Exception as e:
            logger.warning(f"Failed to calculate estimated cost: {e}")
            estimated_cost = None

        request_row: dict[str, Any] = {
            "request_id": str(request_id),
            "app_id": self._coerce_str(app_id),
            "provider": provider_final,
            "model": model_final,
            "request_timestamp": req_dt.isoformat(),
            "response_timestamp": resp_dt.isoformat() if resp_dt is not None else None,
            "latency_ms": duration_ms,
            "status_code": int(status_code),
            "error_message": error_message,
            "request_token_count": request_token_count,
            "response_token_count": response_token_count,
            "prompt_char_count": prompt_char_count,
            "estimated_cost": estimated_cost,
            # Intelligent routing metadata
            "routing_method": routing_method,
            "requested_model": requested_model,
            "selected_model": selected_model,
            "value_index_score": value_index_score,
            "use_case_detected": use_case_detected,
            # Payloads
            "request_payload": request_payload_safe,
            "response_payload": response_payload_safe,
            "metadata": metadata or {},
        }

        self._insert_one(table="abacus_proxy_requests", record=request_row)

        # Always write a context row for analysis
        raw_ctx = raw_request_context
        condensed_ctx = condensed_request_context

        if raw_ctx is None and condensed_ctx is not None:
            raw_ctx = condensed_ctx
        if condensed_ctx is None and raw_ctx is not None:
            condensed_ctx = raw_ctx

        raw_tok = raw_context_token_count
        condensed_tok = condensed_context_token_count
        if raw_tok is None and condensed_tok is not None:
            raw_tok = condensed_tok
        if condensed_tok is None and raw_tok is not None:
            condensed_tok = raw_tok

        context_row: dict[str, Any] = {
            "request_id": str(request_id),
            "raw_request_context": raw_ctx,
            "condensed_request_context": condensed_ctx,
            "condensation_method": condensation_method,
            "condensation_version": condensation_version,
            "condensation_trigger": condensation_trigger,
            "raw_context_chars": len(raw_ctx) if isinstance(raw_ctx, str) else None,
            "condensed_context_chars": (
                len(condensed_ctx) if isinstance(condensed_ctx, str) else None
            ),
            "raw_context_token_count": raw_tok,
            "condensed_context_token_count": condensed_tok,
        }
        self._insert_one(table="abacus_proxy_request_contexts", record=context_row)

        return str(request_id)
