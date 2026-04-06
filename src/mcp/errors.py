"""Shared MCP error types.

This module exists to avoid circular imports between:
- [`src/mcp/tools.py`](src/mcp/tools.py:1)
- [`src/mcp/server.py`](src/mcp/server.py:1)
"""

from __future__ import annotations

from typing import Any


class MCPToolError(Exception):
    """Custom exception for MCP tool errors."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        """
        Args:
            code: JSON-RPC error code
            message: Human-readable error message
            data: Optional structured error payload (must be JSON-serializable)
        """
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)
