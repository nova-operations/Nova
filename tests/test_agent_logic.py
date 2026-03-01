import pytest
import os
from unittest.mock import MagicMock, patch
from nova.agent import get_agent
from nova.tools.dev_protocol import run_protocol
from nova.tools.specialist_registry import list_specialists, seed_default_specialists
from nova.tools.team_manager import run_team


@pytest.fixture
def mock_env():
    with patch.dict(
        os.environ,
        {
            "AGENT_MODEL": "test-model",
            "OPENROUTER_API_KEY": "test-key",
            "DATABASE_URL": "sqlite:///:memory:",
        },
    ):
        yield


def test_get_agent(mock_env):
    """Test that the agent can be initialized with the correct tools."""
    with patch("nova.agent.get_agno_db", return_value=MagicMock()):
        agent = get_agent()
        assert agent is not None

        # Nova should have core orchestration tools
        tool_names = [getattr(t, "__name__", type(t).__name__) for t in agent.tools]
        assert "run_team" in tool_names
        assert "get_system_state" in tool_names
        assert "add_scheduled_task" in tool_names
        # Nova should NOT have file I/O or subagent tools directly
        assert "create_subagent" not in tool_names


def test_agent_tool_count(mock_env):
    """Nova should have a limited number of tools to prevent confusion."""
    with patch("nova.agent.get_agno_db", return_value=MagicMock()):
        agent = get_agent()
        # Max reasonable count: 15 (includes Tavily toolkit if key present)
        assert len(agent.tools) <= 15, f"Too many tools: {len(agent.tools)}"


def test_specialist_list():
    """After seeding, specialists should be available in the DB."""
    seed_default_specialists()
    result = list_specialists()
    assert result != "No specialists registered."
    assert "Bug-Fixer" in result


def test_specialist_tool_cap():
    """Each specialist should have max 5 tools."""
    from nova.tools.specialist_registry import SpecialistConfig, _get_session

    session = _get_session()
    try:
        configs = session.query(SpecialistConfig).all()
        for c in configs:
            tools = c.tools or []
            assert (
                len(tools) <= 5
            ), f"Specialist '{c.name}' has {len(tools)} tools (max 5)"
    finally:
        session.close()


@pytest.mark.asyncio
async def test_run_protocol_logic():
    """Test the run_protocol tool logic (mocking git and pytest)."""
    with patch("subprocess.run") as mock_run:
        # Mock pytest success
        mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")

        # Mock git status showing changes
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="Tests passed", stderr=""),  # pytest
            MagicMock(returncode=0),  # git add
            MagicMock(returncode=0, stdout="M file.py", stderr=""),  # git status
            MagicMock(returncode=0, stdout="committed", stderr=""),  # git commit
        ]

        result = run_protocol("test commit", run_full_suite=True)
        assert "PROTOCOL COMPLETED SUCCESSFULLY" in result
        assert "✅ Tests passed" in result


def test_run_protocol_failure():
    """Test that run_protocol fails if tests fail."""
    with patch("subprocess.run") as mock_run:
        # Mock pytest failure
        mock_run.return_value = MagicMock(
            returncode=1, stdout="Failure", stderr="Error log"
        )

        result = run_protocol("test commit", run_full_suite=True)
        assert "PROTOCOL REJECTED" in result
        assert "❌ Tests failed" in result
