"""Utilities to guard against upstream context-window errors.

This module provides best-effort truncation of chat messages based on character
budgets (tokenization-free heuristic). It is intentionally conservative and aims
to preserve the most recent conversational context.

Model-aware character limits ensure prompt guard triggers AFTER intelligent
condensation, serving as a safety net rather than the primary trimming mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptGuardStats:
    original_chars: int
    final_chars: int
    dropped_messages: int
    truncated_messages: int


def _message_content_chars(msg: dict[str, Any]) -> int:
    content = msg.get("content")
    if isinstance(content, str):
        return len(content)
    # Some clients might send non-string content; ignore for sizing.
    return 0


def _total_chars(messages: list[dict[str, Any]]) -> int:
    return sum(_message_content_chars(m) for m in messages)


def _is_system(msg: dict[str, Any]) -> bool:
    return (msg.get("role") == "system") and isinstance(msg.get("content"), str)


def _truncate_str_keep_head(s: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    return s[:max_chars]


def _truncate_str_keep_tail(s: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


def _calculate_model_aware_char_limit(
    max_context_length: int,
    condensation_threshold: float,
) -> int:
    """Calculate model-aware character limit that triggers AFTER condensation.
    
    Strategy:
    - Large context models (≥32K): Allow ~85% of context as chars (triggers after 75-80% condensation)
    - Medium context models (8K-32K): Allow ~70% of context as chars (triggers after 55% condensation)
    - Small context models (<8K): Allow ~60% of context as chars (triggers after 35% condensation)
    
    Uses 4:1 character-to-token ratio (conservative heuristic).
    
    Args:
        max_context_length: Model's maximum context window in tokens
        condensation_threshold: Condensation trigger point (e.g., 0.80 for 80%)
    
    Returns:
        Character limit that ensures prompt guard triggers AFTER condensation
    """
    # Model-aware multiplier (how much beyond condensation threshold before char limit triggers)
    if max_context_length >= 32000:
        # Large context: Allow up to 85% of context (beyond 75-80% condensation threshold)
        char_threshold = 0.85
    elif max_context_length >= 8000:
        # Medium context: Allow up to 70% of context (beyond 55% condensation threshold)
        char_threshold = 0.70
    else:
        # Small context (vLLM): Allow up to 60% of context (beyond 35% condensation threshold)
        char_threshold = 0.60
    
    # Convert tokens to characters (4 chars per token heuristic)
    char_limit = int(max_context_length * char_threshold * 4)
    
    return char_limit


def guard_chat_messages(
    messages: list[dict[str, Any]],
    *,
    max_input_chars: int,
    max_single_message_chars: int,
    keep_last_messages: int,
    preserve_system_messages: bool = True,
    max_context_length: int | None = None,
) -> tuple[list[dict[str, Any]], PromptGuardStats]:
    """Guard a chat `messages` array against excessive size.

    Strategy:
    1) Limit total number of messages to `keep_last_messages` (while optionally preserving
       any system messages).
    2) Truncate any single message content to `max_single_message_chars`.
       - System: keep head (prefix) since it often contains instructions.
       - Non-system: keep tail (suffix) since newest details are commonly at the end.
    3) If still too large, drop oldest non-system messages until within `max_input_chars`.

    Model-aware behavior:
    - If `max_context_length` is provided, calculates model-aware character limit
    - Character limit is set to trigger AFTER intelligent condensation
    - Ensures prompt guard serves as safety net, not primary trimming mechanism

    Notes:
    - This is a heuristic; upstream context windows are token-based, but char budgets
      prevent worst-case blowups without introducing tokenizer dependencies.
    """
    if not isinstance(messages, list):
        return [], PromptGuardStats(0, 0, 0, 0)

    # Model-aware character limit calculation
    effective_max_input_chars = max_input_chars
    if max_context_length is not None and max_context_length > 0:
        # Calculate threshold that triggers AFTER condensation
        # Use conservative estimate: large models use 75-80% condensation threshold
        condensation_threshold = 0.80 if max_context_length >= 32000 else (
            0.55 if max_context_length >= 8000 else 0.35
        )
        model_aware_limit = _calculate_model_aware_char_limit(
            max_context_length, condensation_threshold
        )
        # Use the larger of configured limit or model-aware limit
        # This ensures we don't accidentally make limits stricter
        effective_max_input_chars = max(max_input_chars, model_aware_limit)

    # Shallow-copy to avoid mutating caller's objects.
    normalized: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, dict):
            normalized.append(dict(m))

    original_chars = _total_chars(normalized)

    dropped_messages = 0
    truncated_messages = 0

    # Step 1: cap message count (preserving system messages if requested).
    if keep_last_messages > 0 and len(normalized) > keep_last_messages:
        if preserve_system_messages:
            system_msgs = [m for m in normalized if _is_system(m)]
            non_system = [m for m in normalized if not _is_system(m)]
            keep_non_system = non_system[-keep_last_messages:]
            dropped_messages += max(0, len(non_system) - len(keep_non_system))
            normalized = system_msgs + keep_non_system
        else:
            dropped_messages += len(normalized) - keep_last_messages
            normalized = normalized[-keep_last_messages:]

    # Step 2: truncate single message content.
    if max_single_message_chars is not None and max_single_message_chars > 0:
        for m in normalized:
            content = m.get("content")
            if not isinstance(content, str):
                continue
            if len(content) <= max_single_message_chars:
                continue
            if preserve_system_messages and _is_system(m):
                m["content"] = _truncate_str_keep_head(content, max_single_message_chars)
            else:
                m["content"] = _truncate_str_keep_tail(content, max_single_message_chars)
            truncated_messages += 1

    # Step 3: drop oldest non-system messages until under budget.
    if effective_max_input_chars is not None and effective_max_input_chars > 0:
        def _within_budget() -> bool:
            return _total_chars(normalized) <= effective_max_input_chars

        if not _within_budget():
            # Separate system/non-system to avoid dropping system unless unavoidable.
            if preserve_system_messages:
                system_msgs = [m for m in normalized if _is_system(m)]
                non_system = [m for m in normalized if not _is_system(m)]
            else:
                system_msgs = []
                non_system = normalized

            # Drop oldest non-system first.
            while non_system and not _within_budget():
                non_system.pop(0)
                dropped_messages += 1
                normalized = system_msgs + non_system

            # If still not within budget (e.g., system itself too large), truncate system.
            if not _within_budget() and system_msgs:
                # Allocate entire budget to system messages proportionally but simply:
                # truncate each system message to fit an even split.
                per_system = effective_max_input_chars // max(1, len(system_msgs))
                for sm in system_msgs:
                    content = sm.get("content")
                    if isinstance(content, str) and len(content) > per_system:
                        sm["content"] = _truncate_str_keep_head(content, per_system)
                        truncated_messages += 1
                normalized = system_msgs + non_system

    final_chars = _total_chars(normalized)
    return normalized, PromptGuardStats(
        original_chars=original_chars,
        final_chars=final_chars,
        dropped_messages=dropped_messages,
        truncated_messages=truncated_messages,
    )
