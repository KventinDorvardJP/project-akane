# Interruptible WAV playback used only by hotkey mode; no sound is played on import.
import io
import wave
import sounddevice as sd
import config


def play_wav(audio_data, control):
    if control.cancel.is_set():
        return False
    try:
        with wave.open(io.BytesIO(audio_data), "rb") as audio:
            width, channels, rate = audio.getsampwidth(), audio.getnchannels(), audio.getframerate()
            dtype = {1: "uint8", 2: "int16", 3: "int24", 4: "int32"}.get(width)
            if dtype is None:
                raise RuntimeError("Unsupported WAV sample width.")
            # Short writes bound how much sound can be queued after a stop request.
            frames_per_chunk = max(1, int(rate * 0.02))
            with sd.RawOutputStream(samplerate=rate, channels=channels, dtype=dtype,
                                    device=config.AUDIO_OUTPUT_DEVICE) as stream:
                while True:
                    if control.cancel.is_set():
                        stream.abort()  # Discard queued output instead of draining it.
                        return False
                    chunk = audio.readframes(frames_per_chunk)
                    if not chunk:
                        break
                    stream.write(chunk)
                if control.cancel.is_set():
                    stream.abort()
                    return False
        return True
    except (sd.PortAudioError, wave.Error, EOFError, ValueError) as error:
        raise RuntimeError(f"Audio playback failed: {error}") from error
