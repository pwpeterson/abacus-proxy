"""MCP tools for advanced memory features.

Provides tools for:
- Decision logging and search
- Error tracking and pattern analysis
- Code indexing
- Memory analytics
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from src.utils.code_indexer import CodeIndexer
from src.utils.decision_logger import DecisionLogger
from src.utils.error_tracker import ErrorTracker
from src.utils.memory_analytics import MemoryAnalytics
from src.utils.memory_service import MemoryService


def create_advanced_memory_tools(memory_service: MemoryService) -> list[dict[str, Any]]:
    """Create MCP tool definitions for advanced memory features.

    Args:
        memory_service: Memory service instance

    Returns:
        List of MCP tool definitions
    """
    decision_logger = DecisionLogger(memory_service)
    error_tracker = ErrorTracker(memory_service)
    code_indexer = CodeIndexer(memory_service)
    analytics = MemoryAnalytics(memory_service)

    tools = [
        # Decision logging tools
        {
            "name": "log_decision",
            "description": "Log an architectural or implementation decision with rationale",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short decision title"},
                    "description": {"type": "string", "description": "Detailed description"},
                    "rationale": {"type": "string", "description": "Why this decision was made"},
                    "alternatives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Alternative approaches considered",
                    },
                    "consequences": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Expected consequences/impacts",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags (e.g., 'architecture', 'security')",
                    },
                    "user_id": {"type": "string", "description": "User identifier"},
                    "project_id": {"type": "string", "description": "Project identifier"},
                },
                "required": ["title", "description"],
            },
            "handler": lambda args: decision_logger.log_decision(**args),
        },
        {
            "name": "search_decisions",
            "description": "Search for logged decisions",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "user_id": {"type": "string", "description": "Filter by user"},
                    "project_id": {"type": "string", "description": "Filter by project"},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags",
                    },
                    "limit": {"type": "integer", "description": "Maximum results", "default": 10},
                },
                "required": ["query"],
            },
            "handler": lambda args: {"decisions": decision_logger.search_decisions(**args)},
        },
        # Error tracking tools
        {
            "name": "log_error",
            "description": "Log an error with context for pattern analysis",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "error": {"type": "string", "description": "Error message"},
                    "context": {"type": "string", "description": "Additional context"},
                    "stack_trace": {"type": "string", "description": "Stack trace"},
                    "severity": {
                        "type": "string",
                        "enum": ["debug", "info", "warning", "error", "critical"],
                        "default": "error",
                    },
                    "component": {"type": "string", "description": "Component/module"},
                    "solution": {"type": "string", "description": "Known solution"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "user_id": {"type": "string"},
                    "project_id": {"type": "string"},
                },
                "required": ["error"],
            },
            "handler": lambda args: error_tracker.log_error(**args),
        },
        {
            "name": "search_similar_errors",
            "description": "Search for similar errors to find patterns and solutions",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "error": {"type": "string", "description": "Error message to search for"},
                    "user_id": {"type": "string"},
                    "project_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["error"],
            },
            "handler": lambda args: {"similar_errors": error_tracker.search_similar_errors(**args)},
        },
        {
            "name": "get_error_patterns",
            "description": "Analyze error patterns and frequency",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "project_id": {"type": "string"},
                    "component": {"type": "string"},
                    "severity": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
            "handler": lambda args: error_tracker.get_error_patterns(**args),
        },
        # Code indexing tools
        {
            "name": "index_code_file",
            "description": "Index a code file for semantic search",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to code file"},
                    "project_id": {"type": "string", "description": "Project identifier"},
                    "user_id": {"type": "string"},
                    "language": {"type": "string", "description": "Programming language"},
                    "chunk_size": {"type": "integer", "default": 50},
                    "overlap": {"type": "integer", "default": 5},
                },
                "required": ["file_path", "project_id"],
            },
            "handler": lambda args: code_indexer.index_file(**args),
        },
        {
            "name": "index_code_directory",
            "description": "Index all code files in a directory",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to index"},
                    "project_id": {"type": "string", "description": "Project identifier"},
                    "user_id": {"type": "string"},
                    "patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File patterns to include",
                    },
                    "exclude_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Patterns to exclude",
                    },
                    "max_files": {"type": "integer", "default": 1000},
                },
                "required": ["directory", "project_id"],
            },
            "handler": lambda args: code_indexer.index_directory(**args),
        },
        # Memory analytics tools
        {
            "name": "get_memory_usage_stats",
            "description": "Get memory usage statistics",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "project_id": {"type": "string"},
                },
            },
            "handler": lambda args: analytics.get_usage_stats(**args),
        },
        {
            "name": "get_memory_health",
            "description": "Get memory system health metrics",
            "inputSchema": {"type": "object", "properties": {}},
            "handler": lambda args: analytics.get_memory_health(),
        },
        {
            "name": "get_memory_type_distribution",
            "description": "Get distribution of memory types",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "project_id": {"type": "string"},
                },
            },
            "handler": lambda args: analytics.get_type_distribution(**args),
        },
        {
            "name": "get_memory_recent_activity",
            "description": "Get recent memory activity",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "default": 24},
                    "user_id": {"type": "string"},
                    "project_id": {"type": "string"},
                },
            },
            "handler": lambda args: analytics.get_recent_activity(**args),
        },
        {
            "name": "get_memory_optimization_recommendations",
            "description": "Get recommendations for optimizing memory usage",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "project_id": {"type": "string"},
                },
            },
            "handler": lambda args: {
                "recommendations": analytics.get_optimization_recommendations(**args)
            },
        },
        {
            "name": "get_memory_search_performance",
            "description": "Get search performance statistics",
            "inputSchema": {"type": "object", "properties": {}},
            "handler": lambda args: analytics.get_search_performance_stats(),
        },
    ]

    return tools


async def handle_advanced_memory_tool(
    tool_name: str,
    arguments: dict[str, Any],
    memory_service: MemoryService,
) -> dict[str, Any]:
    """Handle advanced memory tool calls.

    Args:
        tool_name: Name of the tool to execute
        arguments: Tool arguments
        memory_service: Memory service instance

    Returns:
        Tool execution result
    """
    tools = create_advanced_memory_tools(memory_service)

    for tool in tools:
        if tool["name"] == tool_name:
            try:
                handler = tool["handler"]
                result = handler(arguments)
                return {"success": True, "result": result}
            except Exception as e:
                logger.error(f"Error executing {tool_name}: {e}")
                return {"success": False, "error": str(e)}

    return {"success": False, "error": f"Unknown tool: {tool_name}"}