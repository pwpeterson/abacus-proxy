#!/bin/bash
# Migrate abacus-proxy from container to host-based service

set -e

echo "🔄 Migrating abacus-proxy from container to host..."
echo ""

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "❌ Do not run this script as root. Run as your user account."
    exit 1
fi

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv not found. Please install uv first:"
    echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

echo "✅ uv found at: $(which uv)"
echo ""

# Stop container service
echo "📦 Stopping container service..."
if sudo systemctl is-active --quiet abacus-proxy; then
    sudo systemctl stop abacus-proxy
    echo "✅ Container service stopped"
else
    echo "ℹ️  Container service not running"
fi

if sudo systemctl is-enabled --quiet abacus-proxy 2>/dev/null; then
    sudo systemctl disable abacus-proxy
    echo "✅ Container service disabled"
fi
echo ""

# Install host service
echo "🏠 Installing host service..."
mkdir -p ~/.config/systemd/user
cp abacus-proxy-host.service ~/.config/systemd/user/abacus-proxy.service
echo "✅ Service file copied"
echo ""

# Reload systemd
echo "🔄 Reloading systemd..."
systemctl --user daemon-reload
echo "✅ Systemd reloaded"
echo ""

# Enable and start
echo "🚀 Starting host service..."
systemctl --user enable abacus-proxy
systemctl --user start abacus-proxy
echo "✅ Host service started"
echo ""

# Wait a moment for startup
echo "⏳ Waiting for service to initialize..."
sleep 5
echo ""

# Check status
echo "📊 Service status:"
systemctl --user status abacus-proxy --no-pager -l
echo ""

# Test health endpoint
echo "🏥 Testing health endpoint..."
if curl -fsS http://localhost:8003/health > /dev/null 2>&1; then
    echo "✅ Health check passed!"
    echo ""
    echo "🎉 Migration complete!"
    echo ""
    echo "📝 Next steps:"
    echo "   - View logs: journalctl --user -u abacus-proxy -f"
    echo "   - Check status: systemctl --user status abacus-proxy"
    echo "   - Test API: curl http://localhost:8003/health"
else
    echo "❌ Health check failed. Check logs:"
    echo "   journalctl --user -u abacus-proxy -n 50"
    exit 1
fi
