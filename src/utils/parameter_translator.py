"""Parameter translation layer for Roo Code compatibility.

Roo Code uses different parameter names than actual MCP servers expect.
This module translates between Roo Code's parameter names and MCP server parameter names.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


# Mapping: (server, tool) -> {roo_param_name: mcp_param_name}
PARAMETER_TRANSLATIONS: dict[tuple[str, str], dict[str, str]] = {
    # Filesystem server translations
    ("filesystem", "read_file"): {
        # Roo Code sends individual file paths, MCP expects args array with file objects
        # This requires special handling in translate_parameters()
    },
    ("filesystem", "search_files"): {
        "regex": "pattern",  # Roo uses 'regex', MCP uses 'pattern'
    },
    # Add more translations as needed
}


def translate_parameters(
    *,
    server: str,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Translate Roo Code parameter names to MCP server parameter names.
    
    Args:
        server: MCP server name
        tool: Tool name
        arguments: Original arguments from Roo Code
        
    Returns:
        Translated arguments for MCP server
    """
    logger.debug(f"[ParamTranslator] server={server} tool={tool} args={arguments}")
    # Special case: filesystem read_file
    # Roo Code may send: {"path": "file.txt"}
    # MCP expects: {"args": [{"path": "file.txt"}]}
    if server == "filesystem" and tool == "read_file":
        if "path" in arguments and "args" not in arguments:
            # Single file read - wrap in args array
            return {
                "args": [
                    {
                        "path": arguments["path"],
                        **({
                            "tail": arguments["tail"]
                        } if "tail" in arguments else {}),
                        **({
                            "head": arguments["head"]
                        } if "head" in arguments else {}),
                    }
                ]
            }
        # Already has args, pass through
        return arguments
    
    # Special case: filesystem read_text_file (same as read_file)
    if server == "filesystem" and tool == "read_text_file":
        if "path" in arguments and "args" not in arguments:
            return {
                "args": [
                    {
                        "path": arguments["path"],
                        **({
                            "tail": arguments["tail"]
                        } if "tail" in arguments else {}),
                        **({
                            "head": arguments["head"]
                        } if "head" in arguments else {}),
                    }
                ]
            }
        return arguments
    
    # Generic translation using mapping
    key = (server, tool)
    translations = PARAMETER_TRANSLATIONS.get(key)
    
    if not translations:
        # No translation needed
        logger.debug(f"[ParamTranslator] No translation needed for {server}/{tool}")
        return arguments
    
    # Apply translations
    translated = dict(arguments)
    for roo_param, mcp_param in translations.items():
        if roo_param in translated:
            value = translated.pop(roo_param)
            translated[mcp_param] = value
            logger.debug(f"[ParamTranslator] Translated {roo_param} -> {mcp_param}")
    
    logger.debug(f"[ParamTranslator] Result: {translated}")
    return translated
