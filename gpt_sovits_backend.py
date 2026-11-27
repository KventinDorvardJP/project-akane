"""Optional GPT-SoVITS inference WebUI client. No model imports or UI automation."""
import math
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

import config
from speech_audio import SpeechSynthesisError, validate_wav


def _settings(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Speech text must be non-empty.")
    url = config.GPT_SOVITS_URL
    if not isinstance(url, str):
        raise ValueError("GPT_SOVITS_URL must be a loopback HTTP URL.")
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("GPT_SOVITS_URL must point to the local inference WebUI (for example http://127.0.0.1:9872).")
    parsed.port  # Reject malformed port values before connecting.
    timeout, speed = config.GPT_SOVITS_TIMEOUT, config.GPT_SOVITS_SPEED
    for name, value, low, high in (("GPT_SOVITS_TIMEOUT", timeout, 1, 600),
                                   ("GPT_SOVITS_SPEED", speed, 0.6, 1.65)):
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or not low <= value <= high):
            raise ValueError(f"{name} must be a number between {low} and {high}.")
    for name in ("GPT_SOVITS_REFERENCE_TEXT", "GPT_SOVITS_PROMPT_LANGUAGE",
                 "GPT_SOVITS_TEXT_LANGUAGE", "GPT_SOVITS_TEXT_SPLIT_METHOD"):
        value = getattr(config, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Configure {name} in config.local.json before using GPT-SoVITS.")
    root = Path(__file__).resolve().parent

    def audio_path(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Configure GPT_SOVITS_REFERENCE_AUDIO in config.local.json.")
        path = (root / value).resolve()
        if not path.is_file():
            raise ValueError(f"GPT-SoVITS reference audio not found: {path}")
        return path

    reference = audio_path(config.GPT_SOVITS_REFERENCE_AUDIO)
    extra = config.GPT_SOVITS_AUX_REFERENCE_AUDIO
    if not isinstance(extra, (list, tuple)):
        raise ValueError("GPT_SOVITS_AUX_REFERENCE_AUDIO must be a list of file paths.")
    return url.rstrip("/"), timeout, speed, reference, [audio_path(value) for value in extra]


def synthesize_speech(text):
    """Use the currently loaded WebUI weights; return a complete WAV in memory."""
    client = job = None
    try:
        url, timeout, speed, reference, extras = _settings(text)
        try:
            from gradio_client import Client, handle_file
        except ImportError as error:
            raise ValueError("Install optional GPT-SoVITS support: python -m pip install -r requirements-gpt-sovits.txt") from error
        with tempfile.TemporaryDirectory(prefix="akane-gpt-sovits-") as directory:
            try:
                client = Client(url, verbose=False, max_workers=1, download_files=directory,
                                analytics_enabled=False,
                                httpx_kwargs={"timeout": timeout, "trust_env": False})
                job = client.submit(
                    ref_wav_path=handle_file(str(reference)),
                    prompt_text=config.GPT_SOVITS_REFERENCE_TEXT,
                    prompt_language=config.GPT_SOVITS_PROMPT_LANGUAGE,
                    text=text, text_language=config.GPT_SOVITS_TEXT_LANGUAGE,
                    how_to_cut=config.GPT_SOVITS_TEXT_SPLIT_METHOD,
                    speed=speed, ref_free=False, if_freeze=False,
                    inp_refs=[handle_file(str(path)) for path in extras] or None,
                    api_name="/get_tts_wav",
                )
                try:
                    result = job.result(timeout=timeout)
                except TimeoutError:
                    try:
                        job.cancel()  # Best effort; the server may already be generating.
                    except Exception:
                        pass
                    raise
                if not isinstance(result, (str, Path)):
                    raise ValueError("GPT-SoVITS returned no generated audio file.")
                output = Path(result).resolve()
                if not output.is_relative_to(Path(directory).resolve()) or not output.is_file():
                    raise ValueError("GPT-SoVITS returned an unexpected audio download path.")
                return validate_wav(output.read_bytes())
            finally:
                if client is not None:
                    client.close()
    except TimeoutError as error:
        raise SpeechSynthesisError("GPT-SoVITS generation timed out; its server job may still finish.") from error
    except Exception as error:
        # Gradio versions expose different transport/application exception classes.
        # The rest of Akane sees the same recoverable error as with VOICEVOX.
        raise SpeechSynthesisError(f"GPT-SoVITS synthesis failed: {error}") from error
