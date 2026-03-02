from nova.tools.mcp.mcp_registry import mcp_registry
from typing import List, Dict, Optional
from nova.tools.core.context_optimizer import wrap_tool_output_optimization


@wrap_tool_output_optimization
def add_mcp_server(
    name: str,
    transport: str = "stdio",
    command: str = None,
    args: List[str] = None,
    url: str = None,
    env: Dict[str, str] = None,
) -> str:
    """
    Adds a new MCP server to the permanent registry.
    The agent will gain access to this server's tools after the next initialization.

    Args:
        name: Unique name for the server.
        transport: 'stdio' for local servers, 'streamable-http' for remote ones.
        command: Command to run (required for stdio).
        args: List of arguments for the command.
        url: Connection URL (required for streamable-http).
        env: Environment variables for the server.
    """
    return mcp_registry.register_server(name, transport, command, args, url, env)


@wrap_tool_output_optimization
def remove_mcp_server(name: str) -> str:
    """Removes an MCP server from the registry."""
    return mcp_registry.remove_server(name)


@wrap_tool_output_optimization
def list_registered_mcp_servers() -> str:
    """Lists all registered MCP servers and their configurations."""
    servers = mcp_registry.list_servers()
    if not servers:
        return "No MCP servers registered."

    report = ["Registered MCP Servers:"]
    for s in servers:
        report.append(f"- {s['name']} ({s['transport']})")
        if s["command"]:
            report.append(f"  Command: {s['command']} {' '.join(s['args'])}")
        if s["url"]:
            report.append(f"  URL: {s['url']}")
    return "\n".join(report)
