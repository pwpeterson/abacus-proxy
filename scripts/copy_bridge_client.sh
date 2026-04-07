#!/bin/bash
set -e

# Copy bridge_client.py from model-proxy
cp /home/pwp/srv/apps/model-proxy/src/mcp/bridge_client.py src/mcp/bridge_client.py

# Update references for abacus-proxy
sed -i 's/MCP_MODEL_PROXY_CONFIG_PATH/MCP_ABACUS_PROXY_CONFIG_PATH/g' src/mcp/bridge_client.py
sed -i 's/mcp_model_proxy_config.json/mcp_abacus_proxy_config.json/g' src/mcp/bridge_client.py
sed -i 's/model-proxy-mcp-bridge/abacus-proxy-mcp-bridge/g' src/mcp/bridge_client.py

echo "✓ bridge_client.py copied and adapted successfully"
