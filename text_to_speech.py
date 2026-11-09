# VOICEVOX adapter: Japanese text -> WAV bytes; callers choose saving/playback.
import io
import math
import wave
import requests
import config


class SpeechSynthesisError(Exception):
    pass


def synthesize_speech(text):
    try:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Speech text must be non-empty.")
        volume = config.VOICEVOX_VOLUME_SCALE
        if (not isinstance(volume, (int, float)) or isinstance(volume, bool)
                or not math.isfinite(volume) or not 0 <= volume <= 2):
            raise ValueError("VOICEVOX_VOLUME_SCALE must be a number between 0 and 2.")
        # Prepare pronunciation and timing settings, then synthesize the waveform.
        query_response = requests.post(
            f"{config.VOICEVOX_URL}/audio_query",
            params={"text": text, "speaker": config.VOICEVOX_SPEAKER_ID},
            timeout=config.VOICEVOX_QUERY_TIMEOUT,
        )
        query_response.raise_for_status()
        query = query_response.json()
        if not isinstance(query, dict):
            raise ValueError("VOICEVOX returned invalid synthesis settings.")
        # Set the engine's native gain before synthesis so every playback mode
        # receives the same louder WAV, without changing Windows/OBS volumes.
        query["volumeScale"] = volume
        audio_response = requests.post(
            f"{config.VOICEVOX_URL}/synthesis",
            params={"speaker": config.VOICEVOX_SPEAKER_ID}, json=query,
            timeout=config.VOICEVOX_SYNTHESIS_TIMEOUT,
        )
        audio_response.raise_for_status()
        audio_data = audio_response.content
        # Reject corrupt/empty audio before asking Windows to play it.
        with wave.open(io.BytesIO(audio_data), "rb") as audio:
            if audio.getnframes() == 0:
                raise ValueError("VOICEVOX returned empty audio.")
        return audio_data
    except (requests.exceptions.RequestException, ValueError, TypeError, wave.Error, EOFError) as error:
        raise SpeechSynthesisError(f"VOICEVOX synthesis failed: {error}") from error
