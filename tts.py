# ──────────────────────────────────────────────
# ОЗВУЧКА ТЕКСТА (Text-to-Speech)
# Использует Microsoft Edge TTS (бесплатно)
# ──────────────────────────────────────────────

import io
import edge_tts
from config import TTS_VOICE


async def speak(text: str, voice: str = None) -> io.BytesIO:
    """Преобразует текст в аудио (вьетнамский)."""
    voice = voice or TTS_VOICE
    communicate = edge_tts.Communicate(text, voice)

    audio_buffer = io.BytesIO()
    audio_buffer.name = "speech.mp3"

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.write(chunk["data"])

    audio_buffer.seek(0)
    return audio_buffer


async def speak_words(words: list) -> io.BytesIO:
    """Озвучивает список слов с паузами."""
    text = " ... ".join(w[0] for w in words)
    return await speak(text)


async def speak_phrases(phrases: list) -> io.BytesIO:
    """Озвучивает список фраз с паузами."""
    text = " ... ".join(p[0] for p in phrases)
    return await speak(text)