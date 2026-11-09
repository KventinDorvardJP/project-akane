# Explicit live benchmark: local services by default; cloud STT requires --cloud-stt.
# It never records the microphone, plays sound, or stores conversation contents.

import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import platform
import subprocess
import tempfile
from time import perf_counter
import wave

import requests

import config
from main import initialize_stt, parse_bilingual_reply
from ollama_backend import get_llm_reply
from text_to_speech import synthesize_speech


def resource_snapshot():
    """Best-effort read-only observations; GPU data covers the whole machine."""
    result = {}
    commands = {
        "gpu": ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits"],
        "processes": ["powershell", "-NoProfile", "-Command",
                      "Get-Process | Where-Object { $_.ProcessName -match 'ollama|voicevox|obs64|^run$' } | "
                      "Select-Object ProcessName,Id,WorkingSet64,CPU | ConvertTo-Json -Compress"],
    }
    for name, command in commands.items():
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            result[name] = completed.stdout.strip() if completed.returncode == 0 else "unavailable"
        except (OSError, subprocess.TimeoutExpired):
            result[name] = "unavailable"
    return result


def service_version(url):
    try:
        response = requests.get(url, timeout=3)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return "unavailable"


def run_benchmark(project_dir, runs=3, cloud_stt=False, local_stt=False):
    root = Path(project_dir)
    prompt = (root / "personality.txt").read_text(encoding="utf-8")
    report = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "platform": platform.platform(),
        "ollama_version": service_version(config.OLLAMA_URL.rsplit("/api/", 1)[0] + "/api/version"),
        "voicevox_version": service_version(config.VOICEVOX_URL + "/version"),
        "settings": {"llm_model": config.OLLAMA_MODEL, "voice_style": config.VOICEVOX_SPEAKER_ID,
                     "local_stt_model": config.STT_MODEL, "local_stt_device": config.STT_DEVICE,
                     "local_stt_compute_type": config.STT_COMPUTE_TYPE},
        "resources_before": resource_snapshot(), "runs": [], "stt_checks": {},
    }
    # Identical fresh conversation input per run keeps history size constant.
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": "Give me one short greeting before a game, in one sentence."}]
    for index in range(runs):
        started = perf_counter()
        ja, en = parse_bilingual_reply(get_llm_reply(messages))
        llm_seconds = perf_counter() - started
        started = perf_counter()
        audio = synthesize_speech(ja)
        tts_seconds = perf_counter() - started
        with wave.open(io.BytesIO(audio), "rb") as waveform:
            audio_seconds = waveform.getnframes() / waveform.getframerate()
        row = {"run": index + 1, "llm_seconds": llm_seconds, "tts_seconds": tts_seconds,
               "llm_plus_tts_seconds": llm_seconds + tts_seconds,
               "audio_seconds": audio_seconds, "ja_characters": len(ja), "en_characters": len(en),
               "resources_after": resource_snapshot()}
        report["runs"].append(row)
        print(f"Run {index + 1}: LLM {llm_seconds:.2f}s; TTS {tts_seconds:.2f}s; "
              f"ready {llm_seconds + tts_seconds:.2f}s; audio {audio_seconds:.2f}s", flush=True)

    if cloud_stt or local_stt:
        # This known synthetic phrase contains no microphone/private sample content.
        audio = synthesize_speech("こんにちは。私はアカネです。これは音声認識のテストです。")
        original_backend = config.STT_BACKEND
        try:
            with tempfile.TemporaryDirectory(prefix="akane-benchmark-") as temporary:
                path = Path(temporary) / "synthetic.wav"
                path.write_bytes(audio)
                for backend, enabled in (("local", local_stt), ("cloud", cloud_stt)):
                    if not enabled:
                        continue
                    client = None
                    try:
                        config.STT_BACKEND = backend
                        recognize, client = initialize_stt(root)
                        result = recognize(path)
                        # Record only diagnostics; the actual transcript is not persisted.
                        report["stt_checks"][backend] = {
                            "text_nonempty": bool(result["text"]), "languages": result["languages"],
                            "elapsed_seconds": result["elapsed_seconds"],
                        }
                        print(f"{backend} STT: nonempty={bool(result['text'])}; "
                              f"{result['elapsed_seconds']:.2f}s", flush=True)
                    except Exception as error:
                        report["stt_checks"][backend] = {"error_type": type(error).__name__}
                        print(f"{backend} STT unavailable: {type(error).__name__}", flush=True)
                    finally:
                        if client is not None:
                            client.close()
        finally:
            config.STT_BACKEND = original_backend
    return report


def cli():
    parser = argparse.ArgumentParser(description="Benchmark local Akane services without playback.")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cloud-stt", action="store_true", help="One paid API check using synthetic audio.")
    parser.add_argument("--local-stt", action="store_true", help="Check the configured local model; may download it.")
    parser.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    if not 1 <= args.runs <= 10:
        parser.error("--runs must be between 1 and 10")
    report = run_benchmark(args.project_dir, args.runs, args.cloud_stt, args.local_stt)
    path = args.project_dir / "runtime" / "benchmark.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved diagnostics: {path}")


if __name__ == "__main__":
    cli()
