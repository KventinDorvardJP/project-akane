"""Generate one WAV with the selected engine; never play it automatically."""
import argparse
from datetime import datetime
from pathlib import Path
from time import perf_counter

import config
from text_to_speech import SpeechSynthesisError, synthesize_speech


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tts-backend", choices=("voicevox", "gpt_sovits"), default=config.TTS_BACKEND)
    parser.add_argument("--text", default="こんにちは、アカネです。音声合成のテストをしています。")
    args = parser.parse_args(argv)
    started = perf_counter()
    try:
        audio = synthesize_speech(args.text, backend=args.tts_backend)
        folder = Path(__file__).resolve().parent / "runtime" / "tts_samples"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{args.tts_backend}_{datetime.now():%Y%m%d_%H%M%S_%f}.wav"
        with path.open("xb") as output:
            output.write(audio)
    except (SpeechSynthesisError, OSError) as error:
        print(f"Could not generate sample: {error}")
        return 1
    print(f"TTS backend: {args.tts_backend}; generation/save time: {perf_counter() - started:.2f} seconds")
    print(f"Saved (no playback): {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
