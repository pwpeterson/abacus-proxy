"""Wire-level request/response logging helpers.

Goal: safely log (debug) the payload flow between:
- Roo Code client -> abacus-proxy (/v1/chat/completions)
- abacus-proxy -> upstream LLM
- upstream LLM -> abacus-proxy -> Roo Code

Design constraints:
- Never log secrets by default (best-effort redaction).
- Keep logs bounded (truncate large JSON blobs).
- Provide a compact summary view even when full payload logging is disabled.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable


DEFAULT_REDACT_KEYS: set[str] = {
    # Common auth/secret keys
    "authorization",
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "client_secret",
    # HTTP-ish
    "cookie",
    "cookies",
    "set-cookie",
    # Internal config patterns
    "supabase_service_role_key",
    "service_role_key",
    "supabase_key",
    "openai_api_key",
    "qdrant_api_key",
}


def _truncate_str(s: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


def redact_for_logging(
    value: Any,
    *,
    redact_keys: set[str] | None = None,
    max_depth: int = 8,
    max_list_items: int = 50,
) -> Any:
    """Best-effort redaction of nested structures.

    - Dict keys matching `redact_keys` (case-insensitive) are replaced with "<redacted>".
    - Depth is bounded to avoid pathological payloads.
    - Lists are trimmed to max_list_items.
    """
    if max_depth <= 0:
        return "<max_depth>"

    rk = {k.lower() for k in (redact_keys or DEFAULT_REDACT_KEYS)}

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            ks = str(k)
            if ks.lower() in rk:
                out[ks] = "<redacted>"
            else:
                out[ks] = redact_for_logging(
                    v,
                    redact_keys=rk,
                    max_depth=max_depth - 1,
                    max_list_items=max_list_items,
                )
        return out

    if isinstance(value, list):
        trimmed = value[: max(0, int(max_list_items))]
        return [
            redact_for_logging(
                v,
                redact_keys=rk,
                max_depth=max_depth - 1,
                max_list_items=max_list_items,
            )
            for v in trimmed
        ]

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    # Fallback for non-JSON primitives
    return str(value)


def dumps_json_truncated(
    value: Any,
    *,
    max_chars: int = 4000,
    redact_keys: set[str] | None = None,
    max_depth: int = 8,
    max_list_items: int = 50,
    indent: int | None = None,
) -> str:
    """JSON dump (redacted) with a strict max char budget."""
    redacted = redact_for_logging(
        value,
        redact_keys=redact_keys,
        max_depth=max_depth,
        max_list_items=max_list_items,
    )
    try:
        s = json.dumps(redacted, ensure_ascii=False, default=str, indent=indent)
    except Exception:
        s = str(redacted)
    return _truncate_str(s, int(max_chars))


def _safe_str_len(s: Any) -> int:
    try:
        return len(str(s))
    except Exception:
        return 0


def summarize_messages(messages: Any) -> dict[str, Any]:
    """Return a compact summary of OpenAI-style messages list."""
    if not isinstance(messages, list):
        return {"ok": False, "reason": "messages_not_list", "type": type(messages).__name__}

    roles: list[str] = []
    total_chars = 0
    tool_calls_count = 0

    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if isinstance(role, str) and role:
            roles.append(role)

        content = m.get("content")

        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            # multi-modal-ish content blocks; count text blocks
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total_chars += _safe_str_len(part.get("text", ""))
                    else:
                        total_chars += _safe_str_len(part)
                else:
                    total_chars += _safe_str_len(part)
        else:
            total_chars += _safe_str_len(content)

        tc = m.get("tool_calls")
        if isinstance(tc, list):
            tool_calls_count += len(tc)

    role_counts = dict(Counter(roles))
    return {
        "ok": True,
        "count": len(messages),
        "roles": role_counts,
        "total_content_chars_est": int(total_chars),
        "tool_calls_count": int(tool_calls_count),
    }


def _iter_tool_names(tools: Any) -> Iterable[str]:
    if not isinstance(tools, list):
        return []
    names: list[str] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        if isinstance(fn, dict):
            n = fn.get("name")
            if isinstance(n, str) and n.strip():
                names.append(n.strip())
                continue
        # Some callers may pass MCP tool list format; attempt name
        n2 = t.get("name")
        if isinstance(n2, str) and n2.strip():
            names.append(n2.strip())
    return names


def summarize_tools(tools: Any) -> dict[str, Any]:
    if tools is None:
        return {"present": False}

    if not isinstance(tools, list):
        return {"present": True, "ok": False, "reason": "tools_not_list", "type": type(tools).__name__}

    names = list(_iter_tool_names(tools))
    return {
        "present": True,
        "ok": True,
        "count": len(tools),
        "tool_names": names[:25],
        "tool_names_truncated": len(names) > 25,
    }


def summarize_chat_payload(payload: Any) -> dict[str, Any]:
    """Summarize an OpenAI-compatible chat/completions request payload."""
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "payload_not_dict", "type": type(payload).__name__}

    messages_summary = summarize_messages(payload.get("messages"))
    tools_summary = summarize_tools(payload.get("tools"))

    model = payload.get("model")
    stream = payload.get("stream")
    temperature = payload.get("temperature")
    max_tokens = payload.get("max_tokens")

    # Keep stable ordering-ish for log readability
    top_level_keys = sorted([str(k) for k in payload.keys()])[:50]

    return {
        "ok": True,
        "model": model if isinstance(model, str) else None,
        "stream": bool(stream) if isinstance(stream, bool) else None,
        "temperature": float(temperature) if isinstance(temperature, (int, float)) else None,
        "max_tokens": int(max_tokens) if isinstance(max_tokens, int) else None,
        "messages": messages_summary,
        "tools": tools_summary,
        "top_level_keys": top_level_keys,
    }