"""Opt-in, authenticated loopback mailbox for Witchcraft's terminal adapter.

HTTP threads only move bounded messages. WindowSession drains commands on its
own thread, so the existing controller remains the sole owner of turns/Tk.
"""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import queue
import re
import secrets
import threading
from time import monotonic
from urllib.parse import parse_qs, urlsplit


MAX_TEXT = 8192
MAX_BODY = 40 * 1024
DEFAULT_PORT = 18749
ENDPOINT_NAME = "witchcraft_endpoint.json"
COMMANDS = {"text", "stop", "pause", "reset", "wow_include", "ptt_down", "ptt_up", "ptt_hold", "ptt_cancel"}


class WitchcraftBridge:
    def __init__(self, root, *, port=DEFAULT_PORT, clock=monotonic):
        self.path = Path(root) / "runtime" / ENDPOINT_NAME
        self.port, self.clock = port, clock
        self.session, self.token = secrets.token_hex(32), secrets.token_hex(32)
        self.commands = queue.Queue(maxsize=8)
        self.events, self.seen = deque(maxlen=128), deque(maxlen=256)
        self.lock = threading.Lock()
        self.sequence = 0
        self.server = self.thread = None
        self.closed = False
        self.capabilities = {"sttReady": False}
        self.ptt_state = {"token": None, "state": "idle"}

    def start(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            # No request logging: Authorization and question text stay private.
            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(1)

            def answer(self, status, data):
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def authorized(self):
                expected = f"127.0.0.1:{bridge.port}"
                authorization = self.headers.get("Authorization", "")
                if (self.headers.get("Host") != expected or self.headers.get("Origin") is not None
                        or not hmac.compare_digest(authorization.encode("utf-8"),
                                                   f"Bearer {bridge.token}".encode("ascii"))):
                    self.answer(403, {"error": "Connection refused"})
                    return False
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                url = urlsplit(self.path)
                args = parse_qs(url.query)
                if url.path != "/events" or args.get("session") != [bridge.session]:
                    self.answer(409, {"error": "Akane session changed"})
                    return
                cursor = args.get("after", [""])[0]
                if not re.fullmatch(r"[0-9]{1,16}", cursor):
                    self.answer(400, {"error": "Invalid event cursor"})
                    return
                self.answer(200, bridge.read_events(int(cursor)))

            def do_POST(self):
                if not self.authorized():
                    return
                if self.path != "/command":
                    self.answer(404, {"error": "Unknown command route"})
                    return
                length = self.headers.get("Content-Length", "")
                if (len(length) > 6 or not length.isdecimal() or not 0 < int(length) <= MAX_BODY
                        or self.headers.get("Transfer-Encoding")
                        or self.headers.get("Content-Type") != "application/json"):
                    self.answer(400, {"error": "Invalid command document"})
                    return
                try:
                    data = json.loads(self.rfile.read(int(length)).decode("utf-8"))
                    status, result = bridge.enqueue(data)
                except (ValueError, UnicodeError, RecursionError):
                    status, result = 400, {"error": "Invalid command document"}
                self.answer(status, result)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = False
            # Bound HTTP handlers independently of the bounded command mailbox.
            slots = threading.BoundedSemaphore(4)

            def process_request(self, request, client_address):
                if not self.slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    self.slots.release()
                    raise

            def process_request_thread(self, request, client_address):
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    self.slots.release()

        self.server = Server(("127.0.0.1", self.port), Handler)
        self.port = self.server.server_address[1]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f".{self.session}.tmp")
            try:
                temporary.write_text(json.dumps({"protocol": 1, "port": self.port,
                                                "session": self.session, "token": self.token}), encoding="utf-8")
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.thread = threading.Thread(target=self.server.serve_forever,
                                           kwargs={"poll_interval": 0.05}, name="Akane Witchcraft", daemon=True)
            self.thread.start()
        except BaseException:
            self.server.server_close()
            raise

    def enqueue(self, data):
        if not isinstance(data, dict) or data.get("session") != self.session:
            return 409, {"error": "Akane session changed"}
        identifier, action, value = data.get("id"), data.get("action"), data.get("value")
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            return 400, {"error": "Invalid command ID"}
        if not isinstance(action, str) or action not in COMMANDS:
            return 400, {"error": "Unsupported command"}
        if action == "text":
            if (not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT
                    or re.search(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]", value)):
                return 400, {"error": "Question is empty, too long or contains control characters"}
            value = value.strip()
        elif action == "wow_include":
            if not isinstance(value, bool):
                return 400, {"error": "Context selection must be true or false"}
        elif action.startswith("ptt_"):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{8}:[0-9a-f]{1,8}", value):
                return 400, {"error": "Invalid hold-to-talk gesture"}
        elif value is not None:
            return 400, {"error": "This control takes no value"}
        with self.lock:
            if self.closed:
                return 410, {"error": "Akane is closing"}
            if identifier in self.seen:
                return 200, {"queued": True, "duplicate": True}
            try:
                self.commands.put_nowait((self.clock(), identifier, action, value))
            except queue.Full:
                return 429, {"error": "Akane input mailbox is full; question was not queued"}
            self.seen.append(identifier)
        return 202, {"queued": True}

    def drain(self, request):
        """Only the window's main thread may call request(). Never defer busy turns."""
        for _ in range(8):
            try:
                created, identifier, action, value = self.commands.get_nowait()
            except queue.Empty:
                break
            expired = self.clock() - created > 5
            accepted = False if expired else request(action, value)
            self.publish("ack", id=identifier, accepted=accepted is True,
                         message=("Input expired; send it again." if expired else
                                  "Accepted." if accepted else "Input refused: Akane is busy, paused or closing."))

    def publish(self, kind, **payload):
        # Deliberate whitelist: reply mirroring is English-only.
        if kind == "reply":
            if "en" not in payload:
                return
            payload = {"en": payload["en"]}
        elif kind == "user":
            payload = {"text": payload.get("text", "")}
        elif kind == "notice":
            payload = {"message": payload.get("message", "")}
        elif kind == "status":
            payload = {"state": payload.get("state", "unknown")}
        elif kind == "context":
            payload = {"enabled": payload.get("enabled") is True}
        elif kind == "capabilities":
            payload = {"sttReady": payload.get("sttReady") is True}
        elif kind == "ptt":
            payload = {"token": payload.get("token"), "state": payload.get("state", "idle")}
        elif kind == "reset":
            payload = {}
        elif kind != "ack":
            return
        if any(isinstance(value, str) and len(value) > 65536 for value in payload.values()):
            kind, payload = "notice", {"message": "Large reply available in Akane's control panel."}
        with self.lock:
            if self.closed:
                return
            if kind == "capabilities":
                self.capabilities = dict(payload)
            elif kind == "ptt":
                self.ptt_state = dict(payload)
            self.sequence += 1
            self.events.append({"seq": self.sequence, "kind": kind, **payload})

    def read_events(self, after):
        with self.lock:
            events = [event for event in self.events if event["seq"] > after][:8]
            return {"session": self.session, "sequence": self.sequence, "events": events, "closing": self.closed,
                    "capabilities": dict(self.capabilities), "ptt": dict(self.ptt_state),
                    "gap": bool(self.events and after < self.events[0]["seq"] - 1)}

    def stop_accepting(self):
        with self.lock:
            self.closed = True

    def close(self):
        self.stop_accepting()
        if self.thread is not None:
            self.server.shutdown()
            self.thread.join(timeout=2)
        if self.server is not None:
            self.server.server_close()
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document.get("session") == self.session:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError):
            pass
