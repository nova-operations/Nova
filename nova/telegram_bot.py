import os
import logging
import asyncio
import tempfile
from typing import List, Optional, Any
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
from nova.agent import get_agent
from nova.logger import setup_logging
from nova.tools.heartbeat import get_heartbeat_monitor
from nova.tools.subagent import SUBAGENTS
from agno.media import Audio, Image

# Import the middle-out transformer for explicit prompt compression
from nova.tools.prompt_transformer import (
    MiddleOutTransformer,
    get_transformer,
    DEFAULT_TOKEN_LIMIT,
    SAFE_TOKEN_LIMIT,
)

from nova.long_message_handler import (
    send_message_with_fallback,
    strip_all_formatting,
    TELEGRAM_MAX_LENGTH,
    is_message_too_long,
    create_pdf_from_text,
    process_long_message,
)

import sys

setup_logging()


# Track active tasks per chat to ensure smooth coordination
_ACTIVE_TASKS = {}  # chat_id -> task_name/status
_TASK_QUEUES = {}  # chat_id -> List of messages
_PROCESSING_LOCKS = {}  # chat_id -> asyncio.Lock


def is_authorized(user_id: int) -> bool:
    """Checks if the user is in the authorized whitelist."""
    whitelist_str = os.getenv("TELEGRAM_USER_WHITELIST", "")
    if not whitelist_str:
        logging.warning("TELEGRAM_USER_WHITELIST is not set. Bot is open to everyone.")
        return True

    whitelist = [sid.strip() for sid in whitelist_str.split(",") if sid.strip()]
    return str(user_id) in whitelist


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logging.warning(f"Unauthorized access attempt by user_id: {user_id}")
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Hello! I am Nova (User ID: {user_id}). I can run commands, manage files, spawn subagents, and manage scheduled tasks. I now support VOICE, AUDIO, and IMAGE inputs! How can I help you?",
    )


async def handle_multimodal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice, audio, and photo messages by converting them to text context."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    chat_id = update.effective_chat.id
    message = update.message

    # Placeholder for the final text context
    context_text = ""

    # Handle Voice/Audio via Transcription
    if message.voice or message.audio:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        audio_obj = message.voice if message.voice else message.audio

        try:
            # Download file
            new_file = await context.bot.get_file(audio_obj.file_id)
            audio_bytes = await new_file.download_as_bytearray()

            # Create Agno Audio object
            audio_media = Audio(
                content=bytes(audio_bytes), format="ogg" if message.voice else "mp3"
            )

            # Pass to Nova natively without a system prompt
            await handle_message(
                update,
                context,
                audio=[audio_media],
            )
            return

        except Exception as e:
            logging.error(f"Error processing audio: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text="I heard your voice, but I had trouble transcribing it. Checking my systems! 🎙️",
            )
            return

    # Handle Photo via Vision Analysis
    if message.photo:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        photo = message.photo[-1]  # Highest resolution
        new_file = await context.bot.get_file(photo.file_id)
        photo_bytes = await new_file.download_as_bytearray()

        # Create Agno Image object
        image_media = Image(content=bytes(photo_bytes))

        await handle_message(
            update,
            context,
            images=[image_media],
        )
        return


async def heartbeat_callback(report: str, records: List[object]):
    """Callback for heartbeat monitor to send updates to relevant Telegram chats."""
    if not records:
        return

    chats_to_update = {}
    for record in records:
        if record.chat_id:
            try:
                cid = int(record.chat_id)
                if cid not in chats_to_update:
                    chats_to_update[cid] = []
                chats_to_update[cid].append(record)
            except (ValueError, TypeError):
                continue

    if not chats_to_update:
        return

    global telegram_bot_instance
    if not telegram_bot_instance:
        return

    for chat_id, chat_records in chats_to_update.items():
        finished_records = [
            r for r in chat_records if r.status in ["completed", "failed"]
        ]
        active_records = [
            r for r in chat_records if r.status in ["running", "starting"]
        ]

        for r in finished_records:
            status_text = "DONE" if r.status == "completed" else "FAILED"
            clean_result = strip_all_formatting(str(r.result))
            msg = f"{status_text} Subagent '{r.name}' finished!\n\nResult:\n{clean_result}"

            success, status = await send_message_with_fallback(
                telegram_bot_instance, chat_id, msg, title=f"Subagent Report: {r.name}"
            )

            if not success:
                logging.error(f"Failed to send completion message to {chat_id}")

        if active_records:
            header = "Nova Team Status"
            lines = [header, ""]
            for r in active_records:
                status_indicator = "RUNNING" if r.status == "running" else "STARTING"
                lines.append(f"{status_indicator}: {r.name}")

            msg = "\n".join(lines)

            await send_message_with_fallback(
                telegram_bot_instance, chat_id, msg, title="Heartbeat Update"
            )


async def notify_user(chat_id: str, message: str):
    """Proactively send a message to a user with long message support."""
    global telegram_bot_instance
    if not telegram_bot_instance:
        return

    clean_message = strip_all_formatting(message)

    try:
        await send_message_with_fallback(
            telegram_bot_instance,
            int(chat_id),
            clean_message,
            title="Nova Notification",
        )
    except Exception as e:
        logging.error(f"Failed proactive notification to {chat_id}: {e}")


# Global bot instance for heartbeats
telegram_bot_instance = None

# Global transformer instance for prompt compression
_prompt_transformer: Optional[MiddleOutTransformer] = None


def get_prompt_transformer() -> MiddleOutTransformer:
    """Get or create the global prompt transformer."""
    global _prompt_transformer
    if _prompt_transformer is None:
        max_tokens = int(os.getenv("MAX_CONTEXT_TOKENS", str(DEFAULT_TOKEN_LIMIT)))
        _prompt_transformer = MiddleOutTransformer(max_tokens)
    return _prompt_transformer


async def get_reply_context(update: Update) -> str:
    """Extract context from the message being replied to."""
    if not update.message or not update.message.reply_to_message:
        return ""

    replied_msg = update.message.reply_to_message
    context = "[REPLY CONTEXT]\n"

    if replied_msg.from_user:
        context += f"Author: {replied_msg.from_user.first_name}\n"

    if replied_msg.text:
        context += f"Message: {replied_msg.text}\n"
    elif replied_msg.caption:
        context += f"Caption: {replied_msg.caption}\n"

    context += "---\n"
    return context


async def reinvigorate_nova(
    chat_id: str,
    message: str,
    images: Optional[List[Image]] = None,
    audio: Optional[List[Audio]] = None,
):
    """
    Internal 'wake up' mechanism for Nova.
    This allows subagents or background tasks to re-engage Nova proactively.
    """
    global telegram_bot_instance
    if not telegram_bot_instance:
        return

    cid = int(chat_id)
    if cid not in _PROCESSING_LOCKS:
        _PROCESSING_LOCKS[cid] = asyncio.Lock()

    lock = _PROCESSING_LOCKS[cid]

    # System-triggered message
    system_prompt = f"[INTERNAL SYSTEM ALERT]\nA background task has produced the following result/failure:\n---\n{message}\n---\nNova, analyze this background event and decide if you need to take proactive action (e.g. fix a failure, delegate a recovery task, or notify the user)."

    # Get user id for session tracking
    whitelist_str = os.getenv("TELEGRAM_USER_WHITELIST", "")
    if not whitelist_str:
        return
    user_id = int(whitelist_str.split(",")[0].strip())

    # Trigger a new run in the background
    asyncio.create_task(
        process_nova_intent(cid, user_id, system_prompt, images=images, audio=audio)
    )


async def process_nova_intent(
    chat_id: int,
    user_id: int,
    message: str,
    images: Optional[List[Image]] = None,
    audio: Optional[List[Audio]] = None,
):
    """Core logic to run a Nova iteration without requiring a Telegram Update object."""
    if chat_id not in _PROCESSING_LOCKS:
        _PROCESSING_LOCKS[chat_id] = asyncio.Lock()

    lock = _PROCESSING_LOCKS[chat_id]

    async with lock:
        global telegram_bot_instance
        if telegram_bot_instance:
            await telegram_bot_instance.send_chat_action(
                chat_id=chat_id, action="typing"
            )

        try:
            agent = get_agent(chat_id=str(chat_id))
            session_id = str(user_id)

            # Subagent monitoring
            from nova.tools.subagent import SUBAGENTS as ACTIVE_SUBAGENTS

            active_subs = [
                s["name"]
                for s in ACTIVE_SUBAGENTS.values()
                if s.get("chat_id") == str(chat_id) and s.get("status") == "running"
            ]
            if active_subs:
                message = f"[SYSTEM NOTE: You have active subagents running: {', '.join(active_subs)}]\n{message}"

            response = await agent.arun(
                message, session_id=session_id, images=images, audio=audio
            )

            if response and response.content and telegram_bot_instance:
                await send_message_with_fallback(
                    telegram_bot_instance,
                    chat_id,
                    response.content,
                    title="Nova Response",
                )
        except Exception as e:
            logging.error(f"Error in process_nova_intent: {e}")
            if telegram_bot_instance:
                await send_message_with_fallback(
                    telegram_bot_instance,
                    chat_id,
                    f"⚠️ Error: {str(e)}\n\nI'm having trouble processing your request. Please check my logs or try again.",
                )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    override_text: str = None,
    images: Optional[List[Image]] = None,
    audio: Optional[List[Audio]] = None,
):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logging.warning(f"Unauthorized message from user_id: {user_id}")
        return

    user_message = override_text if override_text else update.message.text
    # If it's a photo/voice with a caption and no override, use the caption
    if not user_message and update.message and update.message.caption:
        user_message = update.message.caption

    # Allow processing if we have either text or media
    if not user_message and not images and not audio:
        return

    chat_id = update.effective_chat.id
    session_id = str(user_id)

    # Check for reply context
    reply_context = await get_reply_context(update)
    if reply_context:
        user_message = reply_context + user_message

    # Concurrency Management: Immediate Engagement
    if chat_id not in _PROCESSING_LOCKS:
        _PROCESSING_LOCKS[chat_id] = asyncio.Lock()

    lock = _PROCESSING_LOCKS[chat_id]

    if lock.locked():
        await context.bot.send_message(
            chat_id=chat_id,
            text="I'm currently processing your previous request. I've noted this and will address it immediately after! 🛰️",
        )

    # Call the core intent processor (which handles its own locking)
    await process_nova_intent(
        chat_id, user_id, user_message, images=images, audio=audio
    )


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handles errors in the telegram bot."""
    if context.error and "Conflict: terminated by other getUpdates request" in str(
        context.error
    ):
        logging.warning("Conflict detected.")
    else:
        logging.error(f"Update {update} caused error {context.error}")


async def post_init(application):
    """Callback to run after the bot starts and the loop is running."""
    from nova.tools.scheduler import initialize_scheduler

    try:
        initialize_scheduler()
    except Exception:
        pass

    monitor = get_heartbeat_monitor()

    def hb_wrapper(report, records):
        asyncio.create_task(heartbeat_callback(report, records))

    monitor.register_callback(hb_wrapper)
    monitor.start()
    get_prompt_transformer()


if __name__ == "__main__":
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        print("Error: TELEGRAM_BOT_TOKEN not set.")
        exit(1)

    application = (
        ApplicationBuilder().token(telegram_token).post_init(post_init).build()
    )

    telegram_bot_instance = application.bot

    try:
        import nova.telegram_bot

        nova.telegram_bot.telegram_bot_instance = application.bot
    except Exception:
        pass

    application.add_error_handler(handle_error)

    # Handlers
    application.add_handler(CommandHandler("start", start))

    # Text Messages
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    )

    # Voice, Audio, and Photos
    application.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO | filters.PHOTO, handle_multimodal)
    )

    print("Nova Agent Bot is running with MULTIMODAL support (Voice/Photo)...")
    application.run_polling()
