#!/usr/bin/env python3
"""Direct Supabase connection test - verify authentication and table access.

This script tests the Supabase connection directly using httpx to identify
authentication and table access issues.
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
        logger.info(f"Loaded environment from: {env_file}")
    else:
        logger.warning(f"Environment file not found: {env_file}")


def test_supabase_connection():
    """Test direct connection to Supabase PostgREST API."""
    logger.info("\n" + "=" * 80)
    logger.info("Testing Direct Supabase Connection")
    logger.info("=" * 80)

    # Get config
    url = os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL")
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_KEY")
    )

    if not url or not key:
        logger.error("Missing SUPABASE_URL or service key in environment")
        return False

    url = url.rstrip("/")
    logger.info(f"URL: {url}")
    logger.info(f"Key length: {len(key)}")
    logger.info(f"Key starts with: {key[:10]}...")

    # Test 1: List tables (should work with any valid key)
    logger.info("\n--- Test 1: List all tables ---")
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{url}/rest/v1/",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                },
            )
            logger.info(f"Status: {response.status_code}")
            if response.status_code == 200:
                logger.info("✓ Successfully connected to Supabase")
                logger.debug(f"Response: {response.text[:500]}")
            else:
                logger.error(f"✗ Failed: {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False
    except Exception as e:
        logger.error(f"✗ Connection error: {e}")
        return False

    # Test 2: Query abacus_proxy_requests table
    logger.info("\n--- Test 2: Query abacus_proxy_requests table ---")
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{url}/rest/v1/abacus_proxy_requests?select=count",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Prefer": "count=exact",
                },
            )
            logger.info(f"Status: {response.status_code}")
            if response.status_code == 200:
                logger.info("✓ Successfully queried abacus_proxy_requests")
                # Check Content-Range header for count
                content_range = response.headers.get("content-range", "")
                logger.info(f"Content-Range: {content_range}")
                logger.debug(f"Response: {response.text[:500]}")
            else:
                logger.error(f"✗ Failed: {response.status_code}")
                logger.error(f"Response: {response.text}")
                if response.status_code == 404:
                    logger.error(
                        "Table not found! Run migration: "
                        "supabase db reset or apply migration manually"
                    )
                return False
    except Exception as e:
        logger.error(f"✗ Query error: {e}")
        return False

    # Test 3: Insert a test record
    logger.info("\n--- Test 3: Insert test record ---")
    test_record = {
        "request_id": "test-connection-check",
        "provider": "test",
        "model": "test-model",
        "status_code": 200,
        "request_timestamp": "2026-04-09T01:00:00Z",
    }
    
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.post(
                f"{url}/rest/v1/abacus_proxy_requests",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json=test_record,
            )
            logger.info(f"Status: {response.status_code}")
            if response.status_code in (200, 201):
                logger.info("✓ Successfully inserted test record")
                logger.debug(f"Response: {response.text}")
            else:
                logger.error(f"✗ Insert failed: {response.status_code}")
                logger.error(f"Response: {response.text}")
                
                if response.status_code == 401:
                    logger.error("\nAuthentication failed!")
                    logger.error("Possible causes:")
                    logger.error("  1. Service role key is incorrect")
                    logger.error("  2. Key is anon key instead of service_role key")
                    logger.error("  3. Supabase instance restarted with new keys")
                    logger.error("\nTo get correct key:")
                    logger.error("  Run: supabase status")
                    logger.error("  Look for: service_role key")
                elif response.status_code == 403:
                    logger.error("\nPermission denied!")
                    logger.error("RLS policies may be too restrictive")
                
                return False
    except Exception as e:
        logger.error(f"✗ Insert error: {e}")
        return False

    # Test 4: Query the inserted record
    logger.info("\n--- Test 4: Query inserted record ---")
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                f"{url}/rest/v1/abacus_proxy_requests?request_id=eq.test-connection-check",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                },
            )
            logger.info(f"Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                if data:
                    logger.info(f"✓ Successfully retrieved record: {data[0].get('request_id')}")
                else:
                    logger.warning("Record not found (may have been deleted)")
            else:
                logger.error(f"✗ Query failed: {response.status_code}")
                logger.error(f"Response: {response.text}")
    except Exception as e:
        logger.error(f"✗ Query error: {e}")

    # Test 5: Clean up - delete test record
    logger.info("\n--- Test 5: Cleanup test record ---")
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.delete(
                f"{url}/rest/v1/abacus_proxy_requests?request_id=eq.test-connection-check",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {key}",
                },
            )
            logger.info(f"Status: {response.status_code}")
            if response.status_code in (200, 204):
                logger.info("✓ Successfully cleaned up test record")
            else:
                logger.warning(f"Cleanup status: {response.status_code}")
    except Exception as e:
        logger.warning(f"Cleanup error (non-critical): {e}")

    return True


def main():
    """Run connection test."""
    logger.info("=" * 80)
    logger.info("Supabase Connection Diagnostic Tool")
    logger.info("=" * 80)

    load_env()

    success = test_supabase_connection()

    logger.info("\n" + "=" * 80)
    if success:
        logger.info("✓ All connection tests passed!")
        logger.info("=" * 80)
        logger.info("\nYour Supabase connection is working correctly.")
        logger.info("If request_logger is still failing, check:")
        logger.info("  1. Headers being sent in request_logger._headers()")
        logger.info("  2. URL construction in request_logger._postgrest_table_url()")
        sys.exit(0)
    else:
        logger.error("✗ Connection tests failed")
        logger.error("=" * 80)
        logger.error("\nPlease fix the issues above and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
