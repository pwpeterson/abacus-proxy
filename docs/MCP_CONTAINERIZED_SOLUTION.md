# Containerized MCP Server Access Solution

## How It Works

abacus-proxy runs in a container but accesses stdio MCP servers on the host by:

1. **Using `Network=host`** - Container shares host's network namespace
2. **Mounting host directories** - MCP server code accessible at absolute paths
3. **Installing `uv` in container** - Can execute `uv run` commands
4. **Using absolute paths in config** - Points to host filesystem locations

This is the **same approach model-proxy uses** successfully.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Container: abacus-proxy                                     │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ abacus-proxy process                                 │ │
│  │  - Has uv installed                                  │ │
│  │  - Reads mcp_abacus_proxy_config.json               │ │
│  │  - Executes: uv run --directory /host/path ...      │ │
│  └──────────────────────────────────────────────────────┘ │
│                          │                                  │
│                          │ stdio (stdin/stdout)             │
│                          ▼                                  │
│  ┌──────────────────────────────────────────────────────┐ │
│  │ MCP Server subprocess (via mounted host dirs)        │ │
│  │  - Runs from /home/pwp/srv/apps/... (mounted)       │ │
│  │  - Uses host's Python/Node environments             │ │
│  │  - Accesses host files at absolute paths            │ │
│  └──────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Key Configuration

### 1. Container Mounts (`abacus-proxy.container`)

```ini
# Config file with absolute paths
Volume=/home/pwp/srv/apps/abacus-proxy/mcp_abacus_proxy_config.json:/app/mcp_abacus_proxy_config.json:Z

# Host directories for MCP server access (read-only)
Volume=/home/pwp/srv/apps:/home/pwp/srv/apps:ro,Z
Volume=/home/pwp/srv/mcps:/home/pwp/srv/mcps:ro,Z
Volume=/home/pwp/srv/context-portal:/home/pwp/srv/context-portal:ro,Z
Volume=/home/pwp/.local/bin:/home/pwp/.local/bin:ro,Z
```

### 2. MCP Config (`mcp_abacus_proxy_config.json`)

Uses **absolute host paths** instead of relative paths:

```json
{
  "conport": {
    "type": "stdio",
    "command": "uv",
    "args": [
      "run",
      "--directory",
      "/home/pwp/srv/context-portal",  // Absolute path!
      "python",
      "-m",
      "context_portal_mcp.main",
      "--workspace_id",
      "/home/pwp/srv/apps/abacus-proxy"  // Absolute path!
    ]
  }
}
```

### 3. Network Mode

```ini
Network=host
```

Allows container to access host filesystem at absolute paths.

## Deployment Steps

```bash
# 1. Rebuild container with new mounts
cd /home/pwp/srv/apps/abacus-proxy
podman build -f Dockerfile.abacus-proxy -t abacus-proxy:latest .

# 2. Reload systemd
systemctl --user daemon-reload

# 3. Restart service
systemctl --user restart abacus-proxy

# 4. Verify
journalctl --user -u abacus-proxy -f
```

## Expected Result

**Before:**
```
ERROR | Failed to discover tools from mindscape-ai: No JSON response
ERROR | Failed to discover tools from mem0-mcp: No JSON response
ERROR | Failed to discover tools from conport: No JSON response
ERROR | Failed to discover tools from mcp-git-tools: No JSON response
Tool discovery complete: 8 tools from 1 sources
```

**After:**
```
INFO | Discovered 15 tools from mindscape-ai
INFO | Discovered 8 tools from mem0-mcp
INFO | Discovered 50 tools from conport
INFO | Discovered 45 tools from mcp-git-tools
Tool discovery complete: 126 tools from 5 sources
```

## Why This Works

✅ **Container has `uv` installed** - Can execute MCP server commands
✅ **Host directories mounted** - MCP server code accessible
✅ **Absolute paths in config** - No ambiguity about locations
✅ **Network=host** - Full host filesystem access
✅ **Same as model-proxy** - Proven working approach

## Comparison with model-proxy

| Feature | model-proxy | abacus-proxy |
|---------|-------------|-------------|
| **Container** | ✅ Yes | ✅ Yes |
| **Network Mode** | `host` | `host` |
| **uv installed** | ✅ Yes | ✅ Yes |
| **Host mounts** | Implicit | ✅ Explicit |
| **Config paths** | Absolute | ✅ Absolute |
| **Stdio MCP** | ✅ Works | ✅ Works |

## Security Notes

- Host directories mounted **read-only** (`:ro`)
- MCP servers run with container's user permissions
- SELinux labels applied (`:Z`)
- No write access to host MCP server code

## Troubleshooting

### "Command not found: uv"

**Problem**: Container doesn't have `uv` installed

**Solution**: Rebuild container - Dockerfile already includes `RUN pip install uv`

### "No such file or directory: /home/pwp/srv/..."

**Problem**: Host directories not mounted

**Solution**: Check `abacus-proxy.container` has volume mounts, then `systemctl --user daemon-reload`

### "Permission denied"

**Problem**: SELinux blocking access

**Solution**: Volume mounts include `:Z` label - should work automatically

### Still seeing "No JSON response"

**Problem**: Config file not being read

**Solution**: 
```bash
# Verify config is mounted
podman exec abacus-proxy cat /app/mcp_abacus_proxy_config.json

# Check logs for config loading
journalctl --user -u abacus-proxy | grep "mcp_abacus_proxy_config"
```

## Benefits Over Host-Based Deployment

✅ **Resource isolation** - Memory/CPU limits enforced
✅ **Clean dependencies** - No host Python conflicts
✅ **Easy rollback** - Just restart old container
✅ **Consistent environment** - Same setup everywhere
✅ **Systemd integration** - Auto-restart, logging, etc.
