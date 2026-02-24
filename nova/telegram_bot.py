import os
import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from nova.agent import get_agent

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text="Hello! I am Nova. I can run commands, manage files, and spawn subagents. How can I help you?"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.effective_chat.id
    
    # Use chat_id as session_id
    session_id = str(chat_id)
    
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

if __name__ == '__main__':
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    
    if not telegram_token:
        print("Error: TELEGRAM_BOT_TOKEN not set.")
        exit(1)
        
    if not openrouter_key:
        print("Warning: OPENROUTER_API_KEY not set. Agent commands involving LLM will fail.")

    application = ApplicationBuilder().token(telegram_token).build()
    
    start_handler = CommandHandler('start', start)
    message_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    
    application.add_handler(start_handler)
    application.add_handler(message_handler)
    
    print("Nova Agent Bot is running...")
    application.run_polling()
