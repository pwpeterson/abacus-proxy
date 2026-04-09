#!/usr/bin/env python3
"""Test script for abacus-proxy request tracking integration.

This script:
1. Enables request tracking temporarily
2. Makes a test request to the chat completions endpoint
3. Verifies data was logged to Supabase
4. Displays the logged request data
"""

import asyncio
import httpx
import json
import os
import sys
from datetime import datetime

import pytest

# Configuration
ABACUS_PROXY_URL = os.getenv("ABACUS_PROXY_URL", "http://localhost:8003")
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:8100")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not SUPABASE_SERVICE_KEY:
    pytest.skip(
        "SUPABASE_SERVICE_KEY not set; skipping request-tracking integration test",
        allow_module_level=True,
    )


async def test_request_tracking():
    """Test the request tracking integration."""
    print("🧪 Testing abacus-proxy request tracking integration\n")
    
    # Step 1: Make a test request to chat completions
    print("📤 Step 1: Sending test request to /v1/chat/completions...")
    
    test_payload = {
        "model": "route-llm",  # This should trigger intelligent routing
        "messages": [
            {
                "role": "user",
                "content": "Write a simple Python function to add two numbers. This is a test request for tracking."
            }
        ],
        "max_tokens": 100,
        "temperature": 0.7
    }
    
    request_id = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ABACUS_PROXY_URL}/v1/chat/completions",
                json=test_payload,
                headers={
                    "Content-Type": "application/json",
                    "x-request-id": request_id
                }
            )
            
            if response.status_code == 200:
                print(f"✅ Request successful (status: {response.status_code})")
                result = response.json()
                print(f"   Model used: {result.get('model', 'unknown')}")
                print(f"   Request ID: {request_id}")
            else:
                print(f"❌ Request failed (status: {response.status_code})")
                print(f"   Response: {response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Error making request: {e}")
        return False
    
    # Step 2: Wait a moment for async logging to complete
    print("\n⏳ Step 2: Waiting 3 seconds for request tracking to complete...")
    await asyncio.sleep(3)
    
    # Step 3: Query Supabase to verify the request was logged
    print("\n🔍 Step 3: Querying Supabase for logged request...")
    
    try:
        assert SUPABASE_SERVICE_KEY is not None

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Query the abacus_proxy_requests table
            supabase_response = await client.get(
                f"{SUPABASE_URL}/rest/v1/abacus_proxy_requests",
                params={
                    "request_id": f"eq.{request_id}",
                    "select": "*"
                },
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "Content-Type": "application/json"
                }
            )
            
            if supabase_response.status_code == 200:
                records = supabase_response.json()
                
                if records:
                    print(f"✅ Found {len(records)} logged request(s)\n")
                    
                    for record in records:
                        print("📊 Request Tracking Data:")
                        print(f"   Request ID: {record.get('request_id')}")
                        print(f"   Timestamp: {record.get('created_at')}")
                        print(f"   Status Code: {record.get('status_code')}")
                        print(f"   Routing Method: {record.get('routing_method')}")
                        print(f"   Requested Model: {record.get('requested_model')}")
                        print(f"   Selected Model: {record.get('selected_model')}")
                        print(f"   Use Case Detected: {record.get('use_case_detected')}")
                        print(f"   Value Index Score: {record.get('value_index_score')}")
                        print(f"   Input Tokens: {record.get('input_tokens')}")
                        print(f"   Output Tokens: {record.get('output_tokens')}")
                        print(f"   Total Tokens: {record.get('total_tokens')}")
                        print(f"   Estimated Cost: ${record.get('estimated_cost_usd', 0):.6f}")
                        print(f"   Duration (ms): {record.get('duration_ms')}")
                    
                    print("\n✅ Request tracking integration is working correctly!")
                    return True
                else:
                    print("⚠️  No records found in Supabase")
                    print("   This could mean:")
                    print("   1. Request tracking is disabled (ENABLE_REQUEST_TRACKING=false)")
                    print("   2. The logger failed silently (check abacus-proxy logs)")
                    print("   3. Supabase credentials are incorrect")
                    return False
            else:
                print(f"❌ Supabase query failed (status: {supabase_response.status_code})")
                print(f"   Response: {supabase_response.text}")
                return False
                
    except Exception as e:
        print(f"❌ Error querying Supabase: {e}")
        return False


async def check_configuration():
    """Check if request tracking is properly configured."""
    print("🔧 Checking configuration...\n")
    
    # Check if abacus-proxy is running
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health_response = await client.get(f"{ABACUS_PROXY_URL}/health")
            if health_response.status_code == 200:
                print(f"✅ abacus-proxy is running at {ABACUS_PROXY_URL}")
            else:
                print(f"⚠️  abacus-proxy health check returned status {health_response.status_code}")
    except Exception as e:
        print(f"❌ Cannot connect to abacus-proxy at {ABACUS_PROXY_URL}")
        print(f"   Error: {e}")
        print("   Make sure abacus-proxy is running before testing.")
        return False
    
    # Check Supabase connectivity
    try:
        assert SUPABASE_SERVICE_KEY is not None

        async with httpx.AsyncClient(timeout=5.0) as client:
            supabase_response = await client.get(
                f"{SUPABASE_URL}/rest/v1/",
                headers={
                    "apikey": SUPABASE_SERVICE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"
                }
            )
            if supabase_response.status_code in [200, 404]:  # 404 is OK for root endpoint
                print(f"✅ Supabase is accessible at {SUPABASE_URL}")
            else:
                print(f"⚠️  Supabase returned status {supabase_response.status_code}")
    except Exception as e:
        print(f"❌ Cannot connect to Supabase at {SUPABASE_URL}")
        print(f"   Error: {e}")
        return False
    
    print("\n📋 Configuration Summary:")
    print(f"   ABACUS_PROXY_URL: {ABACUS_PROXY_URL}")
    print(f"   SUPABASE_URL: {SUPABASE_URL}")
    print(f"   SUPABASE_SERVICE_KEY: {'✅ Set' if SUPABASE_SERVICE_KEY else '❌ Not set'}")
    print()
    
    return True


async def main():
    """Main test function."""
    print("="*70)
    print("  ABACUS-PROXY REQUEST TRACKING INTEGRATION TEST")
    print("="*70)
    print()
    
    # Check configuration first
    if not await check_configuration():
        print("\n❌ Configuration check failed. Please fix the issues above.")
        sys.exit(1)
    
    # Run the test
    success = await test_request_tracking()
    
    print("\n" + "="*70)
    if success:
        print("✅ TEST PASSED: Request tracking is working correctly!")
        print("="*70)
        print("\n📝 Next steps:")
        print("   1. Enable tracking in production: ENABLE_REQUEST_TRACKING=true")
        print("   2. Run analysis queries from docs/REQUEST_TRACKING.md")
        print("   3. Monitor routing effectiveness and cost optimization")
        sys.exit(0)
    else:
        print("❌ TEST FAILED: Request tracking is not working as expected")
        print("="*70)
        print("\n🔍 Troubleshooting:")
        print("   1. Check abacus-proxy logs for errors")
        print("   2. Verify ENABLE_REQUEST_TRACKING=true in config")
        print("   3. Verify Supabase credentials are correct")
        print("   4. Check that migration was applied successfully")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
