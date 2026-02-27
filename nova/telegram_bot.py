import os
import logging
import asyncio
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
        text=f"Hello! I am Nova (User ID: {user_id}). I can run commands, manage files, spawn subagents, and manage scheduled tasks. How can I help you?",
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
    """
    Extract reply context from the incoming update.
    If the user is replying to a specific message, retrieve its content
    and return it as context for the next agent interaction.

    Returns:
        A string with the reply context, or empty string if no reply.
    """
    if not update.message or not update.message.reply_to_message:
        return ""

    replied_msg = update.message.reply_to_message

    # Get the text content of the original message
    original_text = ""
    if replied_msg.text:
        original_text = replied_msg.text
    elif replied_msg.caption:
        original_text = replied_msg.caption

    if not original_text:
        # Try to get content from other message types
        if hasattr(replied_msg, "document") and replied_msg.document:
            original_text = f"[Document: {replied_msg.document.file_name}]"
        elif hasattr(replied_msg, "photo") and replied_msg.photo:
            original_text = "[Photo message]"
        elif hasattr(replied_msg, "voice") and replied_msg.voice:
            original_text = "[Voice message]"
        elif hasattr(replied_msg, "audio") and replied_msg.audio:
            original_text = f"[Audio: {replied_msg.audio.title or 'Unknown'}]"

    if not original_text:
        return ""

    # Get message ID for reference
    msg_id = replied_msg.message_id

    # Build the context string
    context = f"""REPLY CONTEXT:
You are replying to message ID {msg_id}:
---
{original_text[:1000]}  # Truncate if too long
---

"""
    return context


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logging.warning(f"Unauthorized message from user_id: {user_id}")
        return

    user_message = update.message.text
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
        # Nova is busy. Acknowledge immediately to stay engaged.
        await context.bot.send_message(
            chat_id=chat_id,
            text="I'm currently processing your previous request. I've noted this new message and will address it immediately after I finish the current task! 🛰️",
        )
        # We still want to process it, so we wait for the lock

    async with lock:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")

        try:
            agent = get_agent(chat_id=str(chat_id))

            # Inject a note about current active tasks if any exist
            from nova.tools.subagent import SUBAGENTS as ACTIVE_SUBAGENTS

            active_subs = [
                s["name"]
                for s in ACTIVE_SUBAGENTS.values()
                if s.get("chat_id") == str(chat_id) and s.get("status") == "running"
            ]
            if active_subs:
                user_message = f"[SYSTEM NOTE: You have active subagents running: {', '.join(active_subs)}]\n{user_message}"

            response = await agent.arun(user_message, session_id=session_id)

            if response and response.content:
                await send_message_with_fallback(
                    context.bot, chat_id, response.content, title="Nova Response"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="I have processed your request, but have no specific update to share yet.",
                )

        except Exception as e:
            error_msg = str(e)
            logging.error(f"Error running agent: {error_msg}")

            # Basic error handling - be helpful but concise
            if "395051" in error_msg:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="The conversation history is too large. I've cleared some memory to keep going, but if this persists, please use /start.",
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"I encountered a slight hiccup: {error_msg[:200]}... I'm still here and ready to help!",
                )


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handles errors in the telegram bot."""
    if context.error and "Conflict: terminated by other getUpdates request" in str(
        context.error
    ):
        logging.warning(
            "Conflict detected: Another instance of this bot is already running. "
            "If you are testing locally, please stop the container."
        )
    else:
        logging.error(f"Update {update} caused error {context.error}")


async def post_init(application):
    """Callback to run after the bot starts and the loop is running."""
    from nova.tools.scheduler import initialize_scheduler

    try:
        initialize_scheduler()
        print("Scheduler initialized successfully")
    except Exception as e:
        print(f"Scheduler initialization failed: {e}")

    monitor = get_heartbeat_monitor()

    def hb_wrapper(report, records):
        asyncio.create_task(heartbeat_callback(report, records))

    monitor.register_callback(hb_wrapper)
    monitor.start()
    print("Heartbeat Monitor active with Telegram reporting")

    transformer = get_prompt_transformer()
    print(
        f"Middle-out prompt transformer initialized (max tokens: {transformer.max_tokens})"
    )


if __name__ == "__main__":
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")

    if not telegram_token:
        print("Error: TELEGRAM_BOT_TOKEN not set.")
        exit(1)

    if not openrouter_key:
        print(
            "Warning: OPENROUTER_API_KEY not set. Agent commands involving LLM will fail."
        )

    application = (
        ApplicationBuilder().token(telegram_token).post_init(post_init).build()
    )

    telegram_bot_instance = application.bot

    try:
        import nova.telegram_bot

        nova.telegram_bot.telegram_bot_instance = application.bot
    except Exception as e:
        print(f"Error setting global bot instance: {e}")
    application.add_error_handler(handle_error)

    start_handler = CommandHandler("start", start)
    message_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)

    application.add_handler(start_handler)
    application.add_handler(message_handler)

    print("Nova Agent Bot is running...")
    print("Middle-out context compression is ENABLED")
    application.run_polling()
