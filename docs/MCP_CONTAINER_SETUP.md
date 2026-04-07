# MCP Server Setup for Containerized abacus-proxy

## Problem

When abacus-proxy runs in a container, it cannot directly execute stdio-based MCP servers (like `uv run`, `npx` commands) because:

1. The container doesn't have access to the host's Python/Node environments
2. Stdio transport requires local process execution
3. The MCP servers are installed on the host, not in the container

## Solution: Use model-proxy as MCP Bridge

The easiest solution is to connect to MCP servers **through model-proxy** which already runs on the host and has access to all MCP servers.

### Configuration

Create `mcp_abacus_proxy_config.json`:

```json
{
  "mcp_bridge": {
    "server_allowlist": ["model-proxy"],
    "defaults": {
      "timeout_ms": 30000,
      "connect_timeout_ms": 5000,
      "max_concurrent": 5
    }
  },
  "model-proxy": {
    "type": "sse",
    "url": "http://localhost:9002"
  }
}
```

### Update Container Configuration

In `abacus-proxy.container`, add:

```ini
[Container]
Network=host
```

This allows the container to access `localhost:9002` (model-proxy on host).

### Implementation Steps

1. **Create config file**:
   ```bash
   cd /home/pwp/srv/apps/abacus-proxy
   cat > mcp_abacus_proxy_config.json << 'EOF'
   {
     "mcp_bridge": {
       "server_allowlist": ["model-proxy"],
       "defaults": {
         "timeout_ms": 30000,
         "connect_timeout_ms": 5000,
         "max_concurrent": 5
       }
     },
     "model-proxy": {
       "type": "sse",
       "url": "http://localhost:9002"
     }
   }
   EOF
   ```

2. **Update container to use host network**:
   Edit `abacus-proxy.container` and ensure it has:
   ```ini
   [Container]
   Network=host
   ```

3. **Rebuild and restart**:
   ```bash
   podman build -f Dockerfile.abacus-proxy -t abacus-proxy:latest .
   systemctl --user daemon-reload
   systemctl --user restart abacus-proxy
   ```

4. **Verify**:
   ```bash
   journalctl --user -u abacus-proxy -f
   ```

   You should see:
   ```
   Tool discovery complete: 278 tools from 1 sources
   ```

## Why This Works

- **model-proxy runs on host** with access to all MCP servers
- **model-proxy exposes SSE endpoint** at `http://localhost:9002/sse`
- **Container uses host network** so it can reach `localhost:9002`
- **All MCP tools available** through single bridge point
- **No need to expose individual MCP servers**

## Troubleshooting

### model-proxy not running

```bash
systemctl --user status model-proxy
curl http://localhost:9002/health
```

### Container can't reach localhost:9002

Verify `Network=host` is set in `abacus-proxy.container`

### Still seeing "No JSON response from MCP stdio server"

This means auto-discovery is still trying stdio. The manual config file takes precedence and will fix this.
