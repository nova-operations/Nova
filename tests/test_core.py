import pytest
import os
from unittest.mock import patch, MagicMock
from nova.tools.mcp_registry import mcp_registry
from nova.tools.specialist_registry import (
    save_specialist_config,
    get_specialist_config,
    list_specialists,
)
from nova.agent import get_agent


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
    name = "TestSpecialist"
    role = "Unit Test Specialist"
    instructions = "You test code."

    res = save_specialist_config(
        name, role, instructions, tools=["read_file", "write_file"]
    )
    assert "saved" in res

    config = get_specialist_config(name)
    assert config is not None
    assert config["name"] == name
    assert config["role"] == role


def test_specialist_tool_cap_enforced(clean_db):
    """Saving a specialist with >5 tools should return an error."""
    res = save_specialist_config(
        "OverloadedSpec",
        "Too many tools",
        "Instructions",
        tools=[
            "read_file",
            "write_file",
            "list_files",
            "shell",
            "github_push",
            "github_pull",
        ],  # 6 tools
    )
    assert "Error" in res or "Max" in res


@pytest.mark.asyncio
async def test_agent_initialization():
    """Agent can be initialized without crashing."""
    with patch("nova.agent.get_agno_db", return_value=MagicMock()):
        agent = get_agent(chat_id="test_chat")
        assert agent is not None
        assert agent.description is not None


def test_agent_core_tools():
    """Nova should have run_team and orchestration tools, not execution tools."""
    with patch("nova.agent.get_agno_db", return_value=MagicMock()):
        agent = get_agent()
        tool_names = [getattr(t, "__name__", type(t).__name__) for t in agent.tools]
        assert "run_team" in tool_names
        assert "add_scheduled_task" in tool_names
        # Should NOT have raw coding tools
        assert "create_subagent" not in tool_names
        assert "push_to_github" not in tool_names


def test_db_migration(clean_db):
    from nova.db.engine import get_db_engine
    from sqlalchemy import inspect

    engine = get_db_engine()
    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns("scheduled_tasks")]
    assert "team_members" in columns
