"""Codebase JIT context injector (semantic code retrieval via Mem0).

Ported/adapted from model-proxy.

Prerequisites:
- Code chunks must have been indexed into Mem0 with metadata.type == "code_chunk".
- Requests should include a consistent `user_id` and `project_id` where possible.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Protocol

from loguru import logger

from src.models.memory import SemanticCodeSearchRequest
from src.utils.token_estimator import estimate_tokens_by_provider


class _SemanticSearchCapable(Protocol):
    @property
    def is_enabled(self) -> bool: ...

    def semantic_code_search(self, request: SemanticCodeSearchRequest) -> Any: ...


def _is_trivial_query(messages: list[dict[str, Any]]) -> bool:
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
        ".py",
        ".ts",
        ".js",
        ".svelte",
        "api",
        "endpoint",
        "fastapi",
        "sql",
        "docker",
        "kubernetes",
    ]
    lower = text.lower()
    if any(m in lower for m in technical_markers):
        return False

    if re.search(r"[/\\][\w\-. /\\]+\.\w{1,6}\b", text):
        return False

    return len(text) <= 80


def _format_code_chunk(chunk: Any) -> str:
    file_path = getattr(chunk, "file_path", "") or ""
    start_line = getattr(chunk, "start_line", None)
    end_line = getattr(chunk, "end_line", None)
    code = getattr(chunk, "code", "") or ""
    language = getattr(chunk, "language", "") or ""

    loc = file_path
    if start_line and end_line:
        loc = f"{file_path}:{start_line}-{end_line}"

    code = str(code).strip()
    return f"Location: {loc}\n\n```{language}\n{code}\n```".strip()


def _build_code_context_block(chunks_formatted: list[str]) -> str:
    lines = [
        "## Code Context (JIT retrieved)",
        "",
        "The following code snippets were retrieved via semantic search as likely relevant context.",
        "Use them to locate exact files/lines before applying changes.",
        "",
    ]
    for i, body in enumerate(chunks_formatted, 1):
        lines.append(f"### Snippet {i}")
        lines.append(body)
        lines.append("")
    return "\n".join(lines).strip()


async def inject_code_jit_context(
    *,
    messages: list[dict[str, Any]],
    memory_service: _SemanticSearchCapable,
    model_id: str,
    enable: bool,
    user_id: str | None,
    project_id: str | None,
    max_chunks: int,
    max_tokens: int,
    trivial_bypass: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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

    if not getattr(memory_service, "is_enabled", False):
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

    limit = max(1, max_chunks * 3)
    req = SemanticCodeSearchRequest(query=query[:500], project_id=project_id, user_id=user_id, limit=limit)

    try:
        res = await asyncio.to_thread(memory_service.semantic_code_search, req)
    except Exception as e:
        stats["skipped_reason"] = f"search_error:{e}"
        return messages, stats

    results = getattr(res, "results", None) or []
    if not results:
        stats["skipped_reason"] = "no_results"
        return messages, stats

    selected: list[str] = []
    used = 0
    for chunk in results:
        if len(selected) >= max_chunks:
            break
        formatted = _format_code_chunk(chunk)
        tok = estimate_tokens_by_provider(formatted, model_id)
        if used + tok > max_tokens:
            continue
        selected.append(formatted)
        used += tok

    if not selected:
        stats["skipped_reason"] = "budget_exhausted"
        return messages, stats

    block = _build_code_context_block(selected)
    block_tokens = estimate_tokens_by_provider(block, model_id)

    new_messages = list(messages)
    insert_at = 0
    if new_messages and new_messages[0].get("role") == "system":
        insert_at = 1
    new_messages.insert(insert_at, {"role": "system", "content": block})

    stats.update({"injected": True, "retrieved": len(selected), "tokens_used": block_tokens})

    logger.debug(
        f"Injected {len(selected)} code chunks (~{block_tokens} tokens) user_id={user_id}, project_id={project_id}"
    )
    return new_messages, stats