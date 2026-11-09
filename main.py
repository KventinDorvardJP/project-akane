# Terminal entry point and optional hotkey mode. Shared turn logic lives in turns.py.
import argparse
import os
from pathlib import Path
import tempfile
from time import perf_counter, sleep
import winsound
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

import config
from backend_errors import BackendError
from cloud_speech_to_text import transcribe_audio as transcribe_cloud
from conversation import parse_bilingual_reply  # Public compatibility import for tests/tools.
from llm_backends import select_reply_backend
from microphone import record_microphone, RecordingError
from speech_to_text import transcribe_audio as transcribe_local, TranscriptionError
from subtitles import write_subtitle
from source_output import write_sources
from text_to_speech import synthesize_speech
from turns import TurnProcessor


def initialize_stt(project_dir):
    from functools import partial
    print(f"Initializing speech recognition: {config.STT_BACKEND}")
    if config.STT_BACKEND == "local":
        from faster_whisper import WhisperModel
        model = WhisperModel(config.STT_MODEL, device=config.STT_DEVICE,
                             compute_type=config.STT_COMPUTE_TYPE)
        return partial(transcribe_local, model), None
    if config.STT_BACKEND == "cloud":
        load_dotenv(project_dir / ".env")
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured.")
        client = OpenAI(api_key=api_key, max_retries=0, timeout=config.STT_REQUEST_TIMEOUT)
        return partial(transcribe_cloud, client), client
    raise ValueError(f"Unsupported STT_BACKEND: {config.STT_BACKEND}")


def recognize_command(command, recognize_audio, samples_folder):
    if command == "mic":
        with tempfile.TemporaryDirectory(prefix="akane-mic-") as temporary:
            path = record_microphone(Path(temporary) / "input.wav", input_device=config.MIC_INPUT_DEVICE)
            started = perf_counter()
            return recognize_audio(path), started
    filename = input("Audio filename: ").strip()
    path = samples_folder / filename
    if not filename or not path.is_file():
        raise TranscriptionError(f"Audio file not found: {path}")
    started = perf_counter()
    return recognize_audio(path), started


def main(reply_function, *, stt_enabled=True, tts_enabled=None, project_dir=None, hotkey_mode=False, gui_mode=False,
         overlay_enabled=None):
    root = Path(project_dir) if project_dir is not None else Path(__file__).resolve().parent
    tts_enabled = config.TTS_ENABLED if tts_enabled is None else tts_enabled
    client, processor = None, None
    overlay = None
    overlay_enabled = config.SUBTITLE_OVERLAY_ENABLED if overlay_enabled is None else overlay_enabled

    def clear_caption():
        try:
            write_sources(root)
        except OSError as error:
            print(f"Source output unavailable: {error}")
        try:
            write_subtitle(root / "runtime" / "subtitle_en.txt", "")
        except OSError as error:
            print(f"Subtitle output unavailable: {error}")

    try:
        clear_caption()
        try:
            system_prompt = (root / "personality.txt").read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            print(f"Cannot load personality.txt: {error}")
            return 1
        if config.MAX_HISTORY_TURNS < 0:
            print("MAX_HISTORY_TURNS must be zero or greater.")
            return 1
        # Terminal playback remains the existing synchronous Windows WAV call.
        # Hotkey mode uses small sounddevice chunks so stop can discard queued sound.
        def play(audio_data, control):
            return winsound.PlaySound(audio_data, winsound.SND_MEMORY)
        if hotkey_mode or gui_mode:
            from playback import play_wav
            play = play_wav
        processor = TurnProcessor(root, system_prompt, reply_function, tts_enabled=tts_enabled,
                                  synthesize=synthesize_speech, play=play,
                                  write_caption=write_subtitle, sleep_function=sleep)
        processor.overlay_url, processor.overlay_status, processor.overlay_warning = None, "Off", None
        if overlay_enabled:
            from subtitle_overlay import SubtitleOverlay, OverlayStyle
            try:
                style = OverlayStyle(config.SUBTITLE_OVERLAY_MAX_WIDTH, config.SUBTITLE_OVERLAY_FONT_SIZE,
                                     config.SUBTITLE_OVERLAY_BACKGROUND_OPACITY)
                overlay = SubtitleOverlay(root / "runtime" / "subtitle_en.txt",
                                          port=config.SUBTITLE_OVERLAY_PORT, style=style)
                overlay.start()
                processor.overlay_url, processor.overlay_status = overlay.url, "On"
                processor.notice(f"OBS subtitle overlay: {overlay.url}")
            except (OSError, ValueError) as error:
                if overlay is not None:
                    overlay.close()
                processor.overlay_status = "Unavailable"
                processor.overlay_warning = f"Subtitle overlay unavailable: {error}. Text-file captions still work."
                processor.notice(processor.overlay_warning, "warning")
        if gui_mode:
            try:
                from control_window import run_control_window
            except ImportError as error:
                print(f"Control window unavailable: {error}. Use terminal mode or install Python's Tcl/Tk component.")
                return 1
            return run_control_window(processor, record_microphone,
                                      initialize_stt=(lambda: initialize_stt(root)) if stt_enabled else None,
                                      hotkeys_enabled=hotkey_mode)
        recognize_audio = None
        if stt_enabled:
            try:
                recognize_audio, client = initialize_stt(root)
            except (ImportError, RuntimeError, OSError, ValueError, OpenAIError) as error:
                print(f"Speech recognition unavailable: {error}")
                print("Typed chat is still available.")
        if hotkey_mode:
            from stream_controls import StreamController
            return StreamController(processor, recognize_audio, record_microphone).run()

        paused = False
        print("Commands: mic, audio, reset, pause, stop, help, exit. You can also type a message.")
        while True:
            text = input("You: ").strip()
            started, mode, stt_seconds = perf_counter(), "typed", 0.0
            match text.lower():
                case "exit":
                    break
                case "help":
                    print("mic: record with Enter; audio: sample; reset; pause: toggle; stop: clear caption; exit.")
                    print("Use --hotkeys for background recording and interruption during playback.")
                    continue
                case "pause":
                    paused = not paused
                    processor.update_subtitle("")
                    print("Akane paused." if paused else "Akane resumed.")
                    continue
                case "stop":
                    processor.update_subtitle("")
                    continue
                case "reset":
                    processor.reset()
                    continue
                case "":
                    continue
            if paused:
                print("Akane is paused. Enter pause to resume.")
                continue
            if text.lower() in {"audio", "mic"}:
                if recognize_audio is None:
                    print("Speech recognition unavailable in this session.")
                    continue
                mode = text.lower()
                try:
                    result, started = recognize_command(mode, recognize_audio, root / "audio_samples")
                    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                        raise TranscriptionError("STT returned an invalid result.")
                except (RecordingError, TranscriptionError, OSError) as error:
                    print(error)
                    continue
                text = result["text"].strip()
                if not text:
                    print("No speech recognized.")
                    continue
                stt_seconds = result["elapsed_seconds"]
                print(f"You (transcribed): {text}")
                print(f"Languages: {', '.join(result['languages'])}")
                if result["language_probability"] is not None:
                    print(f"Language confidence: {result['language_probability']:.1%}")
                print(f"Transcription time: {stt_seconds:.2f} seconds")
            processor.respond(text, input_mode=mode, stt_seconds=stt_seconds, response_started=started)
    except (EOFError, KeyboardInterrupt):
        print("\nSession ended.")
    finally:
        clear_caption()
        if overlay is not None:
            overlay.close()
        if client is not None:
            client.close()
    return 0


def cli():
    parser = argparse.ArgumentParser(description="Akane terminal or hotkey voice companion.")
    parser.add_argument("--fake-backend", action="store_true", help="Use bilingual echo replies without an LLM request.")
    parser.add_argument("--llm-backend", choices=("ollama", "openai"), help="Override the configured conversation provider.")
    parser.add_argument("--hotkeys", action="store_true", help="Register recording/pause/stop hotkeys on this Windows machine.")
    parser.add_argument("--gui", action="store_true", help="Open the native control window in this process; add --hotkeys for global keys.")
    parser.add_argument("--overlay", action="store_true", help="Serve a loopback OBS Browser Source with a fitted subtitle background.")
    parser.add_argument("--no-stt", action="store_true", help="Skip STT initialization.")
    parser.add_argument("--no-tts", action="store_true", help="Display replies without speech requests/playback.")
    args = parser.parse_args()
    backend = None
    try:
        if args.fake_backend:
            from test_fake_backend import get_llm_reply as backend
        else:
            backend = select_reply_backend(args.llm_backend or config.LLM_BACKEND, Path(__file__).resolve().parent)
        return main(backend, stt_enabled=not args.no_stt,
                    tts_enabled=config.TTS_ENABLED and not args.no_tts, hotkey_mode=args.hotkeys, gui_mode=args.gui,
                    overlay_enabled=True if args.overlay else None)
    except BackendError as error:
        print(error)
        return 1
    finally:
        if backend is not None and callable(getattr(backend, "close", None)):
            backend.close()


if __name__ == "__main__":
    raise SystemExit(cli())
