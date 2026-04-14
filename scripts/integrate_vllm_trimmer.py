#!/usr/bin/env python3
"""Integrate vLLM system trimmer into chat completions endpoint."""

import re

def integrate_vllm_trimmer():
    """Add vLLM system message trimming to the chat completions flow."""
    
    with open("src/app_fastapi.py", "r") as f:
        content = f.read()
    
    # 1. Add import at the top (after other src.utils imports)
    import_pattern = r'(from src\.utils\.performance_middleware import create_performance_middleware)'
    import_replacement = r'''\1
from src.utils.vllm_system_trimmer import trim_system_for_vllm'''
    
    if "from src.utils.vllm_system_trimmer import trim_system_for_vllm" not in content:
        content = re.sub(import_pattern, import_replacement, content, count=1)
        print("✅ Added vllm_system_trimmer import")
    else:
        print("ℹ️  vllm_system_trimmer already imported")
    
    # 2. Add trimming logic right after determining vLLM routing
    # Find the section where we set upstream_model for vLLM
    trim_pattern = r'''(        # Determine which upstream API to use based on requested model
        use_vllm = requested_model == config\.vllm_model_id
        if use_vllm:
            api_base = _normalize_api_base_url\(config\.vllm_api_base_url\)
            # Translate route-vllm to actual vLLM model name
            upstream_model = config\.vllm_target_model
            logger\.info\(f"vLLM routing: requested=\{requested_model\} -> upstream=\{upstream_model\}"\))'''
    
    trim_replacement = r'''\1
            
            # Apply vLLM system message trimming (14K token budget)
            messages = payload.get("messages", [])
            if messages and isinstance(messages, list) and messages:
                first_msg = messages[0]
                if isinstance(first_msg, dict) and first_msg.get("role") == "system":
                    original_content = first_msg.get("content", "")
                    trimmed_content = trim_system_for_vllm(original_content)
                    
                    if trimmed_content != original_content:
                        # Create new message dict to avoid mutating original
                        new_first_msg = dict(first_msg)
                        new_first_msg["content"] = trimmed_content
                        
                        # Replace in payload
                        new_messages = [new_first_msg] + messages[1:]
                        payload["messages"] = new_messages
                        
                        logger.info(
                            f"[VLLM TRIM] System message trimmed for vLLM: "
                            f"{len(str(original_content))} → {len(str(trimmed_content))} chars"
                        )'''
    
    if "[VLLM TRIM]" not in content:
        content = re.sub(trim_pattern, trim_replacement, content, count=1, flags=re.DOTALL)
        print("✅ Added vLLM system trimming logic")
    else:
        print("ℹ️  vLLM trimming logic already present")
    
    with open("src/app_fastapi.py", "w") as f:
        f.write(content)
    
    print("\n✅ Integration complete!")
    print("  - System messages will be trimmed to ~40K chars (~10K tokens) for vLLM")
    print("  - Trimming preserves core identity, tool use, and operational rules")
    print("  - Strips verbose examples and mode-specific deep dives")

if __name__ == "__main__":
    integrate_vllm_trimmer()