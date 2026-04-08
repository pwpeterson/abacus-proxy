"""Roo Code built-in tool definitions.

All 22 Roo Code tools with correct schemas from official documentation.
Reference: https://docs.roocode.com/advanced-usage/available-tools/tool-use-overview
"""

from typing import Any


def get_all_roo_code_tools() -> list[dict[str, Any]]:
    """Get all 22 Roo Code built-in tools with correct schemas."""
    return [
        # File Reading (3 tools)
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read contents of one or more files",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "mode": {"type": "string", "enum": ["slice", "indentation"]},
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                        "args": {
                            "type": "object",
                            "properties": {
                                "file": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "path": {"type": "string"},
                                            "line_range": {"type": "string"}
                                        },
                                        "required": ["path"]
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        {"type": "function", "function": {"name": "list_files", "description": "List files in directory", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "recursive": {"type": "boolean"}}, "required": ["path"]}}},
        {"type": "function", "function": {"name": "read_command_output", "description": "Read output from running command", "parameters": {"type": "object", "properties": {"command_id": {"type": "string"}}, "required": ["command_id"]}}},
        
        # Search (2 tools)
        {"type": "function", "function": {"name": "search_files", "description": "Search files using regex", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "regex": {"type": "string"}, "file_pattern": {"type": "string"}, "respect_gitignore": {"type": "boolean"}}, "required": ["path", "regex"]}}},
        {"type": "function", "function": {"name": "codebase_search", "description": "Semantic codebase search", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "file_pattern": {"type": "string"}}, "required": ["query"]}}},
        
        # File Editing (6 tools)
        {"type": "function", "function": {"name": "apply_diff", "description": "Apply precise modifications", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "diff": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path", "diff"]}}},
        {"type": "function", "function": {"name": "apply_patch", "description": "Apply unified diff patch", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "patch": {"type": "string"}}, "required": ["path", "patch"]}}},
        {"type": "function", "function": {"name": "edit", "description": "Line-based file editing", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array"}, "dryRun": {"type": "boolean"}}, "required": ["path", "edits"]}}},
        {"type": "function", "function": {"name": "edit_file", "description": "Edit with exact text matching", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "edits": {"type": "array"}, "dryRun": {"type": "boolean"}}, "required": ["path", "edits"]}}},
        {"type": "function", "function": {"name": "search_replace", "description": "Search and replace text", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "search": {"type": "string"}, "replace": {"type": "string"}, "regex": {"type": "boolean"}}, "required": ["path", "search", "replace"]}}},
        {"type": "function", "function": {"name": "write_to_file", "description": "Write content to file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
        
        # Command Execution (2 tools)
        {"type": "function", "function": {"name": "execute_command", "description": "Execute CLI command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}}, "required": ["command"]}}},
        {"type": "function", "function": {"name": "run_slash_command", "description": "Run Roo slash command", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "args": {"type": "object"}}, "required": ["command"]}}},
        
        # MCP Integration (2 tools)
        {"type": "function", "function": {"name": "use_mcp_tool", "description": "Use MCP server tool", "parameters": {"type": "object", "properties": {"server_name": {"type": "string"}, "tool_name": {"type": "string"}, "arguments": {"type": "object"}}, "required": ["server_name", "tool_name", "arguments"]}}},
        {"type": "function", "function": {"name": "access_mcp_resource", "description": "Access MCP resource", "parameters": {"type": "object", "properties": {"server_name": {"type": "string"}, "uri": {"type": "string"}}, "required": ["server_name", "uri"]}}},
        
        # Mode & Task (2 tools)
        {"type": "function", "function": {"name": "switch_mode", "description": "Switch Roo mode", "parameters": {"type": "object", "properties": {"mode_slug": {"type": "string"}, "reason": {"type": "string"}}, "required": ["mode_slug"]}}},
        {"type": "function", "function": {"name": "new_task", "description": "Create new task", "parameters": {"type": "object", "properties": {"mode": {"type": "string"}, "message": {"type": "string"}}, "required": ["mode", "message"]}}},
        
        # User Interaction (3 tools)
        {"type": "function", "function": {"name": "ask_followup_question", "description": "Ask clarifying question", "parameters": {"type": "object", "properties": {"question": {"type": "string"}, "follow_up": {"type": "object", "properties": {"suggest": {"type": "array", "items": {"type": "string"}}}, "required": ["suggest"]}}, "required": ["question", "follow_up"]}}},
        {"type": "function", "function": {"name": "attempt_completion", "description": "Signal task completion", "parameters": {"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}}},
        {"type": "function", "function": {"name": "update_todo_list", "description": "Update todo list", "parameters": {"type": "object", "properties": {"todos": {"type": "string"}}, "required": ["todos"]}}},
        
        # Advanced (2 tools)
        {"type": "function", "function": {"name": "generate_image", "description": "Generate AI image", "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}, "size": {"type": "string"}, "style": {"type": "string"}}, "required": ["prompt"]}}},
        {"type": "function", "function": {"name": "skill", "description": "Execute custom skill", "parameters": {"type": "object", "properties": {"skill_name": {"type": "string"}, "parameters": {"type": "object"}}, "required": ["skill_name"]}}},
    ]
