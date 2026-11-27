"""VOICEVOX adapter: Japanese text -> WAV bytes; callers choose playback."""
import math
import wave
import requests
import config
from speech_audio import SpeechSynthesisError, validate_wav


def synthesize_speech(text):
    try:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Speech text must be non-empty.")
        volume = config.VOICEVOX_VOLUME_SCALE
        if (not isinstance(volume, (int, float)) or isinstance(volume, bool)
                or not math.isfinite(volume) or not 0 <= volume <= 2):
            raise ValueError("VOICEVOX_VOLUME_SCALE must be a number between 0 and 2.")
        query_response = requests.post(
            f"{config.VOICEVOX_URL}/audio_query",
            params={"text": text, "speaker": config.VOICEVOX_SPEAKER_ID},
            timeout=config.VOICEVOX_QUERY_TIMEOUT,
        )
        query_response.raise_for_status()
        query = query_response.json()
        if not isinstance(query, dict):
            raise ValueError("VOICEVOX returned invalid synthesis settings.")
        query["volumeScale"] = volume
        audio_response = requests.post(
            f"{config.VOICEVOX_URL}/synthesis",
            params={"speaker": config.VOICEVOX_SPEAKER_ID}, json=query,
            timeout=config.VOICEVOX_SYNTHESIS_TIMEOUT,
        )
        audio_response.raise_for_status()
        return validate_wav(audio_response.content)
    except (requests.exceptions.RequestException, ValueError, TypeError, wave.Error, EOFError) as error:
        raise SpeechSynthesisError(f"VOICEVOX synthesis failed: {error}") from error
