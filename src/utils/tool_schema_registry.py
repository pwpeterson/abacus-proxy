"""Tool schema registry loader and selection logic.

This module supports *server-side tool schema injection* when a client omits `tools`.

Key requirements:
- Conservative injection: only inject when there is positive evidence of tool intent.
- Use registry JSON (OpenAI tool schema objects) instead of hardcoded schemas.
- Avoid injecting control/meta tools (e.g. attempt_completion) unless explicitly needed by other flows.
- Prefer token-budgeted selection for injected tool schemas (tool schemas vary widely in size).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from src.utils.token_estimator import estimate_tokens_openai
from src.utils.tool_filter import ToolFilter

# These tools are "control/meta" tools that should not be injected merely because the client
# omitted `tools`. They can be injected explicitly by other flows (e.g. single-tool sanitizer).
_DEFAULT_INJECTION_DENYLIST: set[str] = {
    "attempt_completion",
    "ask_followup_question",
}


def _get_openai_tool_name(tool: dict[str, Any]) -> str | None:
    """Extract tool function name from an OpenAI Chat Completions tool schema."""
    if not isinstance(tool, dict):
        return None
    if tool.get("type") != "function":
        return None
    fn = tool.get("function")
    if not isinstance(fn, dict):
        return None
    name = fn.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


@dataclass(frozen=True)
class ToolSchemaRegistry:
    """In-memory representation of tool_schema_registry.json."""

    version: int
    tools: list[dict[str, Any]]

    # Optional metadata keys present in the on-disk registry.
    description: str | None = None
    updated_at: str | None = None
    sources: dict[str, Any] | None = None
    discovery_summary: dict[str, Any] | None = None


def load_tool_schema_registry(path: str | Path) -> ToolSchemaRegistry:
    """Load an OpenAI tool schema registry JSON file.

    Args:
        path: Path to the JSON file (relative or absolute).

    Returns:
        ToolSchemaRegistry

    Raises:
        FileNotFoundError: If registry does not exist.
        ValueError: If JSON content is invalid/unexpected.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Tool schema registry not found: {p}")

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # pragma: no cover (defensive)
        raise ValueError(f"Failed to parse registry JSON at {p}: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Invalid registry JSON at {p}: expected object at top level")

    raw_tools = data.get("tools", [])
    if not isinstance(raw_tools, list):
        raise ValueError(f"Invalid registry JSON at {p}: 'tools' must be a list")

    tools: list[dict[str, Any]] = []
    for t in raw_tools:
        if isinstance(t, dict) and _get_openai_tool_name(t):
            tools.append(t)

    version = data.get("version", 1)
    try:
        version_int = int(version)
    except Exception as e:  # pragma: no cover (defensive)
        raise ValueError(f"Invalid registry JSON at {p}: 'version' must be int-like") from e

    return ToolSchemaRegistry(
        version=version_int,
        tools=tools,
        description=data.get("description") if isinstance(data.get("description"), str) else None,
        updated_at=data.get("updated_at") if isinstance(data.get("updated_at"), str) else None,
        sources=data.get("sources") if isinstance(data.get("sources"), dict) else None,
        discovery_summary=(
            data.get("discovery_summary")
            if isinstance(data.get("discovery_summary"), dict)
            else None
        ),
    )


def _estimate_tool_schema_tokens(*, tool: dict[str, Any], model: str = "gpt-4") -> int:
    """Estimate the token footprint of a single OpenAI tool schema dict (best-effort).

    Tool schemas can vary significantly in size. Using a token budget yields more stable behavior
    than selecting solely by tool count.
    """
    try:
        # Use a canonical JSON form for stable token estimation.
        payload = json.dumps(tool, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        payload = str(tool)

    return estimate_tokens_openai(payload, model=model)


def _apply_tool_token_budget(
    *,
    tools: list[dict[str, Any]],
    max_tool_tokens: int,
    token_model: str,
) -> list[dict[str, Any]]:
    """Apply a token budget to a pre-ranked tool list.

    - Preserves input order (assumed already ranked by relevance).
    - Returns at least one tool if `tools` is non-empty and `max_tool_tokens` > 0.
    """
    if max_tool_tokens <= 0:
        return []

    selected: list[dict[str, Any]] = []
    used = 0

    for t in tools:
        t_tokens = _estimate_tool_schema_tokens(tool=t, model=token_model)
        if selected and used + t_tokens > max_tool_tokens:
            break
        selected.append(t)
        used += t_tokens

    # Guarantee at least one tool if possible (even if a single schema exceeds the budget).
    if not selected and tools:
        selected = [tools[0]]

    return selected


def select_registry_tools_for_injection(
    *,
    registry: ToolSchemaRegistry,
    messages: list[dict[str, Any]],
    max_tools: int = 20,
    max_tool_tokens: int | None = None,
    token_model: str = "gpt-4",
    enable_filtering: bool = True,
    denylist: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Select a subset of registry tools to inject into an outbound request.

    This is intentionally conservative: if no tool intent is detected, it returns [].

    Selection strategy:
    - Stage 1 (intent gating + ranking): ToolFilter.filter_tools_for_injection()
    - Stage 2 (optional): apply a token budget to the ranked list (more stable than raw count)

    Args:
        registry: Loaded registry object
        messages: Conversation messages
        max_tools: Max tools to inject (hard cap)
        max_tool_tokens: Optional token budget for injected tool schemas (recommended)
        token_model: Model name used for token estimation (tiktoken best-effort)
        enable_filtering: If True, use ToolFilter.filter_tools_for_injection (recommended)
        denylist: Optional additional tool name denylist

    Returns:
        List of OpenAI tool schema dicts (each with {"type":"function","function":{...}})
    """
    if max_tools <= 0:
        return []

    if not messages:
        return []

    effective_denylist = set(_DEFAULT_INJECTION_DENYLIST)
    if denylist:
        effective_denylist |= set(denylist)

    # Filter out disallowed tools up front (control tools, etc).
    candidate_tools: list[dict[str, Any]] = []
    for t in registry.tools:
        if not isinstance(t, dict):
            continue
        name = _get_openai_tool_name(t)
        if not name:
            continue
        if name in effective_denylist:
            continue
        candidate_tools.append(t)

    if not candidate_tools:
        return []

    if enable_filtering:
        filtered, stats = ToolFilter.filter_tools_for_injection(
            tools=candidate_tools,
            messages=messages,
            max_tools=max_tools,
            enable_filtering=True,
        )
        if stats.get("filtered_count", 0) == 0:
            return []

        if isinstance(max_tool_tokens, int) and max_tool_tokens > 0:
            return _apply_tool_token_budget(
                tools=filtered,
                max_tool_tokens=max_tool_tokens,
                token_model=token_model,
            )

        return filtered

    # If filtering is disabled, still require positive evidence of tool intent.
    # The ToolFilter intent detector is the supported path; without it, be safe by default.
    logger.debug("Tool schema injection: enable_filtering is false; refusing to inject tools")
    return []


def get_registry_tool_schema(
    *,
    registry: ToolSchemaRegistry,
    tool_name: str,
) -> dict[str, Any] | None:
    """Return the OpenAI tool schema dict for a given tool name, if present."""
    name = (tool_name or "").strip()
    if not name:
        return None
    for t in registry.tools:
        if _get_openai_tool_name(t) == name:
            return t
    return None


__all__ = [
    "ToolSchemaRegistry",
    "load_tool_schema_registry",
    "select_registry_tools_for_injection",
    "get_registry_tool_schema",
]
