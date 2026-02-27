import pytest
import os
from unittest.mock import MagicMock, patch
from nova.agent import get_agent, get_mcp_toolkits
from nova.tools.dev_protocol import run_protocol


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


def test_get_mcp_toolkits(mock_env):
    """Test that MCP toolkits can be retrieved without crashing."""
    with patch("nova.tools.mcp_registry.mcp_registry.list_servers", return_value=[]):
        toolkits = get_mcp_toolkits()
        assert isinstance(toolkits, list)


def test_get_agent(mock_env):
    """Test that the agent can be initialized with the correct tools."""
    with patch("nova.agent.get_agno_db", return_value=MagicMock()):
        agent = get_agent()
        assert agent is not None

        # Check if run_protocol is in the tools
        tool_names = [t.__name__ for t in agent.tools if hasattr(t, "__name__")]
        assert "run_protocol" in tool_names
        assert "create_subagent" in tool_names


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
