"""Own ephemeral loopback servers only: no OBS, devices, services or API calls."""
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
import http.client
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import config
import control_window
import main as app
from subtitle_overlay import OverlayStyle, SubtitleOverlay
from subtitles import write_subtitle
from test_fake_backend import get_llm_reply as fake_reply
from text_to_speech import SpeechSynthesisError


class OverlayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.caption = self.root / "runtime" / "subtitle_en.txt"
        self.overlay = SubtitleOverlay(self.caption, port=0)
        self.addCleanup(self.overlay.close)

    def request(self, path="/api/caption", method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.overlay.port, timeout=2)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_caption_tracks_atomic_file_replacement_and_clearing(self):
        self.overlay.start()
        for text in ("Hello 日本語", 'Literal <img src=x onerror="alert(1)">', "", "Next reply"):
            write_subtitle(self.caption, text)
            status, headers, body = self.request()
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["text"], text)
            self.assertEqual(json.loads(body)["style"]["font_size"], 38)
            self.assertIn("no-store", headers["Cache-Control"])
            self.assertIn("application/json", headers["Content-Type"])
            self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_missing_corrupt_and_oversized_caption_are_blank(self):
        self.assertEqual(self.overlay.caption()["text"], "")
        self.caption.parent.mkdir()
        for data in (b"\xff\xfe", b"x" * 65537):
            self.caption.write_bytes(data)
            self.assertEqual(self.overlay.caption()["text"], "")
        self.caption.write_bytes(b"x" * 65536)
        self.assertEqual(len(self.overlay.caption()["text"]), 65536)
        self.caption.unlink()
        self.caption.mkdir()
        self.assertEqual(self.overlay.caption()["text"], "")

    def test_concurrent_polling_and_caption_replacement_do_not_conflict(self):
        self.overlay.start()
        start = threading.Barrier(2)
        captions = {"", "Hello 日本語" * 100, "Second reply" * 100}
        def write():
            start.wait(2)
            for index in range(100):
                write_subtitle(self.caption, "Hello 日本語" * 100 if index % 2 else "Second reply" * 100)
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(write)
            start.wait(2)
            for _ in range(30):
                status, _, data = self.request()
                self.assertEqual(status, 200)
                self.assertIn(json.loads(data)["text"], captions)
            task.result(timeout=3)

    def test_only_named_assets_are_served_and_head_has_no_body(self):
        (self.root / ".env").write_text("do not serve this", encoding="utf-8")
        self.overlay.start()
        for path, mime in (("/", "text/html"), ("/overlay", "text/html"),
                           ("/overlay/", "text/html"), ("/overlay.css", "text/css"),
                           ("/overlay.js", "text/javascript")):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertIn(mime, headers["Content-Type"])
                self.assertTrue(body)
                self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
                head_status, head_headers, head_body = self.request(path, method="HEAD")
                self.assertEqual(head_status, 200)
                self.assertEqual(head_headers["Content-Length"], str(len(body)))
                self.assertEqual(head_body, b"")
        for path in ("/.env", "/main.py", "/../.env", "/%2e%2e/.env", "/runtime/subtitle_en.txt"):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request("/api/caption?fresh=1")[0], 200)

    def test_foreign_hosts_origins_and_writes_are_rejected(self):
        self.overlay.start()
        self.assertEqual(self.request(headers={"Host": "example.invalid"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "https://example.invalid"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": f"http://127.0.0.1:{self.overlay.port}"})[0], 200)
        self.assertEqual(self.request(headers={"Host": f"localhost:{self.overlay.port}"})[0], 200)
        self.assertEqual(self.request(method="POST")[0], 405)
        self.assertEqual(self.overlay.caption()["text"], "")

    def test_close_is_idempotent_stops_worker_and_releases_port(self):
        self.overlay.start()
        worker, port = self.overlay.worker, self.overlay.port
        self.overlay.start()
        self.assertIs(self.overlay.worker, worker)
        self.assertEqual(self.overlay.server.server_address[0], "127.0.0.1")
        self.overlay.close()
        self.overlay.close()
        self.assertFalse(worker.is_alive())
        self.assertIsNone(self.overlay.server)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.2)
        try:
            with self.assertRaises(OSError):
                connection.connect()
        finally:
            connection.close()

    def test_occupied_port_and_missing_assets_fail_without_leaking_worker(self):
        self.overlay.start()
        other = SubtitleOverlay(self.caption, port=self.overlay.port)
        self.addCleanup(other.close)
        with self.assertRaises(OSError):
            other.start()
        self.assertIsNone(other.worker)
        missing = SubtitleOverlay(self.caption, port=0, assets_dir=self.root / "missing")
        self.addCleanup(missing.close)
        with self.assertRaises(OSError):
            missing.start()
        self.assertIsNone(missing.worker)

    def test_style_and_port_validation_reject_invalid_settings(self):
        for settings in ({"max_width": True}, {"max_width": 239}, {"max_width": 3841},
                         {"font_size": 12}, {"font_size": 38.5}, {"background_opacity": -0.1},
                         {"background_opacity": float("nan")}, {"background_opacity": True}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                OverlayStyle(**settings)
        for port in (True, "8765", -1, 65536):
            with self.subTest(port=port), self.assertRaises(ValueError):
                SubtitleOverlay(self.caption, port=port)


class OverlayLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "personality.txt").write_text("test personality", encoding="utf-8")
        self.output = io.StringIO()
        capture = redirect_stdout(self.output)
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)
        self.created = []

    def make_overlay(self, *args, **kwargs):
        overlay = SubtitleOverlay(*args, **{**kwargs, "port": 0})
        self.created.append(overlay)
        self.addCleanup(overlay.close)
        return overlay

    def test_default_does_not_start_server(self):
        with patch.object(config, "SUBTITLE_OVERLAY_ENABLED", False), \
             patch("subtitle_overlay.SubtitleOverlay") as server, \
             patch("builtins.input", return_value="exit"):
            app.main(fake_reply, stt_enabled=False, tts_enabled=False, project_dir=self.root)
        server.assert_not_called()

    def test_file_caption_clears_on_stop_pause_reset_and_exit(self):
        commands = iter(("first", "stop", "second", "pause", "pause", "third", "reset", "last", "exit"))
        expected = iter((False, True, False, True, False, False, True, False, True))
        def user_input(*args):
            self.assertEqual(bool(self.created[0].caption()["text"]), next(expected))
            return next(commands)
        with patch("subtitle_overlay.SubtitleOverlay", side_effect=self.make_overlay), \
             patch("builtins.input", side_effect=user_input):
            self.assertEqual(app.main(fake_reply, stt_enabled=False, tts_enabled=False,
                                      project_dir=self.root, overlay_enabled=True), 0)
        self.assertIn("OBS subtitle overlay: http://127.0.0.1:", self.output.getvalue())
        self.assertEqual(self.created[0].caption()["text"], "")
        self.assertIsNone(self.created[0].worker)

    def test_speech_captions_visible_during_playback_then_clear(self):
        def play(*args):
            self.assertTrue(self.created[0].caption()["text"])
        with patch("subtitle_overlay.SubtitleOverlay", side_effect=self.make_overlay), \
             patch("builtins.input", side_effect=["Hello", "exit"]), \
             patch.object(config, "SUBTITLE_MIN_SECONDS", 0), \
             patch.object(app, "synthesize_speech", return_value=b"mock WAV"), \
             patch.object(app.winsound, "PlaySound", side_effect=play) as playback:
            app.main(fake_reply, stt_enabled=False, tts_enabled=True,
                     project_dir=self.root, overlay_enabled=True)
        playback.assert_called_once()
        self.assertEqual(self.created[0].caption()["text"], "")

    def test_speech_failure_keeps_caption_and_eof_cleans_up(self):
        inputs = 0
        def user_input(*args):
            nonlocal inputs
            inputs += 1
            if inputs == 1:
                return "Hello"
            self.assertTrue(self.created[0].caption()["text"])
            raise EOFError()
        with patch("subtitle_overlay.SubtitleOverlay", side_effect=self.make_overlay), \
             patch("builtins.input", side_effect=user_input), \
             patch.object(app, "synthesize_speech", side_effect=SpeechSynthesisError("offline")):
            app.main(fake_reply, stt_enabled=False, tts_enabled=True,
                     project_dir=self.root, overlay_enabled=True)
        self.assertEqual(self.created[0].caption()["text"], "")
        self.assertIsNone(self.created[0].worker)

    def test_overlay_failure_allows_gui_with_warning_and_file_fallback(self):
        server = Mock()
        server.start.side_effect = OSError("port is occupied")
        def run(processor, *args, **kwargs):
            self.assertEqual(processor.overlay_status, "Unavailable")
            self.assertIsNone(processor.overlay_url)
            self.assertIn("Text-file captions still work", processor.overlay_warning)
            processor.respond("Still works")
            self.assertTrue((self.root / "runtime" / "subtitle_en.txt").read_text(encoding="utf-8"))
            return 0
        with patch("subtitle_overlay.SubtitleOverlay", return_value=server), \
             patch.object(control_window, "run_control_window", side_effect=run):
            self.assertEqual(app.main(fake_reply, stt_enabled=False, tts_enabled=False,
                                      gui_mode=True, project_dir=self.root, overlay_enabled=True), 0)
        self.assertGreaterEqual(server.close.call_count, 1)

    def test_cli_flag_passes_through(self):
        with patch("sys.argv", ["main.py", "--overlay", "--fake-backend", "--no-stt", "--no-tts"]), \
             patch.object(app, "main", return_value=0) as run:
            self.assertEqual(app.cli(), 0)
        self.assertTrue(run.call_args.kwargs["overlay_enabled"])


class BrowserClientTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is optional; needed only for JavaScript tests")
    def test_browser_state_machine(self):
        test_file = Path(__file__).with_name("overlay_client.test.cjs")
        result = subprocess.run([shutil.which("node"), "--test", "--test-reporter=tap", str(test_file)],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
