import asyncio
import os
from telegram import Bot

import pytest


@pytest.mark.asyncio
async def test_send():
    token = "8540001391:AAEz3B8n-ox2YSx1vVYotsvATkwoFvqni2Q"
    chat_id = "98746403"
    bot = Bot(token=token)
    try:
        await bot.send_message(
            chat_id=chat_id, text="🚀 Nova Direct Connectivity Test - Manual Byte Stream"
        )
        print("SUCCESS")
    except Exception as e:
        print(f"FAILED: {e}")


if __name__ == "__main__":
    asyncio.run(test_send())
