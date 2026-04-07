# Running abacus-proxy on Host (Not Containerized)

## Why Run on Host?

When abacus-proxy runs directly on the host instead of in a container:

✅ **Direct access to stdio MCP servers** - Can execute `uv run`, `npx` commands
✅ **No network translation needed** - Direct process communication
✅ **Simpler configuration** - Auto-discovery works perfectly
✅ **Better performance** - No container overhead
✅ **Easier debugging** - Direct log access

## Migration Steps

### 1. Stop Container Service

```bash
# Stop and disable container service
sudo systemctl stop abacus-proxy
sudo systemctl disable abacus-proxy

# Verify it's stopped
sudo systemctl status abacus-proxy
```

### 2. Install Host Service

```bash
cd /home/pwp/srv/apps/abacus-proxy

# Copy service file to systemd user directory
mkdir -p ~/.config/systemd/user
cp abacus-proxy-host.service ~/.config/systemd/user/abacus-proxy.service

# Reload systemd
systemctl --user daemon-reload

# Enable and start
systemctl --user enable abacus-proxy
systemctl --user start abacus-proxy
```

### 3. Verify Service

```bash
# Check status
systemctl --user status abacus-proxy

# Watch logs
journalctl --user -u abacus-proxy -f
```

### 4. Test Health

```bash
# Health check
curl http://localhost:8003/health

# Should return: {"status":"healthy"}
```

## Expected Log Output

```
INFO | Loaded Roo MCP config from .roo/mcp.json
INFO | Auto-discovered 4 MCP servers from Roo config
INFO | Discovered 15 tools from mindscape-ai
INFO | Discovered 8 tools from mem0-mcp
INFO | Discovered 50 tools from conport
INFO | Discovered 45 tools from mcp-git-tools
INFO | Tool discovery complete: 126 tools from 5 sources
```

## Configuration

The service uses:
- **Working Directory**: `/home/pwp/srv/apps/abacus-proxy`
- **Python Environment**: Managed by `uv` (from pyproject.toml)
- **Port**: 8003 (HTTP), 9003 (MCP WebSocket)
- **Environment Files**: Same as container version
- **MCP Config**: Auto-discovery from `.roo/mcp.json`

## Advantages Over Container

| Feature | Container | Host |
|---------|-----------|------|
| **Stdio MCP Servers** | ❌ Broken | ✅ Works |
| **Auto-discovery** | ⚠️ Limited | ✅ Full |
| **Setup Complexity** | High | Low |
| **Performance** | Good | Better |
| **Debugging** | Harder | Easier |
| **Resource Isolation** | ✅ Yes | ❌ No |

## Rollback to Container

If you need to go back to container:

```bash
# Stop host service
systemctl --user stop abacus-proxy
systemctl --user disable abacus-proxy

# Start container service
sudo systemctl start abacus-proxy
sudo systemctl enable abacus-proxy
```

## Troubleshooting

### Service won't start

```bash
# Check for errors
journalctl --user -u abacus-proxy -n 50

# Verify uv is installed
which uv
# Should show: /home/pwp/.local/bin/uv

# Test manually
cd /home/pwp/srv/apps/abacus-proxy
uv run uvicorn src.main_fastapi:app --host 0.0.0.0 --port 8003
```

### Port already in use

```bash
# Check what's using port 8003
sudo lsof -i :8003

# If container is still running, stop it
sudo systemctl stop abacus-proxy
```

### MCP servers not found

```bash
# Verify .roo/mcp.json exists
ls -la /home/pwp/srv/apps/abacus-proxy/.roo/mcp.json

# Check MCP server commands work
uv run --directory /home/pwp/srv/context-portal python -m context_portal_mcp.main --mode stdio
```

## Performance Notes

- **Memory**: ~500MB-1GB (vs 1-2GB in container)
- **Startup**: ~5-10 seconds (vs 30-60 seconds in container)
- **Tool Discovery**: ~2-5 seconds (vs 10-20 seconds in container)

## Security Considerations

- ⚠️ **No container isolation** - Process runs with user permissions
- ✅ **Same environment files** - Credentials still protected
- ✅ **Firewall rules** - Still apply to port 8003
- ⚠️ **Resource limits** - Enforced by systemd, not container runtime
