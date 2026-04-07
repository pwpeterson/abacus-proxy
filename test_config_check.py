#!/usr/bin/env python3
"""Quick test to verify Supabase configuration is loaded correctly."""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv

# Load .env file
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    load_dotenv(env_file)
    print(f"✓ Loaded .env from {env_file}")
else:
    print(f"✗ .env file not found at {env_file}")

# Check for Supabase configuration
print("\n=== Supabase Configuration Check ===")

supabase_url = os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL") or ""
print(f"SUPABASE_URL: {supabase_url[:50] if supabase_url else '(not set)'}")

# Check all possible service key variable names
key_vars = [
    "SUPABASE_SERVICE_ROLE_KEY",
    "SERVICE_ROLE_KEY",
    "SUPABASE_KEY",
    "SUPABASE_SERVICE_KEY",
]

found_key = None
for var_name in key_vars:
    value = os.getenv(var_name)
    if value:
        found_key = var_name
        print(f"{var_name}: {value[:20]}... (found!)")
    else:
        print(f"{var_name}: (not set)")

print("\n=== Request Tracking Configuration ===")
enable_tracking = os.getenv("ENABLE_REQUEST_TRACKING", "false").lower() == "true"
print(f"ENABLE_REQUEST_TRACKING: {enable_tracking}")

if enable_tracking:
    if supabase_url and found_key:
        print("\n✓ Request tracking should be ENABLED")
        print(f"  - URL configured: {supabase_url}")
        print(f"  - Key found in: {found_key}")
    else:
        print("\n✗ Request tracking will be DISABLED")
        if not supabase_url:
            print("  - Missing SUPABASE_URL")
        if not found_key:
            print("  - Missing service role key (checked all variants)")
else:
    print("\n- Request tracking is disabled in config")
