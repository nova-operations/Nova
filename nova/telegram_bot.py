import os
import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from nova.agent import get_agent
from nova.logger import setup_logging

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
    agent = get_agent()
    
    # Send a typing action to indicate processing
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    try:
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
    if "Conflict: terminated by other getUpdates request" in str(context.error):
        logging.warning("⚠️ Conflict detected: Another instance of this bot is already running. "
                        "If you are testing locally, please stop the container.")
    else:
        logging.error(f"Update {update} caused error {context.error}")

if __name__ == '__main__':
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    
    if not telegram_token:
        print("Error: TELEGRAM_BOT_TOKEN not set.")
        exit(1)
        
    if not openrouter_key:
        print("Warning: OPENROUTER_API_KEY not set. Agent commands involving LLM will fail.")

    # Initialize scheduler on startup
    from nova.tools.scheduler import initialize_scheduler
    try:
        initialize_scheduler()
        print("✅ Scheduler initialized successfully")
    except Exception as e:
        print(f"⚠️ Scheduler initialization failed: {e}")

    application = ApplicationBuilder().token(telegram_token).build()
    
    # Register error handler
    application.add_error_handler(handle_error)
    
    start_handler = CommandHandler('start', start)
    message_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    
    application.add_handler(start_handler)
    application.add_handler(message_handler)
    
    print("Nova Agent Bot is running...")
    application.run_polling()