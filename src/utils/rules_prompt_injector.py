"""Mem0-backed `.roo` rules retrieval and compact injection.

This module retrieves a small number of relevant "roo_rule_shard" memories and injects them
into the request as a compact system message block, with a strict token budget.

Important: This does NOT remove Roo Code's original system prompt. It only adds a compact
"Rules (retrieved)" block. The full prompt bloat problem is ultimately solved by configuring
Roo Code to send a minimal system prompt, but this module enables a migration path where
rules can be externalized into Mem0 and selectively re-injected.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService
from src.utils.token_estimator import estimate_tokens_tiktoken


def _is_trivial_query(messages: list[dict[str, Any]]) -> bool:
    """Heuristic: treat short, non-technical user queries as trivial."""
    # Find last user message
    last_user = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user = msg.get("content", "")
            break

    if not isinstance(last_user, str):
        return False

    text = last_user.strip()
    if len(text) == 0:
        return True

    # Very short questions like "2+2" or "hi"
    if len(text) <= 20:
        return True

    # If it looks like code/tools/debugging, not trivial
    technical_markers = [
        "traceback",
        "exception",
        "stack",
        "error",
        "bug",
        "refactor",
        "implement",
        "function",
        "class",
        "import ",
        "def ",
        "const ",
        "let ",
        "SELECT ",
        "INSERT ",
        "docker",
        "kubernetes",
        "mcp",
        ".py",
        ".ts",
        ".js",
        ".svelte",
        "http",
        "api",
    ]
    lower = text.lower()
    if any(m in lower for m in technical_markers):
        return False

    # If it contains a file path-ish token, not trivial
    if re.search(r"[/\\][\w\-. /\\]+\.\w{1,6}\b", text):
        return False

    # Otherwise treat as trivial if it's short-ish
    return len(text) <= 80


def _build_rules_block(shards: list[str]) -> str:
    lines = [
        "## Rules (retrieved from Mem0)",
        "",
        "Apply the following rules. If any conflict, prefer the most specific and most recent.",
        "",
    ]
    for i, shard in enumerate(shards, 1):
        lines.append(f"{i}. {shard.strip()}")
        lines.append("")
    return "\n".join(lines).strip()


def _extract_shard_body(memory_text: str) -> str:
    """Strip the RULE_SHARD header if present."""
    if not isinstance(memory_text, str):
        return ""

    # If it contains a blank line after header, keep only body after first blank line.
    parts = memory_text.split("\n\n", 1)
    if len(parts) == 2 and parts[0].startswith("RULE_SHARD"):
        return parts[1].strip()
    return memory_text.strip()


async def inject_retrieved_rules(
    *,
    messages: list[dict[str, Any]],
    memory_service: MemoryService,
    model_id: str,
    enable: bool,
    user_id: str,
    project_id: str,
    max_shards: int,
    max_tokens: int,
    trivial_bypass: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retrieve relevant rule shards and inject as a system message.

    Returns (new_messages, stats).
    """
    stats: dict[str, Any] = {
        "enabled": enable,
        "injected": False,
        "retrieved": 0,
        "tokens_used": 0,
        "skipped_reason": None,
    }

    if not enable:
        stats["skipped_reason"] = "disabled"
        return messages, stats

    if not memory_service.is_enabled:
        stats["skipped_reason"] = "memory_disabled"
        return messages, stats

    if trivial_bypass and _is_trivial_query(messages):
        stats["skipped_reason"] = "trivial_bypass"
        return messages, stats

    # Build query from last user message (best-effort)
    query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            query = msg["content"].strip()
            break

    if not query:
        stats["skipped_reason"] = "no_user_query"
        return messages, stats

    # Search for relevant rule shards. We rely on the shard header + metadata to bias results.
    # Note: MemoryService.search_memory supports project_id filtering.
    search_query = f"roo rules {query}"
    # Offload blocking mem0/Qdrant search to thread pool to avoid stalling the event loop
    res = await asyncio.to_thread(
        memory_service.search_memory,
        query=search_query[:500],
        user_id=user_id,
        project_id=project_id,
        limit=max(1, max_shards * 3),
    )

    if not res.results:
        stats["skipped_reason"] = "no_results"
        return messages, stats

    # Filter to only rule shards
    candidates: list[str] = []
    for item in res.results:
        md = item.metadata or {}
        if md.get("type") != "roo_rule_shard":
            continue
        body = _extract_shard_body(item.memory)
        if body:
            candidates.append(body)

    if not candidates:
        stats["skipped_reason"] = "no_rule_shards"
        return messages, stats

    # Enforce token budget
    selected: list[str] = []
    used = 0
    for shard in candidates:
        if len(selected) >= max_shards:
            break
        shard_tokens = estimate_tokens_tiktoken(shard, model=model_id, fallback=True)
        if used + shard_tokens > max_tokens:
            continue
        selected.append(shard)
        used += shard_tokens

    if not selected:
        stats["skipped_reason"] = "budget_exhausted"
        return messages, stats

    rules_block = _build_rules_block(selected)
    rules_tokens = estimate_tokens_tiktoken(rules_block, model=model_id, fallback=True)

    # Inject as a system message at the front, but after any existing system message if present.
    new_messages = list(messages)

    insert_at = 0
    if new_messages and new_messages[0].get("role") == "system":
        insert_at = 1

    new_messages.insert(insert_at, {"role": "system", "content": rules_block})

    stats.update(
        {
            "injected": True,
            "retrieved": len(selected),
            "tokens_used": rules_tokens,
        }
    )

    logger.debug(
        f"Injected {len(selected)} rule shards (~{rules_tokens} tokens) for user_id={user_id}, project_id={project_id}"
    )

    return new_messages, stats
