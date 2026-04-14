"""Strip tools array for vLLM requests to save tokens.

vLLM doesn't use the tools array (requires --enable-auto-tool-choice),
so it just wastes ~29K tokens of the 32K budget.
"""

import re

# Read app_fastapi.py
with open("src/app_fastapi.py", "r") as f:
    content = f.read()

# Find the vLLM system trimming section and add tools stripping after it
# Insert after line 1051 (end of system trimming debug logs)

insert_code = '''
            # Strip tools array for vLLM (saves ~29K tokens)
            # vLLM doesn't support tool calling without --enable-auto-tool-choice
            tools = payload.get("tools")
            if tools and isinstance(tools, list):
                tools_count = len(tools)
                payload.pop("tools", None)
                payload.pop("tool_choice", None)  # Also remove tool_choice
                logger.info(f"[VLLM TRIM] Stripped {tools_count} tools from request (saves ~{tools_count * 150} tokens)")
'''

# Find the position after the debug logs (around line 1051)
pattern = r"(logger\.debug\(f\"\[VLLM DEBUG\] System message preview:.*?\n)"

replacement = r"\1" + insert_code

content_new = re.sub(pattern, replacement, content, count=1)

if content_new == content:
    print("❌ Failed to find insertion point")
    exit(1)

# Write back
with open("src/app_fastapi.py", "w") as f:
    f.write(content_new)

print("✅ Added tools stripping for vLLM requests")
print("📍 Location: After system message debug logs (around line 1051)")
