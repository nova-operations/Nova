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
from nova.tools.core.heartbeat import get_heartbeat_monitor
from nova.tools.agents.subagent import SUBAGENTS
from agno.media import Audio, Image, Video, File

# Import the middle-out transformer for explicit prompt compression
from nova.tools.core.prompt_transformer import (
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


def _content_changed(query, new_text: str, new_reply_markup=None) -> bool:
    """
    Check if the new content is different from the existing message content.
    Returns True if content has changed (needs update), False if identical.
    This prevents 'Message is not modified' errors from Telegram API.
    """
    try:
        # Get the current message text
        current_text = query.message.text if query.message else ""
        if current_text is None:
            current_text = ""

        # Compare text
        if current_text != new_text:
            return True

        # Compare reply_markup if provided
        if new_reply_markup is not None:
            current_markup = query.message.reply_markup if query.message else None
            # Convert both to JSON for comparison
            current_markup_json = current_markup.to_json() if current_markup else None
            new_markup_json = new_reply_markup.to_json() if new_reply_markup else None
            if current_markup_json != new_markup_json:
                return True

        return False
    except Exception:
        # If we can't compare, assume content changed to be safe
        return True


async def _safe_edit_message(query, new_text: str, reply_markup=None, parse_mode=None):
    """
    Safely edit a message only if content has changed.
    This avoids 'Message is not modified' errors from Telegram API.
    """
    if not _content_changed(query, new_text, reply_markup):
        logging.debug("Message content unchanged, skipping edit")
        return

    await query.edit_message_text(
        new_text,
        reply_markup=reply_markup,
        parse_mode=parse_mode
    )


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
                "[DEL] Wipe History", callback_data="confirm_delete_history"
            ),
            InlineKeyboardButton("[X] Cancel", callback_data="cancel_delete_history"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "[!] **Wipe Conversation History**\n\nThis will permanently delete agent memories and session data. \n\nSystems (scheduled tasks, specialist configs) will be preserved.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def factory_reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Slash command to trigger full factory reset."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "[☢️] FACTORY RESET", callback_data="confirm_factory_reset"
            ),
            InlineKeyboardButton("[X] Cancel", callback_data="cancel_delete_history"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "[☢️] **NUCLEAR WARNING: FACTORY RESET**\n\nThis deletes **EVERYTHING**:\n- All conversation history\n- All specialist configurations\n- All scheduled tasks\n- All project records\n\nThis cannot be undone. System will return to 'zero' state.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
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
    from nova.tools.scheduler.scheduler import get_session, ScheduledTask
    from nova.db.deployment_models import ActiveTask, TaskStatus as ATS

    db = get_session()
    try:
        sched_count = db.query(ScheduledTask).count()
        active_count = (
            db.query(ActiveTask).filter(ActiveTask.status == ATS.RUNNING).count()
        )

        msg = (
            "**[MNG] Nova Task Manager**\n\n"
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
            await _safe_edit_message(source, msg, reply_markup=reply_markup, parse_mode="Markdown")
    finally:
        db.close()


async def _show_tasks_list(source):
    """Helper to show the list of scheduled tasks with basic info."""
    from nova.tools.scheduler.scheduler import get_session, ScheduledTask

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
                await _safe_edit_message(source, msg, parse_mode="Markdown")
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
            await _safe_edit_message(source, msg, reply_markup=reply_markup, parse_mode="Markdown")
    finally:
        db.close()


async def _show_task_detail(query, task_id: int):
    """Show detailed info and management buttons for a task."""
    import html
    from nova.tools.scheduler.scheduler import get_session, ScheduledTask

    db = get_session()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if not task:
            await _safe_edit_message(query, "[ERR] Task not found.")
            return

        is_running = str(task.status.value).upper() == "RUNNING"
        status_tag = "[RUNNING]" if is_running else "[PAUSED]"
        notify_tag = "On" if task.notification_enabled else "Off"

        # Use HTML mode — avoids Markdown parse failures with special chars in user content
        msg = (
            f"<b>[MNG] Task: {html.escape(task.task_name)}</b>\n\n"
            f"<b>ID:</b> <code>{task.id}</code>\n"
            f"<b>Type:</b> <code>{html.escape(str(task.task_type))}</code>\n"
            f"<b>Status:</b> {status_tag}\n"
            f"<b>Schedule:</b> <code>{html.escape(task.schedule)}</code>\n"
            f"<b>Notifications:</b> {notify_tag}\n"
            f"<b>Target Chat:</b> <code>{html.escape(str(task.target_chat_id or 'Default'))}</code>\n"
        )

        if task.last_run:
            msg += f"<b>Last Run:</b> <code>{task.last_run.strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            msg += f"<b>Last Result:</b> <code>{html.escape(task.last_status or 'None')}</code>\n"

        # Show the script body for inline_script jobs
        if str(task.task_type) == "inline_script" and task.subagent_instructions:
            snippet = task.subagent_instructions[:400]
            if len(task.subagent_instructions) > 400:
                snippet += "..."
            msg += f"\n<b>Script:</b>\n<pre>{html.escape(snippet)}</pre>\n"
        elif task.subagent_task:
            snippet = task.subagent_task[:200]
            if len(task.subagent_task) > 200:
                snippet += "..."
            msg += f"\n<b>Task:</b>\n<code>{html.escape(snippet)}</code>\n"

        if task.last_output:
            output_snippet = task.last_output[:200]
            if len(task.last_output) > 200:
                output_snippet += "..."
            msg += f"\n<b>Last Output:</b>\n<pre>{html.escape(output_snippet)}</pre>\n"

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
                InlineKeyboardButton(
                    "[X] Silence" if task.notification_enabled else "[O] Notify",
                    callback_data=f"mt_toggle_notify:{task_id}",
                ),
            ],
            [
                InlineKeyboardButton("[DEL] Delete", callback_data=f"mt_del_conf:{task_id}"),
            ],
            [InlineKeyboardButton("< Back to List", callback_data="manage_tasks")],
        ]

        await _safe_edit_message(
            query, msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
    finally:
        db.close()


async def _show_task_delete_confirm(query, task_id: int):
    """Show confirmation for task deletion."""
    from nova.tools.scheduler.scheduler import get_session, ScheduledTask

    db = get_session()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        if not task:
            await _safe_edit_message(query, "❌ Task not found.")
            return

        msg = f"[!] **Delete Task: {task.task_name}**\n\nAre you sure you want to permanently remove this scheduled task?"
        keyboard = [
            [
                InlineKeyboardButton("[KILL] Confirm Delete", callback_data=f"mt_del:{task_id}"),
                InlineKeyboardButton("[x] Cancel", callback_data=f"mt_view:{task_id}"),
            ]
        ]
        await _safe_edit_message(
            query, msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def _handle_task_action(query, task_id: int, action: str):
    """Dispatch management actions to the scheduler tools."""
    from nova.tools.scheduler.scheduler import (
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
    from nova.tools.scheduler.scheduler import get_session
    from nova.db.deployment_models import ActiveTask, TaskStatus as ATS

    db = get_session()
    try:
        tasks = (
            db.query(ActiveTask).filter(ActiveTask.status == ATS.RUNNING).all()
        )

        if not tasks:
            msg = "[BOT] **Active Subagents**\n\nNo active subagents running."
            keyboard = [[InlineKeyboardButton("< Back", callback_data="manage_tasks")]]
            await _safe_edit_message(
                query, msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
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
        await _safe_edit_message(
            query, msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def _show_active_task_detail(query, task_id: int):
    """Show details for an active subagent task."""
    from nova.tools.scheduler.scheduler import get_session
    from nova.db.deployment_models import ActiveTask

    db = get_session()
    try:
        task = db.query(ActiveTask).filter(ActiveTask.id == task_id).first()
        if not task:
            await _safe_edit_message(query, "❌ Subagent task not found.")
            return

        msg = (
            f"**[BOT] Subagent Management: {task.subagent_name}**\n\n"
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

        await _safe_edit_message(
            query, msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    finally:
        db.close()


async def _handle_active_task_action(query, task_id: int, action: str):
    """Handle actions on active subagent tasks."""
    from nova.tools.scheduler.scheduler import get_session
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

    try:
        if query.data == "confirm_delete_history":
            from nova.tools.database.db_cleaner import wipe_all_database_tables

            # Check if content changed before editing
            if _content_changed(query, "[DEL] Wiping history... please wait."):
                await query.edit_message_text("[DEL] Wiping history... please wait.")
            result = wipe_all_database_tables(force_all=False)
            await _safe_edit_message(query, f"[DONE] {result}")

        elif query.data == "confirm_factory_reset":
            from nova.tools.database.db_cleaner import wipe_all_database_tables

            if _content_changed(query, "[☢️] NUKE IN PROGRESS... please wait."):
                await query.edit_message_text("[☢️] NUKE IN PROGRESS... please wait.")
            result = wipe_all_database_tables(force_all=True)
            await _safe_edit_message(query, f"[DONE] {result}")

        elif query.data == "cancel_delete_history":
            await _safe_edit_message(query, "[x] Action cancelled. Data preserved.")

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

        elif query.data.startswith("mt_toggle_notify:"):
            task_id = int(query.data.split(":")[1])
            from nova.tools.scheduler.scheduler import get_session, ScheduledTask
            db = get_session()
            try:
                task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
                if task:
                    task.notification_enabled = not task.notification_enabled
                    db.commit()
                    await query.answer(f"Notifications {'Off' if not task.notification_enabled else 'On'}")
                    await _show_task_detail(query, task_id)
            finally:
                db.close()

        elif query.data.startswith("mt_at_pause:"):
            task_id = int(query.data.split(":")[1])
            await _handle_active_task_action(query, task_id, "pause")

        elif query.data.startswith("mt_at_resume:"):
            task_id = int(query.data.split(":")[1])
            await _handle_active_task_action(query, task_id, "resume")

        elif query.data.startswith("mt_at_kill:"):
            task_id = int(query.data.split(":")[1])
            await _handle_active_task_action(query, task_id, "kill")

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"callback_handler error [{query.data}]: {e}")
        try:
            await _safe_edit_message(
                query, f"[ERR] Action failed: {str(e)[:200]}\n\nThe error has been logged for self-healing."
            )
        except Exception:
            pass  # If we can't even edit, just swallow — Telegram likely rate-limiting


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
    """Extract rich context from the message being replied to.

    Captures message_id, author, text/caption, media type, and any
    Telegram client-side quote the user highlighted before replying.
    """
    if not update.message or not update.message.reply_to_message:
        return ""

    replied_msg = update.message.reply_to_message
    parts = ["[REPLY CONTEXT]"]
    parts.append(f"Replied-to message_id: {replied_msg.message_id}")

    if replied_msg.from_user:
        is_bot = replied_msg.from_user.is_bot
        parts.append(
            f"Author: {replied_msg.from_user.first_name}"
            f"{' (bot)' if is_bot else ''}"
        )

    # Text / caption
    if replied_msg.text:
        parts.append(f"Text: {replied_msg.text}")
    if replied_msg.caption:
        parts.append(f"Caption: {replied_msg.caption}")

    # Media type indicators
    if replied_msg.video_note:
        parts.append("Media: video_message (round video note)")
    elif replied_msg.video:
        parts.append("Media: video")
    elif replied_msg.voice:
        parts.append("Media: voice_message")
    elif replied_msg.audio:
        parts.append("Media: audio_file")
    elif replied_msg.photo:
        parts.append("Media: photo")
    elif replied_msg.sticker:
        parts.append(f"Media: sticker (emoji={replied_msg.sticker.emoji})")
    elif replied_msg.document:
        parts.append(
            f"Media: document (name={replied_msg.document.file_name})"
        )
    elif replied_msg.animation:
        parts.append("Media: GIF/animation")
    elif replied_msg.contact:
        parts.append("Media: contact")
    elif replied_msg.location:
        parts.append("Media: location")
    elif replied_msg.poll:
        parts.append(f"Media: poll (question={replied_msg.poll.question})")

    # Client-side quote (Telegram allows highlighting part of a message)
    if hasattr(update.message, 'quote') and update.message.quote:
        quote = update.message.quote
        if hasattr(quote, 'text') and quote.text:
            parts.append(f"Quoted section: \"{quote.text}\"")

    parts.append("---")
    return "\n".join(parts) + "\n"


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
            text="[FAIL] I found an issue. Fixing it now, please wait.",
        )
    except Exception:
        pass

    # System-triggered message
    system_prompt = (
        f"[SYSTEM_ALERT]\n{message}\n"
        "INSTRUCTIONS: A background error occurred. You have already notified the user. "
        "Now fix it by spawning a recovery team (e.g., Bug-Fixer). "
    )
    if os.getenv("GITHUB_TOKEN"):
        system_prompt += "After the fix is applied, push the changes to GitHub using push_to_github(). "
    
    system_prompt += "Report only a brief success message when fully resolved."

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
    reply_to_message_id: Optional[int] = None,
):
    """Core logic to run a Nova iteration without requiring a Telegram Update object.

    Args:
        reply_to_message_id: If set, the bot's final response will be sent
            as a reply to this Telegram message_id.
    """
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

            from nova.tools.agents.subagent import SUBAGENTS as ACTIVE_SUBAGENTS

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
                # Reply to the user's original message when possible
                try:
                    clean_content = strip_all_formatting(response.content)
                    await telegram_bot_instance.send_message(
                        chat_id=chat_id,
                        text=clean_content[:TELEGRAM_MAX_LENGTH],
                        reply_to_message_id=reply_to_message_id,
                    )
                    # If the message was long, send the rest via fallback
                    if len(clean_content) > TELEGRAM_MAX_LENGTH:
                        await send_message_with_fallback(
                            telegram_bot_instance,
                            chat_id,
                            clean_content,
                            title="Nova Response",
                        )
                except Exception as send_err:
                    logging.warning(
                        f"Failed to reply natively (falling back): {send_err}"
                    )
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
                    f"Error: {str(e)}\n\nI'm having trouble processing your request. Please check my logs or try again.",
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
    current_message_id = update.message.message_id if update.message else None

    # Check for reply context
    reply_context = await get_reply_context(update)
    user_message = user_message or ""

    # Inject message metadata so the agent knows the IDs it can reference
    meta_header = f"[MSG_META chat_id={chat_id} message_id={current_message_id}]\n"
    if reply_context:
        user_message = meta_header + reply_context + user_message
    else:
        user_message = meta_header + user_message

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
        reply_to_message_id=current_message_id,
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
    from nova.tools.scheduler.scheduler import initialize_scheduler
    from nova.tools.core.error_bus import start_error_bus
    from nova.tools.core.specialist_registry import seed_default_specialists

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
            BotCommand("delete_history", "Wipe conversation memories (Preserves specialists)"),
            BotCommand("factory_reset", "Wipe EVERYTHING (Nuclear reset)"),
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
    application.add_handler(CommandHandler("reset", delete_history_cmd))
    application.add_handler(CommandHandler("factory_reset", factory_reset_cmd))
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Any message type
    application.add_handler(
        MessageHandler(filters.ALL & (~filters.COMMAND), handle_message)
    )

    print(
        "Nova Agent Bot is running with MULTIMODAL support (Text/Voice/Photo/Video/Document)..."
    )
    application.run_polling()