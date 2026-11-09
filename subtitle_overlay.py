"""Optional loopback-only OBS overlay, reading the existing atomic caption file."""
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import threading
from urllib.parse import urlsplit

from subtitles import read_subtitle


@dataclass(frozen=True)
class OverlayStyle:
    max_width: int = 1100
    font_size: int = 38
    background_opacity: float = 0.78

    def __post_init__(self):
        for name, low, high in (("max_width", 240, 3840), ("font_size", 16, 96)):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValueError(f"Overlay {name} must be an integer between {low} and {high}.")
        opacity = self.background_opacity
        if (not isinstance(opacity, (int, float)) or isinstance(opacity, bool)
                or not math.isfinite(opacity) or not 0 <= opacity <= 1):
            raise ValueError("Overlay background opacity must be between 0 and 1.")


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False  # Fail visibly instead of sharing an occupied port.

    def __init__(self, app, port):
        self.app = app
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", port), _Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(1)  # Bound idle/pre-opened browser connections.
        return connection, address

    def handle_error(self, request, client_address):
        pass  # A disconnected overlay client must not spam chat/GUI logs.


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AkaneOverlay"
    sys_version = ""

    def log_message(self, *args):
        pass  # No caption contents, browsing history, or per-poll logging.

    def _send(self, status, body, content_type="text/plain; charset=utf-8", *, head=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _read(self, *, head=False):
        app = self.server.app
        host = self.headers.get("Host", "").lower()
        allowed = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}
        origin = self.headers.get("Origin")
        if host not in allowed or (origin is not None and origin not in {f"http://{h}" for h in allowed}):
            self.close_connection = True
            self._send(403, b"Loopback overlay requests only.", head=head)
            return
        if self.server.stopping.is_set():
            self.close_connection = True
            self._send(503, b"Overlay is stopping.", head=head)
            return
        path = urlsplit(self.path).path
        if path == "/api/caption":
            body = json.dumps(app.caption(), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8", head=head)
        elif path in app.assets:
            body, content_type = app.assets[path]
            self._send(200, body, content_type, head=head)
        else:
            self._send(404, b"Not found.", head=head)

    def do_GET(self):
        try:
            self._read()
        except (OSError, ConnectionError):
            self.close_connection = True

    def do_HEAD(self):
        try:
            self._read(head=True)
        except (OSError, ConnectionError):
            self.close_connection = True

    def do_POST(self):
        self.close_connection = True
        self._send(405, b"Read-only overlay.")


class SubtitleOverlay:
    def __init__(self, caption_path, *, port=8765, style=None, assets_dir=None):
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("SUBTITLE_OVERLAY_PORT must be an integer from 0 to 65535.")
        self.caption_path, self.port = Path(caption_path), port
        self.style = style or OverlayStyle()
        self.assets_dir = Path(assets_dir) if assets_dir is not None else Path(__file__).parent / "overlay"
        self.assets = {}
        self.server = self.worker = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/overlay"

    def caption(self):
        # Reading on demand makes subtitle_en.txt the source of truth; status and
        # source-page writes elsewhere never change overlay visibility.
        return {"text": read_subtitle(self.caption_path), "style": asdict(self.style)}

    def start(self):
        if self.worker is not None and self.worker.is_alive():
            return
        routes = (("index.html", "text/html; charset=utf-8", ("/", "/overlay", "/overlay/")),
                  ("overlay.css", "text/css; charset=utf-8", ("/overlay.css",)),
                  ("overlay.js", "text/javascript; charset=utf-8", ("/overlay.js",)))
        for filename, content_type, paths in routes:
            data = (self.assets_dir / filename).read_bytes()
            for path in paths:
                self.assets[path] = (data, content_type)
        server = _HTTPServer(self, self.port)
        self.port = server.server_port  # Port zero is useful for isolated tests.
        self.server = server
        self.worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.1),
                                       name="Akane subtitle overlay", daemon=False)
        try:
            self.worker.start()
        except BaseException:
            server.server_close()
            self.server = self.worker = None
            raise

    def close(self):
        if self.server is None:
            return
        self.server.stopping.set()
        if self.worker is not None and self.worker.is_alive():
            self.server.shutdown()
        self.server.server_close()
        if self.worker is not None:
            self.worker.join(1)
        self.server = self.worker = None
