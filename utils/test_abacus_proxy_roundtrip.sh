#!/usr/bin/env bash
set -euo pipefail

# Smoke tests for abacus-proxy OpenAI-compatible endpoints.
#
# Usage:
#   ABACUS_PROXY_BASE_URL="http://localhost:8003/v1" \
#   ABACUS_BEARER_TOKEN="RouteLLM_API_KEY=s2_b5b06aa105d1490db8dc26a8ba3ca399" \
#   ./utils/test_abacus_proxy_roundtrip.sh
#
# Notes:
# - If ABACUS_BEARER_TOKEN is omitted, requests will be sent without Authorization.
# - The proxy may also be configured with ABACUS_API_KEY server-side.
# - Round-trip test requests model "route-llm" (client-facing), but the proxy should
#   route it upstream to a concrete model (default "gpt-5.2").

BASE_URL="${ABACUS_PROXY_BASE_URL:-http://localhost:8003/v1}"
TOKEN="${ABACUS_BEARER_TOKEN:-RouteLLM_API_KEY=s2_b5b06aa105d1490db8dc26a8ba3ca399}"

AUTH_HEADER=()
if [[ -n "${TOKEN}" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer ${TOKEN}")
fi

echo "== abacus-proxy smoke tests =="
echo "BASE_URL: ${BASE_URL}"
if [[ -n "${TOKEN}" ]]; then
  echo "AUTH: Bearer token provided"
else
  echo "AUTH: none (expect 401/403 from upstream if required)"
fi
echo

echo "## GET /models"
curl -sS -i "${BASE_URL}/models" "${AUTH_HEADER[@]}" | sed -n '1,40p'
echo
echo

if [[ -z "${TOKEN}" ]]; then
  echo "## POST /chat/completions"
  echo "SKIP: ABACUS_BEARER_TOKEN not set. Upstream will return 403 (User not logged in)."
  echo "Set ABACUS_BEARER_TOKEN (or configure ABACUS_API_KEY server-side) to run completion tests."
  echo
  echo
else
  echo "## POST /chat/completions (model=route-llm, should route upstream to gpt-5.2 by default)"
  curl -sS -i "${BASE_URL}/chat/completions" \
    -H 'Content-Type: application/json' \
    "${AUTH_HEADER[@]}" \
    -d '{
      "model": "route-llm",
      "messages": [
        { "role": "system", "content": "Return a single short sentence." },
        { "role": "user", "content": "Say hello and include the word ROUTED." }
      ],
      "temperature": 0
    }' | sed -n '1,120p'
  echo
  echo

  echo "## POST /chat/completions streaming (stream=true)"
  # Note: the proxy may translate non-stream upstream responses into SSE, depending on config.
  curl -sS -N "${BASE_URL}/chat/completions" \
    -H 'Content-Type: application/json' \
    "${AUTH_HEADER[@]}" \
    -d '{
      "model": "route-llm",
      "stream": true,
      "messages": [
        { "role": "user", "content": "Stream a short response that includes STREAM_OK." }
      ],
      "temperature": 0
    }' | sed -n '1,80p'
  echo
  echo
fi

echo "Done."