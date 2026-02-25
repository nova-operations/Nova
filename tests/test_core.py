import pytest
import os
import json
import asyncio
from datetime import timedelta
from unittest.mock import patch
from nova.tools.mcp_registry import mcp_registry
from nova.tools.specialist_registry import save_specialist_config, get_specialist_config
from nova.agent import get_agent, get_mcp_toolkits


@pytest.fixture
def clean_db():
    # Use a test sqlite DB for tests
    os.environ["DATABASE_URL"] = "sqlite:///test_nova.db"
    from migrations.migrate import run_migrations

    run_migrations()
    yield
    if os.path.exists("test_nova.db"):
        os.remove("test_nova.db")


def test_mcp_registry(clean_db):
    name = "test-server"
    transport = "stdio"
    command = "npx"
    args = ["-y", "@modelcontextprotocol/server-memory"]

    res = mcp_registry.register_server(name, transport, command, args)
    assert "successfully" in res

    servers = mcp_registry.list_servers()
    assert len(servers) >= 1
    assert any(s["name"] == name for s in servers)

    mcp_registry.remove_server(name)
    servers = mcp_registry.list_servers()
    assert not any(s["name"] == name for s in servers)


def test_specialist_registry(clean_db):
    name = "Tester"
    role = "Unit Test Specialist"
    instructions = "You test code."

    res = save_specialist_config(name, role, instructions)
    assert "saved" in res

    config = get_specialist_config(name)
    assert config is not None
    assert config["name"] == name
    assert config["role"] == role


@pytest.mark.asyncio
async def test_agent_initialization():
    # Test if agent can be initialized without crashing
    # This will attempt to load MCP tools if they exist in env/db
    agent = get_agent(chat_id="test_chat")
    assert agent is not None
    assert agent.description is not None


def test_db_migration(clean_db):
    # Test column existence after migration
    from nova.db.engine import get_db_engine
    from sqlalchemy import inspect

    engine = get_db_engine()
    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns("scheduled_tasks")]
    assert "team_members" in columns


@pytest.mark.asyncio
async def test_multi_mcp_initialization(clean_db):
    # Register two dummy MCP servers (using streamable-http with fake URLs)
    mcp_registry.register_server(
        "mcp1", "streamable-http", url="http://localhost:1234/mcp"
    )
    mcp_registry.register_server("mcp2", "stdio", command="python3", args=["--version"])

    # get toolkits
    toolkits = get_mcp_toolkits()

    # Should have at least the 2 custom ones we just added
    assert len(toolkits) >= 2

    # Find custom MCPTools
    from agno.tools.mcp import MCPTools

    all_mcp_tools = [t for t in toolkits if isinstance(t, MCPTools)]
    assert len(all_mcp_tools) >= 2

    # Since we can't easily check for 'name' on the toolkit anymore (as it's hardcoded to 'MCPTools'),
    # we'll look for the parameters inside.
    mcp1_tool = next(
        (
            t
            for t in all_mcp_tools
            if t.server_params
            and getattr(t.server_params, "url", None) == "http://localhost:1234/mcp"
        ),
        None,
    )
    mcp2_tool = next(
        (
            t
            for t in all_mcp_tools
            if t.server_params
            and getattr(t.server_params, "command", None) == "python3"
        ),
        None,
    )

    assert mcp1_tool is not None
    assert mcp2_tool is not None
    assert mcp1_tool.timeout_seconds == 30
    assert mcp2_tool.timeout_seconds == 30

    # Finally check agent creation
    agent = get_agent(chat_id="test_chat")
    assert agent is not None
