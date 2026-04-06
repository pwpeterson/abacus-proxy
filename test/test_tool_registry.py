"""Test tool schema registry integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.tool_filter import ToolFilter
from src.utils.tool_schema_registry import (
    ToolSchemaRegistry,
    load_tool_schema_registry,
    select_registry_tools_for_injection,
)


@pytest.fixture
def sample_registry_path(tmp_path: Path) -> Path:
    """Create a sample tool registry for testing."""
    registry = {
        "metadata": {
            "version": "1.0.0",
            "description": "Test registry",
            "updated_at": "2026-04-06T00:00:00Z",
        },
        "policy": {
            "tool_categories": {
                "file_operations": ["read_file", "write_file", "list_files"],
                "git_operations": ["git_status", "git_commit", "git_push"],
            },
            "keyword_signals": {
                "file_operations": ["read", "file", "list", "directory"],
                "git_operations": ["git", "commit", "push", "status"],
            },
            "essential_tools": ["read_file"],
        },
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file from the filesystem",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write content to a file",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git_status",
                    "description": "Get git repository status",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
    }

    registry_path = tmp_path / "test_registry.json"
    registry_path.write_text(json.dumps(registry, indent=2))
    return registry_path


def test_load_tool_schema_registry(sample_registry_path: Path):
    """Test loading tool schema registry from file."""
    registry = load_tool_schema_registry(str(sample_registry_path))

    assert isinstance(registry, ToolSchemaRegistry)
    assert registry.version == 1
    assert len(registry.tools) == 3
    assert registry.description == "Test registry"


def test_tool_filter_config_loading(sample_registry_path: Path):
    """Test ToolFilter loads policy from registry."""
    # Set registry path for ToolFilter
    ToolFilter._registry_path = sample_registry_path
    ToolFilter.reload_config()

    categories = ToolFilter.get_tool_categories()
    assert "file_operations" in categories
    assert "read_file" in categories["file_operations"]

    signals = ToolFilter.get_keyword_signals()
    assert "file_operations" in signals
    assert "read" in signals["file_operations"]

    essentials = ToolFilter.get_essential_tools()
    assert "read_file" in essentials


def test_select_registry_tools_for_injection_with_intent(sample_registry_path: Path):
    """Test tool injection when file operation intent is detected."""
    registry = load_tool_schema_registry(str(sample_registry_path))

    messages = [
        {"role": "user", "content": "Can you read the config.json file for me?"}
    ]

    tools = select_registry_tools_for_injection(
        registry=registry,
        messages=messages,
        max_tools=10,
        max_tool_tokens=5000,
    )

    # Should inject file operation tools
    assert len(tools) > 0
    tool_names = [t["function"]["name"] for t in tools]
    assert "read_file" in tool_names


def test_select_registry_tools_for_injection_no_intent(sample_registry_path: Path):
    """Test tool injection when no tool intent is detected."""
    registry = load_tool_schema_registry(str(sample_registry_path))

    messages = [
        {"role": "user", "content": "What is 2 + 2?"}
    ]

    tools = select_registry_tools_for_injection(
        registry=registry,
        messages=messages,
        max_tools=10,
        max_tool_tokens=5000,
    )

    # Should NOT inject tools (no intent detected)
    assert len(tools) == 0


def test_tool_filter_client_tools(sample_registry_path: Path):
    """Test filtering client-provided tools."""
    ToolFilter._registry_path = sample_registry_path
    ToolFilter.reload_config()

    client_tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "git_status",
                "description": "Git status",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    messages = [
        {"role": "user", "content": "Read the README.md file"}
    ]

    filtered, stats = ToolFilter.filter_tools(
        tools=client_tools,
        messages=messages,
        max_tools=2,
        enable_filtering=True,
    )

    # Should prioritize file operations over git
    assert len(filtered) <= 2
    assert stats["filtered"]
    tool_names = [t["function"]["name"] for t in filtered]
    assert "read_file" in tool_names


def test_tool_injection_with_git_intent(sample_registry_path: Path):
    """Test tool injection detects git operations intent."""
    registry = load_tool_schema_registry(str(sample_registry_path))

    messages = [
        {"role": "user", "content": "Show me the git status and commit the changes"}
    ]

    tools = select_registry_tools_for_injection(
        registry=registry,
        messages=messages,
        max_tools=10,
        max_tool_tokens=5000,
    )

    # Should inject git operation tools
    assert len(tools) > 0
    tool_names = [t["function"]["name"] for t in tools]
    assert "git_status" in tool_names


def test_tool_injection_respects_token_budget(sample_registry_path: Path):
    """Test tool injection respects token budget."""
    registry = load_tool_schema_registry(str(sample_registry_path))

    messages = [
        {"role": "user", "content": "Read files and check git status"}
    ]

    # Very low token budget
    tools = select_registry_tools_for_injection(
        registry=registry,
        messages=messages,
        max_tools=10,
        max_tool_tokens=100,  # Very restrictive
    )

    # Should still return at least one tool (guarantee)
    assert len(tools) >= 1
    # But should be limited by token budget
    assert len(tools) < len(registry.tools)