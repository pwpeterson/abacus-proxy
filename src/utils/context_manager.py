"""Context management (conversation condensation) using Mem0.

Ported/adapted from model-proxy's ContextManager to abacus-proxy.

Behavior:
- Estimate token usage for the message list
- If usage exceeds a threshold fraction of model context, "condense":
  - Keep a tail slice of recent messages
  - Store older messages into Mem0 (best-effort)
  - Retrieve semantically relevant context from Mem0 and inject as a system message
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService
from src.utils.token_estimator import estimate_messages_tokens


class ContextManager:
    """Manages context size using Mem0 for intelligent condensation."""

    def __init__(
        self,
        memory_service: MemoryService,
        *,
        context_threshold: float = 0.7,
        keep_recent_ratio: float = 0.2,
        min_keep_messages: int = 5,
        summary_limit: int = 3,
    ) -> None:
        self.memory_service = memory_service
        self.context_threshold = float(context_threshold)
        self.keep_recent_ratio = float(keep_recent_ratio)
        self.min_keep_messages = int(min_keep_messages)
        self.summary_limit = int(summary_limit)

    def manage_context(
        self,
        *,
        messages: list[dict[str, Any]],
        model_id: str,
        max_context_length: int,
        user_id: str | None = None,
        session_id: str | None = None,
        enable_condensation: bool = True,
        project_id: str | None = None,
        agent_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Condense a conversation when it is approaching the model's context limit.

        Improvements over the initial port:
        - Preserve ALL existing system messages (don't store them in Mem0).
        - Condense only non-system history into Mem0 and keep a tail slice.
        - Include tool-schema tokens in the threshold calculation when provided.
        - Insert the retrieved summary AFTER the existing system messages.
        """
        current_tokens = estimate_messages_tokens(messages, model_id, tools=tools)
        threshold_tokens = int(max_context_length * self.context_threshold)

        stats: dict[str, Any] = {
            "original_tokens": current_tokens,
            "original_message_count": len(messages),
            "threshold_tokens": threshold_tokens,
            "max_context_length": max_context_length,
            "utilization_percent": (current_tokens / max_context_length) * 100
            if max_context_length > 0
            else 0.0,
            "condensed": False,
            "messages_stored": 0,
            "messages_kept": 0,
            "system_messages_kept": 0,
            "final_tokens": current_tokens,
            "tokens_saved": 0,
            "savings_percent": 0.0,
            "summary_added": False,
        }

        if not enable_condensation or current_tokens <= threshold_tokens:
            return messages, stats

        if not self.memory_service.is_enabled:
            stats["condensation_error"] = "memory_disabled"
            return messages, stats

        system_messages: list[dict[str, Any]] = []
        non_system_messages: list[dict[str, Any]] = []
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                system_messages.append(m)
            else:
                non_system_messages.append(m)

        stats["system_messages_kept"] = len(system_messages)

        keep_non_system_count = max(
            self.min_keep_messages,
            int(len(non_system_messages) * self.keep_recent_ratio),
        )
        if keep_non_system_count >= len(non_system_messages):
            keep_non_system_count = max(1, len(non_system_messages) - 1)

        recent_non_system = non_system_messages[-keep_non_system_count:]
        old_non_system = non_system_messages[:-keep_non_system_count]

        stats["messages_kept"] = len(system_messages) + len(recent_non_system)
        stats["messages_stored"] = len(old_non_system)

        logger.info(
            "Context threshold exceeded: "
            f"{current_tokens}/{threshold_tokens} tokens "
            f"({stats['utilization_percent']:.1f}% of {max_context_length}). "
            "Condensing using Mem0..."
        )

        condensed_messages: list[dict[str, Any]] = list(system_messages) + list(recent_non_system)

        if not old_non_system:
            # Nothing to store/condense beyond system+tail.
            new_tokens = estimate_messages_tokens(condensed_messages, model_id, tools=tools)
            stats["final_tokens"] = new_tokens
            stats["tokens_saved"] = max(0, current_tokens - new_tokens)
            stats["savings_percent"] = (
                (stats["tokens_saved"] / current_tokens) * 100 if current_tokens > 0 else 0.0
            )
            stats["condensed"] = True
            stats["final_utilization_percent"] = (new_tokens / max_context_length) * 100
            return condensed_messages, stats

        # Store old non-system messages in Mem0 (best-effort).
        try:
            res = self.memory_service.add_memory(
                messages=old_non_system,
                user_id=user_id,
                agent_id=agent_id,
                run_id=session_id,
                project_id=project_id,
                metadata={
                    "type": "context_condensation",
                    "condensed_at": datetime.now().isoformat(),
                    "original_tokens": current_tokens,
                    "model": model_id,
                    "message_count": len(old_non_system),
                },
            )
            if not res.success:
                stats["condensation_error"] = res.message
        except Exception as e:
            stats["condensation_error"] = str(e)
            return messages, stats

        # Avoid stacking multiple summary headers if prior condensations already injected one.
        already_has_summary = any(
            isinstance(m, dict)
            and m.get("role") == "system"
            and isinstance(m.get("content"), str)
            and "Previous Conversation Context" in (m.get("content") or "")
            for m in system_messages
        )

        if not already_has_summary:
            context_summary = self._get_context_summary(
                old_messages=old_non_system,
                user_id=user_id,
                agent_id=agent_id,
                project_id=project_id,
            )
            if context_summary:
                summary_message = {"role": "system", "content": context_summary}
                condensed_messages = list(system_messages) + [summary_message] + list(recent_non_system)
                stats["summary_added"] = True

        new_tokens = estimate_messages_tokens(condensed_messages, model_id, tools=tools)
        stats["final_tokens"] = new_tokens
        stats["tokens_saved"] = max(0, current_tokens - new_tokens)
        stats["savings_percent"] = (
            (stats["tokens_saved"] / current_tokens) * 100 if current_tokens > 0 else 0.0
        )
        stats["condensed"] = True
        stats["final_utilization_percent"] = (new_tokens / max_context_length) * 100

        logger.info(
            f"Context condensed: {current_tokens} → {new_tokens} tokens "
            f"(saved {stats['tokens_saved']} tokens, {stats['savings_percent']:.1f}% reduction)"
        )
        return condensed_messages, stats

    def _get_context_summary(
        self,
        *,
        old_messages: list[dict[str, Any]],
        user_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """Retrieve relevant context from Mem0 based on the tail of old messages."""
        if not self.memory_service.is_enabled:
            return ""

        query_messages = old_messages[-3:] if len(old_messages) >= 3 else old_messages
        query_text = " ".join(
            str(m.get("content", ""))
            for m in query_messages
            if isinstance(m, dict) and isinstance(m.get("content"), str)
        ).strip()

        if not query_text:
            return ""

        context = self.memory_service.get_relevant_context(
            query=query_text[:500],
            user_id=user_id,
            agent_id=agent_id,
            project_id=project_id,
            limit=max(0, self.summary_limit),
        )
        if not context:
            return ""

        return (
            "## Previous Conversation Context (retrieved from Mem0)\n\n"
            "The following items were retrieved from memory as likely relevant context:\n\n"
            f"{context}\n\n"
            "---\n"
            "Continue the conversation with this context in mind."
        ).strip()