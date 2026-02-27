"""
Voice message processing utilities for Telegram bot.
Handles OGG/OPUS audio transcription using OpenAI-compatible Whisper API.
"""
import os
import io
import logging
import tempfile
import asyncio
from typing import Optional

import requests
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


# OpenAI-compatible Whisper API configuration
def get_whisper_api_url() -> str:
    """Get the Whisper API URL from environment or use default Groq."""
    base_url = os.getenv("WHISPER_API_URL", "https://api.groq.com/openai/v1")
    return f"{base_url}/audio/transcriptions"


def get_whisper_api_key() -> str:
    """Get the API key for Whisper service."""
    # Try specific Whisper key first, then fall back to OpenAI
    return os.getenv("WHISPER_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")


def get_whisper_model() -> str:
    """Get the Whisper model to use."""
    # Groq uses whisper-large-v3
    return os.getenv("WHISPER_MODEL", "whisper-large-v3")


async def download_telegram_file(
    bot, file_id: str, timeout: int = 60
) -> Optional[bytes]:
    """Download a file from Telegram and return its:
        file bytes."""
    try = await bot.get_file(file_id)
        file_bytes = await file.download_as_bytearray()
        return bytes(file_bytes)
    except Exception as e:
        logger.error(f"Failed to download Telegram file {file_id}: {e}")
        return None


async def convert_ogg_to_wav(ogg_bytes: bytes) -> Optional[bytes]:
    """
    Convert OGG/OPUS audio to WAV format using pydub.
    Returns WAV bytes or None on failure.
    """
    try:
        from pydub import AudioSegment
        
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as ogg_file:
            ogg_file.write(ogg_bytes)
            ogg_path = ogg_file.name
        
        # Load OGG file
        audio = AudioSegment.from_file(ogg_path, format="ogg")
        
        # Convert to WAV (16kHz is optimal for Whisper)
        audio = audio.set_frame_rate(16000).set_channels(1)
        
        # Export to bytes
        wav_buffer = io.BytesIO()
        audio.export(wav_buffer, format="wav")
        wav_bytes = wav_buffer.getvalue()
        
        os.unlink(ogg_path)
        return wav_bytes
        
    except Exception as e:
        logger.error(f"OGG to WAV conversion failed: {e}")
        return None


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.wav") -> Optional[str]:
    """
    Transcribe audio using OpenAI-compatible Whisper API.
    
    Args:
        audio_bytes: Raw audio file bytes
        filename: Name of the audio file for API
        
    Returns:
        Transcribed text or None on failure
    """
    api_key = get_whisper_api_key()
    if not api_key:
        logger.error("No Whisper API key configured")
        return None
    
    api_url = get_whisper_api_url()
    model = get_whisper_model()
    
    try:
        # Prepare files for multipart request
        files = {
            "file": (filename, audio_bytes, "audio/wav"),
            "model": (None, model),
        }
        
        headers = {
            "Authorization": f"Bearer {api_key}"
        }
        
        # Make the API call
        response = requests.post(
            api_url,
            files=files,
            headers=headers,
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            text = result.get("text", "").strip()
            logger.info(f"Transcription successful: {text[:100]}...")
            return text
        else:
            logger.error(f"Whisper API error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"Transcription request failed: {e}")
        return None


async def transcribe_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """
    Handle a voice message: download, convert, transcribe.
    
    Returns:
        Transcribed text or None if transcription failed
    """
    message = update.message
    bot = context.bot
    
    # Get the file ID
    if message.voice:
        file_id = message.voice.file_id
        duration = message.voice.duration
        logger.info(f"Processing voice message, duration: {duration}s")
    elif message.audio:
        file_id = message.audio.file_id
        logger.info(f"Processing audio message: {message.audio.file_name}")
    else:
        return None
    
    # Send "transcribing" action
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, 
        action="typing"
    )
    
    # Download the file
    file_bytes = await download_telegram_file(bot, file_id)
    if not file_bytes:
        return None
    
    # Determine file type and convert if needed
    # Telegram voice messages are OGG/OPUS
    if message.voice:
        # Convert OGG to WAV for Whisper
        audio_bytes = await convert_ogg_to_wav(file_bytes)
        if not audio_bytes:
            return None
        filename = "voice.wav"
    else:
        # For audio, assume it's already in a compatible format or try direct
        audio_bytes = file_bytes
        filename = message.audio.file_name or "audio.mp3"
    
    # Transcribe
    text = await transcribe_audio(audio_bytes, filename)
    return text


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for voice messages - transcribes and processes as regular message."""
    from nova.telegram_bot import handle_message, is_authorized
    
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logger.warning(f"Unauthorized voice message from user_id: {user_id}")
        return
    
    # Transcribe the voice message
    transcribed_text = await transcribe_voice_message(update, context)
    
    if transcribed_text:
        # Replace the voice message with transcribed text for processing
        update.message.text = f"[Voice Message Transcribed]: {transcribed_text}"
        logger.info(f"Voice transcribed: {transcribed_text[:100]}...")
        
        # Process as regular message
        await handle_message(update, context)
    else:
        # Transcription failed
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Sorry, I couldn't transcribe your voice message. Please try again or send a text message."
        )


async def handle_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for audio messages - transcribes and processes as regular message."""
    from nova.telegram_bot import handle_message, is_authorized
    
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logger.warning(f"Unauthorized audio message from user_id: {user_id}")
        return
    
    # Transcribe the audio
    transcribed_text = await transcribe_voice_message(update, context)
    
    if transcribed_text:
        # Replace the audio message with transcribed text for processing
        update.message.text = f"[Audio Transcribed]: {transcribed_text}"
        logger.info(f"Audio transcribed: {transcribed_text[:100]}...")
        
        # Process as regular message
        await handle_message(update, context)
    else:
        # Transcription failed
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Sorry, I couldn't transcribe your audio. Please try again or send a text message."
        )