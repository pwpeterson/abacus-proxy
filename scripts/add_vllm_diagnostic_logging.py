#!/usr/bin/env python3
"""Add diagnostic logging before vLLM trimmer to debug why it's not running."""

import sys

def add_vllm_diagnostics():
    with open("src/app_fastapi.py", "r") as f:
        lines = f.readlines()
    
    # Find the line "# Apply vLLM system message trimming (14K token budget)"
    insert_index = None
    for i, line in enumerate(lines):
        if "# Apply vLLM system message trimming (14K token budget)" in line:
            insert_index = i
            break
    
    if insert_index is None:
        print("Error: Could not find vLLM trimming section")
        return False
    
    # Add diagnostic logging right after the comment
    diagnostic_code = '''            # DEBUG: Diagnose why trimmer isn't running
            logger.info(f"[VLLM PRE-TRIM] Messages count: {len(messages)}")
            if messages:
                first_msg = messages[0]
                logger.info(f"[VLLM PRE-TRIM] First message role: {first_msg.get('role') if isinstance(first_msg, dict) else 'NOT_DICT'}")
                logger.info(f"[VLLM PRE-TRIM] First message content type: {type(first_msg.get('content')) if isinstance(first_msg, dict) else 'N/A'}")
                if isinstance(first_msg, dict):
                    content = first_msg.get('content', '')
                    logger.info(f"[VLLM PRE-TRIM] Content length: {len(str(content))} chars")
            
'''
    
    # Insert after the comment line
    lines.insert(insert_index + 1, diagnostic_code)
    
    with open("src/app_fastapi.py", "w") as f:
        f.writelines(lines)
    
    print("✅ Added vLLM diagnostic logging")
    print(f"   Location: Line {insert_index + 1}")
    return True

if __name__ == "__main__":
    success = add_vllm_diagnostics()
    sys.exit(0 if success else 1)
