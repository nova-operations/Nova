import os
import logging
import asyncio
import tempfile
from typing import List, Optional, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
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
        text=f"Hello! I am Nova (User ID: {user_id}).\n\nI can run commands, manage files, spawn teams, and manage scheduled tasks.\n\nAvailable slash commands:\n/start - Initial greeting\n/delete_history - Wipe all database memory\n\nI also support VOICE, AUDIO, and IMAGE inputs! How can I help you?",
    )


async def delete_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Slash command to trigger database history deletion with confirmation."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🔥 Confirm Wipe", callback_data="confirm_delete_history"
            ),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete_history"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⚠️ WARNING: This will permanently delete ALL database history, agent memories, and session data. Are you absolutely sure?",
        reply_markup=reply_markup,
    )


async def manage_tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Slash command to manage scheduled tasks and active subagents."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    # Pass update.message for commands, query for button clicks
    await _show_manage_menu(update.message if update.message else update.callback_query)


async def _show_manage_menu(source):
    """Entry point for task management - choose category."""
    from nova.tools.scheduler import get_session, ScheduledTask
    from nova.db.deployment_models import ActiveTask, TaskStatus as ATS

    db = get_session()
    try:
        sched_count = db.query(ScheduledTask).count()
        active_count = (
            db.query(ActiveTask).filter(ActiveTask.status == ATS.RUNNING).count()
        )

        msg = (
            "🛠 **[MNG] Nova Task Manager**\n\n"
            "Monitor and manage Nova's background operations. "
            "Choose a category below:"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    f"[JOB] Background Jobs ({sched_count})", callback_data="mt_list_scheduled"
                )
            ],
            [
                InlineKeyboardButton(
                    f"[BOT] Active Subagents ({active_count})", callback_data="mt_list_active"
                )
            ],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        # Handle cases where source is Update object or Message/CallbackQuery
        if hasattr(source, "reply_text"):
            await source.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
        elif hasattr(source, "message") and hasattr(source.message, "reply_text"):
            await source.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
        elif hasattr(source, "edit_message_text"):
            await source.edit_message_text(
                msg, reply_markup=reply_markup, parse_mode="Markdown"
            )
    finally:
        db.close()


async def _show_tasks_list(source):
    """Helper to show the list of scheduled tasks with basic info."""
    from nova.tools.scheduler import get_session, ScheduledTask

    db = get_session()
    try:
        tasks = db.query(ScheduledTask).order_by(ScheduledTask.id).all()

        if not tasks:
            msg = "[JOB] **Scheduled Tasks**\n\nNo tasks found."
            if hasattr(source, "reply_text"):
                await source.reply_text(msg, parse_mode="Markdown")
            elif hasattr(source, "message") and hasattr(source.message, "reply_text"):
                await source.message.reply_text(msg, parse_mode="Markdown")
            elif hasattr(source, "edit_message_text"):
                await source.edit_message_text(msg, parse_mode="Markdown")
            return

        msg = f"[JOB] **Scheduled Tasks ({len(tasks)})**\n\nSelect a task to manage:"
        keyboard = []
        for task in tasks:
            # Simple [x] or [o] for status instead of emojis
            status_tag = "[OK]" if str(task.status.value).upper() == "RUNNING" else "[||]"
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{status_tag} {task.task_name}",
                        callback_data=f"mt_view:{task.id}",
                    )
                ]
            )

        keyboard.append([InlineKeyboardButton("< Back", callback_data="manage_tasks")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        if hasattr(source, "reply_text"):
            await source.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
        elif hasattr(source, "message") and hasattr(source.message, "reply_text"):
            await source.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
        elif hasattr(source, "edit_message_text"):
            await source.edit_message_text(
                msg, reply_markup=reply_markup, parse_mode="Markdown"
            )
    finally:
        db.close()


async def _show_task_detail(query, task_id: int):
    """Show detailed info and management buttons for a task."""
    from nova.tools.scheduler import get_session, ScheduledTask

    db = get_session()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if not task:
            await query.edit_message_text("❌ Task not found.")
            return

        is_running = str(task.status.value).upper() == "RUNNING"
        status_tag = "[RUNNING]" if is_running else "[PAUSED]"

        msg = (
            f"🛠 **[MNG] Task Management: {task.task_name}**\n\n"
            f"**ID:** `{task.id}`\n"
            f"**Type:** `{task.task_type}`\n"
            f"**Status:** {status_tag}\n"
            f"**Schedule:** `{task.schedule}`\n"
            f"**Notifications:** `{'On' if task.notification_enabled else 'Off'}`\n"
            f"**Target Chat:** `{task.target_chat_id or 'Default'}`\n"
        )

        if task.last_run:
            msg += f"**Last Run:** `{task.last_run.strftime('%Y-%m-%d %H:%M:%S')}`\n"
            msg += f"**Last Result:** `{task.last_status or 'None'}`\n"

        if task.last_output:
            output_snippet = (
                task.last_output[:200] + "..."
                if len(task.last_output) > 200
                else task.last_output
            )
            msg += f"\n**Last Output Snippet:**\n`{output_snippet}`\n"

        keyboard = [
            [
                InlineKeyboardButton("|> Run Now", callback_data=f"mt_run:{task_id}"),
            ],
            [
                InlineKeyboardButton(
                    "|| Pause" if is_running else "> Resume",
                    callback_data=f"mt_pause:{task_id}"
                    if is_running
                    else f"mt_resume:{task_id}",
                ),
                InlineKeyboardButton("[DEL] Delete", callback_data=f"mt_del_conf:{task_id}"),
            ],
            [InlineKeyboardButton("< Back to List", callback_data="manage_tasks")],
        ]

        await query.edit_message_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def _show_task_delete_confirm(query, task_id: int):
    """Show confirmation for task deletion."""
    from nova.tools.scheduler import get_session, ScheduledTask

    db = get_session()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if not task:
            await query.edit_message_text("❌ Task not found.")
            return

        msg = f"[!] **Delete Task: {task.task_name}**\n\nAre you sure you want to permanently remove this scheduled task?"
        keyboard = [
            [
                InlineKeyboardButton("[KILL] Confirm Delete", callback_data=f"mt_del:{task_id}"),
                InlineKeyboardButton("[x] Cancel", callback_data=f"mt_view:{task_id}"),
            ]
        ]
        await query.edit_message_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def _handle_task_action(query, task_id: int, action: str):
    """Dispatch management actions to the scheduler tools."""
    from nova.tools.scheduler import (
        get_session,
        ScheduledTask,
        run_scheduled_task_now,
        pause_scheduled_task,
        resume_scheduled_task,
        remove_scheduled_task,
    )

    db = get_session()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if not task:
            await query.answer("Task not found.", show_alert=True)
            return

        task_name = task.task_name
        result = "Done"

        if action == "run":
            result = run_scheduled_task_now(task_name)
        elif action == "pause":
            result = pause_scheduled_task(task_name)
        elif action == "resume":
            result = resume_scheduled_task(task_name)
        elif action == "delete":
            result = remove_scheduled_task(task_name)

        await query.answer(result)

        if action == "delete":
            await _show_tasks_list(query)  # Return to list
        else:
            await _show_task_detail(query, task_id)  # Refresh details

    finally:
        db.close()


async def _show_active_tasks_list(query):
    """List currently running subagents."""
    from nova.tools.scheduler import get_session
    from nova.db.deployment_models import ActiveTask, TaskStatus as ATS

    db = get_session()
    try:
        tasks = (
            db.query(ActiveTask).filter(ActiveTask.status == ATS.RUNNING).all()
        )

        if not tasks:
            msg = "[BOT] **Active Subagents**\n\nNo active subagents running."
            keyboard = [[InlineKeyboardButton("< Back", callback_data="manage_tasks")]]
            await query.edit_message_text(
                msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
            )
            return

        msg = f"[BOT] **Active Subagents ({len(tasks)})**\n\nSelect a subagent to manage:"
        keyboard = []
        for task in tasks:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"[BOT] {task.subagent_name} ({task.task_id[:8]})",
                        callback_data=f"mt_at_view:{task.id}",
                    )
                ]
            )

        keyboard.append([InlineKeyboardButton("< Back", callback_data="manage_tasks")])
        await query.edit_message_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def _show_active_task_detail(query, task_id: int):
    """Show details for an active subagent task."""
    from nova.tools.scheduler import get_session
    from nova.db.deployment_models import ActiveTask

    db = get_session()
    try:
        task = db.query(ActiveTask).filter(ActiveTask.id == task_id).first()
        if not task:
            await query.edit_message_text("❌ Subagent task not found.")
            return

        msg = (
            f"[BOT] **Subagent Management: {task.subagent_name}**\n\n"
            f"**Task ID:** `{task.task_id}`\n"
            f"**Type:** `{task.task_type}`\n"
            f"**Status:** `{task.status.value}`\n"
            f"**Started:** `{task.started_at.strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"**Progress:** `{task.progress_percentage}%`\n"
            f"**Description:** `{task.description or 'No desc'}`\n"
        )

        keyboard = []
        # ActiveTask status is lowercase running
        is_running = str(task.status.value).lower() == "running"
        keyboard.append(
            [
                InlineKeyboardButton(
                    "|| Pause" if is_running else "> Resume",
                    callback_data=f"mt_at_pause:{task.id}"
                    if is_running
                    else f"mt_at_resume:{task.id}",
                ),
                InlineKeyboardButton("[STOP] Kill / Stop", callback_data=f"mt_at_kill:{task_id}"),
            ]
        )
        keyboard.append(
            [InlineKeyboardButton("< Back to List", callback_data="mt_list_active")]
        )

        await query.edit_message_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def _handle_active_task_action(query, task_id: int, action: str):
    """Handle actions on active subagent tasks."""
    from nova.tools.scheduler import get_session
    from nova.db.deployment_models import ActiveTask, TaskStatus as ATS
    from nova.deployment_task_manager import get_manager

    db = get_session()
    try:
        task = db.query(ActiveTask).filter(ActiveTask.id == task_id).first()
        if not task:
            await query.answer("Subagent not found.", show_alert=True)
            return

        tracker = get_manager().task_tracker
        tid = task.task_id

        if action == "pause":
            tracker.pause_task(tid)
            res = "Paused"
        elif action == "resume":
            tracker.resume_task(tid)
            res = "Resumed"
        elif action == "kill":
            tracker.unregister_task(tid, {"status": "cancelled", "reason": "User manual stop"})
            res = "Killed (Unregistered)"

        await query.answer(f"Subagent {res}")

        if action == "kill":
            await _show_active_tasks_list(query)
        else:
            await _show_active_task_detail(query, task_id)

    finally:
        db.close()


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks for confirmations."""
    query = update.callback_query
    user_id = query.from_user.id

    if not is_authorized(user_id):
        await query.answer("Unauthorized", show_alert=True)
        return

    await query.answer()

    if query.data == "confirm_delete_history":
        from nova.tools.db_cleaner import wipe_all_database_tables

        await query.edit_message_text("[DEL] Wiping all history... please wait.")
        result = wipe_all_database_tables()
        await query.edit_message_text(f"[DONE] {result}")

    elif query.data == "cancel_delete_history":
        await query.edit_message_text("[x] Action cancelled. History preserved.")

    elif query.data == "manage_tasks":
        await _show_manage_menu(query)

    elif query.data == "mt_list_scheduled":
        await _show_tasks_list(query)

    elif query.data == "mt_list_active":
        await _show_active_tasks_list(query)

    elif query.data.startswith("mt_view:"):
        task_id = int(query.data.split(":")[1])
        await _show_task_detail(query, task_id)

    elif query.data.startswith("mt_at_view:"):
        task_id = int(query.data.split(":")[1])
        await _show_active_task_detail(query, task_id)

    elif query.data.startswith("mt_run:"):
        task_id = int(query.data.split(":")[1])
        await _handle_task_action(query, task_id, "run")

    elif query.data.startswith("mt_pause:"):
        task_id = int(query.data.split(":")[1])
        await _handle_task_action(query, task_id, "pause")

    elif query.data.startswith("mt_resume:"):
        task_id = int(query.data.split(":")[1])
        await _handle_task_action(query, task_id, "resume")

    elif query.data.startswith("mt_del_conf:"):
        task_id = int(query.data.split(":")[1])
        await _show_task_delete_confirm(query, task_id)

    elif query.data.startswith("mt_del:"):
        task_id = int(query.data.split(":")[1])
        await _handle_task_action(query, task_id, "delete")

    elif query.data.startswith("mt_at_pause:"):
        task_id = int(query.data.split(":")[1])
        await _handle_active_task_action(query, task_id, "pause")

    elif query.data.startswith("mt_at_resume:"):
        task_id = int(query.data.split(":")[1])
        await _handle_active_task_action(query, task_id, "resume")

    elif query.data.startswith("mt_at_kill:"):
        task_id = int(query.data.split(":")[1])
        await _handle_active_task_action(query, task_id, "kill")


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

    # Immediately acknowledge the issue to the user
    try:
        await telegram_bot_instance.send_message(
            chat_id=cid,
            text="I found an issue. Fixing it now, please wait.",
        )
    except Exception:
        pass

    # System-triggered message
    system_prompt = (
        f"[SYSTEM_ALERT]\n{message}\n"
        "INSTRUCTIONS: A background error occurred. You have already notified the user. "
        "Now fix it by spawning a recovery team (e.g., Bug-Fixer). "
        "After the fix is applied, push the changes to GitHub using push_to_github(). "
        "Report only a brief success message when fully resolved."
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
                user_id=str(user_id),
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

    # Update Bot Identity (Name, Descriptions, and Profile Picture)
    try:
        # Set Bot Name
        await application.bot.set_my_name(name="Nova")

        # Set Short Description (shown on the bot's profile and in sharing)
        await application.bot.set_my_short_description(
            short_description="Nova - Advanced Agentic AI Assistant for coding, automation, and system management."
        )

        # Set Description (shown in the "What can this bot do?" section)
        await application.bot.set_my_description(
            description="I am Nova, an advanced self-improving AI agent. I specialize in coding, system orchestration, and multi-project management. I can execute commands, manage files, spawn specialist teams, and handle scheduled tasks autonomously."
        )

        token = os.getenv("TELEGRAM_BOT_TOKEN")
        photo_path = "Nova.png"
        if token and os.path.exists(photo_path):
            import requests

            with open(photo_path, "rb") as photo:
                url = f"https://api.telegram.org/bot{token}/setMyProfilePhoto"
                resp = requests.post(url, files={"photo": photo})
                if resp.status_code == 200:
                    print(
                        "Nova identity: name, descriptions, and profile picture updated."
                    )
                else:
                    print(
                        f"Nova identity: descriptions updated, but photo failed: {resp.text}"
                    )
        else:
            print("Nova identity: name and descriptions updated. Photo skipped.")
    except Exception as e:
        print(f"Failed to update Nova identity: {e}")

    # Set Bot Commands for the menu
    try:
        commands = [
            BotCommand("start", "Initial greeting and help info"),
            BotCommand("manage_tasks", "Manage all background jobs and tasks"),
            BotCommand("delete_history", "Wipe all database memory (destructive)"),
        ]
        await application.bot.set_my_commands(commands)
        print("Nova command menu updated.")
    except Exception as e:
        print(f"Failed to set bot commands: {e}")

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
    application.add_handler(CommandHandler("manage_tasks", manage_tasks_cmd))
    application.add_handler(CommandHandler("delete_history", delete_history_cmd))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Any message type
    application.add_handler(
        MessageHandler(filters.ALL & (~filters.COMMAND), handle_message)
    )

    print(
        "Nova Agent Bot is running with MULTIMODAL support (Text/Voice/Photo/Video/Document)..."
    )
    application.run_polling()
