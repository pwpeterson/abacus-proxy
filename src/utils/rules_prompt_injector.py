"""Mem0-backed `.roo` rules retrieval and compact injection.

Ported/adapted from model-proxy.

This retrieves a small number of relevant "roo_rule_shard" memories and injects them
into the request as a compact system message block, with a strict token budget.

Important:
- This does NOT remove an existing large system prompt. It's a migration helper.
- For best results, configure the client to send a minimal system prompt and rely
  on Mem0-retrieved shards + local rules.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService
from src.utils.token_estimator import estimate_tokens_by_provider


def _is_trivial_query(messages: list[dict[str, Any]]) -> bool:
    """Heuristic: treat short, non-technical user queries as trivial."""
    last_user: Any = None
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            last_user = msg.get("content", "")
            break

    if not isinstance(last_user, str):
        return False

    text = last_user.strip()
    if len(text) == 0:
        return True

    if len(text) <= 20:
        return True

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
        "select ",
        "insert ",
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

    if re.search(r"[/\\][\w\-. /\\]+\.\w{1,6}\b", text):
        return False

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

    query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            query = msg["content"].strip()
            break

    if not query:
        stats["skipped_reason"] = "no_user_query"
        return messages, stats

    # Bias search toward rule shards
    search_query = f"roo rules {query}"

    try:
        res = await asyncio.to_thread(
            memory_service.search_memory,
            query=search_query[:500],
            user_id=user_id,
            project_id=project_id,
            limit=max(1, max_shards * 3),
        )
    except Exception as e:
        stats["skipped_reason"] = f"search_error:{e}"
        return messages, stats

    if not getattr(res, "results", None):
        stats["skipped_reason"] = "no_results"
        return messages, stats

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

    selected: list[str] = []
    used = 0
    for shard in candidates:
        if len(selected) >= max_shards:
            break
        shard_tokens = estimate_tokens_by_provider(shard, model_id)
        if used + shard_tokens > max_tokens:
            continue
        selected.append(shard)
        used += shard_tokens

    if not selected:
        stats["skipped_reason"] = "budget_exhausted"
        return messages, stats

    rules_block = _build_rules_block(selected)
    rules_tokens = estimate_tokens_by_provider(rules_block, model_id)

    new_messages = list(messages)
    insert_at = 0
    if new_messages and new_messages[0].get("role") == "system":
        insert_at = 1
    new_messages.insert(insert_at, {"role": "system", "content": rules_block})

    stats.update({"injected": True, "retrieved": len(selected), "tokens_used": rules_tokens})

    logger.debug(
        f"Injected {len(selected)} rule shards (~{rules_tokens} tokens) "
        f"user_id={user_id}, project_id={project_id}"
    )

    return new_messages, stats