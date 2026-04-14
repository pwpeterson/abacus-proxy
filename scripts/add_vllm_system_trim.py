#!/usr/bin/env python3
"""Add ultra-aggressive vLLM system message trimming after tool filtering."""

import re

# Read the file
with open("src/app_fastapi.py", "r") as f:
    content = f.read()

# Find the insertion point (after tool filtering, before emergency trim)
pattern = r"(                            msg\[\"content\"\] = filtered_text\n            \n            payload\[\"messages\"\] = messages\n\n)(        # Emergency safety: Hard trim ONLY if filtering wasn't enough)"

replacement = r'''\1        # vLLM-specific: Ultra-aggressive system message trimming
        # Apply AFTER tool filtering to get entire system message down to ~10K chars
        if use_vllm_ctx:
            from src.utils.vllm_system_trimmer import trim_system_for_vllm
            
            messages = payload.get("messages", [])
            if isinstance(messages, list):
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("role") == "system":
                        content = msg.get("content", "")
                        
                        # Handle both string and list formats
                        if isinstance(content, str):
                            msg["content"] = trim_system_for_vllm(content)
                        elif isinstance(content, list):
                            # For list format, extract text, trim, then rebuild
                            text_parts = []
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    text_parts.append(str(block.get("text", "")))
                            
                            if text_parts:
                                full_text = "\n".join(text_parts)
                                trimmed_text = trim_system_for_vllm(full_text)
                                
                                # Rebuild with trimmed content
                                new_content = []
                                for block in content:
                                    if isinstance(block, dict) and block.get("type") == "text":
                                        new_content.append({
                                            "type": "text",
                                            "text": trimmed_text
                                        })
                                        break  # Only replace first text block
                                    else:
                                        new_content.append(block)
                                msg["content"] = new_content
                
                payload["messages"] = messages

\2'''

new_content = re.sub(pattern, replacement, content)

if new_content == content:
    print("❌ Pattern not found - no changes made")
    exit(1)

# Write back
with open("src/app_fastapi.py", "w") as f:
    f.write(new_content)

print("✅ Added vLLM system trimmer integration")