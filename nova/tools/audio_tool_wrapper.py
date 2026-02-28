"""
Audio tool wrapper for Nova agent integration.
Provides send_audio_message as an agent tool.
"""
import os
import logging
from typing import Optional

from telegram import Bot
from nova.tools.context_optimizer import wrap_tool_output_optimization

logger = logging.getLogger(__name__)


# Get bot instance from telegram_bot module
def get_telegram_bot():
    """Get the Telegram bot instance for sending messages."""
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
async def send_audio_message_tool(
    text: str, chat_id: str, voice: str = "nova", caption: Optional[str] = None
) -> str:
    """
    Send an audio (voice) message to a Telegram user.

    Args:
        text: The text to convert to speech and send as audio
        chat_id: The target user's Telegram chat ID
        voice: Voice to use (nova, alloy, echo, fable, onyx, shimmer)
        caption: Optional plaintext caption for the audio message

    Returns:
        Success or error message
    """
    from nova.tools.audio_tools import send_audio_message

    bot = get_telegram_bot()

    if not bot:
        return "Error: Telegram bot not available"

    try:
        chat_id_int = int(chat_id)
    except ValueError:
        return f"Error: Invalid chat_id '{chat_id}' - must be a number"

    try:
        success = await send_audio_message(
            bot=bot,
            chat_id=chat_id_int,
            text=text,
            voice=voice,
            caption=caption,  # Plaintext only - no markdown
        )

        if success:
            return f"Audio message sent successfully to {chat_id}"
        else:
            return "Failed to send audio message"

    except Exception as e:
        logger.error(f"Error sending audio message: {e}")
        return f"Error sending audio: {str(e)}"


# Export for agent tools
send_audio_message = send_audio_message_tool
