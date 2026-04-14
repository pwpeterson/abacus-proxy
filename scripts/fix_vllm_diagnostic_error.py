#!/usr/bin/env python3
"""Fix the UnboundLocalError in vLLM diagnostic logging."""

import sys

def fix_diagnostic_error():
    with open("src/app_fastapi.py", "r") as f:
        content = f.read()
    
    # Replace the diagnostic code that uses undefined 'messages' variable
    old_code = '''            # DEBUG: Diagnose why trimmer isn't running
            logger.info(f"[VLLM PRE-TRIM] Messages count: {len(messages)}")
            if messages:
                first_msg = messages[0]
                logger.info(f"[VLLM PRE-TRIM] First message role: {first_msg.get('role') if isinstance(first_msg, dict) else 'NOT_DICT'}")
                logger.info(f"[VLLM PRE-TRIM] First message content type: {type(first_msg.get('content')) if isinstance(first_msg, dict) else 'N/A'}")
                if isinstance(first_msg, dict):
                    content = first_msg.get('content', '')
                    logger.info(f"[VLLM PRE-TRIM] Content length: {len(str(content))} chars")
            '''
    
    # Fixed code that uses payload.get("messages", [])
    new_code = '''            # DEBUG: Diagnose why trimmer isn't running
            _diag_messages = payload.get("messages", [])
            logger.info(f"[VLLM PRE-TRIM] Messages count: {len(_diag_messages)}")
            if _diag_messages:
                first_msg = _diag_messages[0]
                logger.info(f"[VLLM PRE-TRIM] First message role: {first_msg.get('role') if isinstance(first_msg, dict) else 'NOT_DICT'}")
                logger.info(f"[VLLM PRE-TRIM] First message content type: {type(first_msg.get('content')) if isinstance(first_msg, dict) else 'N/A'}")
                if isinstance(first_msg, dict):
                    content = first_msg.get('content', '')
                    logger.info(f"[VLLM PRE-TRIM] Content length: {len(str(content))} chars")
            '''
    
    if old_code not in content:
        print("Error: Could not find diagnostic code to fix")
        return False
    
    content = content.replace(old_code, new_code)
    
    with open("src/app_fastapi.py", "w") as f:
        f.write(content)
    
    print("✅ Fixed UnboundLocalError in vLLM diagnostic logging")
    print("   Changed 'messages' → '_diag_messages = payload.get(\"messages\", [])'")
    return True

if __name__ == "__main__":
    success = fix_diagnostic_error()
    sys.exit(0 if success else 1)
