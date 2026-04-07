# MCP Auto-Discovery

Abacus-proxy now automatically discovers and configures MCP servers from Roo's configuration, eliminating the need for manual configuration.

## How It Works

1. **Configuration Priority**:
   - First checks for manual config: `mcp_abacus_proxy_config.json`
   - Falls back to auto-discovery from Roo's MCP settings
   - Searches in order:
     - `.roo/mcp.json` (project-specific)
     - `~/.vscode-server/data/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json`
     - `~/.config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json`

2. **Server Detection**:
   - Automatically detects transport type (stdio, SSE, WebSocket)
   - Expands variables like `${workspaceFolder}` and `${ENV}`
   - Skips disabled servers
   - Excludes model-proxy by default (to avoid circular dependency)

3. **Configuration Conversion**:
   - Converts Roo's MCP format to abacus-proxy format
   - Preserves timeouts, environment variables, and commands
   - Generates proper server allowlist

## Benefits

- ✅ **Zero Configuration**: Works out of the box with Roo's existing MCP setup
- ✅ **Automatic Updates**: Changes to Roo's MCP config are automatically picked up
- ✅ **Transport Agnostic**: Supports stdio, SSE, and WebSocket servers
- ✅ **Variable Expansion**: Handles workspace and environment variables
- ✅ **Fallback Support**: Manual config still works if needed

## Usage

### Auto-Discovery (Recommended)

Simply remove or rename `mcp_abacus_proxy_config.json`:

```bash
mv mcp_abacus_proxy_config.json mcp_abacus_proxy_config.json.backup
```

Abacus-proxy will automatically discover servers from Roo's configuration.

### Manual Configuration (Optional)

Create `mcp_abacus_proxy_config.json` to override auto-discovery:

```json
{
  "mcp_bridge": {
    "enabled": true,
    "server_allowlist": ["server1", "server2"],
    ...
  },
  "server1": {
    "type": "sse",
    "url": "http://localhost:3000/sse"
  }
}
```

## Testing Auto-Discovery

Run the test script to see what servers will be discovered:

```bash
python test_mcp_auto_discovery.py
```

This generates `mcp_auto_discovered_config.json` showing the full configuration.

## Excluding Servers

By default, `model-proxy` is excluded to avoid circular dependencies. To exclude additional servers, modify `src/mcp/bridge_client.py`:

```python
exclude_servers = ["model-proxy", "other-server"]
```

## Supported Server Types

### stdio Servers
- Command-based servers (npx, uv, python, etc.)
- Environment variable expansion
- Working directory support

### SSE Servers
- HTTP/HTTPS endpoints
- URL-based configuration
- Timeout configuration

### WebSocket Servers
- ws:// and wss:// protocols
- Connection management
- Auto-reconnect support

## Architecture

```
┌─────────────────────────────────────┐
│  Abacus-Proxy Startup               │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Load MCP Config                    │
│  1. Check mcp_abacus_proxy_config   │
│  2. Auto-discover from Roo          │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  MCP Auto-Discovery                 │
│  - Find Roo's mcp.json              │
│  - Parse server configs             │
│  - Detect transport types           │
│  - Expand variables                 │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Tool Discovery                     │
│  - Connect to each server           │
│  - Call tools/list                  │
│  - Cache tool schemas               │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Ready to Serve Requests            │
│  - All tools available              │
│  - Routing configured               │
└─────────────────────────────────────┘
```

## Implementation Files

- `src/utils/mcp_auto_discovery.py` - Auto-discovery logic
- `src/mcp/bridge_client.py` - Config loading with fallback
- `src/utils/tool_discovery.py` - Tool enumeration
- `test_mcp_auto_discovery.py` - Testing utility

## Troubleshooting

### No servers discovered

Check that Roo's MCP config exists:
```bash
ls -la .roo/mcp.json
ls -la ~/.vscode-server/data/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json
```

### Server connection failures

Verify servers are running:
```bash
# For stdio servers, check processes
ps aux | grep mcp

# For SSE servers, check endpoints
curl http://localhost:PORT/sse
```

### Variable expansion issues

Check environment variables:
```bash
echo $WORKSPACE_FOLDER
env | grep MCP
```

## Migration from Manual Config

1. Backup existing config:
   ```bash
   mv mcp_abacus_proxy_config.json mcp_abacus_proxy_config.json.backup
   ```

2. Test auto-discovery:
   ```bash
   python test_mcp_auto_discovery.py
   ```

3. Restart abacus-proxy:
   ```bash
   sudo podman restart abacus-proxy
   ```

4. Verify tool discovery in logs:
   ```bash
   sudo podman logs abacus-proxy | grep "Auto-discovered"