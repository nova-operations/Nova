import os
from telegram import Bot
import asyncio

async def send_reminder():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = "98746403"
    message = "🔔 [SystemCheck] - Everything is working correctly. (Minute-Test-1/30)"
    
    if not bot_token:
        print("Error: No bot token found")
        return
        
    bot = Bot(token=bot_token)
    await bot.send_message(chat_id=chat_id, text=message)

if __name__ == "__main__":
    asyncio.run(send_reminder())