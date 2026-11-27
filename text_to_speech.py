"""Selectable TTS entry point: Japanese text -> complete WAV bytes."""
import config
from speech_audio import SpeechSynthesisError  # Preserve the public error import.
from voicevox_backend import synthesize_speech as synthesize_voicevox


def synthesize_speech(text, *, backend=None):
    backend = config.TTS_BACKEND if backend is None else backend
    if backend == "voicevox":
        return synthesize_voicevox(text)
    if backend == "gpt_sovits":
        # Optional dependencies and model services are untouched in default mode.
        from gpt_sovits_backend import synthesize_speech as synthesize_gpt_sovits
        return synthesize_gpt_sovits(text)
    raise SpeechSynthesisError(f"Unsupported TTS_BACKEND: {backend!r}. Use voicevox or gpt_sovits.")
