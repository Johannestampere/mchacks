"""
Text-to-speech module using ElevenLabs API.
"""

from __future__ import annotations

import os
from typing import AsyncIterator

import httpx

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Default: Rachel


async def generate_tts(text: str) -> bytes:
    """
    Generate text-to-speech audio from ElevenLabs (non-streaming).

    Args:
        text: The text to convert to speech

    Returns:
        Complete audio as bytes (mp3 format)
    """
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY environment variable is not set")

    if not text.strip():
        return b""

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }

    params = {
        "output_format": "mp3_44100_128",
    }

    payload = {
        "text": text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            url,
            headers=headers,
            params=params,
            json=payload,
        )
        if response.status_code != 200:
            error_text = response.text
            raise RuntimeError(f"ElevenLabs API error {response.status_code}: {error_text}")
        return response.content


async def stream_tts(text: str) -> AsyncIterator[bytes]:
    """
    Stream text-to-speech audio from ElevenLabs.
    Falls back to non-streaming if streaming fails.

    Args:
        text: The text to convert to speech

    Yields:
        Audio chunks as bytes (mp3 format)
    """
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY environment variable is not set")

    if not text.strip():
        return

    # Use non-streaming endpoint for reliability, then chunk the response
    audio_data = await generate_tts(text)

    # Send in chunks for consistent behavior
    chunk_size = 4096
    for i in range(0, len(audio_data), chunk_size):
        yield audio_data[i:i + chunk_size]
