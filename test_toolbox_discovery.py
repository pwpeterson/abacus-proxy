#!/usr/bin/env python3
"""Test script to verify mcp_toolbox tool discovery."""

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.utils.tool_discovery import discover_all_tools


async def main():
    """Test mcp_toolbox discovery."""
    print("Testing mcp_toolbox tool discovery...")
    print("=" * 60)
    
    # Discover all tools
    discovered = await discover_all_tools(force_refresh=True)
    
    # Check if mcp_toolbox was discovered
    if "mcp_toolbox" in discovered:
        toolbox_tools = discovered["mcp_toolbox"]
        print(f"\n✓ Successfully discovered {len(toolbox_tools)} mcp_toolbox tools:")
        print("-" * 60)
        
        for tool in toolbox_tools[:10]:  # Show first 10
            name = tool["function"]["name"]
            desc = tool["function"]["description"]
            print(f"  • {name}")
            if desc:
                print(f"    {desc[:80]}...")
        
        if len(toolbox_tools) > 10:
            print(f"\n  ... and {len(toolbox_tools) - 10} more tools")
    else:
        print("\n✗ mcp_toolbox not discovered")
        print("  Make sure the toolbox server is running at http://127.0.0.1:8004")
    
    # Show summary
    print("\n" + "=" * 60)
    print("Discovery Summary:")
    for server_name, tools in discovered.items():
        print(f"  {server_name}: {len(tools)} tools")
    
    total = sum(len(tools) for tools in discovered.values())
    print(f"\nTotal: {total} tools from {len(discovered)} sources")


if __name__ == "__main__":
    asyncio.run(main())