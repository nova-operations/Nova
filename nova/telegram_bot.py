import os
import logging
import asyncio
from typing import List, Optional, Any
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from html import escape
from nova.agent import get_agent
from nova.logger import setup_logging
from nova.tools.heartbeat import get_heartbeat_monitor
from nova.tools.subagent import SUBAGENTS

import sys

setup_logging()

def is_authorized(user_id: int) -> bool:
    """Checks if the user is in the authorized whitelist."""
    whitelist_str = os.getenv("TELEGRAM_USER_WHITELIST", "")
    if not whitelist_str:
        # If no whitelist is defined, allow everyone by default, 
        # but warn in logs.
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
        text=f"Hello! I am Nova (User ID: {user_id}). I can run commands, manage files, spawn subagents, and manage scheduled tasks. How can I help you?"
    )

async def heartbeat_callback(report: str, records: List[object]):
    """Callback for heartbeat monitor to send updates to relevant Telegram chats."""
    if not records:
        return

    # Group active heartbeat records by chat_id
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
        finished_records = [r for r in chat_records if r.status in ["completed", "failed"]]
        active_records = [r for r in chat_records if r.status in ["running", "starting"]]

        for r in finished_records:
            status_emoji = "✅" if r.status == "completed" else "❌"
            # Use HTML and escape result for resilience
            clean_result = escape(str(r.result))
            msg = f"{status_emoji} <b>{escape(r.name)} has finished!</b>\n\n<b>Result:</b>\n{clean_result}"
            try:
                await telegram_bot_instance.send_message(chat_id=chat_id, text=msg, parse_mode='HTML')
            except Exception as e:
                logging.error(f"Failed to send completion message to {chat_id}: {e}")

        if active_records:
            header = f"📊 <b>Nova Team Heartbeat Update</b>"
            lines = [header, ""]
            for r in active_records:
                status_emoji = "🔄" if r.status == "running" else "⏳"
                lines.append(f"{status_emoji} <b>{escape(r.name)}</b>: {r.status}")
            
            try:
                await telegram_bot_instance.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode='HTML')
            except Exception as e:
                logging.error(f"Failed to send heartbeat to {chat_id}: {e}")

async def notify_user(chat_id: str, message: str):
    """Proactively send a message to a user."""
    global telegram_bot_instance
    if not telegram_bot_instance:
        return
    try:
        await telegram_bot_instance.send_message(chat_id=int(chat_id), text=message, parse_mode='HTML')
    except Exception as e:
        logging.error(f"Failed proactive notification to {chat_id}: {e}")

# Global bot instance for heartbeats
telegram_bot_instance = None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logging.warning(f"Unauthorized message from user_id: {user_id}")
        return

    user_message = update.message.text
    chat_id = update.effective_chat.id
    
    # Use user_id as session_id for consistency in authorization
    session_id = str(user_id)
    
    # We instantiate the agent per message to ensure clean state for the session config if needed,
    # but the underlying tools and DB connections should be handled efficiently.
    # Note: Global state like SUBAGENTS in nova.tools.subagent persists.
    # Send a typing action to indicate processing
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
        # We instantiate the agent inside the try block to catch initialization errors (like MCP issues)
        agent = get_agent(chat_id=str(chat_id))
        
        # Run the agent asynchronously
        response = await agent.arun(user_message, session_id=session_id)
        
        # response is RunOutput object. content is the text.
        if response and response.content:
            await context.bot.send_message(chat_id=chat_id, text=response.content)
        else:
             await context.bot.send_message(chat_id=chat_id, text="I have nothing to say.")

    except Exception as e:
        logging.error(f"Error running agent: {e}")
        await context.bot.send_message(chat_id=chat_id, text=f"Error: {e}")

async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handles errors in the telegram bot."""
    if context.error and "Conflict: terminated by other getUpdates request" in str(context.error):
        logging.warning("⚠️ Conflict detected: Another instance of this bot is already running. "
                        "If you are testing locally, please stop the container.")
    else:
        logging.error(f"Update {update} caused error {context.error}")

async def post_init(application):
    """Callback to run after the bot starts and the loop is running."""
    # Initialize scheduler
    from nova.tools.scheduler import initialize_scheduler
    try:
        initialize_scheduler()
        print("✅ Scheduler initialized successfully")
    except Exception as e:
        print(f"⚠️ Scheduler initialization failed: {e}")

    # Initialize Heartbeat Monitor with Telegram callback
    monitor = get_heartbeat_monitor()
    
    # Wrap the async callback for the monitor
    def hb_wrapper(report, records):
        asyncio.create_task(heartbeat_callback(report, records))
    
    monitor.register_callback(hb_wrapper)
    monitor.start()
    print("💓 Heartbeat Monitor active with Telegram reporting")

if __name__ == '__main__':
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    
    if not telegram_token:
        print("Error: TELEGRAM_BOT_TOKEN not set.")
        exit(1)
        
    if not openrouter_key:
        print("Warning: OPENROUTER_API_KEY not set. Agent commands involving LLM will fail.")

    application = ApplicationBuilder().token(telegram_token).post_init(post_init).build()
    
    # Set global bot instance for heartbeat callback
    telegram_bot_instance = application.bot
    
    # Register error handler
    application.add_error_handler(handle_error)
    
    start_handler = CommandHandler('start', start)
    message_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    
    application.add_handler(start_handler)
    application.add_handler(message_handler)
    
    print("Nova Agent Bot is running...")
    application.run_polling()