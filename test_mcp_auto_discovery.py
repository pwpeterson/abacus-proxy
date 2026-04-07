#!/usr/bin/env python3
"""Test MCP auto-discovery from Roo's configuration."""

import json
from src.utils.mcp_auto_discovery import auto_discover_mcp_servers

def main():
    print("Testing MCP auto-discovery...")
    print("-" * 80)
    
    # Auto-discover servers (excluding model-proxy)
    config = auto_discover_mcp_servers(exclude_servers=["model-proxy"])
    
    if not config:
        print("❌ Auto-discovery failed - no servers found")
        return 1
    
    # Print summary
    bridge = config.get("mcp_bridge", {})
    allowlist = bridge.get("server_allowlist", [])
    
    print(f"\n✅ Auto-discovered {len(allowlist)} MCP servers:")
    print("-" * 80)
    
    # Group by transport type
    stdio_servers = []
    sse_servers = []
    
    for server_name in allowlist:
        server_config = config.get(server_name, {})
        transport = server_config.get("type", "unknown")
        
        if transport == "stdio":
            stdio_servers.append(server_name)
        elif transport == "sse":
            sse_servers.append(server_name)
    
    if stdio_servers:
        print(f"\n📝 stdio servers ({len(stdio_servers)}):")
        for name in stdio_servers:
            server = config[name]
            cmd = server.get("command", "")
            args = server.get("args", [])
            print(f"  • {name}")
            print(f"    Command: {cmd} {' '.join(args[:2])}...")
    
    if sse_servers:
        print(f"\n🌐 SSE servers ({len(sse_servers)}):")
        for name in sse_servers:
            server = config[name]
            url = server.get("url", "")
            print(f"  • {name}")
            print(f"    URL: {url}")
    
    # Save to file for inspection
    output_file = "mcp_auto_discovered_config.json"
    with open(output_file, "w") as f:
        json.dump(config, f, indent=2)
    
    print(f"\n💾 Full config saved to: {output_file}")
    print("-" * 80)
    
    return 0

if __name__ == "__main__":
    exit(main())