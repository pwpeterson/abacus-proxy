"""Minimal stub for tool_schema_registry (MCP bridge compatibility).

The tool registry feature is disabled, but the MCP bridge code still references
these functions for schema validation. This stub provides minimal compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ToolSchemaRegistry:
    """Minimal registry stub."""

    def __init__(self) -> None:
        self.sources: dict[str, Any] = {}
        self.tools: dict[str, Any] = {}


def load_tool_schema_registry(path: Path) -> ToolSchemaRegistry:
    """Load tool schema registry (stub - returns empty registry)."""
    return ToolSchemaRegistry()


def get_registry_tool_schema(
    *, registry: ToolSchemaRegistry, tool_name: str
) -> dict[str, Any] | None:
    """Get tool schema from registry (stub - returns None)."""
    return None
