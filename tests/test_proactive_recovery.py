import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import sys

# Set dummy environment for tests
os.environ["TELEGRAM_USER_WHITELIST"] = "123456"

# Mock the dependency that imports croniter before it's loaded
sys.modules["croniter"] = MagicMock()

from nova.telegram_bot import reinvigorate_nova, process_nova_intent
from nova.tools.agents.subagent import SUBAGENTS


@pytest.mark.asyncio
async def test_reinvigorate_nova_triggers_task():
    """Test that reinvigorate_nova correctly schedules a background recovery task."""
    mock_process = AsyncMock()
    # Set TELEGRAM_CHAT_ID or fall back to whitelist
    os.environ["TELEGRAM_CHAT_ID"] = "123456"
    with patch("nova.telegram_bot.process_nova_intent", mock_process):
        with patch("nova.telegram_bot.telegram_bot_instance", new=MagicMock()):
            await reinvigorate_nova("456", "Test failure message")

            # Allow event loop to run the created task
            await asyncio.sleep(0.1)

            # Verify process_nova_intent was called with system prompt
            mock_process.assert_called_once()
            args = mock_process.call_args[0]
            assert args[0] == 456  # chat_id
            assert args[1] == 123456  # user_id (from TELEGRAM_CHAT_ID)
            assert "Test failure message" in args[2]  # error included
            assert "[SYSTEM_ALERT]" in args[2]  # new format


@pytest.mark.asyncio
async def test_process_nova_intent_delegation_note():
    """Test that process_nova_intent includes active subagent notes in the prompt."""
    chat_id = 456
    user_id = 123456
    message = "Test message"

    # Mock active subagents
    SUBAGENTS["test_sub"] = {
        "name": "Specialist_A",
        "chat_id": str(chat_id),
        "status": "running",
    }

    mock_agent = AsyncMock()
    mock_agent.arun.return_value = MagicMock(content="Recovery started")

    mock_bot = MagicMock()
    mock_bot.send_chat_action = AsyncMock()

    with patch("nova.telegram_bot.get_agent", return_value=mock_agent):
        with patch("nova.telegram_bot.telegram_bot_instance", new=mock_bot):
            with patch(
                "nova.telegram_bot.send_message_with_fallback", new_callable=AsyncMock
            ) as mock_send:
                await process_nova_intent(chat_id, user_id, message)

                # Check if arun was called with active subagent note
                call_msg = mock_agent.arun.call_args[0][0]
                assert (
                    "[SYSTEM NOTE: You have active subagents running: Specialist_A]"
                    in call_msg
                )
                assert message in call_msg

                assert mock_send.called


@pytest.mark.asyncio
async def test_subagent_failure_triggers_reinvigorate():
    """Test that run_subagent_task calls reinvigorate_nova on exception."""
    from nova.tools.agents.subagent import run_subagent_task

    subagent_id = "fail_sub"
    SUBAGENTS[subagent_id] = {
        "name": "FailureAgent",
        "chat_id": "456",
        "status": "starting",
    }

    mock_agent = AsyncMock()
    mock_agent.arun.side_effect = Exception("Critical Error")

    # Mock StreamingContext for 'async with'
    mock_stream = AsyncMock()
    mock_stream.send = AsyncMock()

    class MockContext:
        async def __aenter__(self):
            return mock_stream

        async def __aexit__(self, exc_type, exc, tb):
            pass

    with patch("nova.tools.agents.subagent.StreamingContext", return_value=MockContext()):
        with patch(
            "nova.telegram_bot.reinvigorate_nova", new_callable=AsyncMock
        ) as mock_reinvigorate:
            # We need to mock the get_task_tracker to avoid DB issues in unit test
            with patch(
                "nova.tools.agents.subagent.get_task_tracker", return_value=MagicMock()
            ):
                await run_subagent_task(subagent_id, mock_agent, "Do something risky")

                # Verify reinvigorate_nova was called with error info
                assert mock_reinvigorate.called
                args = mock_reinvigorate.call_args[0]
                assert args[0] == "456"
                assert "Critical Error" in args[1]
