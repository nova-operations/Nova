import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import sys
import importlib

# Set dummy environment for tests
os.environ["TELEGRAM_USER_WHITELIST"] = "123456"

# Mock dependencies
sys.modules["croniter"] = MagicMock()

from nova.telegram_bot import handle_message, get_reply_context
from nova.tools.scheduler import ScheduledTask, initialize_scheduler
from sqlalchemy import create_engine, inspect, text
from nova.db.base import Base


# Safe import for migration with numeric prefix
def get_migration_module():
    return importlib.import_module("migrations.001_initial_schema")


@pytest.mark.asyncio
async def test_handle_message_scope():
    """Verify that handle_message has all required functions in scope for media."""
    mock_update = MagicMock()
    mock_update.effective_user.id = 123456
    mock_update.effective_chat.id = 456
    mock_update.message.text = None
    mock_update.message.voice = MagicMock(file_id="voice123")
    mock_update.message.audio = None
    mock_update.message.photo = None
    mock_update.message.video = None
    mock_update.message.video_note = None
    mock_update.message.document = None

    mock_context = MagicMock()
    mock_context.bot = AsyncMock()

    # We want to check if it calls process_nova_intent and if process_nova_intent is defined
    with patch(
        "nova.telegram_bot.process_nova_intent", new_callable=AsyncMock
    ) as mock_handle:
        with patch("nova.telegram_bot.is_authorized", return_value=True):
            # We mock get_file to avoid network calls
            mock_file = AsyncMock()
            mock_context.bot.get_file.return_value = mock_file

            await handle_message(mock_update, mock_context)

            # Verify process_nova_intent was called
            assert mock_handle.called


def test_scheduled_task_schema_match():
    """Verify that the ScheduledTask model matches expectation (status column)."""
    # Create an in-memory SQLite DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns("scheduled_tasks")]

    # Check for critical columns reported as missing on server
    assert "status" in columns
    assert "team_members" in columns
    assert "task_name" in columns


@pytest.mark.asyncio
async def test_migration_fixes_missing_column():
    """Verify that migrations correctly add the 'status' column if it's missing."""
    engine = create_engine("sqlite:///:memory:")

    # Create table WITHOUT status column manually
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE scheduled_tasks (id INTEGER PRIMARY KEY, task_name VARCHAR(255))"
            )
        )
        conn.commit()

    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns("scheduled_tasks")]
    assert "status" not in columns

    # Run migrations using the engine
    migration_mod = get_migration_module()
    # We must patch it where it was imported
    with patch("migrations.001_initial_schema.get_db_engine", return_value=engine):
        migration_mod.run_migrations()

    # Verify column was added
    inspector = inspect(engine)
    columns = [c["name"] for c in inspector.get_columns("scheduled_tasks")]
    assert "status" in columns
