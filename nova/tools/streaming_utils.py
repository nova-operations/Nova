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

# Timeout configuration for Telegram API calls (in seconds)
# These are more aggressive for streaming updates to avoid blocking
# connect_timeout: time to establish connection  
# read_timeout: time to wait for response
TELEGRAM_CONNECT_TIMEOUT = 5.0
TELEGRAM_READ_TIMEOUT = 10.0

# Retry configuration - reduced for streaming to fail fast
MAX_RETRIES = 2
RETRY_DELAY = 0.5  # seconds

# Flag to disable streaming in case of persistent failures
_streaming_disabled = False
_streaming_failure_count = 0
STREAMING_DISABLE_THRESHOLD = 5  # Disable after 5 consecutive failures


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
    """Ensure the bot is properly initialized."""
    if bot is None:
        return False
        
    try:
        # Check if bot has request attribute and needs initialization
        if hasattr(bot, "initialize") and not hasattr(bot, "_initialized"):
            await bot.initialize()
            bot._initialized = True
            logger.info("Telegram bot initialized successfully")
        return True
    except Exception as e:
        logger.warning(f"Bot initialization failed: {e}")
        return False


def _increment_failure_count():
    """Track consecutive failures to optionally disable streaming."""
    global _streaming_failure_count, _streaming_disabled
    _streaming_failure_count += 1
    if _streaming_failure_count >= STREAMING_DISABLE_THRESHOLD:
        _streaming_disabled = True
        logger.warning(f"Streaming disabled after {_streaming_failure_count} consecutive failures")


def _reset_failure_count():
    """Reset failure counter on successful send."""
    global _streaming_failure_count, _streaming_disabled
    _streaming_failure_count = 0
    if _streaming_disabled:
        _streaming_disabled = False
        logger.info("Streaming re-enabled after successful send")


async def _send_with_retry(bot, chat_id: int, message: str, parse_mode=None, is_document: bool = False, document_path: str = None, caption: str = None) -> bool:
    """
    Send a message with retry logic and proper timeout handling.
    Uses aggressive timeouts for streaming to avoid blocking.
    
    Args:
        bot: The telegram bot instance
        chat_id: Target chat ID
        message: Message text
        parse_mode: Parse mode (None for plaintext)
        is_document: Whether to send as document
        document_path: Path to document if sending as file
        caption: Caption for document
    
    Returns:
        True if sent successfully, False otherwise
    """
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        try:
            if is_document and document_path:
                # Send document with timeout
                with open(document_path, "rb") as pdf_file:
                    await asyncio.wait_for(
                        bot.send_document(
                            chat_id=chat_id,
                            document=pdf_file,
                            caption=caption,
                            parse_mode=parse_mode,
                            connect_timeout=TELEGRAM_CONNECT_TIMEOUT,
                            read_timeout=TELEGRAM_READ_TIMEOUT,
                        ),
                        timeout=TELEGRAM_READ_TIMEOUT + 2  # Overall timeout
                    )
            else:
                # Send regular message with timeout
                await asyncio.wait_for(
                    bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=parse_mode,
                        connect_timeout=TELEGRAM_CONNECT_TIMEOUT,
                        read_timeout=TELEGRAM_READ_TIMEOUT,
                    ),
                    timeout=TELEGRAM_READ_TIMEOUT + 2  # Overall timeout
                )
            # Success - reset failure counter
            _reset_failure_count()
            return True
            
        except asyncio.TimeoutError:
            last_error = f"Timeout on attempt {attempt + 1}/{MAX_RETRIES}"
            logger.warning(f"Timeout sending message to {chat_id}: {last_error}")
            # Increment failure count for timeouts
            _increment_failure_count()
        except Exception as e:
            last_error = f"Error on attempt {attempt + 1}/{MAX_RETRIES}: {e}"
            logger.warning(f"Failed to send message to {chat_id}: {last_error}")
            # Check for network/connection errors that should increment failure count
            err_str = str(e).lower()
            if any(x in err_str for x in ['timeout', 'network', 'connection', 'unavailable', 'conflict']):
                _increment_failure_count()
        
        # Wait before retry (exponential backoff)
        if attempt < MAX_RETRIES - 1:
            wait_time = RETRY_DELAY * (2 ** attempt)  # 0.5s, 1s
            logger.info(f"Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)
    
    logger.error(f"All {MAX_RETRIES} attempts failed for chat {chat_id}: {last_error}")
    return False


async def send_live_update(
    message: str,
    chat_id: Optional[str] = None,
    subagent_name: str = "Unknown",
    message_type: str = "update",
    silent: bool = False,
) -> bool:
    """
    Send a live streaming update to the user via Telegram.
    Includes retry logic and timeout handling.
    
    Note: This function is designed to be non-blocking. If Telegram is slow,
    it will fail gracefully rather than block the subagent workflow.
    """
    global _streaming_disabled
    
    # Check if streaming has been disabled due to persistent failures
    if _streaming_disabled:
        logger.debug(f"Streaming disabled, skipping message to {chat_id}")
        return False
        
    if silent:
        return True

    chat_id = _get_chat_id(chat_id, subagent_name)
    message = strip_all_formatting(str(message))

    if not message:
        return False

    header = STREAM_HEADER.format(name=subagent_name)
    formatted_message = f"{header} {message}"

    try:
        bot = _get_telegram_bot()
        if not bot:
            logger.debug("No Telegram bot available")
            _increment_failure_count()
            return False

        # Determine how to send
        from nova.long_message_handler import send_message_with_fallback

        # We use short-circuit here: if message is tiny, send directly to avoid PDF overhead
        if len(formatted_message) < 3800:
            # Use retry-enabled sender with explicit timeouts
            success = await _send_with_retry(
                bot,
                int(chat_id),
                formatted_message,
                parse_mode=None
            )
            if not success:
                # Fallback to original function if retry fails - but with timeout
                logger.warning("Using fallback send method after retries failed")
                try:
                    await asyncio.wait_for(
                        bot.send_message(
                            chat_id=int(chat_id),
                            text=formatted_message,
                            parse_mode=None,
                            connect_timeout=TELEGRAM_CONNECT_TIMEOUT,
                            read_timeout=TELEGRAM_READ_TIMEOUT,
                        ),
                        timeout=TELEGRAM_READ_TIMEOUT + 2
                    )
                    _reset_failure_count()
                    return True
                except asyncio.TimeoutError:
                    logger.warning(f"Fallback send timed out for chat {chat_id}")
                    _increment_failure_count()
                    return False
                except Exception as e:
                    logger.error(f"Fallback send also failed: {e}")
                    _increment_failure_count()
                    return False
            return success
        else:
            # For long messages, use the fallback handler with timeout
            try:
                await asyncio.wait_for(
                    send_message_with_fallback(
                        bot,
                        int(chat_id),
                        formatted_message,
                        title=f"SAU: {subagent_name}",
                        parse_mode=None,
                    ),
                    timeout=TELEGRAM_READ_TIMEOUT + 5
                )
                _reset_failure_count()
                return True
            except asyncio.TimeoutError:
                logger.warning(f"Long message send timed out for chat {chat_id}")
                _increment_failure_count()
                return False
    except Exception as e:
        logger.error(f"Failed to send live update: {e}")
        _increment_failure_count()
        return False


async def send_streaming_start(
    chat_id: Optional[str], name: str, silent: bool = False
) -> str:
    success = await send_live_update(
        "Task started", chat_id, name, "start", silent=silent
    )
    return "Started" if success else "Failed"


async def send_streaming_progress(
    chat_id: Optional[str], name: str, progress: str, silent: bool = False
) -> str:
    success = await send_live_update(progress, chat_id, name, "progress", silent=silent)
    return "Sent" if success else "Failed"


async def send_streaming_complete(
    chat_id: Optional[str],
    name: str,
    summary: Optional[str] = None,
    silent: bool = False,
) -> str:
    msg = f"Task completed successfully! {summary if summary else ''}"
    success = await send_live_update(msg, chat_id, name, "complete", silent=silent)
    return "Completed" if success else "Failed"


async def send_streaming_error(
    chat_id: Optional[str], name: str, error: str, silent: bool = False
) -> str:
    success = await send_live_update(
        f"Error: {error}", chat_id, name, "error", silent=silent
    )
    return "Error sent" if success else "Failed"


class StreamingContext:
    def __init__(
        self,
        chat_id: Optional[str],
        subagent_name: str,
        auto_complete: bool = True,
        silent: bool = False,
    ):
        self.chat_id = _get_chat_id(chat_id, subagent_name)
        self.subagent_name = subagent_name
        self.auto_complete = auto_complete
        self.silent = silent
        self._progress_messages = []

    async def __aenter__(self):
        await send_streaming_start(self.chat_id, self.subagent_name, silent=self.silent)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await send_streaming_error(self.chat_id, self.subagent_name, str(exc_val))
        elif self.auto_complete:
            # Send completion with a tiny summary of the last 200 chars if available
            summary = ""
            if self._progress_messages:
                summary = f"Result: {self._progress_messages[-1][:200]}..."
            await send_streaming_complete(
                self.chat_id, self.subagent_name, summary, silent=self.silent
            )
        return False

    async def send(self, message: str, msg_type: str = "update", silent: bool = False):
        if self.silent or silent:
            return True
        clean_msg = strip_all_formatting(str(message))
        if not clean_msg:
            return False
        self._progress_messages.append(clean_msg)
        await send_live_update(clean_msg, self.chat_id, self.subagent_name, "progress")
        return True