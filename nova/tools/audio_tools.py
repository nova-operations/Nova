"""
Audio generation tools for Telegram integration.
Uses edge-tts (free, no API key) with OpenAI TTS as fallback.
"""
import os
import logging
import uuid
import asyncio
from pathlib import Path
from typing import Optional, List

import requests

logger = logging.getLogger(__name__)

# Audio output directory
AUDIO_DIR = Path("data/audio")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Edge TTS voices - Microsoft's free TTS service
EDGE_VOICES = {
    "alloy": "en-US-AriaNeural",      # Similar to OpenAI's alloy
    "echo": "en-US-GuyNeural",        # Similar to OpenAI's echo  
    "fable": "en-US-SaraNeural",      # Similar to OpenAI's fable
    "onyx": "en-US-JacobNeural",      # Similar to OpenAI's onyx
    "nova": "en-US-JennyNeural",      # Similar to OpenAI's nova
    "shimmer": "en-US-AmberNeural",   # Similar to OpenAI's shimmer
}

# Default voice
DEFAULT_VOICE = "nova"


async def generate_edge_tts(text: str, voice: str = "nova") -> Optional[bytes]:
    """
    Generate TTS audio using edge-tts (Microsoft's free TTS).
    
    Args:
        text: The text to convert to speech
        voice: Voice name mapping (alloy, echo, fable, onyx, nova, shimmer)
    
    Returns:
        Audio bytes (mp3) or None if failed
    """
    try:
        import edge_tts
        
        # Map voice names to Edge TTS voices
        edge_voice = EDGE_VOICES.get(voice, DEFAULT_VOICE)
        
        logger.info(f"Generating edge-tts with voice: {edge_voice}")
        
        # Create communicate object
        communicate = edge_tts.Communicate(text, edge_voice)
        
        # Collect audio data
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        
        if audio_data:
            logger.info(f"Edge-TTS generated {len(audio_data)} bytes")
            return audio_data
        else:
            logger.error("Edge-TTS returned empty audio")
            return None
            
    except ImportError:
        logger.error("edge-tts not installed. Run: pip install edge-tts")
        return None
    except Exception as e:
        logger.error(f"Edge-TTS generation failed: {e}")
        return None


def generate_openai_tts(
    text: str,
    voice: str = "alloy",
    model: str = "tts-1"
) -> Optional[bytes]:
    """
    Generate TTS audio using OpenAI API (fallback method).
    
    Args:
        text: The text to convert to speech
        voice: Voice to use
        model: TTS model
    
    Returns:
        Audio bytes or None if failed
    """
    api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        logger.error("No OPENAI_API_KEY available for TTS")
        return None
    
    try:
        url = "https://api.openai.com/v1/audio/speech"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "voice": voice,
            "input": text,
            "response_format": "mp3"
        }
        
        logger.info(f"Generating OpenAI TTS with voice={voice}")
        
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            logger.info(f"OpenAI TTS generated {len(response.content)} bytes")
            return response.content
        else:
            logger.error(f"OpenAI TTS error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"OpenAI TTS failed: {e}")
        return None


def generate_tts_audio(text: str, voice: str = "alloy") -> Optional[bytes]:
    """
    Generate TTS audio. Tries edge-tts first (free), then OpenAI as fallback.
    
    Args:
        text: The text to convert to speech
        voice: Voice name (alloy, echo, fable, onyx, nova, shimmer)
    
    Returns:
        Audio bytes or None if failed
    """
    # First try edge-tts (free, no API key needed)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're in an async context, create a new task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, generate_edge_tts(text, voice))
                return future.result()
        else:
            return asyncio.run(generate_edge_tts(text, voice))
    except Exception as e:
        logger.warning(f"Edge-TTS failed, trying OpenAI: {e}")
    
    # Fallback to OpenAI TTS
    return generate_openai_tts(text, voice)


def save_audio_file(audio_bytes: bytes, filename: Optional[str] = None) -> Optional[Path]:
    """
    Save audio bytes to a file.
    
    Args:
        audio_bytes: Audio data
        filename: Optional custom filename
    
    Returns:
        Path to saved file or None
    """
    if audio_bytes is None:
        return None
    
    if filename is None:
        filename = f"tts_{uuid.uuid4().hex[:8]}.mp3"
    
    filepath = AUDIO_DIR / filename
    
    try:
        with open(filepath, "wb") as f:
            f.write(audio_bytes)
        logger.info(f"Audio saved to {filepath}")
        return filepath
    except Exception as e:
        logger.error(f"Failed to save audio: {e}")
        return None


async def send_audio_message(
    bot,
    chat_id: int,
    text: str,
    voice: str = "alloy",
    caption: Optional[str] = None
) -> bool:
    """
    Generate and send an audio message to a Telegram chat.
    
    Args:
        bot: Telegram bot instance
        chat_id: Target chat ID
        text: Text to convert to speech
        voice: Voice to use (alloy, echo, fable, onyx, nova, shimmer)
        caption: Optional caption (plaintext only, no markdown)
    
    Returns:
        True if successful, False otherwise
    """
    from telegram.error import TelegramError
    
    try:
        # Generate TTS audio
        audio_bytes = generate_tts_audio(text, voice=voice)
        
        if audio_bytes is None:
            await bot.send_message(
                chat_id=chat_id,
                text="Failed to generate audio. Please check API configuration."
            )
            return False
        
        # Save to file
        filepath = save_audio_file(audio_bytes)
        
        if filepath is None:
            await bot.send_message(
                chat_id=chat_id,
                text="Failed to save audio file."
            )
            return False
        
        # Send as voice message (more natural for TTS)
        try:
            with open(filepath, "rb") as audio_file:
                await bot.send_voice(
                    chat_id=chat_id,
                    voice=audio_file,
                    caption=caption,  # No markdown - just plain text
                    disable_notification=False
                )
            logger.info(f"Voice message sent to {chat_id}")
            return True
        except TelegramError as e:
            # Fallback to send_audio if send_voice fails
            logger.warning(f"send_voice failed, trying send_audio: {e}")
            with open(filepath, "rb") as audio_file:
                await bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_file,
                    caption=caption,
                    disable_notification=False
                )
            logger.info(f"Audio message sent to {chat_id}")
            return True
            
    except Exception as e:
        logger.error(f"Failed to send audio message: {e}")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"Audio generation failed: {str(e)}"
            )
        except:
            pass
        return False


def cleanup_old_audio_files(max_age_hours: int = 24):
    """Remove audio files older than specified hours."""
    import time
    
    try:
        current_time = time.time()
        max_age_seconds = max_age_hours * 3600
        
        removed_count = 0
        for filepath in AUDIO_DIR.glob("tts_*.mp3"):
            file_age = current_time - filepath.stat().st_mtime
            if file_age > max_age_seconds:
                filepath.unlink()
                removed_count += 1
        
        if removed_count > 0:
            logger.info(f"Cleaned up {removed_count} old audio files")
            
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")