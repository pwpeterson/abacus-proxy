#!/bin/bash
# Test abacus-proxy direct connection (without model-proxy)

set -e

echo "Testing abacus-proxy direct connection..."
echo "================================================"

# Configuration
ABACUS_URL="http://localhost:8003"
ROUTELLM_MODEL="route-llm"

# Test 1: Health check
echo "\n1. Testing /health endpoint..."
curl -s "${ABACUS_URL}/health" | jq .
if [ $? -eq 0 ]; then
    echo "✅ Health check passed"
else
    echo "❌ Health check failed"
    exit 1
fi

# Test 2: Models endpoint
echo "\n2. Testing /v1/models endpoint..."
curl -s "${ABACUS_URL}/v1/models" | jq '.data[0:3]'
if [ $? -eq 0 ]; then
    echo "✅ Models endpoint passed"
else
    echo "❌ Models endpoint failed"
    exit 1
fi

# Test 3: Simple chat completion (non-streaming)
echo "\n3. Testing /v1/chat/completions (non-streaming)..."
RESPONSE=$(curl -s "${ABACUS_URL}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'${ROUTELLM_MODEL}'",
    "messages": [{"role": "user", "content": "Say hello in exactly 3 words."}],
    "max_tokens": 50,
    "stream": false
  }')

echo "$RESPONSE" | jq .

# Check for either content or tool_calls (abacus-proxy wraps text in attempt_completion)
if echo "$RESPONSE" | jq -e '.choices[0].message.content' > /dev/null 2>&1; then
    echo "✅ Chat completion passed (text response)"
    echo "Response: $(echo "$RESPONSE" | jq -r '.choices[0].message.content')"
elif echo "$RESPONSE" | jq -e '.choices[0].message.tool_calls[0].function.name' > /dev/null 2>&1; then
    TOOL_NAME=$(echo "$RESPONSE" | jq -r '.choices[0].message.tool_calls[0].function.name')
    TOOL_ARGS=$(echo "$RESPONSE" | jq -r '.choices[0].message.tool_calls[0].function.arguments')
    echo "✅ Chat completion passed (tool call response)"
    echo "Tool: $TOOL_NAME"
    echo "Arguments: $TOOL_ARGS"
    
    # Extract result from attempt_completion if present
    if [ "$TOOL_NAME" = "attempt_completion" ]; then
        RESULT=$(echo "$TOOL_ARGS" | jq -r '.result // empty')
        if [ -n "$RESULT" ]; then
            echo "Result: $RESULT"
        fi
    fi
else
    echo "❌ Chat completion failed - no content or tool_calls"
    echo "Full response: $RESPONSE"
    exit 1
fi

echo "\n================================================"
echo "✅ All tests passed! Abacus-proxy is working correctly."
echo "\nYou can now configure Roo Code to use:"
echo "  Provider: OpenAI Compatible"
echo "  Base URL: http://localhost:8003/v1"
echo "  Model: route-llm (or any model from /v1/models)"
