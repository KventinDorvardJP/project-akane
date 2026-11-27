"""Shared synthesis error and the WAV contract used by both playback modes."""
import io
import wave


class SpeechSynthesisError(Exception):
    pass


def validate_wav(audio_data):
    """Reject empty, unsupported or truncated WAVs before playback; return bytes."""
    if not isinstance(audio_data, bytes):
        raise ValueError("Speech engine did not return WAV bytes.")
    with wave.open(io.BytesIO(audio_data), "rb") as audio:
        frames, width, channels = audio.getnframes(), audio.getsampwidth(), audio.getnchannels()
        if frames <= 0 or audio.getframerate() <= 0:
            raise ValueError("Speech engine returned empty audio.")
        if audio.getcomptype() != "NONE" or width not in (1, 2, 3, 4) or channels < 1:
            raise ValueError("Speech engine returned an unsupported WAV format.")
        if len(audio.readframes(frames)) != frames * width * channels:
            raise ValueError("Speech engine returned truncated audio.")
    return audio_data
