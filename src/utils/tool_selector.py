"""Intelligent tool selection based on request context.

Analyzes user messages and context to select relevant tools.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


# Keyword patterns for tool selection
TOOL_KEYWORDS = {
    "filesystem": [
        r"\bread\b", r"\bwrite\b", r"\bfile\b", r"\bedit\b", r"\bmodify\b",
        r"\bcreate\b", r"\bdelete\b", r"\blist\b", r"\bdirectory\b", r"\bfolder\b",
        r"\bsearch\b", r"\bfind\b", r"\bgrep\b", r"\bpath\b"
    ],
    "conport": [
        r"\bdecision\b", r"\bcontext\b", r"\bprogress\b", r"\bpattern\b",
        r"\blog\b", r"\btrack\b", r"\bhistory\b", r"\bproject\b"
    ],
    "mem0-mcp": [
        r"\bmemory\b", r"\bremember\b", r"\brecall\b", r"\bforget\b",
        r"\bstore\b", r"\bretrieve\b", r"\bsearch.*memor"
    ],
    "git-safe": [
        r"\bgit\b", r"\bcommit\b", r"\bpush\b", r"\bpull\b", r"\bbranch\b",
        r"\bstatus\b", r"\bdiff\b", r"\blog\b", r"\bstage\b"
    ],
    "roo_code": [
        r"\bexecute\b", r"\bcommand\b", r"\brun\b", r"\bash\b", r"\bquestion\b",
        r"\bcomplete\b", r"\bdone\b", r"\bfinish\b"
    ]
}


# Always include these core tools
CORE_TOOLS = [
    "read_file", "write_to_file", "apply_diff", "list_files",
    "execute_command", "ask_followup_question", "attempt_completion"
]


def _extract_text_from_messages(messages: list[dict[str, Any]]) -> str:
    """Extract all text content from messages."""
    text_parts = []
    for msg in messages:
        if isinstance(msg.get("content"), str):
            text_parts.append(msg["content"])
        elif isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
    return " ".join(text_parts).lower()


def _score_server_relevance(text: str, server_name: str) -> float:
    """Score how relevant a server is based on keyword matches.
    
    Args:
        text: Combined text from messages
        server_name: Name of the MCP server
        
    Returns:
        Relevance score (0.0 to 1.0)
    """
    keywords = TOOL_KEYWORDS.get(server_name, [])
    if not keywords:
        return 0.0
    
    matches = 0
    for pattern in keywords:
        if re.search(pattern, text, re.IGNORECASE):
            matches += 1
    
    # Normalize score
    return min(matches / len(keywords), 1.0)


async def select_tools_for_request(
    messages: list[dict[str, Any]],
    mem0_context: dict[str, Any] | None,
    available_tools: dict[str, list[dict[str, Any]]],
    max_tools: int = 50
) -> list[dict[str, Any]]:
    """Select relevant tools based on request context.
    
    Args:
        messages: Chat messages from the request
        mem0_context: Optional Mem0 context data
        available_tools: Dictionary of server names to tool lists
        max_tools: Maximum number of tools to include
        
    Returns:
        List of selected tools in OpenAI function calling format
    """
    # Extract text from messages
    text = _extract_text_from_messages(messages)
    
    # Score each server's relevance
    server_scores = {}
    for server_name in available_tools.keys():
        score = _score_server_relevance(text, server_name)
        server_scores[server_name] = score
        logger.debug(f"Server {server_name} relevance score: {score:.2f}")
    
    # Always include Roo Code tools (core functionality)
    selected_tools = []
    roo_tools = available_tools.get("roo_code", [])
    selected_tools.extend(roo_tools)
    logger.info(f"Included {len(roo_tools)} Roo Code core tools")
    
    # Add tools from relevant servers (score > 0.1)
    for server_name, score in sorted(server_scores.items(), key=lambda x: x[1], reverse=True):
        if server_name == "roo_code":
            continue  # Already added
        
        if score > 0.1:  # Relevance threshold
            server_tools = available_tools.get(server_name, [])
            selected_tools.extend(server_tools)
            logger.info(f"Included {len(server_tools)} tools from {server_name} (score: {score:.2f})")
        
        # Stop if we've reached max_tools
        if len(selected_tools) >= max_tools:
            logger.warning(f"Reached max_tools limit ({max_tools}), stopping selection")
            break
    
    # If no servers scored above threshold, include filesystem as fallback
    if len(selected_tools) == len(roo_tools):
        logger.info("No servers scored above threshold, including filesystem as fallback")
        filesystem_tools = available_tools.get("filesystem", [])
        selected_tools.extend(filesystem_tools)
    
    # Trim to max_tools if needed
    if len(selected_tools) > max_tools:
        selected_tools = selected_tools[:max_tools]
        logger.warning(f"Trimmed tools to max_tools limit: {max_tools}")
    
    logger.info(f"Selected {len(selected_tools)} tools for request")
    return selected_tools


async def select_all_tools(
    available_tools: dict[str, list[dict[str, Any]]],
    max_tools: int = 100
) -> list[dict[str, Any]]:
    """Select all available tools (for requests that need full access).
    
    Args:
        available_tools: Dictionary of server names to tool lists
        max_tools: Maximum number of tools to include
        
    Returns:
        List of all tools in OpenAI function calling format
    """
    all_tools = []
    for server_name, tools in available_tools.items():
        all_tools.extend(tools)
        logger.debug(f"Included {len(tools)} tools from {server_name}")
    
    if len(all_tools) > max_tools:
        logger.warning(f"Total tools ({len(all_tools)}) exceeds max_tools ({max_tools}), trimming")
        all_tools = all_tools[:max_tools]
    
    logger.info(f"Selected all {len(all_tools)} available tools")
    return all_tools
