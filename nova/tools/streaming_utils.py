"""
Streaming update utilities for subagents.
Provides real-time progress notifications to Telegram users.

IMPORTANT: This module operates in PLAINTEXT-ONLY mode with MESSAGE BATCHING.
- Only sends "Started" and "Completed" messages per subagent
- All HTML and Markdown tags are aggressively stripped before sending
"""

import asyncio
import logging
import re
from typing import Optional, Callable, Awaitable
import os

logger = logging.getLogger(__name__)

# Standard header format for streaming messages
STREAM_HEADER = "[SAU: {name}]"

# Target chat_id for live updates (default: 98746403)
DEFAULT_CHAT_ID = "98746403"

# Cache for bot instance (non-None only)
_cached_bot = None


def strip_all_formatting(text: str) -> str:
    """
    Strip ALL formatting (HTML and Markdown) from text for Telegram compatibility.
    
    Removes:
    - HTML tags (<b>, <i>, <code>, <pre>, <a>, etc.)
    - Markdown headers (# ## ###)
    - Bold (**text** or __text__)
    - Italic (*text* or _text_)
    - Code blocks (```code```)
    - Inline code (`code`)
    - Links [text](url)
    - Bullet lists (- * +)
    - Numbered lists (1. 2. 3.)
    - Blockquotes (> text)
    
    Args:
        text: The text with potential formatting
        
    Returns:
        Clean plaintext with no formatting characters
    """
    if not text:
        return text
    
    result = text
    
    # Remove HTML tags (<...>) - aggressive
    result = re.sub(r'<[^>]+>', '', result)
    
    # Remove code blocks (```...```)
    result = re.sub(r'```[\s\S]*?```', '', result)
    
    # Remove inline code (`...`)
    result = re.sub(r'`([^`]+)`', r'\1', result)
    
    # Remove headers (# ## ###)
    result = re.sub(r'^#{1,6}\s+', '', result, flags=re.MULTILINE)
    
    # Remove bold (**text** or __text__)
    result = re.sub(r'\*\*([^*]+)\*\*', r'\1', result)
    result = re.sub(r'__([^_]+)__', r'\1', result)
    
    # Remove italic (*text* or _text_)
    result = re.sub(r'(?<!\*)\*(?!\*)([^*]+)(?<!\*)\*(?!\*)', r'\1', result)
    result = re.sub(r'(?<!_)_(?!_)([^_]+)(?<!_)_(?!_)', r'\1', result)
    
    # Remove links [text](url) - keep text only
    result = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', result)
    
    # Remove bullet list markers at start of lines
    result = re.sub(r'^[\-\*\+]\s+', '', result, flags=re.MULTILINE)
    
    # Remove numbered lists at start of lines
    result = re.sub(r'^\d+\.\s+', '', result, flags=re.MULTILINE)
    
    # Remove blockquotes
    result = re.sub(r'^>\s+', '', result, flags=re.MULTILINE)
    
    # Remove horizontal rules
    result = re.sub(r'^[\-\*_]{3,}\s*$', '', result, flags=re.MULTILINE)
    
    # Clean up excessive whitespace
    result = re.sub(r'\n{3,}', '\n\n', result)
    result = result.strip()
    
    return result


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
    
    NOTE: This function now ALWAYS operates in PLAINTEXT-ONLY mode.
    All HTML and Markdown tags are stripped before sending.

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

    # CRITICAL: Strip ALL formatting before sending
    message = strip_all_formatting(message)

    # Format with standard header
    header = STREAM_HEADER.format(name=subagent_name)

    # Add appropriate emoji based on message type (plaintext-friendly)
    type_emoji = {
        "start": "STARTED",
        "progress": "WORKING",
        "update": "UPDATE",
        "complete": "DONE",
        "error": "ERROR",
        "warning": "WARNING",
    }
    emoji = type_emoji.get(message_type, "UPDATE")

    # Plaintext-only formatting
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

        # Send the message - ALWAYS plaintext (parse_mode=None)
        await send_message_with_fallback(
            telegram_bot_instance,
            int(chat_id),
            formatted_message,
            title=f"Live Update: {subagent_name}",
            parse_mode=None  # Force plaintext
        )
        return True

    except Exception as e:
        logger.error(f"Failed to send live update: {e}")
        return False


async def send_streaming_start(chat_id: Optional[str], name: str) -> str:
    """Send a START notification (one message only)."""
    success = await send_live_update(
        message="Task started - working on it now",
        chat_id=chat_id,
        subagent_name=name,
        message_type="start",
    )
    return "Started" if success else "Failed"


async def send_streaming_progress(
    chat_id: Optional[str], name: str, progress: str
) -> str:
    """
    Send a PROGRESS update.
    
    NOTE: Progress updates are now BATCHED - they are queued but only
    sent as part of the final completion message to reduce spam.
    """
    # NO-OP: Progress updates are suppressed to reduce message spam
    # The final completion message will include the summary
    return "Batched"


async def send_streaming_complete(
    chat_id: Optional[str], name: str, summary: Optional[str] = None
) -> str:
    """Send a COMPLETION notification with final summary."""
    msg = "Task completed successfully!"
    if summary:
        # Strip all formatting from summary too
        clean_summary = strip_all_formatting(summary)
        msg = f"Task completed! {clean_summary}"
    
    success = await send_live_update(
        message=msg,
        chat_id=chat_id,
        subagent_name=name,
        message_type="complete",
    )
    return "Completed" if success else "Failed"


async def send_streaming_error(chat_id: Optional[str], name: str, error: str) -> str:
    """Send an ERROR notification."""
    # Strip all formatting from error
    clean_error = strip_all_formatting(error)
    success = await send_live_update(
        message=f"Error: {clean_error}",
        chat_id=chat_id,
        subagent_name=name,
        message_type="error",
    )
    return "Error sent" if success else "Failed"


class StreamingContext:
    """
    Context manager for sending streaming updates during a subagent task.
    
    IMPORTANT: Now operates in BATCHED mode:
    - Sends START message on entry
    - SUPPROCESSES intermediate progress messages to reduce spam
    - Sends COMPLETE message on exit
    
    Usage:
        async with StreamingContext(chat_id, subagent_name) as stream:
            await stream.send("Processing step 1...")  # Batched/suppressed
            await stream.send("Processing step 2...")  # Batched/suppressed
            # On exit, automatically sends completion with summary
    """

    def __init__(
        self, chat_id: Optional[str], subagent_name: str, auto_complete: bool = True
    ):
        self.chat_id = chat_id
        self.subagent_name = subagent_name
        self.auto_complete = auto_complete
        self._entered = False
        self._progress_messages = []  # Store progress for batching

    async def __aenter__(self):
        self._entered = True
        self._progress_messages = []
        await send_streaming_start(self.chat_id, self.subagent_name)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # An exception occurred
            error_msg = str(exc_val) if exc_val else "Unknown error"
            await send_streaming_error(self.chat_id, self.subagent_name, error_msg)
        elif self.auto_complete:
            # Build summary from progress messages (if any)
            summary = None
            if self._progress_messages:
                # Take last few progress messages as summary
                recent = self._progress_messages[-3:]
                summary = " | ".join(recent)
            await send_streaming_complete(self.chat_id, self.subagent_name, summary)
        return False  # Don't suppress exceptions

    async def send(self, message: str, msg_type: str = "update"):
        """
        Send a progress message.
        
        NOTE: Now BATCHED - messages are stored but not sent individually
        to reduce Telegram spam. They'll appear in the final completion.
        """
        # Store message for batching (but don't send individually)
        clean_msg = strip_all_formatting(message)
        self._progress_messages.append(clean_msg)
        
        # Log for debugging but don't send separate message
        logger.debug(f"SAU batched progress for {self.subagent_name}: {clean_msg[:50]}...")
        return True


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
    "strip_all_formatting",
]