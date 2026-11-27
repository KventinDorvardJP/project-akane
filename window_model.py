"""Plain display state; no Tk widgets, devices or provider calls."""
from collections import deque
import math


PHASE_LABELS = {
    "idle": "Ready", "starting": "Starting microphone…", "recording": "Recording — press Finish recording",
    "transcribing": "Transcribing…", "thinking": "Thinking…",
    "context": "Getting WoW context — keep WoW foreground…",
    "synthesizing": "Generating Japanese speech…", "speaking": "Speaking…",
    "captioning": "Showing subtitle pages…",
    "paused": "Paused", "stopped": "Stopped",
}


class WindowModel:
    def __init__(self, backend_label, *, tts_enabled, budget):
        self.backend_label, self.tts_enabled, self.budget = backend_label, tts_enabled, budget
        self.status = {"state": "idle", "phase": "idle", "paused": False, "busy": False}
        self.messages = deque(maxlen=100)
        self.activity = deque(maxlen=30)
        self.sources = []
        self.metrics = {}
        self.transcription = {}
        self.session_cost, self.cost_unknown = None, False
        self.stt_state, self.hotkeys_status = "Starting…", "Off — window buttons are available"
        self.stt_ready, self.closing = False, False
        self.notice, self.notice_level = "Type a message, or wait for the microphone to initialize.", "info"
        self.revision = 0
        self.context_text, self.context_status = "", "Disconnected"
        self.context_running = self.context_stopping = self.include_context = False
        self.include_wow_context = False
        self.wow_snapshot, self.wow_status = None, "Off"
        self.retry_text, self.retry_revision = None, 0
        self.overlay_url, self.overlay_status = None, "Off"
        self.tts_backend = "voicevox"
        self.session_log_path = None
        self.session_log_status = "Off"

    def apply(self, kind, payload):
        if kind == "status":
            self.status = dict(payload)
        elif kind == "user":
            self.messages.append({"role": "user", "text": payload["text"], "mode": payload["mode"],
                                  "japanese_context": payload.get("japanese_context"),
                                  "wow_context": payload.get("wow_context")})
            self.notice, self.notice_level = "", "info"
            self.revision += 1
        elif kind == "reply":
            self.messages.append({"role": "assistant", **payload})
            self.revision += 1
        elif kind == "memory":
            self.messages.append({"role": "memory", "text": payload["text"]})
            self.revision += 1
        elif kind == "sources":
            self.sources = list(payload.get("sources", []))
        elif kind == "transcription":
            self.transcription = dict(payload)
        elif kind == "wow_context" and self.include_wow_context:
            self.wow_snapshot = payload["snapshot"]
            requested = self.wow_snapshot.get("requestedStatus", self.wow_snapshot["status"])
            suffix = " · other game data partial" if requested == "ready" and self.wow_snapshot["status"] == "partial" else ""
            self.wow_status = "Last snapshot · " + requested + " for this question" + suffix
        elif kind == "wow_context_failed":
            self.wow_snapshot, self.wow_status = None, "Unavailable — " + payload["message"]
            if payload.get("text"):
                self.retry_text = payload["text"]
                self.retry_revision += 1
        elif kind == "metrics":
            self.metrics = dict(payload["row"])
            value = payload.get("session_cost")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                self.session_cost = value
            self.cost_unknown = payload.get("cost_unknown") is True
        elif kind == "notice":
            self.notice = str(payload["message"])
            self.notice_level = payload.get("level", "info")
            self.activity.append((self.notice_level, self.notice))
        elif kind == "reset":
            self.messages.clear()
            self.sources = []
            self.transcription = {}
            self.revision += 1  # Session spend deliberately survives Reset chat.

    @property
    def status_text(self):
        if self.closing:
            return "Stopping — waiting for active work to finish…"
        return PHASE_LABELS.get(self.status["state"], self.status["state"].capitalize())

    def buttons(self):
        idle = not self.status["busy"] and not self.status["paused"] and not self.closing
        recording = self.status["phase"] in {"starting", "recording"} and self.status["busy"]
        return {
            "send": idle, "record": self.stt_ready and not self.closing and not self.status["paused"] and (idle or recording),
            "audio": self.stt_ready and idle, "pause": not self.closing,
            "stop": not self.closing, "reset": not self.closing,
            "record_text": "Finish recording" if recording else "Record",
            "pause_text": "Resume" if self.status["paused"] else "Pause",
        }

    @property
    def cost_text(self):
        if self.session_cost is None:
            return "No cloud conversation estimate yet.\nCloud STT is billed separately."
        allowance = "No local cap" if self.budget is None else f"Soft cap: ${self.budget:.2f}"
        prefix = "At least " if self.cost_unknown else ""
        suffix = "\nSome usage is unknown." if self.cost_unknown else ""
        return f"{prefix}${self.session_cost:.5f} this session\n{allowance} · STT extra{suffix}"


def backend_label(backend):
    model = getattr(backend, "model", None)
    if isinstance(model, str):
        return f"OpenAI · {model}"
    module = getattr(backend, "__module__", "")
    if module == "test_fake_backend":
        return "Test backend · no LLM requests"
    if module == "ollama_backend":
        import config
        return f"Ollama · {config.OLLAMA_MODEL}"
    return "Custom conversation backend"
