#!/usr/bin/env python3
"""Add diagnostic logging to see why tool filtering isn't running."""

import re

# Read the file
with open('src/app_fastapi.py', 'r') as f:
    content = f.read()

# Find the tool filtering block and add diagnostic logging BEFORE the check
# This will help us understand what's actually in the system message

# Pattern to match the start of the filtering block
pattern = r'(        messages = payload\.get\("messages", \[\]\)\n        if isinstance\(messages, list\):\n            for msg in messages:\n                if isinstance\(msg, dict\) and msg\.get\("role"\) == "system":\n                    content = msg\.get\("content", ""\))'

replacement = r'''\1
                    # DEBUG: Log what we're seeing
                    logger.info(
                        f"[TOOL FILTER DEBUG] Found system message: "
                        f"len={len(content) if isinstance(content, str) else 'N/A'}, "
                        f"has_MCP_SERVERS={'MCP SERVERS' in content if isinstance(content, str) else False}, "
                        f"content_type={type(content).__name__}"
                    )'''

content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

# Write back
with open('src/app_fastapi.py', 'w') as f:
    f.write(content)

print("✅ Added diagnostic logging to tool filtering")