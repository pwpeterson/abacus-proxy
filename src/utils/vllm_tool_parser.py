"""Parse vLLM Hermes-format tool calls into OpenAI structured format.

vLLM with --tool-call-parser hermes generates tool calls as plain text:
    <|tool_call>call:function_name{arg1:value1,arg2:value2}<tool_call|>

This module converts that format to OpenAI's structured tool_calls format:
    {
        "tool_calls": [{
            "id": "call_0",
            "type": "function",
            "function": {
                "name": "function_name",
                "arguments": "{\"arg1\": \"value1\", \"arg2\": \"value2\"}"
            }
        }]
    }
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger


def parse_hermes_tool_calls(content: str) -> list[dict[str, Any]] | None:
    """Parse vLLM Hermes-format tool calls from text content.
    
    Args:
        content: Response content that may contain tool call syntax
        
    Returns:
        List of tool call dicts in OpenAI format, or None if no valid tool calls found
    """
    if not isinstance(content, str) or not content.strip():
        return None
    
    # Pattern: <|tool_call>call:function_name{arguments}<tool_call|>
    # Use non-greedy match to capture everything between call:name and <tool_call|>
    pattern = r'<\|tool_call\>call:(\w+)(.*?)<tool_call\|>'
    
    matches = re.findall(pattern, content, re.DOTALL)
    if not matches:
        logger.debug(f"[VLLM TOOL PARSER] No Hermes tool calls found in content: {content[:200]}")
        return None
    
    logger.info(f"[VLLM TOOL PARSER] Found {len(matches)} Hermes tool call(s)")
    
    tool_calls = []
    for idx, (func_name, args_section) in enumerate(matches):
        try:
            # The args_section includes the braces: {...}
            # Clean it up first
            args_section = args_section.strip()
            
            logger.debug(f"[VLLM TOOL PARSER] Raw args for {func_name}: {args_section[:200]}")
            
            # Replace <|"|> with actual quotes
            cleaned_args = args_section.replace('<|"|>', '"').replace("<|'|>", "'")
            
            # The cleaned args should now be valid JSON (or close to it)
            # Try to parse directly
            try:
                args_dict = json.loads(cleaned_args)
                logger.info(f"[VLLM TOOL PARSER] Successfully parsed {func_name} args as JSON")
            except json.JSONDecodeError as e:
                logger.warning(f"[VLLM TOOL PARSER] JSON parse failed for {func_name}: {e}")
                logger.debug(f"[VLLM TOOL PARSER] Cleaned args: {cleaned_args[:200]}")
                
                # Fallback: try to fix common issues
                # Sometimes the format is key:value instead of "key":"value"
                # This is a best-effort heuristic
                
                # If it looks like {key:value}, try to quote keys
                if cleaned_args.startswith('{') and ':' in cleaned_args:
                    # Simple regex to add quotes around unquoted keys
                    fixed = re.sub(r'(\w+):', r'"\1":', cleaned_args)
                    try:
                        args_dict = json.loads(fixed)
                        logger.info(f"[VLLM TOOL PARSER] Fixed and parsed {func_name} args")
                    except:
                        # Last resort: create a simple dict
                        args_dict = {"raw_args": cleaned_args}
                        logger.warning(f"[VLLM TOOL PARSER] Using raw args for {func_name}")
                else:
                    args_dict = {"raw_args": cleaned_args}
            
            # Convert to JSON string for arguments field
            arguments_json = json.dumps(args_dict, ensure_ascii=False)
            
            tool_call = {
                "id": f"call_{idx}",
                "type": "function",
                "function": {
                    "name": func_name,
                    "arguments": arguments_json
                }
            }
            tool_calls.append(tool_call)
            
            logger.info(f"[VLLM TOOL PARSER] ✓ Parsed {func_name}: {arguments_json[:100]}...")
            
        except Exception as e:
            logger.error(f"[VLLM TOOL PARSER] Failed to parse tool call {func_name}: {e}", exc_info=True)
            continue
    
    return tool_calls if tool_calls else None


def convert_vllm_response_to_openai(response_data: dict[str, Any]) -> dict[str, Any]:
    """Convert vLLM response with Hermes tool calls to OpenAI format.
    
    Args:
        response_data: vLLM response dict
        
    Returns:
        Modified response dict with structured tool_calls
    """
    if not isinstance(response_data, dict):
        return response_data
    
    choices = response_data.get("choices")
    if not isinstance(choices, list) or not choices:
        return response_data
    
    # Process first choice (most common case)
    choice = choices[0]
    if not isinstance(choice, dict):
        return response_data
    
    message = choice.get("message")
    if not isinstance(message, dict):
        return response_data
    
    content = message.get("content")
    if not isinstance(content, str):
        logger.debug(f"[VLLM TOOL PARSER] Content is not string, skipping: {type(content)}")
        return response_data
    
    logger.debug(f"[VLLM TOOL PARSER] Checking content for Hermes tool calls: {content[:200]}...")
    
    # Try to parse tool calls from content
    tool_calls = parse_hermes_tool_calls(content)
    
    if tool_calls:
        # Found tool calls - restructure response
        logger.info(f"[VLLM TOOL PARSER] ✅ Converted {len(tool_calls)} Hermes tool call(s) to OpenAI format")
        
        # Clear content and add tool_calls
        message["content"] = None
        message["tool_calls"] = tool_calls
        choice["finish_reason"] = "tool_calls"
        
        # Log for debugging
        for tc in tool_calls:
            func = tc.get("function", {})
            logger.info(
                f"[VLLM TOOL PARSER]   - {func.get('name')}: {func.get('arguments', '')[:80]}..."
            )
    else:
        logger.debug(f"[VLLM TOOL PARSER] No tool calls detected in response")
    
    return response_data
