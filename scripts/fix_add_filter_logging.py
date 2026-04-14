#!/usr/bin/env python3
"""Add logging to tool filtering block to verify it's running."""

import re

# Read the file
with open('src/app_fastapi.py', 'r') as f:
    content = f.read()

# Find the tool filtering block and add logging
# We'll add logging after line 1038 (after filter_tools_for_vllm call)
# and after line 1050 (after filter_tools_by_category call)

# Pattern to match the vLLM filtering line and add logging after it
vllm_pattern = r"(                            msg\[\"content\"\] = filter_tools_for_vllm\(content\))"
vllm_replacement = r'''\1
                            orig_len = len(content)
                            new_len = len(msg["content"])
                            reduction_pct = ((orig_len - new_len) / orig_len * 100) if orig_len > 0 else 0
                            logger.info(
                                f"[TOOL FILTER] vLLM: {orig_len} → {new_len} chars ({reduction_pct:.1f}% reduction)"
                            )'''

content = re.sub(vllm_pattern, vllm_replacement, content)

# Pattern to match the category filtering line and add logging after it
category_pattern = r"(                            msg\[\"content\"\] = filter_tools_by_category\(content, categories\))"
category_replacement = r'''\1
                            orig_len = len(content)
                            new_len = len(msg["content"])
                            reduction_pct = ((orig_len - new_len) / orig_len * 100) if orig_len > 0 else 0
                            logger.info(
                                f"[TOOL FILTER] {use_case}: {orig_len} → {new_len} chars "
                                f"({reduction_pct:.1f}% reduction, categories={categories})"
                            )'''

content = re.sub(category_pattern, category_replacement, content)

# Write back
with open('src/app_fastapi.py', 'w') as f:
    f.write(content)

print("✅ Added logging to tool filtering blocks")