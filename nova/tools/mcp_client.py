import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Union
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPClient:
    def __init__(self):
        self.sessions: Dict[str, ClientSession] = {}
        self.server_params: Dict[str, StdioServerParameters] = {}
        self.connected: Dict[str, bool] = {}

    async def register_server(self, name: str, command: str, args: List[str] = None, env: Dict[str, str] = None):
        self.server_params[name] = StdioServerParameters(
            command=command,
            args=args or [],
            env=env
        )
        self.connected[name] = False
        return f"Server '{name}' registered successfully."

    async def connect_to_server(self, name: str):
        if name not in self.server_params:
            return f"Error: Server '{name}' not found."
        
        try:
            async with stdio_client(self.server_params[name]) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self.sessions[name] = session
                    self.connected[name] = True
                    return f"Connected to MCP server: {name}"
        except Exception as e:
            return f"Failed to connect to {name}: {str(e)}"

    async def list_tools(self, name: str):
        if name not in self.sessions or not self.connected[name]:
            return "Server not connected."
        
        tools = await self.sessions[name].list_tools()
        return tools

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]):
        if server_name not in self.sessions:
            return "Server not connected."
        
        result = await self.sessions[server_name].call_tool(tool_name, arguments)
        return result

# Global instance
mcp_manager = MCPClient()