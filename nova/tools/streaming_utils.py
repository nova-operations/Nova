"""
Streaming update utilities for subagents.
Provides real-time progress notifications to Telegram users.
"""
import asyncio
import logging
from typing import Optional, Callable, Awaitable
import os

logger = logging.getLogger(__name__)

# Standard header format for streaming messages
STREAM_HEADER = "[SAU: {name}]"

# Target chat_id for live updates (default: 98746403)
DEFAULT_CHAT_ID = "98746403"

# Cache for bot instance (non-None only)
_cached_bot = None


def _get_telegram_bot():
    """
    Get the Telegram bot instance with comprehensive fallback handling.
    
    Tries multiple sources in order:
    1. Import from nova.telegram_bot module (works when bot is running as main)
    2. Search sys.modules for any loaded telegram_bot with an instance
    3. Create a new bot instance from TELEGRAM_BOT_TOKEN (last resort)
    """
    global _cached_bot
    
    # Return cached bot if we already have one
    if _cached_bot is not None:
        return _cached_bot
    
    # First, try to get from telegram_bot module
    try:
        import nova.telegram_bot as tb_module

        # Check if the module has the instance and it's not None
        if hasattr(tb_module, "telegram_bot_instance"):
            bot = tb_module.telegram_bot_instance
            if bot is not None:
                logger.debug("Found telegram_bot_instance in nova.telegram_bot module")
                _cached_bot = bot
                return bot
    except ImportError as e:
        logger.debug(f"Could not import telegram_bot module: {e}")

    # Try alternate import path for when running as subagent
    try:
        import sys
        # Check if telegram_bot is in sys.modules
        for mod_name, mod in sys.modules.items():
            if 'telegram_bot' in mod_name and mod is not None:
                if hasattr(mod, 'telegram_bot_instance'):
                    bot = mod.telegram_bot_instance
                    if bot is not None:
                        logger.debug(f"Found bot in sys.modules: {mod_name}")
                        _cached_bot = bot
                        return bot
    except Exception as e:
        logger.debug(f"Error searching sys.modules: {e}")

    # Last resort: try creating a bot from the token
    try:
        from telegram import Bot
        from telegram.error import TelegramError
        
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            # Create a new bot instance (expensive but works as fallback)
            logger.debug("Creating new Telegram bot instance from token (fallback)")
            bot = Bot(token=token)
            # Store in cache - initialization will be done when sending message
            _cached_bot = bot
            return bot
    except ImportError:
        logger.debug("telegram package not available")
    except TelegramError as e:
        logger.debug(f"Failed to create bot: {e}")

    # If still not available, return None
    logger.warning("Telegram bot instance not found - SAU updates will be disabled")
    return None


async def _ensure_bot_initialized(bot):
    """Ensure the bot is initialized before use."""
    if bot and hasattr(bot, 'initialize') and not hasattr(bot, '_initialized'):
        try:
            await bot.initialize()
            bot._initialized = True
            logger.debug("Bot initialized successfully")
        except Exception as e:
            logger.warning(f"Bot initialization failed: {e}")


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
            return False

        # Ensure bot is initialized before sending
        await _ensure_bot_initialized(telegram_bot_instance)

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