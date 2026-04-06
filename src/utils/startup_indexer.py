"""Startup indexer for automatic rules indexing on application startup.

This module provides automatic indexing of .roo/rules files into mem0 when the
application starts, with configurable behavior via environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger

from src.utils.memory_service import MemoryService
from src.utils.rules_indexer import index_rules_into_mem0


def should_auto_index() -> bool:
    """Check if auto-indexing should run based on environment configuration.

    Returns:
        True if auto-indexing is enabled, False otherwise
    """
    # Check if auto-indexing is explicitly enabled
    auto_index = os.getenv("AUTO_INDEX_RULES_ON_STARTUP", "false").lower()
    return auto_index in ("true", "1", "yes", "on")


def get_index_interval_hours() -> int:
    """Get the minimum hours between re-indexing operations.

    Returns:
        Hours between re-indexing (default: 24)
    """
    try:
        return int(os.getenv("AUTO_INDEX_INTERVAL_HOURS", "24"))
    except ValueError:
        return 24


def should_skip_indexing(marker_file: Path, interval_hours: int) -> bool:
    """Check if indexing should be skipped based on last index time.

    Args:
        marker_file: Path to the marker file tracking last index time
        interval_hours: Minimum hours between indexing operations

    Returns:
        True if indexing should be skipped, False if it should run
    """
    if not marker_file.exists():
        logger.debug(f"Rules index marker not found: {marker_file}")
        return False

    try:
        import time

        last_index_time = marker_file.stat().st_mtime
        hours_since_index = (time.time() - last_index_time) / 3600

        if hours_since_index < interval_hours:
            logger.info(
                f"Skipping rules indexing: last indexed {hours_since_index:.1f}h ago "
                f"(interval: {interval_hours}h, marker={marker_file})"
            )
            return True

        logger.debug(
            f"Rules index marker is older than interval: {hours_since_index:.1f}h "
            f"(interval: {interval_hours}h, marker={marker_file})"
        )
    except Exception as e:
        logger.warning(f"Could not check last index time for marker {marker_file}: {e}")

    return False


def _default_state_dir() -> Path:
    """Directory for persisting auto-index state across restarts.

    We prefer `.roo` for locality, but systemd/container deployments may run with a
    read-only bind mount or a different user that cannot write to the repo.
    """
    # Allow explicit override
    env = os.getenv("AUTO_INDEX_STATE_DIR")
    if env:
        return Path(env).expanduser()

    # XDG cache if available, else ~/.cache
    xdg = os.getenv("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else (Path.home() / ".cache")
    return base / "abacus-proxy" / "auto_index"


def _can_write_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        test = path / ".write_test"
        test.write_text("ok", encoding="utf-8")
        test.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def update_index_marker(marker_file: Path) -> None:
    """Update the marker file to track successful indexing.

    Args:
        marker_file: Path to the marker file
    """
    try:
        marker_file.parent.mkdir(parents=True, exist_ok=True)
        marker_file.touch()
        logger.debug(f"Updated index marker: {marker_file}")
    except Exception as e:
        logger.warning(f"Could not update index marker {marker_file}: {e}")


def auto_index_rules_on_startup(memory_service: MemoryService) -> dict[str, Any]:
    """Automatically index .roo/rules files on application startup.

    This function:
    1. Checks if auto-indexing is enabled via AUTO_INDEX_RULES_ON_STARTUP
    2. Checks if enough time has passed since last indexing
    3. Indexes rules if conditions are met
    4. Updates marker file to track last index time

    Environment Variables:
        AUTO_INDEX_RULES_ON_STARTUP: Enable auto-indexing (default: false)
        AUTO_INDEX_INTERVAL_HOURS: Hours between re-indexing (default: 24)
        RULES_MEMORY_USER_ID: User ID for indexed rules (default: system)
        RULES_MEMORY_PROJECT_ID: Project ID for indexed rules (default: roo_rules)
        AUTO_INDEX_MAX_CHARS: Max chars per shard (default: 1200)
        AUTO_INDEX_OVERLAP_CHARS: Overlap between shards (default: 150)

    Args:
        memory_service: Initialized memory service instance

    Returns:
        Dict with indexing results and status
    """
    result = {
        "auto_index_enabled": False,
        "indexing_attempted": False,
        "success": False,
        "indexed": 0,
        "total": 0,
        "message": "Auto-indexing not enabled",
    }

    # Check if auto-indexing is enabled
    if not should_auto_index():
        logger.debug("Auto-indexing disabled (set AUTO_INDEX_RULES_ON_STARTUP=true to enable)")
        return result

    result["auto_index_enabled"] = True

    # Check if memory service is available
    if not memory_service.is_enabled:
        result["message"] = "Memory service not enabled"
        logger.warning("Cannot auto-index rules: memory service not enabled")
        return result

    # Check if .roo directory exists
    roo_dir = Path(".roo")
    if not roo_dir.exists():
        result["message"] = ".roo directory not found"
        logger.debug("Skipping auto-indexing: .roo directory not found")
        return result

    # IMPORTANT: `.roo` may be a symlink. Use the resolved path for marker/manifest files
    # so the timestamp persists across restarts regardless of the process CWD.
    try:
        roo_dir_resolved = roo_dir.resolve()
    except Exception:
        roo_dir_resolved = roo_dir

    # Check if we should skip based on interval
    interval_hours = get_index_interval_hours()

    # Prefer storing marker/manifest under `.roo`, but fall back to a writable state dir
    # if `.roo` is not writable in this deployment.
    state_dir = roo_dir_resolved
    if not _can_write_dir(state_dir):
        fallback = _default_state_dir()
        logger.warning(
            f"`.roo` is not writable for auto-index state; falling back to {fallback} "
            f"(roo_dir={roo_dir_resolved})"
        )
        state_dir = fallback

    marker_file = state_dir / ".last_index"

    if should_skip_indexing(marker_file, interval_hours):
        result["message"] = f"Skipped: indexed within last {interval_hours}h"
        return result

    logger.info(
        f"Rules auto-index check passed (roo_dir={roo_dir_resolved}, marker={marker_file}, interval_hours={interval_hours})"
    )

    # Perform indexing
    result["indexing_attempted"] = True
    logger.info("🔄 Auto-indexing .roo/rules files into mem0...")

    try:
        user_id = os.getenv("RULES_MEMORY_USER_ID", "system")
        project_id = os.getenv("RULES_MEMORY_PROJECT_ID", "roo_rules")
        max_chars = int(os.getenv("AUTO_INDEX_MAX_CHARS", "1200"))
        overlap_chars = int(os.getenv("AUTO_INDEX_OVERLAP_CHARS", "150"))

        # Incremental indexing: only upsert shards that are new/changed since last run.
        # This uses a local manifest file under `.roo` to avoid re-embedding unchanged shards.
        manifest_path = state_dir / ".rules_index_manifest.json"

        index_result = index_rules_into_mem0(
            memory_service=memory_service,
            user_id=user_id,
            project_id=project_id,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
            dry_run=False,
            incremental=True,
            manifest_path=manifest_path,
            prune_removed=True,
        )

        result.update(
            {
                "success": index_result.get("success", False),
                "indexed": index_result.get("indexed", 0),
                "total": index_result.get("total", 0),
                "attempted": index_result.get("attempted", None),
                "skipped_unchanged": index_result.get("skipped_unchanged", None),
                "pruned": index_result.get("pruned", None),
                "errors": index_result.get("errors", []),
            }
        )

        if result["success"]:
            result["message"] = (
                f"Successfully indexed {result['indexed']}/{result['total']} rule shards"
            )
            logger.info(f"✅ {result['message']}")
            update_index_marker(marker_file)
        else:
            result["message"] = f"Indexing failed: {index_result.get('errors', ['Unknown error'])}"
            logger.error(f"❌ {result['message']}")

    except Exception as e:
        result["message"] = f"Indexing error: {str(e)}"
        logger.error(f"❌ Auto-indexing failed: {e}")

    return result
