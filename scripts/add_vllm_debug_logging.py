#!/usr/bin/env python3
"""Add debug logging to show vLLM's final system message and response body."""

import re

def add_vllm_debug_logging():
    """Add debug logging after system trimming and vLLM response."""
    
    with open("src/app_fastapi.py", "r") as f:
        content = f.read()
    
    # 1. Add logging after system trimming (after line 1140: payload["messages"] = messages)
    # Find the section and add logging
    pattern1 = r'(                msg\["content"\] = new_content\n                \n                payload\["messages"\] = messages)'
    
    replacement1 = r'''\1
                
                # DEBUG: Log final system message for vLLM
                logger.debug(
                    f"[VLLM DEBUG] Final system message length: {len(str(payload['messages'][0].get('content', '')))}"
                )
                # Log first 500 chars of system message
                sys_msg = payload["messages"][0] if payload["messages"] else {}
                sys_content = sys_msg.get("content", "")
                if isinstance(sys_content, list):
                    # Extract text from list format
                    sys_text_parts = []
                    for block in sys_content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            sys_text_parts.append(str(block.get("text", "")))
                    sys_text = "\\n".join(sys_text_parts)
                else:
                    sys_text = str(sys_content)
                logger.debug(f"[VLLM DEBUG] System message preview: {sys_text[:500]}...")'''
    
    content = re.sub(pattern1, replacement1, content, count=1)
    
    # 2. Add logging after vLLM response (after line 1275: upstream_json = r.json())
    pattern2 = r'(                    try:\n                        upstream_json = r\.json\(\)\n                    except Exception:\n                        upstream_json = \{"raw_text": r\.text\})'
    
    replacement2 = r'''\1
                    
                    # DEBUG: Log vLLM's full response
                    logger.debug(f"[VLLM DEBUG] Response status: {r.status_code}")
                    logger.debug(f"[VLLM DEBUG] Response body: {json.dumps(upstream_json, indent=2)[:2000]}")
                    
                    # Check for empty response
                    choices = upstream_json.get("choices", []) if isinstance(upstream_json, dict) else []
                    if not choices:
                        logger.warning("[VLLM DEBUG] No choices in response!")
                    elif isinstance(choices, list) and choices:
                        msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                        content_val = msg.get("content")
                        logger.debug(
                            f"[VLLM DEBUG] Response content length: {len(str(content_val)) if content_val else 0}"
                        )
                        if not content_val or (isinstance(content_val, str) and not content_val.strip()):
                            logger.warning("[VLLM DEBUG] Empty content in vLLM response!")'''
    
    content = re.sub(pattern2, replacement2, content, count=1)
    
    with open("src/app_fastapi.py", "w") as f:
        f.write(content)
    
    print("✅ Added vLLM debug logging")
    print("  - Logs final system message preview after trimming")
    print("  - Logs vLLM's full response body and diagnoses empty responses")

if __name__ == "__main__":
    add_vllm_debug_logging()