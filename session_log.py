"""Opt-in local conversation transcripts. No audio, network or configuration dumps."""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from time import perf_counter
from uuid import uuid4


class SessionLog:
    """Serialize worker/UI events; a disk failure disables logging, not chat.

    UTC aligns with an external recording clock. Monotonic elapsed time preserves
    intervals if the Windows wall clock changes. Both outputs flush per event;
    abrupt process/power loss can still leave an incomplete last line.
    """
    def __init__(self, root, *, metadata=None, clock=perf_counter, utc_now=None):
        self.lock = threading.RLock()
        self.clock = clock
        self.utc_now = utc_now or (lambda: datetime.now(timezone.utc))
        self.started = clock()
        self.sequence = self.turns = self.markers = 0
        self.error = None
        self.closed = False
        self.files_created = False
        self.handles = []
        stamp = self.utc_now().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        self.session_id = f"{stamp}_{uuid4().hex[:8]}"
        folder = Path(root) / "runtime" / "session_logs"
        self.path = folder / f"{self.session_id}.jsonl"
        self.text_path = folder / f"{self.session_id}.txt"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for path in (self.path, self.text_path):
                self.handles.append(path.open("x", encoding="utf-8", newline="\n"))
            self.files_created = True
            self.record("session_start", metadata=metadata or {})
        except (OSError, UnicodeError) as error:
            self._fail(error)

    @property
    def active(self):
        with self.lock:
            return not self.closed and self.error is None and len(self.handles) == 2

    def _close_handles(self):
        for handle in self.handles:
            try:
                handle.close()
            except OSError:
                pass
        self.handles.clear()

    def _fail(self, error):
        self.error = f"{type(error).__name__}: {error}"
        self._close_handles()

    def record(self, kind, *, turn_id=None, **payload):
        with self.lock:
            if not self.active:
                return None
            try:
                event = {
                    "schema_version": 1, "session_id": self.session_id,
                    "sequence": self.sequence + 1,
                    "timestamp_utc": self.utc_now().astimezone(timezone.utc).isoformat(timespec="milliseconds"),
                    "elapsed_seconds": round(self.clock() - self.started, 6),
                    "event": kind, "turn_id": turn_id, "data": payload,
                }
                encoded = json.dumps(event, ensure_ascii=False, allow_nan=False)
                readable = self._readable(event)
                self.handles[0].write(encoded + "\n")
                self.handles[0].flush()
                self.handles[1].write(readable + "\n")
                self.handles[1].flush()
                self.sequence += 1
                return event
            except (OSError, ValueError, TypeError, UnicodeError) as error:
                self._fail(error)
                return None

    @staticmethod
    def _readable(event):
        data, kind = event["data"], event["event"]
        header = (f"[{event['timestamp_utc']} | +{event['elapsed_seconds']:.3f}s] "
                  f"{kind} {event['turn_id'] or ''}")
        if kind == "user":
            body = f"YOU ({data['mode']}): {data['text']}"
            if data.get("japanese_context"):
                body += f"\nAttached Japanese line: {data['japanese_context']}"
            if data.get("wow_context"):
                body += "\nAttached WoW snapshot: " + json.dumps(data["wow_context"], ensure_ascii=False)
        elif kind == "reply":
            body = f"AKANE GENERATED (playback recorded separately)\nJA: {data['ja']}"
            for language in ("en", "ru"):
                if language in data:
                    body += f"\n{language.upper()}: {data[language]}"
            body += "\n" + json.dumps({key: value for key, value in data.items() if key not in {"ja", "en", "ru"}}, ensure_ascii=False)
        elif kind == "notice":
            body = f"{data.get('level', 'info')}: {data['message']}"
        else:
            body = json.dumps(data, ensure_ascii=False)
        return header + "\n" + body + "\n"

    def begin_turn(self, mode, japanese_context=None):
        with self.lock:
            if not self.active:
                return None
            self.turns += 1
            turn_id = f"turn_{self.turns:04d}"
            self.record("turn_start", turn_id=turn_id, input_mode=mode, japanese_context=japanese_context)
            return turn_id

    def mark(self, *, turn_id=None):
        with self.lock:
            if not self.active:
                return None
            self.markers += 1
            return self.record("recording_marker", turn_id=turn_id, number=self.markers)

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.record("session_end")
            self.closed = True
            self._close_handles()
