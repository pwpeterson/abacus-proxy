#!/usr/bin/env python3
"""Fix syntax error from previous script."""

with open("src/app_fastapi.py", "r") as f:
    lines = f.readlines()

# Fix line 1020 (index 1019) - incorrect indentation
if len(lines) > 1019 and "else:" in lines[1019]:
    lines[1019] = "        else:\n"
    print(f"Fixed line 1020: {lines[1019].rstrip()}")

with open("src/app_fastapi.py", "w") as f:
    f.writelines(lines)

print("✓ Fixed syntax error on line 1020")
