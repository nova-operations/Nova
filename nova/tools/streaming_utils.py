"""
Streaming update utilities for subagents.
Provides real-time progress notifications to Telegram users.
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable

logger = logging.getLogger(__name__)

# Standard header format for streaming messages
STREAM_HEADER = "[SAU: {name}]"

# Target chat_id for live updates (default: 98746403)
DEFAULT_CHAT_ID = "98746403"

# Cached bot instance to avoid repeated imports
_cached_bot_instance = None


def _get_telegram_bot():
    """
    Get the Telegram bot instance with better fallback handling.
    Tries multiple sources to find a valid bot instance.
    """
    global _cached_bot_instance

    # Return cached instance if available
    if _cached_bot_instance:
        return _cached_bot_instance

    # Try to get from telegram_bot module using a function to avoid issues
    # with module-level global not being set yet
    try:
        # Import the module, not the instance directly
        import nova.telegram_bot as tb_module

        # Check if the module has the instance
        if hasattr(tb_module, "telegram_bot_instance"):
            bot = tb_module.telegram_bot_instance
            if bot:
                _cached_bot_instance = bot
                return bot
    except ImportError as e:
        logger.debug(f"Could not import telegram_bot module: {e}")

    # If still not available, return None (will log warning)
    return None


async def send_live_update(
    message: str,
    chat_id: Optional[str] = None,
    subagent_name: str = "Unknown",
    message_type: str = "update",
) -> bool:
    """
    Send a live streaming update to the user via Telegram.

    Args:
        message: The update message content
        chat_id: Target Telegram chat ID (defaults to 98746403)
        subagent_name: Name of the subagent sending the update
        message_type: Type of update (update, start, progress, complete, error)

    Returns:
        True if message sent successfully, False otherwise
    """
    if chat_id is None:
        chat_id = DEFAULT_CHAT_ID

    # Format with standard header
    header = STREAM_HEADER.format(name=subagent_name)

    # Add appropriate emoji based on message type
    type_emoji = {
        "start": "🚀",
        "progress": "⚙️",
        "update": "📝",
        "complete": "✅",
        "error": "❌",
        "warning": "⚠️",
    }
    emoji = type_emoji.get(message_type, "📝")

    formatted_message = f"{emoji} {header} {message}"

    try:
        # Get bot instance using our helper function
        telegram_bot_instance = _get_telegram_bot()

        if not telegram_bot_instance:
            logger.warning(
                f"Telegram bot instance not available for live update to {subagent_name}"
            )
            # Debug: try to show what's happening
            try:
                import nova.telegram_bot as tb

                logger.debug(
                    f"telegram_bot module attributes: {[a for a in dir(tb) if not a.startswith('_')]}"
                )
                logger.debug(
                    f"telegram_bot_instance value: {getattr(tb, 'telegram_bot_instance', 'NOT FOUND')}"
                )
            except Exception as e2:
                logger.debug(f"Could not inspect telegram_bot: {e2}")
            return False

        from nova.long_message_handler import send_message_with_fallback

        # Send the message (short live updates should fit in Telegram limits)
        await send_message_with_fallback(
            telegram_bot_instance,
            int(chat_id),
            formatted_message,
            title=f"Live Update: {subagent_name}",
        )
        return True

    except Exception as e:
        logger.error(f"Failed to send live update: {e}")
        return False


async def send_streaming_start(chat_id: Optional[str], name: str) -> str:
    """Send a start notification."""
    success = await send_live_update(
        message="Started working on task...",
        chat_id=chat_id,
        subagent_name=name,
        message_type="start",
    )
    return "Update sent successfully" if success else "Failed to send update"


async def send_streaming_progress(
    chat_id: Optional[str], name: str, progress: str
) -> str:
    """Send a progress update."""
    success = await send_live_update(
        message=progress, chat_id=chat_id, subagent_name=name, message_type="progress"
    )
    return "Update sent successfully" if success else "Failed to send update"


async def send_streaming_complete(
    chat_id: Optional[str], name: str, summary: Optional[str] = None
) -> str:
    """Send a completion notification."""
    msg = "Task completed successfully!"
    if summary:
        msg = f"Task completed! {summary}"
    success = await send_live_update(
        message=msg, chat_id=chat_id, subagent_name=name, message_type="complete"
    )
    return "Update sent successfully" if success else "Failed to send update"


async def send_streaming_error(chat_id: Optional[str], name: str, error: str) -> str:
    """Send an error notification."""
    success = await send_live_update(
        message=f"Error: {error}",
        chat_id=chat_id,
        subagent_name=name,
        message_type="error",
    )
    return "Update sent successfully" if success else "Failed to send update"


class StreamingContext:
    """
    Context manager for sending streaming updates during a subagent task.

    Usage:
        async with StreamingContext(chat_id, subagent_name) as stream:
            await stream.send("Processing step 1...")
            await stream.send("Processing step 2...")
            # On exit, automatically sends completion
    """

    def __init__(
        self, chat_id: Optional[str], subagent_name: str, auto_complete: bool = True
    ):
        self.chat_id = chat_id
        self.subagent_name = subagent_name
        self.auto_complete = auto_complete
        self._entered = False

    async def __aenter__(self):
        self._entered = True
        await send_streaming_start(self.chat_id, self.subagent_name)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # An exception occurred
            error_msg = str(exc_val) if exc_val else "Unknown error"
            await send_streaming_error(self.chat_id, self.subagent_name, error_msg)
        elif self.auto_complete:
            await send_streaming_complete(self.chat_id, self.subagent_name)
        return False  # Don't suppress exceptions

    async def send(self, message: str, msg_type: str = "update"):
        """Send a progress message."""
        return await send_live_update(
            message=message,
            chat_id=self.chat_id,
            subagent_name=self.subagent_name,
            message_type=msg_type,
        )


# Export the key functions for easy importing
__all__ = [
    "send_streaming_start",
    "send_streaming_progress",
    "send_streaming_complete",
    "send_streaming_error",
    "send_live_update",
    "StreamingContext",
    "DEFAULT_CHAT_ID",
    "STREAM_HEADER",
]
