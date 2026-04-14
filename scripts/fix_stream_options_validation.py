#!/usr/bin/env python3
"""Fix vLLM validation error: stream_options requires stream=True.

When converting streaming requests to non-streaming, remove stream_options parameter.
"""

import sys

def apply_fix():
    """Apply the stream_options fix to app_fastapi.py"""
    
    file_path = "src/app_fastapi.py"
    
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    # Find the section where we set stream=False (around line 1083)
    modified = False
    for i, line in enumerate(lines):
        # Look for: payload_nostream["stream"] = False
        if 'payload_nostream["stream"] = False' in line:
            # Insert line to remove stream_options right after
            indent = len(line) - len(line.lstrip())
            new_line = ' ' * indent + 'payload_nostream.pop("stream_options", None)  # Remove to avoid vLLM validation error\n'
            lines.insert(i + 1, new_line)
            modified = True
            print(f"✓ Inserted stream_options removal at line {i + 2}")
            break
    
    if not modified:
        print("❌ Could not find target line to modify", file=sys.stderr)
        return False
    
    # Write back
    with open(file_path, 'w') as f:
        f.writelines(lines)
    
    print(f"✓ Fixed {file_path}")
    return True

if __name__ == "__main__":
    success = apply_fix()
    sys.exit(0 if success else 1)
