# Shared startup settings. Restart Akane after changing them.
# Credentials belong in the environment/.env, never in this file.

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen3:4b-instruct-2507-q4_K_M"
REQUEST_TIMEOUT = 120  # Seconds to wait, rather than reasoning duration.
MAX_REPLY_TOKENS = 384  # Budget for the complete bilingual JSON response.
MAX_HISTORY_TURNS = 6  # User/assistant pairs; the system prompt is kept separately.

# Conversation provider is independent of STT. Keep local mode as the default.
LLM_BACKEND = "ollama"  # "ollama" or "openai"; CLI --llm-backend overrides this.
OPENAI_MODEL = "gpt-6-luna"
OPENAI_REQUEST_TIMEOUT = 60
OPENAI_MAX_OUTPUT_TOKENS = 1024  # Includes reasoning tokens as well as the JA/EN reply.
OPENAI_REASONING_EFFORT = "low"  # Set None for models that do not support this option.
# Soft conversation-only cap based on reported usage, not an account limit. STT
# charges are separate; stopping a request cannot guarantee avoiding its charge.
OPENAI_SESSION_BUDGET_USD = 0.50  # None disables it; a request can overshoot the cap.
# Standard text rates checked 2026-11-09. Estimates exclude cache writes, extra
# processing/tiers, and other charges; update rates when changing model or pricing.
OPENAI_PRICING_USD_PER_MILLION = {
    "gpt-6.1-sol": {"input": 2.0, "cached_input": 0.10, "output": 10.0},
    "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "output": 0.50},
}

# OpenAI-hosted lookup only; no local browser control. Applies to cloud conversation.
OPENAI_WEB_SEARCH_ENABLED = True
OPENAI_WEB_SEARCH_MAX_CALLS = 2  # Total tool operations per reply (search/open/find).
OPENAI_WEB_SEARCH_USD_PER_CALL = 0.01  # Search fee; retrieved content also uses tokens.

STT_BACKEND = "cloud"  # Independent of the conversation backend.
STT_MODEL = "base"
STT_DEVICE = "cpu"
STT_COMPUTE_TYPE = "int8"
STT_LANGUAGE = None  # None detects language; "ja", for example, forces it locally.
STT_MULTILINGUAL = True
CLOUD_STT_MODEL = "gpt-transcribe"
STT_LANGUAGES = ("en", "ru", "ja")  # Cloud hints, not guaranteed per-word detection.
STT_REQUEST_TIMEOUT = 60

VOICEVOX_URL = "http://127.0.0.1:50021"
VOICEVOX_SPEAKER_ID = 14  # 冥鳴ひまり・ノーマル (voice style ID).
VOICEVOX_QUERY_TIMEOUT = 30
VOICEVOX_SYNTHESIS_TIMEOUT = 60
# Synthesis amplitude: 1.0 is normal, 1.5 boosts speech; Akane accepts 0.0–2.0.
VOICEVOX_VOLUME_SCALE = 1.5  # Lower this if louder passages sound distorted.
TTS_ENABLED = True

# Device IDs are machine-specific: python -m sounddevice. None selects the default.
MIC_INPUT_DEVICE = None  # Use the OS default; put personal device IDs in config.local.json.
MIC_MIN_SECONDS = 0.3
MIC_MAX_SECONDS = 30  # Terminal: Enter to continue; hotkeys: continue automatically.
MIC_MIN_RMS = 100  # Raw 16-bit units; calibrate for the actual microphone.

# OBS polls files periodically. A short utterance needs enough caption visibility
# to survive a polling interval; longer speech already exceeds this minimum.
SUBTITLE_MIN_SECONDS = 2.0

# Optional same-process Browser Source; loopback binding is fixed in the server.
SUBTITLE_OVERLAY_ENABLED = False  # Or launch with --overlay / -Overlay.
SUBTITLE_OVERLAY_PORT = 8765
SUBTITLE_OVERLAY_MAX_WIDTH = 1100  # CSS pixels; OBS viewport must leave room for padding.
SUBTITLE_OVERLAY_FONT_SIZE = 38
SUBTITLE_OVERLAY_BACKGROUND_OPACITY = 0.78

# Register these only when the user starts --hotkeys mode. No keyboard hooks/logging.
HOTKEY_RECORD = "ctrl+shift+f8"
HOTKEY_PAUSE = "ctrl+shift+f9"
HOTKEY_STOP = "ctrl+shift+f10"
AUDIO_OUTPUT_DEVICE = None  # sounddevice output ID; None follows the default device.

# Optional LunaTranslator original-text feed. Connect/attach explicitly in the GUI.
TEXT_CONTEXT_WS_URL = "ws://127.0.0.1:2333/api/ws/text/origin"
TEXT_CONTEXT_MAX_CHARS = 2000
TEXT_CONTEXT_MAX_AGE_SECONDS = 300  # Refuse a stale line instead of silently attaching it.


def _load_local_overrides():
    """Optional JSON settings stay on this machine; credentials still belong in .env."""
    import json
    from pathlib import Path
    path = Path(__file__).with_name("config.local.json")
    if not path.is_file():
        return
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Cannot read config.local.json; check its JSON syntax and encoding.") from error
    if not isinstance(settings, dict):
        raise ValueError("config.local.json must contain a JSON object of existing setting names.")
    if any(not isinstance(name, str) or not name.isupper() or name.startswith("_") or name not in globals()
           for name in settings):
        raise ValueError("config.local.json contains an unknown setting; use names already defined in config.py.")
    globals().update(settings)


_load_local_overrides()
