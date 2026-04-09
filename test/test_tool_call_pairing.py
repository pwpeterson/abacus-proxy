from __future__ import annotations


from src.utils.context_manager import ContextManager


def test_ensure_tool_call_output_pairs_keeps_output_when_call_kept() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
        {"role": "assistant", "content": "done"},
    ]

    # Simulate importance-scoring selection that kept the assistant tool call but
    # accidentally dropped the tool output.
    keep_positions = {1}
    keep_positions = ContextManager._ensure_tool_call_output_pairs(messages, keep_positions)

    assert 2 in keep_positions


def test_ensure_tool_call_output_pairs_keeps_call_when_output_kept() -> None:
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_2", "content": "ok"},
        {"role": "assistant", "content": "done"},
    ]

    # Simulate a guard that kept tool output but dropped the corresponding call.
    keep_positions = {2}
    keep_positions = ContextManager._ensure_tool_call_output_pairs(messages, keep_positions)

    assert 1 in keep_positions


def test_audit_dangling_tool_calls_detects_missing_tool_output() -> None:
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_3",
                    "type": "function",
                    "function": {"name": "x", "arguments": "{}"},
                }
            ],
        }
    ]

    audit = ContextManager._audit_dangling_tool_calls(messages)
    assert audit["missing_outputs"] == ["call_3"]

