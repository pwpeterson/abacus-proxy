from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, cast

import pytest

import src.utils.context_manager as context_manager_mod
import src.utils.startup_indexer as startup_indexer_mod
from src.utils.context_manager import ContextManager
from src.utils.prompt_guard import guard_chat_messages
from src.utils.rules_prompt_injector import inject_retrieved_rules
from src.utils.code_jit_prompt_injector import inject_code_jit_context


@dataclass
class _AddMemoryResult:
    success: bool = True
    message: str = "ok"


class FakeMemoryService:
    """Minimal fake for ContextManager and injectors (no mem0 dependency)."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        relevant_context: str = "MEMCTX",
        search_results: list[Any] | None = None,
        code_results: list[Any] | None = None,
    ) -> None:
        self.is_enabled = enabled
        self._relevant_context = relevant_context
        self._search_results = search_results or []
        self._code_results = code_results or []
        self.add_memory_calls: list[dict[str, Any]] = []
        self.search_memory_calls: list[dict[str, Any]] = []
        self.semantic_code_search_calls: list[dict[str, Any]] = []

    def add_memory(
        self,
        *,
        messages: list[dict[str, Any]],
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> _AddMemoryResult:
        self.add_memory_calls.append(
            {
                "messages": messages,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "project_id": project_id,
                "metadata": metadata or {},
            }
        )
        return _AddMemoryResult(success=True, message="stored")

    def get_relevant_context(
        self,
        *,
        query: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        limit: int = 3,
    ) -> str:
        _ = (query, user_id, agent_id, project_id, limit)
        return self._relevant_context

    def search_memory(
        self,
        *,
        query: str,
        user_id: str | None = None,
        project_id: str | None = None,
        limit: int = 10,
    ) -> Any:
        self.search_memory_calls.append(
            {"query": query, "user_id": user_id, "project_id": project_id, "limit": limit}
        )
        return SimpleNamespace(results=self._search_results)

    def semantic_code_search(self, request: Any) -> Any:
        # request is a SemanticCodeSearchRequest but we avoid importing it here.
        self.semantic_code_search_calls.append({"request": request})
        return SimpleNamespace(results=self._code_results)


def _seq_returns(*vals: int) -> Callable[..., int]:
    it = iter(vals)

    def _f(*_args: Any, **_kwargs: Any) -> int:
        return next(it)

    return _f


def test_prompt_guard_truncates_user_tail_and_system_head() -> None:
    messages = [
        {"role": "system", "content": "SYSTEM-" + ("A" * 50)},
        {"role": "user", "content": "USER-" + ("B" * 50)},
    ]

    guarded, stats = guard_chat_messages(
        messages,
        max_input_chars=10_000,
        max_single_message_chars=10,
        keep_last_messages=0,
        preserve_system_messages=True,
    )

    assert len(guarded) == 2
    assert guarded[0]["role"] == "system"
    assert guarded[1]["role"] == "user"

    # System keeps head
    assert guarded[0]["content"].startswith("SYSTEM-")
    assert len(guarded[0]["content"]) == 10

    # User keeps tail
    assert guarded[1]["content"].endswith("B" * 10)
    assert len(guarded[1]["content"]) == 10

    assert stats.truncated_messages == 2
    assert stats.dropped_messages == 0


def test_prompt_guard_keeps_system_and_drops_oldest_non_system() -> None:
    messages = [{"role": "system", "content": "sys"}]
    for i in range(10):
        messages.append({"role": "user", "content": f"u{i}"})

    guarded, stats = guard_chat_messages(
        messages,
        max_input_chars=10_000,
        max_single_message_chars=10_000,
        keep_last_messages=3,
        preserve_system_messages=True,
    )

    # system + last 3 non-system
    assert guarded[0]["role"] == "system"
    assert [m["content"] for m in guarded[1:]] == ["u7", "u8", "u9"]
    assert stats.dropped_messages == 7


def test_context_manager_condenses_and_injects_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force token estimator to cross threshold, then return smaller tokens post-condense.
    monkeypatch.setattr(
        context_manager_mod, "estimate_messages_tokens", _seq_returns(1_000, 200)
    )

    mem = FakeMemoryService(enabled=True, relevant_context="Important remembered context.")
    cm = ContextManager(
        cast(Any, mem),
        context_threshold=0.5,
        keep_recent_ratio=0.25,
        min_keep_messages=2,
        summary_limit=3,
    )

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "m1"},
        {"role": "assistant", "content": "m2"},
        {"role": "user", "content": "m3"},
        {"role": "assistant", "content": "m4"},
        {"role": "user", "content": "m5"},
    ]

    new_messages, stats = cm.manage_context(
        messages=messages,
        model_id="gpt-5.2",
        max_context_length=1_000,
        user_id="u",
        agent_id="a",
        project_id="p",
        session_id="s",
        enable_condensation=True,
    )

    assert stats["condensed"] is True
    assert stats["summary_added"] is True
    assert mem.add_memory_calls, "Expected old messages to be stored in memory"

    # Existing system message is preserved at the head; summary is inserted after it.
    assert new_messages[0]["role"] == "system"
    assert new_messages[0]["content"] == "sys"

    assert new_messages[1]["role"] == "system"
    assert "Previous Conversation Context" in new_messages[1]["content"]
    assert "Important remembered context." in new_messages[1]["content"]

    # Kept tail messages count at least min_keep_messages
    assert stats["messages_kept"] >= 2
    assert stats["messages_stored"] == len(messages) - stats["messages_kept"]


@pytest.mark.asyncio
async def test_rules_injector_inserts_after_existing_system(monkeypatch: pytest.MonkeyPatch) -> None:
    # Make token estimation deterministic and tiny for tests.
    import src.utils.rules_prompt_injector as rules_mod

    monkeypatch.setattr(rules_mod, "estimate_tokens_by_provider", lambda _t, _m: 1)

    # Provide mixed results (only roo_rule_shard should be used).
    shard_text = "RULE_SHARD\nkey: x\n\nDO THIS RULE"
    results = [
        SimpleNamespace(memory=shard_text, metadata={"type": "roo_rule_shard"}),
        SimpleNamespace(memory="ignore", metadata={"type": "other"}),
    ]
    mem = FakeMemoryService(enabled=True, search_results=results)

    messages = [
        {"role": "system", "content": "base system"},
        {"role": "user", "content": "please refactor this module"},
    ]

    new_messages, stats = await inject_retrieved_rules(
        messages=messages,
        memory_service=mem,  # type: ignore[arg-type]
        model_id="gpt-5.2",
        enable=True,
        user_id="system",
        project_id="roo_rules",
        max_shards=5,
        max_tokens=50,
        trivial_bypass=False,
    )

    assert stats["injected"] is True
    assert stats["retrieved"] == 1

    # Inserted after existing system message
    assert new_messages[0]["role"] == "system"
    assert new_messages[1]["role"] == "system"
    assert "Rules (retrieved from Mem0)" in new_messages[1]["content"]
    assert "DO THIS RULE" in new_messages[1]["content"]


@pytest.mark.asyncio
async def test_rules_injector_trivial_bypass_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.utils.rules_prompt_injector as rules_mod

    monkeypatch.setattr(rules_mod, "estimate_tokens_by_provider", lambda _t, _m: 1)

    mem = FakeMemoryService(enabled=True, search_results=[])
    messages = [{"role": "user", "content": "2+2"}]

    new_messages, stats = await inject_retrieved_rules(
        messages=messages,
        memory_service=mem,  # type: ignore[arg-type]
        model_id="gpt-5.2",
        enable=True,
        user_id="system",
        project_id="roo_rules",
        max_shards=5,
        max_tokens=50,
        trivial_bypass=True,
    )

    assert new_messages == messages
    assert stats["injected"] is False
    assert stats["skipped_reason"] == "trivial_bypass"


@pytest.mark.asyncio
async def test_code_jit_injector_inserts_block(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.utils.code_jit_prompt_injector as code_mod

    monkeypatch.setattr(code_mod, "estimate_tokens_by_provider", lambda _t, _m: 1)

    chunk = SimpleNamespace(
        file_path="src/app_fastapi.py",
        start_line=10,
        end_line=20,
        language="python",
        code="print('hi')",
    )
    mem = FakeMemoryService(enabled=True, code_results=[chunk])

    messages = [
        {"role": "system", "content": "base system"},
        {"role": "user", "content": "where is create_app defined?"},
    ]

    new_messages, stats = await inject_code_jit_context(
        messages=messages,
        memory_service=mem,  # type: ignore[arg-type]
        model_id="gpt-5.2",
        enable=True,
        user_id="u",
        project_id="p",
        max_chunks=3,
        max_tokens=50,
        trivial_bypass=False,
    )

    assert stats["injected"] is True
    assert stats["retrieved"] == 1
    assert new_messages[1]["role"] == "system"
    assert "Code Context (JIT retrieved)" in new_messages[1]["content"]
    assert "src/app_fastapi.py:10-20" in new_messages[1]["content"]


def test_startup_indexer_indexes_and_touches_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Work in isolated temp CWD and create .roo presence.
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roo" / "rules-code").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".roo" / "rules-code" / "x.md").write_text("# Rule\n", encoding="utf-8")

    mem = FakeMemoryService(enabled=True)

    captured: dict[str, Any] = {}

    def _fake_index_rules_into_mem0(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "success": True,
            "indexed": 1,
            "total": 1,
            "attempted": 1,
            "skipped_unchanged": 0,
            "pruned": 0,
            "errors": [],
        }

    # Patch the imported symbol inside the module.
    monkeypatch.setattr(startup_indexer_mod, "index_rules_into_mem0", _fake_index_rules_into_mem0)

    state_dir = tmp_path / "state"
    out = startup_indexer_mod.auto_index_rules_on_startup(
        memory_service=mem,  # type: ignore[arg-type]
        enabled=True,
        interval_hours=24,
        state_dir_override=str(state_dir),
        user_id="system",
        project_id="roo_rules",
        max_chars=1200,
        overlap_chars=150,
    )

    assert out["success"] is True
    assert out["indexed"] == 1
    assert (state_dir / ".last_index").exists()
    assert "manifest_path" in captured
    assert str(captured["manifest_path"]).endswith(".rules_index_manifest.json")


def test_startup_indexer_skips_if_recent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".roo").mkdir(parents=True, exist_ok=True)

    mem = FakeMemoryService(enabled=True)

    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / ".last_index"
    marker.touch()

    # Make it look like it was indexed "now"
    out = startup_indexer_mod.auto_index_rules_on_startup(
        memory_service=mem,  # type: ignore[arg-type]
        enabled=True,
        interval_hours=9999,  # huge interval ensures it skips
        state_dir_override=str(state_dir),
        user_id="system",
        project_id="roo_rules",
        max_chars=1200,
        overlap_chars=150,
    )

    assert out["indexing_attempted"] is False
    assert "Skipped" in out["message"]