#!/usr/bin/env python3
"""Test script to insert test records into Supabase abacus-proxy request tracking tables.

This script verifies that the request logger can successfully insert records into:
- public.abacus_proxy_requests
- public.abacus_proxy_request_contexts

Usage:
    python test/test_supabase_insert.py
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from loguru import logger

from src.utils.request_logger import AbacusRequestLogger


def load_env_file():
    """Load environment variables from abacus-proxy.env."""
    env_file = Path(__file__).parent.parent / "abacus-proxy.env"
    if env_file.exists():
        logger.info(f"Loading environment from: {env_file}")
        load_dotenv(env_file)
    else:
        logger.warning(f"Environment file not found: {env_file}")


def test_basic_insert():
    """Test basic request logging with minimal data."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 1: Basic Request Insert")
    logger.info("=" * 80)

    request_logger = AbacusRequestLogger(
        enabled=True,
        supabase_url=os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL"),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SERVICE_ROLE_KEY"),
    )

    request_id = f"test-basic-{uuid4()}"
    request_payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello, this is a test!"}],
    }
    response_payload = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "This is a test response."},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }

    result = request_logger.log_request(
        request_id=request_id,
        request_payload=request_payload,
        response_payload=response_payload,
        status_code=200,
        app_id="test-app",
        provider_name="routellm",
        model="gpt-4o",
        duration_ms=250,
    )

    logger.info(f"✓ Basic insert completed. Request ID: {result}")
    return result is not None


def test_routing_intelligence_insert():
    """Test request logging with intelligent routing metadata."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 2: Intelligent Routing Metadata Insert")
    logger.info("=" * 80)

    request_logger = AbacusRequestLogger(
        enabled=True,
        supabase_url=os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL"),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SERVICE_ROLE_KEY"),
    )

    request_id = f"test-routing-{uuid4()}"
    request_payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": "Write a Python function to calculate fibonacci numbers.",
            }
        ],
    }
    response_payload = {
        "id": "chatcmpl-test-routing",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "qwen/qwen-2.5-coder-32b-instruct",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 25, "completion_tokens": 35, "total_tokens": 60},
    }

    result = request_logger.log_request(
        request_id=request_id,
        request_payload=request_payload,
        response_payload=response_payload,
        status_code=200,
        app_id="test-app",
        provider_name="routellm",
        model="qwen/qwen-2.5-coder-32b-instruct",
        duration_ms=450,
        # Intelligent routing metadata
        routing_method="intelligent",
        requested_model="gpt-4o",
        selected_model="qwen/qwen-2.5-coder-32b-instruct",
        value_index_score=85,
        use_case_detected="coding",
        metadata={
            "router_confidence": 0.92,
            "alternative_models": ["claude-3-5-sonnet", "gpt-4o-mini"],
        },
    )

    logger.info(f"✓ Routing intelligence insert completed. Request ID: {result}")
    return result is not None


def test_context_condensation_insert():
    """Test request logging with context condensation analysis."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 3: Context Condensation Analysis Insert")
    logger.info("=" * 80)

    request_logger = AbacusRequestLogger(
        enabled=True,
        supabase_url=os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL"),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SERVICE_ROLE_KEY"),
    )

    request_id = f"test-condensation-{uuid4()}"

    # Simulate a large context that gets condensed
    raw_context = """
    # Project Context (Raw)
    
    This is a large project with many files:
    - src/main.py (500 lines)
    - src/utils/helper.py (300 lines)
    - src/models/user.py (200 lines)
    - src/api/routes.py (400 lines)
    - tests/test_main.py (350 lines)
    
    Dependencies:
    - fastapi==0.104.1
    - pydantic==2.5.0
    - uvicorn==0.24.0
    - sqlalchemy==2.0.23
    
    Recent changes:
    - Updated authentication flow
    - Added new API endpoints
    - Refactored database models
    """.strip()

    condensed_context = """
    # Project Context (Condensed via mem0)
    
    Python FastAPI project. Recent auth changes + new endpoints + DB refactor.
    Key deps: fastapi 0.104, pydantic 2.5, sqlalchemy 2.0
    """.strip()

    request_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "system", "content": condensed_context},
            {"role": "user", "content": "Help me add a new user registration endpoint."},
        ],
    }

    response_payload = {
        "id": "msg-test-condensation",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "I'll help you add a user registration endpoint. Based on your FastAPI setup...",
            }
        ],
        "model": "claude-3-5-sonnet-20241022",
        "usage": {"input_tokens": 120, "output_tokens": 45},
    }

    # Try to count tokens, but gracefully handle missing tiktoken
    raw_token_count = None
    condensed_token_count = None
    try:
        raw_token_count = request_logger.count_text_tokens(
            raw_context, model="claude-3-5-sonnet"
        )
        condensed_token_count = request_logger.count_text_tokens(
            condensed_context, model="claude-3-5-sonnet"
        )
        logger.debug(
            f"Token counts: raw={raw_token_count}, condensed={condensed_token_count}"
        )
    except ModuleNotFoundError:
        logger.warning(
            "tiktoken not available - token counting disabled. "
            "Install with: uv sync --extra memory"
        )
        # Use character count as approximate fallback
        raw_token_count = len(raw_context) // 4  # Rough estimate: 4 chars per token
        condensed_token_count = len(condensed_context) // 4

    result = request_logger.log_request(
        request_id=request_id,
        request_payload=request_payload,
        response_payload=response_payload,
        status_code=200,
        app_id="test-app",
        provider_name="anthropic",
        model="claude-3-5-sonnet",
        duration_ms=850,
        routing_method="intelligent",
        requested_model="claude-3-5-sonnet",
        selected_model="claude-3-5-sonnet",
        use_case_detected="architecting",
        # Context condensation metadata
        raw_request_context=raw_context,
        condensed_request_context=condensed_context,
        condensation_method="mem0",
        condensation_version="1.0",
        condensation_trigger="threshold",
        raw_context_token_count=raw_token_count,
        condensed_context_token_count=condensed_token_count,
    )

    logger.info(f"✓ Context condensation insert completed. Request ID: {result}")
    return result is not None


def test_error_case_insert():
    """Test request logging with error scenarios."""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 4: Error Case Insert")
    logger.info("=" * 80)

    request_logger = AbacusRequestLogger(
        enabled=True,
        supabase_url=os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL"),
        supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SERVICE_ROLE_KEY"),
    )

    request_id = f"test-error-{uuid4()}"
    request_payload = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "This will trigger an error."}],
    }

    result = request_logger.log_request(
        request_id=request_id,
        request_payload=request_payload,
        response_payload=None,
        status_code=503,
        error_message="Service temporarily unavailable",
        app_id="test-app",
        provider_name="routellm",
        model="gpt-4o",
        duration_ms=5000,
        routing_method="fallback",
        requested_model="gpt-4o",
        selected_model=None,
        metadata={"error_type": "timeout", "retry_attempted": True},
    )

    logger.info(f"✓ Error case insert completed. Request ID: {result}")
    return result is not None


def verify_connection():
    """Verify Supabase connection is properly configured."""
    logger.info("\n" + "=" * 80)
    logger.info("Verifying Supabase Connection")
    logger.info("=" * 80)

    url = os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_PUBLIC_URL")
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_SERVICE_KEY")
    )

    logger.info(f"SUPABASE_URL: {url}")
    logger.info(f"Service key present: {bool(key)}")
    logger.info(f"Service key length: {len(key) if key else 0}")
    
    # Show which env var was used
    if key:
        if os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
            logger.info("Using: SUPABASE_SERVICE_ROLE_KEY")
        elif os.getenv("SERVICE_ROLE_KEY"):
            logger.info("Using: SERVICE_ROLE_KEY")
        elif os.getenv("SUPABASE_SERVICE_KEY"):
            logger.info("Using: SUPABASE_SERVICE_KEY")
        
        # Show first/last 10 chars for debugging (masked middle)
        if len(key) > 20:
            masked = f"{key[:10]}...{key[-10:]}"
            logger.info(f"Service key (masked): {masked}")

    if not url or not key:
        logger.error("❌ Supabase connection not configured!")
        logger.error("Please ensure these environment variables are set:")
        logger.error("  - SUPABASE_URL or SUPABASE_PUBLIC_URL")
        logger.error("  - SUPABASE_SERVICE_ROLE_KEY or SERVICE_ROLE_KEY")
        return False

    logger.info("✓ Supabase connection configured")
    logger.warning(
        "\nNote: If you get 401 errors, verify your service role key is correct."
    )
    logger.warning(
        "The key should start with 'eyJ' and be ~200+ characters long (JWT format)."
    )
    return True


def main():
    """Run all test inserts."""
    logger.info("=" * 80)
    logger.info("Abacus Proxy Supabase Request Tracking - Test Insert Script")
    logger.info("=" * 80)

    # Load environment
    load_env_file()

    # Verify connection
    if not verify_connection():
        logger.error("\n❌ Tests aborted due to missing configuration")
        sys.exit(1)

    # Run tests
    results = {
        "basic_insert": test_basic_insert(),
        "routing_intelligence": test_routing_intelligence_insert(),
        "context_condensation": test_context_condensation_insert(),
        "error_case": test_error_case_insert(),
    }

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("Test Summary")
    logger.info("=" * 80)

    passed = sum(results.values())
    total = len(results)

    for test_name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        logger.info(f"{status}: {test_name}")

    logger.info("\n" + "=" * 80)
    logger.info(f"Results: {passed}/{total} tests passed")
    logger.info("=" * 80)

    if passed == total:
        logger.info("✓ All tests passed!")
        logger.info("\nNote: Check logs above for any 401 authentication errors.")
        logger.info(
            "If inserts failed with 401, verify SUPABASE_SERVICE_ROLE_KEY is correct."
        )
        sys.exit(0)
    else:
        logger.error(f"✗ {total - passed} test(s) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
