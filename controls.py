# Pure control state. The listener only enqueues actions; one worker owns each turn.
from dataclasses import dataclass, field
import threading


class TurnCancelled(Exception):
    """A user stopped/paused a turn; do not present any later result from it."""


@dataclass
class TurnControl:
    cancel: threading.Event = field(default_factory=threading.Event)
    stop_recording: threading.Event = field(default_factory=threading.Event)
    phase_callback: object = None
    caption_guard: object = None
    turn_id: str | None = None
    context_seconds: float = 0.0

    def check(self):
        if self.cancel.is_set():
            raise TurnCancelled()

    def phase(self, name):
        self.check()
        if self.phase_callback:
            self.phase_callback(name)

    def show_caption(self, writer, text):
        if self.caption_guard:
            return self.caption_guard(self, writer, text)
        self.check()
        return writer(text)


class ControlState:
    def __init__(self):
        self.lock = threading.RLock()
        self.paused = False
        self.phase = "idle"
        self.current = None

    def snapshot(self):
        with self.lock:
            return {"state": "paused" if self.paused else self.phase,
                    "phase": self.phase, "paused": self.paused, "busy": self.current is not None}

    def begin(self, phase_callback):
        with self.lock:
            if self.paused or self.current is not None:
                return None
            control = TurnControl(phase_callback=phase_callback, caption_guard=self.caption)
            self.current, self.phase = control, "starting"
            return control

    def set_phase(self, control, phase):
        with self.lock:
            if self.current is control:
                control.check()
                self.phase = phase

    def stop(self, clear_caption):
        # Serialize clearing against the worker's caption write. A stopped worker
        # cannot race in afterward and resurrect a subtitle.
        with self.lock:
            if self.current:
                self.current.cancel.set()
                self.current.stop_recording.set()
            clear_caption("")

    def toggle_pause(self, clear_caption):
        with self.lock:
            self.paused = not self.paused
            if self.paused:
                self.stop(clear_caption)
            return self.paused

    def caption(self, control, writer, text):
        with self.lock:
            if self.current is not control:
                raise TurnCancelled()
            control.check()
            return writer(text)

    def finish(self, control):
        with self.lock:
            if self.current is control:
                self.current, self.phase = None, "idle"
