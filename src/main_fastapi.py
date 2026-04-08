"""Main entry point for the Abacus Proxy FastAPI application.

This module is intentionally small and mirrors the model-proxy pattern:
- Load configuration from environment
- Configure logging
- Create the FastAPI app
- Start an MCP WebSocket server in the FastAPI lifespan (optional)
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from loguru import logger

from src.app_fastapi import create_app
from src.models.config import AppConfig
from src.utils.config_loader import load_app_config
from src.utils.logging_setup import setup_logging

# Global MCP server instance (started in lifespan)
mcp_server: Any = None
mcp_task: asyncio.Task | None = None

# Load configuration at import time (uvicorn imports `app`)
config: AppConfig = load_app_config()

# Setup logging (structured logs are useful under systemd/podman)
import os

setup_logging(
    log_dir=config.log_dir,
    log_level=config.log_level,
    enable_rich=False,
    enable_structured=True,
    app_name="abacus_proxy",
    rotation_size=os.getenv("LOG_ROTATION_SIZE", "10 MB"),
    retention_days=int(os.getenv("LOG_RETENTION_DAYS", "7")),
    error_retention_days=int(os.getenv("ERROR_LOG_RETENTION_DAYS", "14")),
    redact_headers=os.getenv("LOG_REDACT_HEADERS", "Authorization,X-API-Key").split(","),
)

logger.bind(
    enable_mcp=config.enable_mcp,
    mcp_host=config.mcp_host,
    mcp_port=config.mcp_port,
).info("Abacus-proxy configuration loaded")


async def _start_mcp_server(app_resources: dict[str, Any]) -> None:
    """Start MCP server in the background."""
    global mcp_server, mcp_task

    if not config.enable_mcp:
        logger.info("MCP server disabled in configuration")
        return

    try:
        from src.mcp.server import MCPServer

        mcp_server = MCPServer(
            host=config.mcp_host,
            port=config.mcp_port,
            debug=config.mcp_debug,
            app_resources=app_resources,
        )
        mcp_task = asyncio.create_task(mcp_server.start())
        logger.info(f"✅ MCP server starting on ws://{config.mcp_host}:{config.mcp_port}")
    except Exception as e:
        logger.error(f"Failed to start MCP server: {e}")
        logger.warning("Application will continue without MCP server")


async def _stop_mcp_server() -> None:
    """Stop MCP server gracefully."""
    global mcp_server, mcp_task

    if mcp_server:
        with suppress(Exception):
            logger.info("🛑 Stopping MCP server...")
            await mcp_server.stop()

    if mcp_task:
        mcp_task.cancel()
        with suppress(asyncio.CancelledError):
            await mcp_task


# Create FastAPI app
app = create_app(config, on_startup=_start_mcp_server, on_shutdown=_stop_mcp_server)
