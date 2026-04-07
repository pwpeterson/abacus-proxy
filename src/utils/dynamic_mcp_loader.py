"""Dynamic MCP server configuration loader.

Loads MCP server configurations from both global and project settings,
merging them with project settings taking precedence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger


class DynamicMCPLoader:
    """Load and merge MCP server configurations from multiple sources."""
    
    def __init__(self):
        self.global_config_path = Path.home() / ".vscode-server/data/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json"
        self.project_config_path = Path(".roo/mcp.json")
        self._merged_servers: dict[str, dict[str, Any]] = {}
        self._server_list: list[str] = []
        
    def load_all_servers(self) -> dict[str, dict[str, Any]]:
        """Load and merge all MCP server configurations.
        
        Returns:
            Dictionary mapping server names to their configurations
        """
        all_servers = {}
        
        # Load global servers first
        global_servers = self._load_global_servers()
        if global_servers:
            all_servers.update(global_servers)
            logger.info(f"Loaded {len(global_servers)} global MCP servers")
        
        # Load project servers (override global)
        project_servers = self._load_project_servers()
        if project_servers:
            all_servers.update(project_servers)
            logger.info(f"Loaded {len(project_servers)} project MCP servers")
        
        # Store merged configuration
        self._merged_servers = all_servers
        self._server_list = list(all_servers.keys())
        
        logger.info(f"Total MCP servers available: {len(all_servers)}")
        logger.debug(f"Available servers: {', '.join(sorted(self._server_list))}")
        
        return all_servers
    
    def _load_global_servers(self) -> dict[str, dict[str, Any]]:
        """Load MCP servers from global Roo settings."""
        if not self.global_config_path.exists():
            logger.debug(f"Global MCP config not found: {self.global_config_path}")
            return {}
        
        try:
            with open(self.global_config_path) as f:
                config = json.load(f)
            
            servers = config.get("mcpServers", {})
            logger.debug(f"Found {len(servers)} servers in global config")
            return servers
            
        except Exception as e:
            logger.warning(f"Failed to load global MCP config: {e}")
            return {}
    
    def _load_project_servers(self) -> dict[str, dict[str, Any]]:
        """Load MCP servers from project .roo/mcp.json."""
        if not self.project_config_path.exists():
            logger.debug(f"Project MCP config not found: {self.project_config_path}")
            return {}
        
        try:
            with open(self.project_config_path) as f:
                config = json.load(f)
            
            servers = config.get("mcpServers", {})
            logger.debug(f"Found {len(servers)} servers in project config")
            return servers
            
        except Exception as e:
            logger.warning(f"Failed to load project MCP config: {e}")
            return {}
    
    def get_server_config(self, server_name: str) -> dict[str, Any] | None:
        """Get configuration for a specific server.
        
        Args:
            server_name: Name of the MCP server
            
        Returns:
            Server configuration dict or None if not found
        """
        return self._merged_servers.get(server_name)
    
    def get_all_server_names(self) -> list[str]:
        """Get list of all available server names."""
        return self._server_list.copy()
    
    def is_server_available(self, server_name: str) -> bool:
        """Check if a server is configured and available."""
        return server_name in self._merged_servers
    
    def get_servers_by_category(self, category: str) -> list[str]:
        """Get servers that match a specific category.
        
        Categories can be inferred from server names or metadata:
        - git: git-related operations
        - browser: browser automation
        - search: web search
        - filesystem: file operations
        - memory: memory/context management
        - database: database operations
        
        Args:
            category: Category name
            
        Returns:
            List of server names matching the category
        """
        category_keywords = {
            "git": ["git"],
            "browser": ["browser", "chrome", "puppeteer"],
            "search": ["search", "brave"],
            "filesystem": ["filesystem", "file"],
            "memory": ["mem0", "memory", "conport", "mindscape"],
            "database": ["sqlite", "db", "qdrant"],
            "web": ["fetch", "crawl", "http"],
            "ai": ["model", "proxy", "thinking"],
        }
        
        keywords = category_keywords.get(category.lower(), [])
        if not keywords:
            return []
        
        matching_servers = []
        for server_name in self._server_list:
            server_lower = server_name.lower()
            if any(keyword in server_lower for keyword in keywords):
                matching_servers.append(server_name)
        
        return matching_servers


# Global instance
_loader: DynamicMCPLoader | None = None


def get_mcp_loader() -> DynamicMCPLoader:
    """Get or create the global MCP loader instance."""
    global _loader
    if _loader is None:
        _loader = DynamicMCPLoader()
        _loader.load_all_servers()
    return _loader


def reload_mcp_servers() -> dict[str, dict[str, Any]]:
    """Reload all MCP server configurations."""
    global _loader
    _loader = DynamicMCPLoader()
    return _loader.load_all_servers()
