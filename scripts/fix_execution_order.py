#!/usr/bin/env python3
"""Fix execution order: move emergency trim after tool filtering."""

import re

# Read the file
with open("src/app_fastapi.py", "r") as f:
    content = f.read()

# Define the sections to reorder
# Section A: vLLM limits + Mem0 enhancements (lines 1003-1019)
# Section B: Emergency trim (lines 1021-1055) - MOVE THIS
# Section C: else branch (lines 1056-1057)
# Section D: Prompt guard (lines 1059-1060) 
# Section E: Tool filtering (lines 1062-1088) - KEEP HERE
# New order: A, C, D, E, then B (modified)

# Extract emergency trim section (to be moved)
emergency_trim_pattern = re.compile(
    r'(\n            # Emergency safety.*?\n                    payload\["messages"\] = trimmed_messages\n)',
    re.DOTALL
)

match = emergency_trim_pattern.search(content)
if not match:
    print("ERROR: Could not find emergency trim section")
    exit(1)

emergency_trim_code = match.group(1)

# Remove emergency trim from current location (after Mem0, before else)
content = content.replace(emergency_trim_code, "")

# Find the location after tool filtering (after payload["messages"] = messages)
# This is around line 1088
insertion_point = content.find('            payload["messages"] = messages\n\n        # ----------')

if insertion_point == -1:
    print("ERROR: Could not find insertion point after tool filtering")
    exit(1)

# Modify emergency trim to:
# 1. Move it under use_vllm_ctx check
# 2. Update comment
modified_emergency_trim = """
        # Emergency safety: Hard trim ONLY if filtering wasn't enough (rare)
        # This executes AFTER tool filtering to prevent unnecessary context loss
        if use_vllm_ctx:
            from src.utils.token_estimator import estimate_messages_tokens
            messages = payload.get("messages", [])
            if isinstance(messages, list) and messages:
                current_tokens = estimate_messages_tokens(messages, payload.get("model", "unknown"))
                if current_tokens > 17000:  # 18K - 1K safety margin
                    logger.warning(
                        f"[EMERGENCY TRIM] Tool filtering wasn't enough: {current_tokens} tokens. "
                        "Applying hard trim..."
                    )
                    
                    # Keep only: 1 system message + last N user/assistant messages
                    system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
                    non_system_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
                    
                    # Trim system to just 1 minimal message
                    if system_msgs:
                        trimmed_system = [{
                            "role": "system",
                            "content": "You are Roo, an expert AI assistant. Previous context was trimmed due to token limits."
                        }]
                    else:
                        trimmed_system = []
                    
                    # Keep last 5 non-system messages (should be ~2-3K tokens)
                    trimmed_non_system = non_system_msgs[-5:] if len(non_system_msgs) > 5 else non_system_msgs
                    
                    trimmed_messages = trimmed_system + trimmed_non_system
                    final_tokens = estimate_messages_tokens(trimmed_messages, payload.get("model", "unknown"))
                    
                    logger.warning(
                        f"[EMERGENCY TRIM] Reduced {current_tokens} → {final_tokens} tokens "
                        f"(kept {len(trimmed_messages)} messages)"
                    )
                    payload["messages"] = trimmed_messages
"""

# Insert after tool filtering
content = content[:insertion_point + len('            payload["messages"] = messages\n')] + modified_emergency_trim + content[insertion_point + len('            payload["messages"] = messages\n'):]

# Update the "FINAL OPERATION" comment since it's no longer final
content = content.replace(
    "        # FINAL OPERATION: Intelligent tool filtering\n        # This MUST be last to operate on final message content and ensure accurate token counts",
    "        # Intelligent tool filtering - MUST run before emergency trim\n        # This provides 98% reduction for vLLM, 60-75% for other models"
)

# Write the modified content
with open("src/app_fastapi.py", "w") as f:
    f.write(content)

print("✓ Fixed execution order: emergency trim now runs AFTER tool filtering")