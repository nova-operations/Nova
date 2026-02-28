"""
Streaming update utilities for subagents.
Provides real-time progress notifications to Telegram users.
"""

import asyncio
import logging
import re
from typing import Optional, Callable, Awaitable
import os

logger = logging.getLogger(__name__)

# Minimal header format for streaming messages - just the name in brackets
STREAM_HEADER = "[{name}]"

# Target chat_id for live updates (default: 98746403)
DEFAULT_CHAT_ID = "98746403"

# Cache for bot instance (non-None only)
_cached_bot = None

# Real-time mode flag - MUST be True for instant delivery
REAL_TIME_MODE = True


def strip_all_formatting(text: str) -> str:
    """
    Strip ALL formatting (HTML and Markdown) from text for Telegram compatibility.
    """
    if not text:
        return ""

    result = str(text)

    # Remove HTML tags (<...>) - aggressive
    result = re.sub(r"<[^>]+>", "", result)

    # Remove code blocks (```...```)
    result = re.sub(r"```[\s\S]*?```", "", result)

    # Remove inline code (`...`)
    result = re.sub(r"`([^`]+)`", r"\1", result)

    # Remove headers (# ## ###)
    result = re.sub(r"^#{1,6}\s+", "", result, flags=re.MULTILINE)

    # Remove bold (**text** or __text__)
    result = re.sub(r"\*\*([^*]+)\*\*", r"\1", result)
    result = re.sub(r"__([^_]+)__", r"\1", result)

    # Remove italic (*text* or _text_)
    result = re.sub(r"(?<!\*)\*(?!\*)([^*]+)(?<!\*)\*(?!\*)", r"\1", result)
    result = re.sub(r"(?<!_)_(?!_)([^_]+)(?<!_)_(?!_)", r"\1", result)

    # Remove links [text](url) - keep text only
    result = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", result)

    # Remove bullet list markers at start of lines
    result = re.sub(r"^[\-\*\+]\s+", "", result, flags=re.MULTILINE)

    # Remove numbered lists at start of lines
    result = re.sub(r"^\d+\.\s+", "", result, flags=re.MULTILINE)

    # Remove blockquotes
    result = re.sub(r"^>\s+", "", result, flags=re.MULTILINE)

    # Remove horizontal rules
    result = re.sub(r"^[\-\*_]{3,}\s*$", "", result, flags=re.MULTILINE)

    # Clean up excessive whitespace
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip()

    return result


def _get_chat_id(chat_id: Optional[str], subagent_name: str = "") -> str:
    """
    Safely get the chat_id, ensuring it's always a valid numeric string.
    """
    if chat_id is None or chat_id == "" or str(chat_id).lower() == "none":
        return DEFAULT_CHAT_ID

    chat_id_str = str(chat_id).strip()
    if not chat_id_str.isdigit():
        return DEFAULT_CHAT_ID

    return chat_id_str


def _get_telegram_bot():
    """
    Get the Telegram bot instance with comprehensive fallback handling.
    """
    global _cached_bot
    if _cached_bot is not None:
        return _cached_bot

    try:
        import nova.telegram_bot as tb_module
        if hasattr(tb_module, "telegram_bot_instance"):
            bot = tb_module.telegram_bot_instance
            if bot is not None:
                _cached_bot = bot
                return bot
    except ImportError:
        pass

    try:
        import sys
        for mod_name, mod in sys.modules.items():
            if "telegram_bot" in mod_name and mod is not None:
                if hasattr(mod, "telegram_bot_instance"):
                    bot = mod.telegram_bot_instance
                    if bot is not None:
                        _cached_bot = bot
                        return bot
    except Exception:
        pass

    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if token:
            from telegram import Bot
            bot = Bot(token=token)
            _cached_bot = bot
            return bot
    except Exception:
        pass

    return None


async def _ensure_bot_initialized(bot):
    if bot and hasattr(bot, "initialize") and not hasattr(bot, "_initialized"):
        try:
            await bot.initialize()
            bot._initialized = True
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
    """
    chat_id = _get_chat_id(chat_id, subagent_name)
    message = strip_all_formatting(str(message))
    
    if not message:
        return False

    header = STREAM_HEADER.format(name=subagent_name)
    formatted_message = f"{header} {message}"

    try:
        bot = _get_telegram_bot()
        if not bot:
            return False

        await _ensure_bot_initialized(bot)
        
        # Determine how to send
        from nova.long_message_handler import send_message_with_fallback
        
        # We use short-circuit here: if message is tiny, send directly to avoid PDF overhead
        if len(formatted_message) < 3800:
            await bot.send_message(
                chat_id=int(chat_id),
                text=formatted_message,
                parse_mode=None
            )
        else:
            await send_message_with_fallback(
                bot,
                int(chat_id),
                formatted_message,
                title=f"SAU: {subagent_name}",
                parse_mode=None
            )
        return True
    except Exception as e:
        logger.error(f"Failed to send live update: {e}")
        return False


async def send_streaming_start(chat_id: Optional[str], name: str) -> str:
    success = await send_live_update("Task started", chat_id, name, "start")
    return "Started" if success else "Failed"


async def send_streaming_progress(chat_id: Optional[str], name: str, progress: str) -> str:
    success = await send_live_update(progress, chat_id, name, "progress")
    return "Sent" if success else "Failed"


async def send_streaming_complete(chat_id: Optional[str], name: str, summary: Optional[str] = None) -> str:
    msg = f"Task completed successfully! {summary if summary else ''}"
    success = await send_live_update(msg, chat_id, name, "complete")
    return "Completed" if success else "Failed"


async def send_streaming_error(chat_id: Optional[str], name: str, error: str) -> str:
    success = await send_live_update(f"Error: {error}", chat_id, name, "error")
    return "Error sent" if success else "Failed"


class StreamingContext:
    def __init__(self, chat_id: Optional[str], subagent_name: str, auto_complete: bool = True):
        self.chat_id = _get_chat_id(chat_id, subagent_name)
        self.subagent_name = subagent_name
        self.auto_complete = auto_complete
        self._progress_messages = []

    async def __aenter__(self):
        await send_streaming_start(self.chat_id, self.subagent_name)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await send_streaming_error(self.chat_id, self.subagent_name, str(exc_val))
        elif self.auto_complete:
            # Send completion with a tiny summary of the last 200 chars if available
            summary = ""
            if self._progress_messages:
                summary = f"Result: {self._progress_messages[-1][:200]}..."
            await send_streaming_complete(self.chat_id, self.subagent_name, summary)
        return False

    async def send(self, message: str, msg_type: str = "update"):
        clean_msg = strip_all_formatting(str(message))
        if not clean_msg:
            return False
        self._progress_messages.append(clean_msg)
        await send_live_update(clean_msg, self.chat_id, self.subagent_name, "progress")
        return True