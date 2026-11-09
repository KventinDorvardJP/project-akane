# Capture one bounded mono utterance; either STT backend can consume the saved WAV.

from pathlib import Path
import wave
import numpy as np
import sounddevice as sd
import config
import threading
from controls import TurnCancelled


class RecordingError(Exception):
    pass


def record_microphone(output_path, input_device=None, *, stop_event=None, cancel_event=None, on_started=None):
    output_path = Path(output_path)
    audio_chunks = []  # Fresh per call so earlier turns cannot leak into a recording.
    captured_frames = 0
    finished = threading.Event()
    try:
        if not (0 <= config.MIC_MIN_SECONDS <= config.MIC_MAX_SECONDS) or config.MIC_MAX_SECONDS <= 0:
            raise RecordingError("Invalid microphone duration limits.")
        if config.MIC_MIN_RMS < 0:
            raise RecordingError("MIC_MIN_RMS must be zero or greater.")
        device_info = sd.query_devices(input_device, "input")
        sample_rate = int(device_info["default_samplerate"])
        max_frames = int(config.MIC_MAX_SECONDS * sample_rate)

        def collect_audio(indata, frames, time, status):
            nonlocal captured_frames
            if status:
                print(f"Recording warning: {status}")
            # Copy the stream-owned buffer. Each mono int16 frame uses two bytes.
            remaining = max_frames - captured_frames
            chunk = bytes(indata)[:remaining * 2]
            if chunk:
                audio_chunks.append(chunk)
                captured_frames += len(chunk) // 2
            if captured_frames >= max_frames:
                print("Recording limit reached." if stop_event is not None else "Recording limit reached. Press Enter to continue.")
                raise sd.CallbackStop

        if cancel_event is not None and cancel_event.is_set():
            raise TurnCancelled()
        if stop_event is None:
            input("Press Enter to start recording...")
        # Leaving this context closes capture before STT/TTS, including on Ctrl+C.
        with sd.RawInputStream(
            samplerate=sample_rate, channels=1, dtype="int16",
            device=input_device, callback=collect_audio, finished_callback=finished.set,
        ):
            if on_started:
                on_started()
            if stop_event is None:
                input("Recording... Press Enter to stop.\n")
            else:
                while not stop_event.is_set() and not finished.is_set():
                    if cancel_event is not None and cancel_event.is_set():
                        raise TurnCancelled()
                    stop_event.wait(0.02)
        if cancel_event is not None and cancel_event.is_set():
            raise TurnCancelled()
        if not audio_chunks:
            raise RecordingError("No audio captured. Try again.")
        audio_data = b"".join(audio_chunks)
        # Convert before squaring so signed 16-bit multiplication cannot overflow.
        samples = np.frombuffer(audio_data, dtype="<i2").astype(np.float32)
        duration = len(samples) / sample_rate
        if duration < config.MIC_MIN_SECONDS:
            raise RecordingError("Recording too short. Try again.")
        rms = float(np.sqrt(np.mean(samples ** 2)))
        print(f"Recording: {duration:.2f} seconds, RMS level: {rms:.0f}")
        if rms < config.MIC_MIN_RMS:
            raise RecordingError("Recording too quiet. Try again.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as audio_file:
            audio_file.setnchannels(1)
            audio_file.setsampwidth(2)  # int16 = two bytes per sample.
            audio_file.setframerate(sample_rate)
            audio_file.writeframes(audio_data)
        return output_path
    except (sd.PortAudioError, OSError, ValueError, wave.Error) as error:
        raise RecordingError(f"Microphone recording failed: {error}") from error
