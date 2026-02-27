"""
Test script to send audio greeting via Telegram.
Usage: python test_audio_tts.py
"""
import os
import asyncio
import logging

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
from dotenv import load_dotenv

load_dotenv()


async def verify_audio_message():
    """Test sending an audio message to the user."""
    from telegram import Bot
    from nova.tools.audio_tools import (
        send_audio_message,
        generate_tts_audio,
        save_audio_file,
    )

    # Get credentials
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_USER_WHITELIST", "98746403")

    if not telegram_token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    # Initialize bot
    bot = Bot(token=telegram_token)

    # Test message
    test_text = "Hello! This is a test message from Nova. Audio integration is working!"

    logger.info(f"Generating TTS for: {test_text[:50]}...")

    # First, test TTS generation locally
    audio_bytes = generate_tts_audio(test_text, voice="nova")

    if audio_bytes:
        logger.info(f"TTS generated successfully: {len(audio_bytes)} bytes")

        # Save locally to verify
        filepath = save_audio_file(audio_bytes, "test_greeting.mp3")
        if filepath:
            logger.info(f"Audio saved to: {filepath}")
    else:
        logger.error("TTS generation failed")
        return

    # Now send to Telegram
    logger.info(f"Sending voice message to chat_id: {chat_id}")

    success = await send_audio_message(
        bot=bot,
        chat_id=int(chat_id),
        text=test_text,
        voice="nova",
        caption="Audio test from Nova",  # Plaintext only - no markdown
    )

    if success:
        logger.info("Audio message sent successfully!")
    else:
        logger.error("Failed to send audio message")


async def verify_edge_tts_direct():
    """Direct test of edge-tts generation."""
    from nova.tools.audio_tools import generate_edge_tts, save_audio_file

    test_text = "Testing edge t t s integration. This is a free text to speech service."

    logger.info("Testing edge-tts directly...")
    audio_bytes = await generate_edge_tts(test_text, voice="nova")

    if audio_bytes:
        logger.info(f"Edge-TTS generated: {len(audio_bytes)} bytes")
        filepath = save_audio_file(audio_bytes, "edge_test.mp3")
        if filepath:
            logger.info(f"Saved to: {filepath}")
        return True
    return False


if __name__ == "__main__":
    print("=" * 50)
    print("Audio TTS Test Script")
    print("=" * 50)

    # First test edge-tts directly
    print("\n1. Testing edge-tts generation...")
    result = asyncio.run(verify_edge_tts_direct())

    if result:
        print("Edge-TTS test: PASSED")
    else:
        print("Edge-TTS test: FAILED")

    # Then test Telegram sending
    print("\n2. Testing Telegram audio message...")
    asyncio.run(verify_audio_message())

    print("\nTest complete!")
