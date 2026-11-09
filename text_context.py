"""Opt-in, receive-only LunaTranslator text feed. Never starts on import."""
from dataclasses import dataclass
import ipaddress
import math
import threading
from time import monotonic
from urllib.parse import urlsplit


class TextContextError(ValueError):
    pass


def validate_url(url):
    try:
        parts = urlsplit(url)
        host = parts.hostname
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
        if (not local or parts.scheme not in {"ws", "wss"} or parts.username or parts.password
                or parts.path != "/api/ws/text/origin" or parts.query or parts.fragment
                or parts.port is None or not 1 <= parts.port <= 65535
                or any(ord(character) <= 32 for character in url)):
            raise ValueError()
    except (ValueError, TypeError):
        raise TextContextError("Use a loopback ws:// address with a port and /api/ws/text/origin.") from None
    return url


@dataclass(frozen=True)
class ContextSnapshot:
    text: str
    received_at: float | None
    connected: bool
    status: str


def connect_socket(*args, **kwargs):
    # Keep the rest of Akane usable if the optional package is not installed yet.
    from websockets.sync.client import connect
    return connect(*args, **kwargs)


class TextContextListener:
    def __init__(self, url, *, max_chars=2000, connect=connect_socket, clock=monotonic, retry_seconds=2):
        self.url, self.max_chars, self.connect, self.clock = url, max_chars, connect, clock
        self.retry_seconds = retry_seconds
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.worker = None
        self._text, self._received_at = "", None
        self._connected, self._status = False, "Disconnected"

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def snapshot(self):
        with self.lock:
            return ContextSnapshot(self._text, self._received_at, self._connected, self._status)

    def clear(self):
        with self.lock:
            self._text, self._received_at = "", None

    def _state(self, connected, status):
        with self.lock:
            if connected and self.stop_event.is_set():
                return
            self._connected, self._status = connected, status
            if not connected:
                self.clear()

    def accept(self, message):
        if not isinstance(message, str):
            self.clear()
            raise TextContextError("Ignored a non-text frame.")
        text = message.replace("\r\n", "\n").strip()
        if not text:
            return False
        if len(text) > self.max_chars or any(ord(c) < 32 and c not in "\n\t" for c in text):
            self.clear()
            raise TextContextError("Ignored an oversized line or text containing control characters.")
        with self.lock:
            if self.stop_event.is_set() or not self._connected:
                return False
            self._text, self._received_at = text, self.clock()
        return True

    def fresh_text(self, max_age):
        if not isinstance(max_age, (int, float)) or isinstance(max_age, bool) or not math.isfinite(max_age) or max_age <= 0:
            raise TextContextError("TEXT_CONTEXT_MAX_AGE_SECONDS must be positive.")
        with self.lock:
            if not self._connected or not self._text or self._received_at is None:
                raise TextContextError("No current Japanese line. Connect and capture a line, or uncheck Include Japanese line.")
            if self.clock() - self._received_at > max_age:
                self.clear()
                raise TextContextError("The Japanese line is stale. Capture it again, or uncheck Include Japanese line.")
            return self._text

    def start(self):
        if self.running:
            return False
        validate_url(self.url)
        if not isinstance(self.max_chars, int) or isinstance(self.max_chars, bool) or self.max_chars <= 0:
            raise TextContextError("TEXT_CONTEXT_MAX_CHARS must be a positive integer.")
        self.stop_event.clear()
        self._state(False, "Connecting…")
        self.worker = threading.Thread(target=self._run, name="Akane original-text feed", daemon=False)
        self.worker.start()
        return True

    def request_stop(self):
        # No socket.close()/join on Tk's thread; recv and handshake are bounded.
        self.stop_event.set()
        self._state(False, "Disconnecting…" if self.running else "Disconnected")

    def _run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    with self.connect(self.url, proxy=None, compression=None, open_timeout=2,
                                      close_timeout=0.5, ping_interval=None, max_size=16384, max_queue=1) as socket:
                        if self.stop_event.is_set():
                            break
                        self._state(True, "Connected — waiting for a new line")
                        while not self.stop_event.is_set():
                            try:
                                message = socket.recv(timeout=0.2)
                            except TimeoutError:
                                continue
                            try:
                                if self.accept(message):
                                    self._state(True, "Connected")
                            except TextContextError as error:
                                self._state(True, str(error))
                except ImportError:
                    self._state(False, "Install requirements.txt to enable the text feed.")
                    return
                except Exception as error:
                    if not self.stop_event.is_set():
                        self._state(False, f"Disconnected — retrying: {type(error).__name__}: {error}")
                if self.stop_event.wait(self.retry_seconds):
                    break
        finally:
            if self.stop_event.is_set():
                self._state(False, "Disconnected")
            else:
                self.clear()

    def join(self, timeout=None):
        if self.worker is not None:
            self.worker.join(timeout)
