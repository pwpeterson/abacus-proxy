"""Index Roo `.roo` rules into Mem0 as small "rule shards".

Ported/adapted from model-proxy.

Goal: avoid sending the full `.roo` rules payload to the model. Instead:
- chunk `.roo/rules*/*.md` into small shards
- store shards in Mem0 with metadata
- later retrieve only relevant shards per request

This module is intentionally conservative:
- only indexes markdown files under `.roo/rules*`
- uses stable shard IDs derived from file path + chunk index + content hash
- supports idempotent-ish re-indexing by using deterministic point IDs when direct Qdrant upsert is available
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class RuleShard:
    shard_key: str
    source_path: str
    mode: str  # e.g. "global", "code", "architect"
    title: str
    content: str
    chunk_index: int
    total_chunks: int


def _infer_mode_from_path(path: Path) -> str:
    parent = path.parent.name
    if parent == "rules":
        return "global"
    if parent.startswith("rules-"):
        return parent.removeprefix("rules-")
    return "unknown"


def _normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_markdown_into_sections(md: str) -> list[tuple[str, str]]:
    md = _normalize_whitespace(md)
    if not md:
        return []

    lines = md.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_title = "Untitled"
    current_body: list[str] = []

    heading_re = re.compile(r"^(#{1,6})\s+(.*)\s*$")

    for line in lines:
        m = heading_re.match(line)
        if m:
            if current_body:
                sections.append((current_title, current_body))
            current_title = m.group(2).strip() or "Untitled"
            current_body = []
        else:
            current_body.append(line)

    if current_body:
        sections.append((current_title, current_body))

    return [(title, _normalize_whitespace("\n".join(body))) for title, body in sections]


def _chunk_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    text = _normalize_whitespace(text)
    if not text:
        return []

    if max_chars <= 0:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(n, start + max_chars)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= n:
            break

        start = max(0, end - max(0, overlap_chars))
        if start >= end:
            start = end

    return chunks


def _default_workspace_root() -> Path:
    # This module lives at `<root>/src/utils/rules_indexer.py`, so `parents[2]` is repo root.
    return Path(__file__).resolve().parents[2]


def _resolve_roo_dir(roo_dir: Path | None) -> Path:
    candidates: list[Path] = []
    if roo_dir is not None:
        candidates.append(roo_dir)
    candidates.extend([Path.cwd() / ".roo", _default_workspace_root() / ".roo"])

    for cand in candidates:
        if cand.is_dir():
            return cand

    # walk upward a few levels
    p = Path.cwd().resolve()
    for _ in range(8):
        cand = p / ".roo"
        if cand.is_dir():
            return cand
        if p.parent == p:
            break
        p = p.parent

    return Path(".roo")


def iter_rule_shards(
    roo_dir: Path | None = None,
    *,
    max_chars: int = 1200,
    overlap_chars: int = 150,
) -> Iterable[RuleShard]:
    """Yield RuleShard objects from `.roo/rules*/*.md`."""
    roo_dir_resolved = _resolve_roo_dir(roo_dir)
    rules_dirs = [p for p in roo_dir_resolved.glob("rules*") if p.is_dir()]

    for rules_dir in sorted(rules_dirs):
        for md_path in sorted(rules_dir.rglob("*.md")):
            try:
                raw = md_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Skipping unreadable rules file {md_path}: {e}")
                continue

            mode = _infer_mode_from_path(md_path)
            sections = _split_markdown_into_sections(raw)
            if not sections:
                continue

            for section_title, section_body in sections:
                combined = f"{section_title}\n\n{section_body}".strip()
                chunks = _chunk_text(combined, max_chars=max_chars, overlap_chars=overlap_chars)
                total = len(chunks)

                for idx, chunk in enumerate(chunks):
                    h = hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:16]
                    shard_key = f"roo_rules::{md_path.as_posix()}::{section_title}::{idx}::{h}"
                    yield RuleShard(
                        shard_key=shard_key,
                        source_path=md_path.as_posix(),
                        mode=mode,
                        title=section_title,
                        content=chunk,
                        chunk_index=idx,
                        total_chunks=total,
                    )


def index_rules_into_mem0(
    memory_service: Any,
    *,
    user_id: str = "system",
    project_id: str = "roo_rules",
    max_chars: int = 1200,
    overlap_chars: int = 150,
    dry_run: bool = False,
    incremental: bool = False,
    manifest_path: Path | None = None,
    prune_removed: bool = False,
) -> dict[str, Any]:
    """Index `.roo` rules into Mem0 using MemoryService.

    When `incremental=True`, a local manifest file stores shard_keys so we only upsert changed
    shards on subsequent runs.
    """
    if not getattr(memory_service, "is_enabled", False):
        return {"success": False, "message": "Memory service not enabled", "indexed": 0}

    shards = list(iter_rule_shards(None, max_chars=max_chars, overlap_chars=overlap_chars))
    logger.info(
        f"Prepared {len(shards)} rule shards for indexing (cwd={Path.cwd()}, roo_dir={_resolve_roo_dir(None)})"
    )

    if dry_run:
        return {"success": True, "message": "Dry run", "indexed": len(shards), "dry_run": True}

    has_raw_upsert = hasattr(memory_service, "upsert_raw_text")

    manifest: dict[str, Any] = {}
    prev_keys: set[str] = set()
    if incremental:
        if manifest_path is None:
            manifest_path = _resolve_roo_dir(None) / ".rules_index_manifest.json"

        try:
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    prev = manifest.get("shard_keys")
                    if isinstance(prev, list):
                        prev_keys = {str(x) for x in prev}
        except Exception as e:
            logger.warning(f"Could not read rules index manifest {manifest_path}: {e}")
            manifest = {}
            prev_keys = set()

    current_keys = {s.shard_key for s in shards}
    to_upsert = shards
    skipped_unchanged = 0
    if incremental and prev_keys:
        to_upsert = [s for s in shards if s.shard_key not in prev_keys]
        skipped_unchanged = len(shards) - len(to_upsert)

    if incremental:
        logger.info(
            f"Incremental rules indexing: upserting {len(to_upsert)}/{len(shards)} "
            f"(skipped_unchanged={skipped_unchanged})"
        )

    indexed = 0
    errors: list[str] = []

    for shard in to_upsert:
        try:
            content = (
                "RULE_SHARD\n"
                f"mode={shard.mode}\n"
                f"source={shard.source_path}\n"
                f"title={shard.title}\n"
                f"chunk={shard.chunk_index + 1}/{shard.total_chunks}\n"
                f"shard_key={shard.shard_key}\n\n"
                f"{shard.content}"
            )

            metadata = {
                "type": "roo_rule_shard",
                "shard_key": shard.shard_key,
                "mode": shard.mode,
                "source_path": shard.source_path,
                "title": shard.title,
                "chunk_index": shard.chunk_index,
                "total_chunks": shard.total_chunks,
                "project_id": project_id,
            }

            if has_raw_upsert:
                import uuid

                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, shard.shard_key))
                res = memory_service.upsert_raw_text(  # type: ignore[attr-defined]
                    text=content,
                    user_id=user_id,
                    metadata=metadata,
                    point_id=point_id,
                )
            else:
                res = memory_service.add_memory(
                    messages=[{"role": "assistant", "content": content}],
                    user_id=user_id,
                    project_id=project_id,
                    metadata=metadata,
                )

            if getattr(res, "success", False):
                indexed += 1
            else:
                errors.append(str(getattr(res, "message", "unknown_error")))
        except Exception as e:
            errors.append(str(e))

    pruned = 0
    if incremental and prune_removed and has_raw_upsert:
        try:
            mem = getattr(memory_service, "_memory", None)
            vector_store = getattr(mem, "vector_store", None) if mem else None
            qdrant_client = getattr(vector_store, "client", None) if vector_store else None
            collection_name = getattr(vector_store, "collection_name", None) if vector_store else None

            if qdrant_client and collection_name:
                removed_keys = prev_keys - current_keys
                if removed_keys:
                    import uuid

                    removed_ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, k)) for k in sorted(removed_keys)]
                    qdrant_client.delete(collection_name=collection_name, points_selector=removed_ids)
                    pruned = len(removed_ids)
        except Exception as e:
            logger.warning(f"Failed to prune removed rule shards: {e}")

    if incremental:
        try:
            assert manifest_path is not None
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_out = {
                "version": 1,
                "project_id": project_id,
                "user_id": user_id,
                "max_chars": max_chars,
                "overlap_chars": overlap_chars,
                "shard_keys": sorted(current_keys),
            }
            manifest_path.write_text(
                json.dumps(manifest_out, indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"Could not write rules index manifest {manifest_path}: {e}")

    return {
        "success": len(errors) == 0,
        "indexed": indexed,
        "total": len(shards),
        "attempted": len(to_upsert),
        "skipped_unchanged": skipped_unchanged,
        "pruned": pruned,
        "errors": errors[:10],
    }