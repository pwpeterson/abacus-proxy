"""Auto-discovery of MCP servers from Roo's configuration.

This module reads Roo's MCP settings and automatically configures abacus-proxy
to connect to all available MCP servers without manual configuration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger


def _expand_vars(value: str, workspace_folder: str | None = None) -> str:
    """Expand ${workspaceFolder} and ${ENV} variables in config strings."""
    if workspace_folder is None:
        workspace_folder = os.getcwd()
    
    out = value.replace("${workspaceFolder}", workspace_folder)
    
    # Expand environment variables in ${NAME} form
    for k, v in os.environ.items():
        out = out.replace(f"${{{k}}}", v)
    
    return out


def _find_roo_mcp_config() -> Path | None:
    """Find Roo's MCP configuration file.
    
    Searches in order:
    1. .roo/mcp.json (project-specific)
    2. ~/.vscode-server/data/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json
    3. ~/.config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json
    """
    # Project-specific config
    project_config = Path(".roo/mcp.json")
    if project_config.exists():
        return project_config
    
    # VSCode server config (remote development)
    vscode_server = Path.home() / ".vscode-server/data/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json"
    if vscode_server.exists():
        return vscode_server
    
    # Local VSCode config
    vscode_local = Path.home() / ".config/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json"
    if vscode_local.exists():
        return vscode_local
    
    return None


def load_roo_mcp_config() -> dict[str, Any]:
    """Load Roo's MCP configuration.
    
    Returns:
        Dictionary with mcpServers configuration, or empty dict if not found
    """
    config_path = _find_roo_mcp_config()
    if not config_path:
        logger.warning("Roo MCP config not found, auto-discovery disabled")
        return {}
    
    try:
        with open(config_path) as f:
            data = json.load(f)
        
        logger.info(f"Loaded Roo MCP config from {config_path}")
        return data
    except Exception as e:
        logger.error(f"Failed to load Roo MCP config from {config_path}: {e}")
        return {}


def convert_roo_to_abacus_config(
    roo_config: dict[str, Any],
    workspace_folder: str | None = None,
    exclude_servers: list[str] | None = None,
) -> dict[str, Any]:
    """Convert Roo's MCP config to abacus-proxy format.
    
    Args:
        roo_config: Roo's MCP configuration (from mcp_settings.json)
        workspace_folder: Workspace folder for variable expansion
        exclude_servers: List of server names to exclude (e.g., ["model-proxy"])
    
    Returns:
        Configuration dict in abacus-proxy format with:
        - mcp_bridge section with server_allowlist
        - Individual server configurations
    """
    if workspace_folder is None:
        workspace_folder = os.getcwd()
    
    if exclude_servers is None:
        exclude_servers = []
    
    mcp_servers = roo_config.get("mcpServers", {})
    if not mcp_servers:
        logger.warning("No mcpServers found in Roo config")
        return {}
    
    # Build abacus-proxy config
    config: dict[str, Any] = {
        "mcp_bridge": {
            "enabled": True,
            "server_allowlist": [],
            "defaults": {
                "enabled": True,
                "timeout_ms": 30000,
                "connect_timeout_ms": 5000,
                "max_concurrent": 5,
                "deny_by_default": False,
                "allowed_tools": ["*"],
                "denied_tools": [],
                "allowed_transports": ["sse", "stdio"],
            },
            "servers": {},
            "schema_validation": {
                "enabled": True,
                "source_precedence": ["live_tools_list_cache", "registry_if_unambiguous"],
                "live_cache_ttl_seconds": 300,
            },
            "hygiene": {"enabled": True, "max_output_chars": 20000},
            "audit": {
                "enabled": True,
                "log_arguments": True,
                "redact_keys": ["password", "token", "key", "secret", "api_key", "apikey"],
                "max_arg_chars": 2000,
            },
        }
    }
    
    # Convert each MCP server
    for server_name, server_config in mcp_servers.items():
        if not isinstance(server_config, dict):
            continue
        
        # Skip disabled servers
        if server_config.get("disabled", False):
            logger.debug(f"Skipping disabled server: {server_name}")
            continue
        
        # Skip excluded servers
        if server_name in exclude_servers:
            logger.debug(f"Skipping excluded server: {server_name}")
            continue
        
        # Determine transport type
        transport = server_config.get("transport") or server_config.get("type", "stdio")
        
        # Convert to abacus-proxy format
        abacus_server: dict[str, Any] = {}
        
        if transport == "sse":
            # SSE server
            url = server_config.get("url", "")
            if not url:
                logger.warning(f"SSE server {server_name} missing url, skipping")
                continue
            
            abacus_server = {
                "type": "sse",
                "url": _expand_vars(url, workspace_folder),
                "description": server_config.get("description", f"{server_name} MCP server"),
            }
        
        else:
            # stdio server
            command = server_config.get("command")
            if not command:
                logger.warning(f"stdio server {server_name} missing command, skipping")
                continue
            
            args = server_config.get("args", [])
            env = server_config.get("env", {})
            
            # Expand variables in args and env
            expanded_args = [_expand_vars(str(arg), workspace_folder) for arg in args]
            expanded_env = {k: _expand_vars(str(v), workspace_folder) for k, v in env.items()}
            
            abacus_server = {
                "type": "stdio",
                "command": command,
                "args": expanded_args,
                "description": server_config.get("description", f"{server_name} MCP server"),
            }
            
            if expanded_env:
                abacus_server["env"] = expanded_env
        
        # Add timeout if specified
        timeout = server_config.get("timeout")
        if timeout:
            config["mcp_bridge"]["servers"][server_name] = {
                "enabled": True,
                "timeout_ms": int(timeout) * 1000,
            }
        
        # Add to config
        config[server_name] = abacus_server
        config["mcp_bridge"]["server_allowlist"].append(server_name)
        
        logger.debug(f"Configured server: {server_name} (transport={transport})")
    
    logger.info(
        f"Auto-discovered {len(config['mcp_bridge']['server_allowlist'])} MCP servers from Roo config"
    )
    
    return config


def auto_discover_mcp_servers(
    workspace_folder: str | None = None,
    exclude_servers: list[str] | None = None,
) -> dict[str, Any]:
    """Auto-discover MCP servers from Roo's configuration.
    
    This is the main entry point for auto-discovery. It:
    1. Finds and loads Roo's MCP config
    2. Converts it to abacus-proxy format
    3. Returns ready-to-use configuration
    
    Args:
        workspace_folder: Workspace folder for variable expansion
        exclude_servers: List of server names to exclude
    
    Returns:
        Configuration dict in abacus-proxy format
    """
    roo_config = load_roo_mcp_config()
    if not roo_config:
        return {}
    
    return convert_roo_to_abacus_config(
        roo_config,
        workspace_folder=workspace_folder,
        exclude_servers=exclude_servers,
    )