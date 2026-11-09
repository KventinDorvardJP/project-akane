# Cloud STT adapter; credentials and client lifetime belong to the caller.
from time import perf_counter
from openai import APIError
import config
from speech_to_text import TranscriptionError


def transcribe_audio(client, audio_path):
    started = perf_counter()
    try:
        with audio_path.open("rb") as audio_file:
            response = client.audio.transcriptions.create(
                model=config.CLOUD_STT_MODEL, file=audio_file,
                prompt=(
                    "English or Russian speech containing Japanese words and quotations "
                    "or full Japanese speech. "
                    "Preserve English in English, Russian in Russian and Japanese in Japanese script "
                    "(kanji and kana), not romaji. Do not translate."
                ),
                # Preserve existing language/name hints; exact spelling is not guaranteed.
                extra_body={"keywords": ["アカネ"], "languages": list(config.STT_LANGUAGES)},
            )
        if not isinstance(response.text, str):
            raise ValueError("Missing transcription text.")
        data = response.model_dump()
        metadata = data.get("languages") or []
        if not isinstance(metadata, list):
            metadata = []
        return {
            "text": response.text.strip(),
            "languages": [item["code"] for item in metadata
                          if isinstance(item, dict) and isinstance(item.get("code"), str)],
            "language_probability": None,  # No comparable cloud confidence value.
            "elapsed_seconds": perf_counter() - started,
        }
    except (APIError, OSError, ValueError, TypeError, AttributeError) as error:
        raise TranscriptionError(f"Cloud transcription failed: {error}") from error
