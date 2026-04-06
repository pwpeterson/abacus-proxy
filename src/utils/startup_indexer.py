"""Startup indexer for automatic `.roo` rules indexing into Mem0.

Adapted from model-proxy.

This module provides automatic indexing of `.roo/rules*/*.md` into Mem0 on
application startup, with configurable behavior via environment/AppConfig.

Key design points:
- Idempotent-ish indexing via deterministic shard keys + point IDs (when Qdrant direct upsert available)
- Incremental manifest to avoid re-embedding unchanged shards
- Marker file to avoid re-indexing too frequently
- Fallback state directory when `.roo` is not writable in container/systemd deployments
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService
from src.utils.rules_indexer import index_rules_into_mem0


def _can_write_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test = path / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _default_state_dir() -> Path:
    """Directory for persisting auto-index state across restarts."""
    env = os.getenv("AUTO_INDEX_STATE_DIR")
    if env:
        return Path(env).expanduser()

    xdg = os.getenv("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else (Path.home() / ".cache")
    return base / "abacus-proxy" / "auto_index"


def _should_skip_indexing(marker_file: Path, interval_hours: int) -> bool:
    if not marker_file.exists():
        return False

    try:
        import time

        last_index_time = marker_file.stat().st_mtime
        hours_since = (time.time() - last_index_time) / 3600
        if hours_since < interval_hours:
            logger.info(
                f"Skipping rules indexing: last indexed {hours_since:.1f}h ago "
                f"(interval={interval_hours}h, marker={marker_file})"
            )
            return True
    except Exception as e:
        logger.warning(f"Could not check marker file age ({marker_file}): {e}")

    return False


def _touch(marker_file: Path) -> None:
    try:
        marker_file.parent.mkdir(parents=True, exist_ok=True)
        marker_file.touch()
    except Exception as e:
        logger.warning(f"Could not update marker file {marker_file}: {e}")


def auto_index_rules_on_startup(
    *,
    memory_service: MemoryService,
    enabled: bool,
    interval_hours: int,
    state_dir_override: str | None,
    user_id: str,
    project_id: str,
    max_chars: int,
    overlap_chars: int,
) -> dict[str, Any]:
    """Run incremental `.roo` rules indexing if configured to do so."""
    result: dict[str, Any] = {
        "auto_index_enabled": bool(enabled),
        "indexing_attempted": False,
        "success": False,
        "indexed": 0,
        "total": 0,
        "message": "Auto-indexing not enabled",
    }

    if not enabled:
        return result

    if not memory_service.is_enabled:
        result["message"] = "Memory service not enabled"
        logger.warning("Cannot auto-index rules: memory service not enabled")
        return result

    roo_dir = Path(".roo")
    if not roo_dir.exists():
        result["message"] = ".roo directory not found"
        logger.debug("Skipping auto-indexing: .roo directory not found")
        return result

    try:
        roo_dir_resolved = roo_dir.resolve()
    except Exception:
        roo_dir_resolved = roo_dir

    state_dir = Path(state_dir_override).expanduser() if state_dir_override else roo_dir_resolved
    if not _can_write_dir(state_dir):
        fallback = _default_state_dir()
        logger.warning(
            f"`.roo` not writable for auto-index state; falling back to {fallback} (roo_dir={roo_dir_resolved})"
        )
        state_dir = fallback
        state_dir.mkdir(parents=True, exist_ok=True)

    marker_file = state_dir / ".last_index"
    if _should_skip_indexing(marker_file, int(interval_hours)):
        result["message"] = f"Skipped: indexed within last {int(interval_hours)}h"
        return result

    result["indexing_attempted"] = True
    logger.info(
        f"🔄 Auto-indexing .roo/rules into Mem0 (state_dir={state_dir}, roo_dir={roo_dir_resolved})"
    )

    manifest_path = state_dir / ".rules_index_manifest.json"

    try:
        index_result = index_rules_into_mem0(
            memory_service=memory_service,
            user_id=user_id,
            project_id=project_id,
            max_chars=int(max_chars),
            overlap_chars=int(overlap_chars),
            dry_run=False,
            incremental=True,
            manifest_path=manifest_path,
            prune_removed=True,
        )

        result.update(
            {
                "success": bool(index_result.get("success", False)),
                "indexed": int(index_result.get("indexed", 0)),
                "total": int(index_result.get("total", 0)),
                "attempted": index_result.get("attempted"),
                "skipped_unchanged": index_result.get("skipped_unchanged"),
                "pruned": index_result.get("pruned"),
                "errors": index_result.get("errors", []),
            }
        )

        if result["success"]:
            result["message"] = f"Indexed {result['indexed']}/{result['total']} rule shards"
            logger.info(f"✅ {result['message']}")
            _touch(marker_file)
        else:
            result["message"] = f"Indexing failed: {result.get('errors', [])[:1]}"
            logger.error(f"❌ {result['message']}")

        return result

    except Exception as e:
        result["message"] = f"Indexing error: {e}"
        logger.error(f"❌ Auto-indexing failed: {e}")
        return result