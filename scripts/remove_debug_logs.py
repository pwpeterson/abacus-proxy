#!/usr/bin/env python3
"""Remove debug logging statements added for troubleshooting."""

import sys

def apply_fix():
    """Remove debug logs from app_fastapi.py"""
    
    file_path = "src/app_fastapi.py"
    
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    # Remove lines 1099-1114 (the debug logging block)
    # First, identify the section
    new_lines = []
    skip_mode = False
    
    for i, line in enumerate(lines, 1):
        # Start skipping at "# DEBUG: Log vLLM's full response"
        if "# DEBUG: Log vLLM's full response" in line:
            skip_mode = True
            continue
        
        # Stop skipping after the empty content warning
        if skip_mode and 'logger.warning("[VLLM DEBUG] Empty content in vLLM response!")' in line:
            skip_mode = False
            continue
        
        if not skip_mode:
            new_lines.append(line)
    
    # Write back
    with open(file_path, 'w') as f:
        f.writelines(new_lines)
    
    print(f"✓ Removed debug logging from {file_path}")
    removed_count = len(lines) - len(new_lines)
    print(f"✓ Removed {removed_count} lines")
    return True

if __name__ == "__main__":
    success = apply_fix()
    sys.exit(0 if success else 1)
