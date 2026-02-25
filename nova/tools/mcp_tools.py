import asyncio
import json
from mcp_client import mcp_manager

async def register_mcp_server(name: str, command: str, args: list = None, env: dict = None):
    """Registers a new MCP server configuration."""
    return await mcp_manager.register_server(name, command, args, env)

async def connect_mcp_server(name: str):
    """Establishes connection to a registered MCP server."""
    return await mcp_manager.connect_to_server(name)

async def list_mcp_tools(name: str):
    """Lists available tools from a connected MCP server."""
    return await mcp_manager.list_tools(name)

async def call_mcp_tool(server_name: str, tool_name: str, arguments: dict):
    """Calls a specific tool on a connected MCP server."""
    return await mcp_manager.call_tool(server_name, tool_name, arguments)