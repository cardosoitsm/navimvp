import io

import requests
from openai import OpenAI

from app.config import get_settings
from app.services.logger import get_logger

logger = get_logger("navi.voice")

SUPPORTED_AUDIO_TYPES = {
    "audio/ogg",
    "audio/ogg; codecs=opus",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/webm",
    "audio/amr",
}


def is_audio_media(content_type: str) -> bool:
    return content_type.split(";")[0].strip() in SUPPORTED_AUDIO_TYPES or content_type.startswith("audio/")


def transcribe_audio(media_url: str) -> str | None:
    settings = get_settings()
    if not settings.openai_api_key:
        logger.warning("transcribe_audio skipped: OPENAI_API_KEY not set")
        return None

    try:
        response = requests.get(
            media_url,
            auth=(settings.account_sid, settings.auth_token),
            timeout=30,
        )
        response.raise_for_status()
        audio_bytes = response.content
    except Exception:
        logger.exception("transcribe_audio download_error url=%s", media_url)
        return None

    try:
        client = OpenAI(api_key=settings.openai_api_key)
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=("audio.ogg", io.BytesIO(audio_bytes), "audio/ogg"),
            language="pt",
        )
        text = (transcript.text or "").strip()
        logger.info("transcribe_audio ok chars=%d", len(text))
        return text or None
    except Exception:
        logger.exception("transcribe_audio whisper_error")
        return None
