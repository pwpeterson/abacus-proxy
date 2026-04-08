"""Multi-provider token estimation utilities.

Ported (simplified) from model-proxy to support:
- context management/condensation heuristics
- token-budgeting for Mem0-backed rule/code JIT injection

Design goals:
- Prefer accurate counters when optional deps are installed (litellm, tiktoken)
- Degrade gracefully to a deterministic heuristic when not available
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any

from loguru import logger

# Try litellm (universal token counting)
try:  # pragma: no cover
    from litellm import token_counter  # type: ignore

    _litellm_available = True
except Exception:  # pragma: no cover
    token_counter = None  # type: ignore[assignment]
    _litellm_available = False

# Try tiktoken (OpenAI token counting fallback)
try:  # pragma: no cover
    import tiktoken  # type: ignore

    _tiktoken_available = True
except Exception:  # pragma: no cover
    tiktoken = None  # type: ignore[assignment]
    _tiktoken_available = False


LITELLM_AVAILABLE = _litellm_available
TIKTOKEN_AVAILABLE = _tiktoken_available


def detect_provider(model: str) -> str:
    """Best-effort provider detection from model id."""
    m = (model or "").lower()
    if any(x in m for x in ("claude", "anthropic", "sonnet", "opus", "haiku")):
        return "anthropic"
    if any(x in m for x in ("gpt-", "text-davinci", "text-curie", "text-babbage", "text-ada")):
        return "openai"
    return "unknown"


def estimate_tokens(text: str) -> int:
    """Improved heuristic fallback when no tokenizer is available.
    
    Accounts for common patterns that affect token density:
    - Code blocks are denser (more tokens per char)
    - JSON/structured data is denser
    - Natural language is sparser
    """
    s = text if isinstance(text, str) else str(text)
    
    # Base calculation
    base_chars_per_token = 3.5
    
    # Detect code blocks
    code_blocks = s.count("```")
    # Detect JSON/structured data
    json_chars = s.count("{") + s.count("[") + s.count(":")
    # Detect whitespace density (code has more)
    whitespace_ratio = s.count(" ") / max(len(s), 1)
    
    # Adjust chars/token ratio based on content type
    if code_blocks > 0:
        # Code is denser: ~3.0 chars/token
        base_chars_per_token = 3.0
    elif json_chars > 10:
        # JSON is denser: ~3.2 chars/token
        base_chars_per_token = 3.2
    elif whitespace_ratio > 0.15:
        # High whitespace (likely code): ~3.3 chars/token
        base_chars_per_token = 3.3
    
    return int(len(s) / base_chars_per_token) + 1


@lru_cache(maxsize=1024)
def _cached_estimate_tokens(text: str, model: str) -> int:
    """Cached token estimation for repeated content.
    
    This is particularly useful for:
    - System messages that appear in every request
    - Rules that are injected repeatedly
    - Tool schemas that don't change
    """
    return estimate_tokens_by_provider(text, model)


def estimate_tokens_openai(text: str, model: str = "gpt-4") -> int:
    """Estimate tokens for OpenAI-ish models using tiktoken (if available)."""
    if not TIKTOKEN_AVAILABLE or tiktoken is None:
        return estimate_tokens(text)

    try:
        try:
            encoding = tiktoken.encoding_for_model(model)
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception as e:  # pragma: no cover
        logger.debug(f"tiktoken failed for model={model}: {e}")
        return estimate_tokens(text)


def estimate_tokens_litellm(text: str, model: str) -> int:
    """Estimate tokens using litellm (if available)."""
    if not LITELLM_AVAILABLE or token_counter is None:
        # Fall back to OpenAI tiktoken if applicable; else heuristic.
        provider = detect_provider(model)
        if provider == "openai":
            return estimate_tokens_openai(text, model=model)
        return estimate_tokens(text)

    try:
        return int(token_counter(model=model, text=text))
    except Exception as e:  # pragma: no cover
        logger.debug(f"litellm token_counter failed for model={model}: {e}")
        provider = detect_provider(model)
        if provider == "openai":
            return estimate_tokens_openai(text, model=model)
        return estimate_tokens(text)


def estimate_tokens_by_provider(text: str, model: str) -> int:
    """Universal estimator: litellm -> tiktoken(openai) -> heuristic."""
    if LITELLM_AVAILABLE:
        return estimate_tokens_litellm(text, model)
    provider = detect_provider(model)
    if provider == "openai" and TIKTOKEN_AVAILABLE:
        return estimate_tokens_openai(text, model=model)
    return estimate_tokens(text)


def estimate_tools_tokens(tools: list[dict[str, Any]], model: str | None = None) -> int:
    """Estimate tokens consumed by OpenAI-style tool schemas."""
    if not tools:
        return 0
    try:
        import json

        blob = json.dumps(tools, ensure_ascii=False, sort_keys=True, default=str)
        if model:
            return estimate_tokens_by_provider(blob, model)
        return estimate_tokens(blob)
    except Exception:  # pragma: no cover
        # Rough fallback per tool schema.
        return len(tools) * 750


def estimate_messages_tokens(
    messages: list[dict[str, Any]],
    model: str | None = None,
    *,
    tools: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    use_cache: bool = True,
) -> int:
    """Estimate total tokens for chat messages (+ optional tools).

    Notes:
    - Adds small per-message overhead to approximate chat framing.
    - Treats non-string content as str(...) for estimation stability.
    - Uses caching for repeated content when use_cache=True.
    
    Args:
        messages: List of chat messages
        model: Model ID for provider-specific estimation
        tools: Optional tool schemas
        provider: Optional provider override
        use_cache: Whether to use cached estimates (default True)
    """
    if not isinstance(messages, list):
        return 0

    if not provider and model:
        provider = detect_provider(model)

    overhead_per_message = 6 if provider == "anthropic" else 4
    total = len(messages) * overhead_per_message + 3  # priming

    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = m.get("content", "")
        if not isinstance(role, str):
            role = str(role)
        if not isinstance(content, str):
            content = str(content)

        if model:
            if use_cache:
                # Use cached estimation for repeated content (e.g., system messages)
                total += _cached_estimate_tokens(role, model)
                total += _cached_estimate_tokens(content, model)
            else:
                total += estimate_tokens_by_provider(role, model)
                total += estimate_tokens_by_provider(content, model)
        else:
            total += estimate_tokens(role)
            total += estimate_tokens(content)

    if tools:
        total += estimate_tools_tokens(tools, model)

    # Provider-specific adjustment (best-effort)
    if provider == "anthropic":
        total += 50
        if tools:
            total += int(len(tools) * 5)

    return int(total)