# Local Faster-Whisper adapter; return the same result fields as cloud STT.
from time import perf_counter
import av
import config


class TranscriptionError(Exception):
    pass


def transcribe_audio(model, audio_path):
    started = perf_counter()  # Caller loads the model outside this per-request timer.
    try:
        segments, info = model.transcribe(
            str(audio_path), language=config.STT_LANGUAGE, task="transcribe",
            multilingual=config.STT_MULTILINGUAL, vad_filter=True,
        )
        # Consume lazy segments before stopping the timer: inference continues here.
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return {
            "text": text,
            # Overall reported language, not every language occurring in the audio.
            "languages": [info.language] if info.language else [],
            "language_probability": info.language_probability,
            "elapsed_seconds": perf_counter() - started,
        }
    except (av.error.FFmpegError, OSError, RuntimeError, ValueError) as error:
        raise TranscriptionError(f"Local transcription failed: {error}") from error
