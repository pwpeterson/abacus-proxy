#!/usr/bin/env python3
"""Comprehensive Supabase authentication diagnostics.

Tests various scenarios to identify the authentication issue.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from dotenv import load_dotenv
from loguru import logger


def load_env():
    """Load environment variables."""
    env_file = Path(__file__).parent.parent / "abacus-proxy.env"
    if env_file.exists():
        load_dotenv(env_file)
        logger.info(f"Loaded: {env_file}")


def test_endpoint_availability():
    """Test if Supabase endpoint is reachable."""
    logger.info("\n" + "=" * 80)
    logger.info("Test 1: Endpoint Availability")
    logger.info("=" * 80)
    
    url = (os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL") or "").rstrip("/")
    
    if not url:
        logger.error("No SUPABASE_URL found in environment")
        return False
    
    logger.info(f"Testing: {url}")
    
    try:
        # Test base URL (should respond even without auth)
        response = httpx.get(f"{url}/", timeout=3.0)
        logger.info(f"Base URL status: {response.status_code}")
        
        # Test REST API endpoint
        response = httpx.get(f"{url}/rest/v1/", timeout=3.0)
        logger.info(f"REST API status: {response.status_code}")
        
        if response.status_code == 401:
            logger.info("✓ Endpoint is reachable (401 expected without auth)")
            return True
        elif response.status_code == 200:
            logger.info("✓ Endpoint is reachable and accessible")
            return True
        else:
            logger.warning(f"Unexpected status: {response.status_code}")
            logger.debug(f"Response: {response.text[:200]}")
            return True  # Still reachable
            
    except httpx.ConnectError as e:
        logger.error(f"✗ Cannot connect to {url}")
        logger.error(f"Error: {e}")
        logger.error("\nIs Supabase running? Try: docker ps | grep supabase")
        return False
    except Exception as e:
        logger.error(f"✗ Error: {e}")
        return False


def test_with_different_keys():
    """Test with all available key variants."""
    logger.info("\n" + "=" * 80)
    logger.info("Test 2: Test All Available Keys")
    logger.info("=" * 80)
    
    url = (os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL") or "").rstrip("/")
    
    # Collect all possible keys
    keys = {
        "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        "SERVICE_ROLE_KEY": os.getenv("SERVICE_ROLE_KEY"),
        "SUPABASE_SERVICE_KEY": os.getenv("SUPABASE_SERVICE_KEY"),
        "SUPABASE_KEY": os.getenv("SUPABASE_KEY"),
        "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY"),
    }
    
    # Show what we found
    for name, value in keys.items():
        if value:
            logger.info(f"{name}: {len(value)} chars, starts: {value[:15]}...")
        else:
            logger.debug(f"{name}: not set")
    
    # Test each key
    success = False
    for name, key in keys.items():
        if not key:
            continue
            
        logger.info(f"\n--- Testing with {name} ---")
        try:
            response = httpx.get(
                f"{url}/rest/v1/",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                },
                timeout=3.0,
            )
            logger.info(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                logger.info(f"✓ SUCCESS! {name} works!")
                logger.info(f"Use this key: {key[:15]}...{key[-15:]}")
                success = True
                break
            else:
                logger.warning(f"Failed with {response.status_code}: {response.text[:100]}")
                
        except Exception as e:
            logger.error(f"Error testing {name}: {e}")
    
    return success


def test_table_existence():
    """Check if tables exist (using any working key)."""
    logger.info("\n" + "=" * 80)
    logger.info("Test 3: Table Existence Check")
    logger.info("=" * 80)
    
    url = (os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL") or "").rstrip("/")
    
    # Try to find any working key
    keys_to_try = [
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        os.getenv("SERVICE_ROLE_KEY"),
        os.getenv("SUPABASE_ANON_KEY"),
        os.getenv("SUPABASE_KEY"),
    ]
    
    working_key = None
    for key in keys_to_try:
        if not key:
            continue
        try:
            response = httpx.get(
                f"{url}/rest/v1/",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
                timeout=3.0,
            )
            if response.status_code == 200:
                working_key = key
                break
        except:
            continue
    
    if not working_key:
        logger.warning("No working auth key found - skipping table check")
        logger.info("Try manually: curl http://localhost:8100/rest/v1/abacus_proxy_requests")
        return False
    
    logger.info(f"Using working key for table check...")
    
    # Check for our tables
    tables = ["abacus_proxy_requests", "abacus_proxy_request_contexts"]
    
    for table in tables:
        try:
            response = httpx.get(
                f"{url}/rest/v1/{table}?limit=0",
                headers={"apikey": working_key, "Authorization": f"Bearer {working_key}"},
                timeout=3.0,
            )
            if response.status_code == 200:
                logger.info(f"✓ Table exists: {table}")
            elif response.status_code == 404:
                logger.error(f"✗ Table not found: {table}")
                logger.error("Run migration: psql -f supabase/migrations/*.sql")
            else:
                logger.warning(f"Table {table}: status {response.status_code}")
        except Exception as e:
            logger.error(f"Error checking {table}: {e}")
    
    return True


def suggest_fixes():
    """Provide actionable suggestions."""
    logger.info("\n" + "=" * 80)
    logger.info("Suggested Fixes")
    logger.info("=" * 80)
    
    logger.info("""
1. If Supabase is NOT running:
   cd /path/to/supabase/project
   supabase start
   # or
   docker-compose up -d

2. If Supabase IS running, get the correct service_role key:
   supabase status | grep "service_role key"
   # Copy the key shown there

3. Update abacus-proxy.env:
   SUPABASE_SERVICE_ROLE_KEY=eyJ... (paste the key from step 2)

4. If using remote/hosted Supabase:
   - Go to dashboard: https://supabase.com/dashboard
   - Select project → Settings → API
   - Copy the "service_role" key (NOT "anon" key)
   - Paste into SUPABASE_SERVICE_ROLE_KEY

5. Verify the key format:
   - Should be JWT format (starts with "eyJ")
   - Should be 200+ characters
   - Should contain dots: xxx.yyy.zzz

6. If tables don't exist:
   psql -h localhost -p 54322 -U postgres -d postgres -f supabase/migrations/20260406_create_abacus_proxy_request_tracking.sql
""")


def main():
    """Run all diagnostics."""
    logger.info("=" * 80)
    logger.info("Supabase Authentication Diagnostic Tool")
    logger.info("=" * 80)
    
    load_env()
    
    # Run tests
    endpoint_ok = test_endpoint_availability()
    if not endpoint_ok:
        logger.error("\n✗ Supabase endpoint not reachable - fix this first!")
        suggest_fixes()
        sys.exit(1)
    
    auth_ok = test_with_different_keys()
    if auth_ok:
        logger.info("\n✓ Found working authentication!")
        test_table_existence()
        sys.exit(0)
    else:
        logger.error("\n✗ No working authentication keys found")
        test_table_existence()  # Try anyway
        suggest_fixes()
        sys.exit(1)


if __name__ == "__main__":
    main()
