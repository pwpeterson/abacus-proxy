#!/bin/bash
# Test script runner for Supabase request tracking inserts

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=========================================="
echo "Abacus Proxy - Supabase Insert Test"
echo "=========================================="
echo ""

# Check if .env file exists
ENV_FILE="$PROJECT_DIR/abacus-proxy.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Error: abacus-proxy.env not found at: $ENV_FILE"
    echo ""
    echo "Please create this file with the following variables:"
    echo "  SUPABASE_URL=<your-supabase-url>"
    echo "  SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>"
    exit 1
fi

echo "✓ Found environment file: $ENV_FILE"
echo ""

# Check for required Python packages
echo "Checking dependencies..."
cd "$PROJECT_DIR"

if ! command -v uv &> /dev/null; then
    echo "❌ Error: 'uv' command not found"
    echo "Please install uv: https://github.com/astral-sh/uv"
    exit 1
fi

echo "✓ uv is installed"
echo ""

# Run the test script
echo "Running test script..."
echo ""

uv run python "$SCRIPT_DIR/test_supabase_insert.py"

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "✓ All tests passed!"
    echo "=========================================="
    echo ""
    echo "You can verify the inserts in Supabase:"
    echo "  1. Go to your Supabase dashboard"
    echo "  2. Navigate to Table Editor"
    echo "  3. View tables:"
    echo "     - public.abacus_proxy_requests"
    echo "     - public.abacus_proxy_request_contexts"
else
    echo ""
    echo "=========================================="
    echo "✗ Tests failed with exit code: $exit_code"
    echo "=========================================="
fi

exit $exit_code
