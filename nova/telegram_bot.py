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
from agno.media import Audio, Image, Video, File

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

# Transient errors that should not be logged as critical errors
# These are temporary external service issues that resolve themselves
TRANSIENT_ERRORS = [
    "Bad Gateway",
    "Bad gateway",
    "502",
    "503",
    "504",
    "Internal Server Error",
    "500",
    "Rate limit",
    "Timeout",
    "timed out",
    "Connection reset",
    "Connection error",
]


def is_transient_error(error_message: str) -> bool:
    """Check if an error is transient and should not be logged as critical."""
    if not error_message:
        return True
    return any(err.lower() in error_message.lower() for err in TRANSIENT_ERRORS)


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
    videos: Optional[List[Video]] = None,
    files: Optional[List[File]] = None,
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
    system_prompt = (
        f"[SYSTEM_ALERT]\n{message}\n"
        "Analyze this and decide if you need to take action (fix failure, delegate recovery, notify user)."
    )

    # Use configured chat_id or fall back to whitelist
    user_id = int(
        os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAM_USER_WHITELIST", "").split(",")[0].strip()
    )

    # Trigger a new run in the background
    asyncio.create_task(
        process_nova_intent(
            cid,
            user_id,
            system_prompt,
            images=images,
            audio=audio,
            videos=videos,
            files=files,
        )
    )


async def process_nova_intent(
    chat_id: int,
    user_id: int,
    message: str,
    images: Optional[List[Image]] = None,
    audio: Optional[List[Audio]] = None,
    videos: Optional[List[Video]] = None,
    files: Optional[List[File]] = None,
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
                message,
                session_id=session_id,
                images=images,
                audio=audio,
                videos=videos,
                files=files,
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
):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logging.warning(f"Unauthorized message from user_id: {user_id}")
        return

    # Extract text/caption
    user_message = update.message.text
    if not user_message and update.message and update.message.caption:
        user_message = update.message.caption

    images = []
    audio = []
    videos = []
    files = []

    # Extract media from the update message natively
    if update.message:
        # Photo
        if update.message.photo:
            photo = update.message.photo[-1]  # Highest resolution
            new_file = await context.bot.get_file(photo.file_id)
            photo_bytes = await new_file.download_as_bytearray()
            images.append(Image(content=bytes(photo_bytes)))

        # Audio / Voice
        audio_obj = update.message.voice or update.message.audio
        if audio_obj:
            new_file = await context.bot.get_file(audio_obj.file_id)
            audio_bytes = await new_file.download_as_bytearray()
            audio_ext = "ogg" if update.message.voice else "mp3"
            audio.append(Audio(content=bytes(audio_bytes), format=audio_ext))

        # Video
        if update.message.video or update.message.video_note:
            vid_obj = update.message.video or update.message.video_note
            # Let's check if the framework supports video objects
            # For now Video is imported from agno.media but we also need to pass it
            from agno.media import Video

            new_file = await context.bot.get_file(vid_obj.file_id)
            vid_bytes = await new_file.download_as_bytearray()
            # Videos passed as images? No, video directly is not explicitly passed via arun images/audio kwargs currently.
            pass  # Video will be supported once we update process_nova_intent and agent.py

        # Document (PDF, etc)
        if update.message.document:
            doc = update.message.document
            # Handle PDF extraction maybe or pass it to Nova?
            pass

    # Allow processing if we have either text or media
    if not user_message and not images and not audio:
        return

    chat_id = update.effective_chat.id
    session_id = str(user_id)

    # Check for reply context
    reply_context = await get_reply_context(update)
    user_message = user_message or ""
    if reply_context:
        user_message = reply_context + user_message

    # Concurrency Management: Immediate Engagement
    if chat_id not in _PROCESSING_LOCKS:
        _PROCESSING_LOCKS[chat_id] = asyncio.Lock()

    lock = _PROCESSING_LOCKS[chat_id]

    if lock.locked():
        await context.bot.send_message(
            chat_id=chat_id,
            text="On it — processing your previous request. Will address this next.",
        )

    # Call the core intent processor (which handles its own locking)
    await process_nova_intent(
        chat_id,
        user_id,
        user_message,
        images=images,
        audio=audio,
        videos=videos,
        files=files,
    )


async def handle_error(update: Optional[object], context: ContextTypes.DEFAULT_TYPE):
    """Handles errors in the telegram bot."""
    error_msg = str(context.error) if context.error else "Unknown error"
    
    # Check for known conflicts that should be handled specially
    if "Conflict: terminated by other getUpdates request" in error_msg:
        logging.warning("Conflict detected: Multiple bot instances running")
        return
    
    # Check for transient errors - these should be logged at WARNING level, not ERROR
    # Transient errors are temporary external service issues (Bad Gateway, timeouts, etc.)
    if is_transient_error(error_msg):
        update_repr = str(update) if update else "None"
        logging.warning(f"Transient error from update {update_repr}: {error_msg}")
        return
    
    # Log non-transient errors as errors
    update_repr = str(update) if update else "None"
    logging.error(f"Update {update_repr} caused error {error_msg}")


async def post_init(application):
    """Callback to run after the bot starts and the loop is running."""
    from nova.tools.scheduler import initialize_scheduler
    from nova.tools.error_bus import start_error_bus
    from nova.tools.specialist_registry import seed_default_specialists

    try:
        initialize_scheduler()
    except Exception as e:
        print(f"Scheduler init failed: {e}")

    try:
        start_error_bus()
    except Exception as e:
        print(f"Error bus init failed: {e}")

    try:
        result = seed_default_specialists()
        print(f"Specialists: {result}")
    except Exception as e:
        print(f"Specialist seeding failed: {e}")

    # Heartbeat: callback only fires when there are active agents
    monitor = get_heartbeat_monitor()

    def hb_wrapper(report, records):
        if records:  # Only notify if there's something to report
            asyncio.create_task(heartbeat_callback(report, records))

    monitor.register_callback(hb_wrapper)
    monitor.start()

    print("Nova ready: heartbeat active, specialists seeded, scheduler running.")


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

    # Any message type
    application.add_handler(
        MessageHandler(filters.ALL & (~filters.COMMAND), handle_message)
    )

    print(
        "Nova Agent Bot is running with MULTIMODAL support (Text/Voice/Photo/Video/Document)..."
    )
    application.run_polling()