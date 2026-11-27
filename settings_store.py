"""Typed local settings, independent of Tk or running services."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

import config
from windows_hotkeys import HotkeyError, parse_hotkey

RESTART_SESSION = 75
NULLABLE = {"MIC_INPUT_DEVICE", "AUDIO_OUTPUT_DEVICE", "STT_LANGUAGE",
            "OPENAI_REASONING_EFFORT", "OPENAI_SESSION_BUDGET_USD"}
CHOICES = {
    "LLM_BACKEND": ("ollama", "openai"), "TTS_BACKEND": ("voicevox", "gpt_sovits"),
    "STT_BACKEND": ("cloud", "local"), "STT_DEVICE": ("cpu", "cuda", "auto"),
    "STT_LANGUAGE": ("", "en", "ja", "ru"),
    "SUBTITLE_LANGUAGE": ("en", "ru"),
    "OPENAI_REASONING_EFFORT": ("", "none", "low", "medium", "high", "xhigh", "max"),
    "GPT_SOVITS_PROMPT_LANGUAGE": ("Japanese", "English", "Chinese"),
    "GPT_SOVITS_TEXT_LANGUAGE": ("Japanese", "Japanese-English Mixed", "English", "Multilingual Mixed"),
    "GPT_SOVITS_TEXT_SPLIT_METHOD": ("No slice", "Slice once every 4 sentences", "Slice per 50 characters",
                                   "Slice by Chinese punct", "Slice by English punct", "Slice by every punct"),
}
STRICT_CHOICES = {"LLM_BACKEND", "TTS_BACKEND", "STT_BACKEND", "SUBTITLE_LANGUAGE"}


def is_decimal(name, default):
    # A user may write gain=2 in config.py; it must still accept a fractional gain.
    return (isinstance(default, float) or name.endswith(("TIMEOUT", "_SECONDS"))
            or name in {"VOICEVOX_VOLUME_SCALE", "GPT_SOVITS_SPEED", "OPENAI_SESSION_BUDGET_USD",
                        "OPENAI_WEB_SEARCH_USD_PER_CALL", "SUBTITLE_OVERLAY_BACKGROUND_OPACITY"})


def group_for(name):
    if name in {"FAKE_BACKEND_ENABLED", "HOTKEYS_ENABLED", "STT_ENABLED", "TTS_ENABLED", "LLM_BACKEND",
                "TTS_BACKEND", "TTS_PREFETCH_NEXT", "SESSION_LOG_ENABLED", "MEMORY_ENABLED"}:
        return "Session"
    if name.startswith("GPT_SOVITS"):
        return "GPT-SoVITS"
    if name.startswith("VOICEVOX"):
        return "VOICEVOX"
    if name.startswith(("STT_", "CLOUD_STT")):
        return "Recognition"
    if name.startswith(("MIC_", "AUDIO_", "HOTKEY_")):
        return "Audio & keys"
    if name.startswith(("SUBTITLE", "REPLY_")):
        return "Subtitles"
    if name.startswith("TEXT_CONTEXT"):
        return "Japanese text"
    if name.startswith("WOW_CONTEXT") or name == "WITCHCRAFT_ENABLED":
        return "WoW context"
    return "Conversation"


def label_for(name):
    labels = {"FAKE_BACKEND_ENABLED": "Use test echo replies (no LLM requests)",
              "HOTKEYS_ENABLED": "Enable global hotkeys", "STT_ENABLED": "Enable speech recognition",
              "TTS_ENABLED": "Enable spoken replies", "LLM_BACKEND": "Conversation provider",
              "TTS_BACKEND": "Speech engine", "SUBTITLE_OVERLAY_ENABLED": "Enable OBS browser overlay",
              "TTS_PREFETCH_NEXT": "Prepare one speech part ahead",
              "SESSION_LOG_ENABLED": "Record session transcript",
              "MEMORY_ENABLED": "Enable persistent memory",
              "WOW_CONTEXT_CACHE_PATH": "Witchcraft wow-context.json path",
              "WOW_CONTEXT_MAX_AGE_SECONDS": "Maximum snapshot age (seconds)",
              "WOW_CONTEXT_WAIT_SECONDS": "Maximum refresh wait (seconds)",
              "WITCHCRAFT_ENABLED": "Enable Witchcraft in-game chat (local connection)",
              "REPLY_CHUNK_JA_CHARS": "Japanese characters per speech part",
              "SUBTITLE_LANGUAGE": "Subtitle language (en = English, ru = Russian)",
              "REPLY_CHUNK_EN_CHARS": "Characters per subtitle part",
              "REPLY_MAX_CHUNKS": "Maximum parts per answer",
              "SUBTITLE_READING_CHARS_PER_SECOND": "Text-only reading speed (characters/second)",
              "GPT_SOVITS_REFERENCE_AUDIO": "Main reference audio", "GPT_SOVITS_REFERENCE_TEXT": "Exact reference transcript",
              "GPT_SOVITS_AUX_REFERENCE_AUDIO": "Extra reference audio (one file per line)",
              "MIC_INPUT_DEVICE": "Microphone (blank = system default)",
              "AUDIO_OUTPUT_DEVICE": "Audio output (blank = system default)"}
    return labels.get(name, name.replace("GPT_SOVITS_", "").replace("VOICEVOX_", "")
                      .replace("OPENAI_", "Cloud ").replace("OLLAMA_", "Ollama ")
                      .replace("_", " ").strip().capitalize())


def format_value(name, value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, (list, tuple)):
        return "\n".join(value) if name == "GPT_SOVITS_AUX_REFERENCE_AUDIO" else ", ".join(value)
    return str(value)


def parse_value(name, value, default):
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{label_for(name)} must be on or off.")
        return value
    text = str(value).strip()
    if name in NULLABLE and not text:
        return None
    try:
        if name in {"MIC_INPUT_DEVICE", "AUDIO_OUTPUT_DEVICE"}:
            return int(text) if text.isdecimal() else text
        if isinstance(default, dict):
            return json.loads(text)
        if isinstance(default, (tuple, list)):
            return [part.strip() for part in (text.splitlines() if name == "GPT_SOVITS_AUX_REFERENCE_AUDIO"
                                              else text.split(",")) if part.strip()]
        if is_decimal(name, default):
            return float(text)
        if isinstance(default, int):
            return int(text)
        return text
    except (ValueError, TypeError) as error:
        raise ValueError(f"Invalid value for {label_for(name)}.") from error


def validate_settings(values, defaults, project_dir):
    if set(values) != set(defaults):
        raise ValueError("Settings are missing fields or contain unknown fields; reopen Settings.")
    for name, default in defaults.items():
        value = values[name]
        if value is None and name in NULLABLE:
            continue
        if name in {"MIC_INPUT_DEVICE", "AUDIO_OUTPUT_DEVICE"}:
            valid = (isinstance(value, str) and bool(value.strip())) or (type(value) is int and value >= 0)
        elif isinstance(default, bool):
            valid = type(value) is bool
        elif is_decimal(name, default):
            valid = type(value) in (int, float) and math.isfinite(value)
        elif isinstance(default, int):
            valid = type(value) is int
        elif isinstance(default, (list, tuple)):
            valid = isinstance(value, (list, tuple)) and all(isinstance(item, str) and item.strip() for item in value)
        elif isinstance(default, dict):
            valid = isinstance(value, dict)
        else:
            valid = isinstance(value, str)
        if not valid:
            raise ValueError(f"Invalid type or number for {label_for(name)}.")
        if name in STRICT_CHOICES and value not in CHOICES[name]:
            raise ValueError(f"Choose a supported {label_for(name)}.")
        if type(value) in (int, float) and (not math.isfinite(value) or value < 0):
            raise ValueError(f"{label_for(name)} must be finite and non-negative.")
        if name.endswith("TIMEOUT") or name in {"MAX_REPLY_TOKENS", "OPENAI_MAX_OUTPUT_TOKENS", "TEXT_CONTEXT_MAX_CHARS", "MIC_MAX_SECONDS"}:
            if type(value) not in (int, float) or value <= 0:
                raise ValueError(f"{label_for(name)} must be greater than zero.")
        if name.endswith("URL"):
            parts = urlsplit(value)
            parts.port
            schemes = ("ws", "wss") if name == "TEXT_CONTEXT_WS_URL" else ("http", "https")
            if parts.scheme not in schemes or not parts.hostname or parts.username or parts.password:
                raise ValueError(f"Enter a valid URL for {label_for(name)} without embedded credentials.")
            if name == "GPT_SOVITS_URL" and (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost")):
                raise ValueError("GPT-SoVITS must use a local HTTP inference URL.")
    ranges = {"VOICEVOX_VOLUME_SCALE": (0, 2), "SUBTITLE_OVERLAY_PORT": (1, 65535),
              "SUBTITLE_OVERLAY_MAX_WIDTH": (240, 3840), "SUBTITLE_OVERLAY_FONT_SIZE": (16, 96),
              "SUBTITLE_OVERLAY_BACKGROUND_OPACITY": (0, 1), "OPENAI_WEB_SEARCH_MAX_CALLS": (1, 10),
              "GPT_SOVITS_SPEED": (0.6, 1.65), "GPT_SOVITS_TIMEOUT": (1, 600),
              "REPLY_CHUNK_JA_CHARS": (20, 300), "REPLY_CHUNK_EN_CHARS": (40, 400),
              "REPLY_MAX_CHUNKS": (1, 24), "SUBTITLE_READING_CHARS_PER_SECOND": (0, 100)}
    ranges.update({"WOW_CONTEXT_MAX_AGE_SECONDS": (35, 180), "WOW_CONTEXT_WAIT_SECONDS": (1, 60)})
    for name, (low, high) in ranges.items():
        if not low <= values[name] <= high:
            raise ValueError(f"{label_for(name)} must be between {low} and {high}.")
    if values["MIC_MIN_SECONDS"] > values["MIC_MAX_SECONDS"]:
        raise ValueError("Minimum recording duration must not exceed maximum duration.")
    from wow_context import cache_path
    cache_path(values["WOW_CONTEXT_CACHE_PATH"], project_dir)
    required = []
    if not values["FAKE_BACKEND_ENABLED"]:
        required.append("OPENAI_MODEL" if values["LLM_BACKEND"] == "openai" else "OLLAMA_MODEL")
    if values["STT_ENABLED"]:
        required.extend(("STT_MODEL", "STT_COMPUTE_TYPE") if values["STT_BACKEND"] == "local" else ("CLOUD_STT_MODEL",))
    if values["TTS_ENABLED"] and values["TTS_BACKEND"] == "gpt_sovits":
        required.extend(("GPT_SOVITS_PROMPT_LANGUAGE", "GPT_SOVITS_TEXT_LANGUAGE", "GPT_SOVITS_TEXT_SPLIT_METHOD"))
    if any(not values[name].strip() for name in required):
        raise ValueError("Fill in the model/language settings required by your selected providers.")
    try:
        keys = {parse_hotkey(values[name]) for name in ("HOTKEY_RECORD", "HOTKEY_PAUSE", "HOTKEY_STOP")}
    except HotkeyError as error:
        raise ValueError(f"Invalid global hotkey: {error}") from error
    if len(keys) != 3:
        raise ValueError("Recording, pause and stop must use different hotkeys.")
    prices = values["OPENAI_PRICING_USD_PER_MILLION"]
    for model, rates in prices.items():
        if (not isinstance(model, str) or not isinstance(rates, dict)
                or not {"input", "cached_input", "output"}.issubset(rates)
                or any(type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0 for rate in rates.values())):
            raise ValueError("Model prices require non-negative input, cached_input and output rates.")
    if values["TTS_ENABLED"] and values["TTS_BACKEND"] == "gpt_sovits":
        refs = [values["GPT_SOVITS_REFERENCE_AUDIO"], *values["GPT_SOVITS_AUX_REFERENCE_AUDIO"]]
        if not values["GPT_SOVITS_REFERENCE_TEXT"].strip():
            raise ValueError("GPT-SoVITS needs the matching reference transcript.")
        for reference in refs:
            if not reference or not (Path(project_dir) / reference).is_file():
                raise ValueError(f"GPT-SoVITS reference file not found: {reference}")


class SettingsStore:
    """Save only differences from public defaults, never mutate active config."""
    def __init__(self, project_dir):
        self.root = Path(project_dir)
        self.path = self.root / "config.local.json"
        self.defaults = deepcopy(config._defaults)
        self.original = None

    def load(self):
        self.original = self.path.read_bytes() if self.path.exists() else None
        overrides = json.loads(self.original.decode("utf-8")) if self.original is not None else {}
        if not isinstance(overrides, dict) or set(overrides) - set(self.defaults):
            raise ValueError("Local settings contain unknown fields. Check config.local.json.")
        return {**deepcopy(self.defaults), **overrides}

    def save(self, values):
        validate_settings(values, self.defaults, self.root)
        current = self.path.read_bytes() if self.path.exists() else None
        if current != self.original:
            raise ValueError("Local settings changed while this panel was open. Reopen Settings before saving.")
        overrides = {name: value for name, value in values.items() if value != self.defaults[name]
                     and not (isinstance(value, (tuple, list)) and list(value) == list(self.defaults[name]))}
        data = json.dumps(overrides, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        _atomic_write(self.path, data)
        self.original = data.encode("utf-8")


def _atomic_write(path, text):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                     dir=path.parent, suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
        output.write(text)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
