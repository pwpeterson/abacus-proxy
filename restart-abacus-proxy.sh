#!/bin/bash
# Restart abacus-proxy service to apply parameter translation changes

set -euo pipefail

echo "Restarting abacus-proxy service..."
systemctl --user restart abacus-proxy

echo "Waiting for service to start..."
sleep 2

echo "Checking service status:"
systemctl --user status abacus-proxy --no-pager

echo ""
echo "Service restarted. Parameter translation layer is now active."
echo "Roo Code tools will now work correctly with MCP servers."
