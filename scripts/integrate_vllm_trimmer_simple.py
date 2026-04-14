#!/usr/bin/env python3
"""Integrate vLLM system trimmer into chat completions endpoint."""

def integrate_vllm_trimmer():
    """Add vLLM system message trimming to the chat completions flow."""
    
    with open("src/app_fastapi.py", "r") as f:
        lines = f.readlines()
    
    # 1. Add import after performance_middleware import
    import_added = False
    for i, line in enumerate(lines):
        if "from src.utils.performance_middleware import create_performance_middleware" in line:
            if i + 1 < len(lines) and "from src.utils.vllm_system_trimmer" not in lines[i + 1]:
                lines.insert(i + 1, "from src.utils.vllm_system_trimmer import trim_system_for_vllm\n")
                import_added = True
                print("✅ Added vllm_system_trimmer import")
            break
    
    if not import_added:
        print("ℹ️  Import already exists or performance_middleware import not found")
    
    # 2. Find vLLM routing section and add trimming logic
    trimming_added = False
    for i, line in enumerate(lines):
        # Look for the specific log line that indicates vLLM routing
        if 'logger.info(f"vLLM routing: requested={requested_model} -> upstream={upstream_model}")' in line:
            # Check if trimming logic already exists
            if i + 1 < len(lines) and "[VLLM TRIM]" in "".join(lines[i:i+20]):
                print("ℹ️  vLLM trimming logic already present")
                trimming_added = True
                break
            
            # Insert trimming logic right after this log line
            trimming_code = '''            
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
                        )
'''
            lines.insert(i + 1, trimming_code)
            trimming_added = True
            print("✅ Added vLLM system trimming logic")
            break
    
    if not trimming_added:
        print("⚠️  Could not find vLLM routing section to insert trimming logic")
        return
    
    # Write the modified file
    with open("src/app_fastapi.py", "w") as f:
        f.writelines(lines)
    
    print("\n✅ Integration complete!")
    print("  - System messages will be trimmed to ~40K chars (~10K tokens) for vLLM")
    print("  - Trimming preserves core identity, tool use, and operational rules")
    print("  - Strips verbose examples and mode-specific deep dives")

if __name__ == "__main__":
    integrate_vllm_trimmer()