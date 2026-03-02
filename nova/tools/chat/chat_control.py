"""
Chat Control Tools for Nova

Provides native Telegram chat actions:
- reply_to_message: Reply to a specific message by its ID
- pin_message: Pin a message in the chat
- unpin_message: Unpin a message in the chat
- forward_message: Forward a message to another chat
- delete_message: Delete a message from the chat
"""
import os
import logging
from typing import Optional

from telegram import Bot
from nova.tools.core.context_optimizer import wrap_tool_output_optimization

logger = logging.getLogger(__name__)


def _get_telegram_bot():
    """Get the Telegram bot instance (shared with audio_tool_wrapper)."""
    try:
        from nova import telegram_bot

        if telegram_bot.telegram_bot_instance:
            return telegram_bot.telegram_bot_instance
    except Exception as e:
        logger.warning(f"Could not import telegram_bot instance: {e}")

    # Fallback: create new bot instance
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if telegram_token:
        return Bot(token=telegram_token)

    return None


@wrap_tool_output_optimization
async def reply_to_message(
    chat_id: str,
    message_id: int,
    text: str,
) -> str:
    """
    Reply to a specific message in the chat by its message_id.
    Use this when the user asks you to reply to something, or when quoting
    a previous message makes the conversation clearer.

    Args:
        chat_id: The Telegram chat ID.
        message_id: The message_id of the message to reply to.
        text: The reply text content.

    Returns:
        Success or error message including the sent message's ID.
    """
    bot = _get_telegram_bot()
    if not bot:
        return "Error: Telegram bot not available"

    try:
        sent = await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            reply_to_message_id=message_id,
        )
        return f"Replied successfully (sent message_id={sent.message_id})"
    except Exception as e:
        logger.error(f"reply_to_message error: {e}")
        return f"Error replying to message: {e}"


@wrap_tool_output_optimization
async def pin_message(
    chat_id: str,
    message_id: int,
    disable_notification: bool = False,
) -> str:
    """
    Pin a message in the chat. Use this when the user asks you to pin
    a message so it stays visible at the top of the chat.

    Args:
        chat_id: The Telegram chat ID.
        message_id: The message_id of the message to pin.
        disable_notification: If True, pin silently without notifying members.

    Returns:
        Success or error message.
    """
    bot = _get_telegram_bot()
    if not bot:
        return "Error: Telegram bot not available"

    try:
        await bot.pin_chat_message(
            chat_id=int(chat_id),
            message_id=message_id,
            disable_notification=disable_notification,
        )
        return f"Message {message_id} pinned successfully"
    except Exception as e:
        logger.error(f"pin_message error: {e}")
        return f"Error pinning message: {e}"


@wrap_tool_output_optimization
async def unpin_message(
    chat_id: str,
    message_id: int,
) -> str:
    """
    Unpin a specific pinned message in the chat.

    Args:
        chat_id: The Telegram chat ID.
        message_id: The message_id of the pinned message to unpin.

    Returns:
        Success or error message.
    """
    bot = _get_telegram_bot()
    if not bot:
        return "Error: Telegram bot not available"

    try:
        await bot.unpin_chat_message(
            chat_id=int(chat_id),
            message_id=message_id,
        )
        return f"Message {message_id} unpinned successfully"
    except Exception as e:
        logger.error(f"unpin_message error: {e}")
        return f"Error unpinning message: {e}"


@wrap_tool_output_optimization
async def forward_message(
    from_chat_id: str,
    to_chat_id: str,
    message_id: int,
) -> str:
    """
    Forward a message from one chat to another.

    Args:
        from_chat_id: The source chat ID.
        to_chat_id: The destination chat ID.
        message_id: The message_id to forward.

    Returns:
        Success or error message.
    """
    bot = _get_telegram_bot()
    if not bot:
        return "Error: Telegram bot not available"

    try:
        sent = await bot.forward_message(
            chat_id=int(to_chat_id),
            from_chat_id=int(from_chat_id),
            message_id=message_id,
        )
        return f"Message forwarded successfully (new message_id={sent.message_id})"
    except Exception as e:
        logger.error(f"forward_message error: {e}")
        return f"Error forwarding message: {e}"


@wrap_tool_output_optimization
async def delete_message(
    chat_id: str,
    message_id: int,
) -> str:
    """
    Delete a message from the chat. The bot can only delete messages
    it sent, or messages in groups/supergroups where it has delete permissions.

    Args:
        chat_id: The Telegram chat ID.
        message_id: The message_id to delete.

    Returns:
        Success or error message.
    """
    bot = _get_telegram_bot()
    if not bot:
        return "Error: Telegram bot not available"

    try:
        await bot.delete_message(
            chat_id=int(chat_id),
            message_id=message_id,
        )
        return f"Message {message_id} deleted successfully"
    except Exception as e:
        logger.error(f"delete_message error: {e}")
        return f"Error deleting message: {e}"
