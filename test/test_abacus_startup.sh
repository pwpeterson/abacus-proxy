#!/bin/bash
# Test abacus-proxy startup after removing tool injection code

set -e

echo "Testing abacus-proxy startup..."

# Kill any existing abacus-proxy processes
pkill -f "uvicorn.*abacus-proxy" || true
sleep 2

# Start abacus-proxy in background
cd /home/pwp/srv/apps/abacus-proxy
source abacus-proxy.env
uvicorn src.main_fastapi:app --host 0.0.0.0 --port 8003 --log-level info &
ABACUS_PID=$!

echo "Started abacus-proxy with PID: $ABACUS_PID"

# Wait for startup
echo "Waiting for abacus-proxy to start..."
sleep 5

# Test health endpoint
echo "Testing /health endpoint..."
curl -s http://localhost:8003/health | jq .

if [ $? -eq 0 ]; then
    echo "✅ Health check passed!"
else
    echo "❌ Health check failed!"
    kill $ABACUS_PID
    exit 1
fi

# Test models endpoint
echo "Testing /v1/models endpoint..."
curl -s http://localhost:8003/v1/models | jq '.data[0]'

if [ $? -eq 0 ]; then
    echo "✅ Models endpoint passed!"
else
    echo "❌ Models endpoint failed!"
    kill $ABACUS_PID
    exit 1
fi

echo "✅ All tests passed! Abacus-proxy is running correctly."
echo "Keeping abacus-proxy running. PID: $ABACUS_PID"
echo "To stop: kill $ABACUS_PID"
