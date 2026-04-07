#!/usr/bin/env python3
"""Merge all MCP server configurations from project and global settings."""

import json
import os
import sys
from pathlib import Path


def load_json_file(filepath: str) -> dict:
    """Load JSON file, return empty dict if not found."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: Could not load {filepath}: {e}", file=sys.stderr)
        return {}


def convert_to_abacus_format(server_name: str, config: dict) -> dict:
    """Convert Roo MCP config to abacus-proxy format with absolute paths."""
    result = {
        "transport": "stdio",
        "command": config.get("command", ""),
        "args": config.get("args", []),
    }
    
    # Add environment variables if present
    if "env" in config:
        result["env"] = config["env"]
    
    # Add timeout if specified
    if "timeout" in config:
        result["timeout"] = config["timeout"]
    
    return result


def main():
    # Paths to check
    project_mcp = ".roo/mcp.json"
    global_mcp = os.path.expanduser(
        "~/.vscode-server/data/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json"
    )
    
    # Load configurations
    project_config = load_json_file(project_mcp)
    global_config = load_json_file(global_mcp)
    
    # Merge servers (project takes precedence)
    all_servers = {}
    
    # Add global servers first
    if "mcpServers" in global_config:
        for name, config in global_config["mcpServers"].items():
            all_servers[name] = convert_to_abacus_format(name, config)
            print(f"Added global server: {name}", file=sys.stderr)
    
    # Override with project servers
    if "mcpServers" in project_config:
        for name, config in project_config["mcpServers"].items():
            all_servers[name] = convert_to_abacus_format(name, config)
            print(f"Added/overrode with project server: {name}", file=sys.stderr)
    
    # Create output config
    output = {
        "mcpServers": all_servers
    }
    
    # Write to stdout
    print(json.dumps(output, indent=2))
    print(f"\nTotal servers: {len(all_servers)}", file=sys.stderr)
    print(f"Servers: {', '.join(sorted(all_servers.keys()))}", file=sys.stderr)


if __name__ == "__main__":
    main()
