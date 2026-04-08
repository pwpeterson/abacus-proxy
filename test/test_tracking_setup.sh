#!/bin/bash
# Quick setup and test script for request tracking

set -e

echo "🚀 Setting up request tracking test environment"
echo "================================================"
echo ""

# Check if abacus-proxy is running
if ! curl -s http://localhost:8003/health > /dev/null 2>&1; then
    echo "❌ Error: abacus-proxy is not running at http://localhost:8003"
    echo "   Please start abacus-proxy first:"
    echo "   podman start abacus-proxy"
    echo "   OR"
    echo "   cd /home/pwp/srv/apps/abacus-proxy && uv run python src/main_fastapi.py"
    exit 1
fi

echo "✅ abacus-proxy is running"
echo ""

# Check if Supabase is running
if ! curl -s http://localhost:8100/rest/v1/ > /dev/null 2>&1; then
    echo "❌ Error: Supabase is not running at http://localhost:8100"
    echo "   Please start Supabase first"
    exit 1
fi

echo "✅ Supabase is running"
echo ""

# Temporarily enable request tracking for testing
echo "📝 Enabling request tracking for test..."
export ENABLE_REQUEST_TRACKING=true
export SUPABASE_URL=http://localhost:8100
export SUPABASE_SERVICE_ROLE_KEY="${SUPABASE_SERVICE_KEY}"

echo "✅ Request tracking enabled"
echo ""

# Note: The test will use environment variables, but abacus-proxy needs to be restarted
# with ENABLE_REQUEST_TRACKING=true for the integration to actually work

echo "⚠️  IMPORTANT: For the test to work, abacus-proxy must be running with:"
echo "   ENABLE_REQUEST_TRACKING=true"
echo ""
echo "   You can either:"
echo "   1. Add to .env: ENABLE_REQUEST_TRACKING=true"
echo "   2. Restart abacus-proxy: podman restart abacus-proxy"
echo ""
echo "   OR run directly with:"
echo "   ENABLE_REQUEST_TRACKING=true uv run python src/main_fastapi.py"
echo ""

read -p "Press Enter to run the test (make sure tracking is enabled)..."

echo ""
echo "🧪 Running request tracking test..."
echo "================================================"
echo ""

# Run the test
python3 test_request_tracking.py

echo ""
echo "✅ Test complete!"
