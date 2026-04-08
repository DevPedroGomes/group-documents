import os
import tempfile
import time
import logging

import google.generativeai as genai

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_configured = False


def _ensure_configured():
    global _configured
    if not _configured:
        settings = get_settings()
        if settings.google_api_key:
            genai.configure(api_key=settings.google_api_key)
            _configured = True
        else:
            raise RuntimeError("GOOGLE_API_KEY not configured")


def process_media_with_gemini(
    data: bytes, mime_type: str, prompt: str = "Describe this content in detail.", filename: str | None = None
) -> str:
    _ensure_configured()
    temp_path = None
    try:
        suffix = ".bin"
        if filename:
            ext = os.path.splitext(filename)[1]
            if ext:
                suffix = ext
        if suffix == ".bin":
            if "image" in mime_type: suffix = ".jpg"
            elif "audio" in mime_type: suffix = ".mp3"
            elif "video" in mime_type: suffix = ".mp4"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            temp_path = tmp.name

        uploaded_file = genai.upload_file(temp_path, mime_type=mime_type)

        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = genai.get_file(uploaded_file.name)

        if uploaded_file.state.name == "FAILED":
            raise Exception("Gemini File Upload Failed")

        settings = get_settings()
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(
            [uploaded_file, prompt],
            request_options={"timeout": 600},
        )
        return response.text

    except Exception as e:
        logger.error(f"Gemini processing error: {e}")
        return f"Error processing media: {str(e)}"
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def process_image(image_bytes: bytes) -> str:
    return process_media_with_gemini(image_bytes, "image/jpeg", "Describe this image in extreme detail for search and analysis purposes. Include text, objects, colors, and sentiments.")


def process_audio(audio_bytes: bytes, filename: str = "audio.mp3") -> str:
    return process_media_with_gemini(audio_bytes, "audio/mp3", "Transcribe this audio completely and generate a summary of the main points.", filename=filename)


def process_video(video_bytes: bytes) -> str:
    return process_media_with_gemini(video_bytes, "video/mp4", "Watch this video. 1. Transcribe what is spoken. 2. Describe what happens visually frame by frame at key moments.")
