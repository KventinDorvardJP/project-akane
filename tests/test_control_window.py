"""Headless control-window tests: no real Tk root, hotkeys, browser, audio or API."""
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch

import config
import control_window
import main as app
from backend_errors import BackendError
from controls import TurnCancelled
from subtitles import write_subtitle
from test_fake_backend import get_llm_reply as fake_reply
from turns import TurnProcessor
from window_model import WindowModel
from window_session import WindowSession
from windows_hotkeys import HotkeyError


class FakeView:
    def __init__(self):
        self.destroyed = False
        self.jobs, self.render_threads = [], []
    def render(self, model):
        self.render_threads.append(threading.get_ident())
    def after(self, delay, callback):
        self.jobs.append(callback)
    def destroy(self):
        self.destroyed = True


class ModelTests(unittest.TestCase):
    def model(self):
        return WindowModel("Test", tts_enabled=True, budget=0.5)

    def test_replies_survive_source_and_subtitle_clearing(self):
        model = self.model()
        model.apply("reply", {"ja": "こんにちは", "en": "Hello", "sources": []})
        model.apply("sources", {"sources": []})
        model.apply("status", {"state": "idle", "phase": "idle", "paused": False, "busy": False})
        self.assertEqual(model.messages[-1]["en"], "Hello")

    def test_controls_reflect_recording_busy_pause_and_closing(self):
        model = self.model()
        self.assertFalse(model.buttons()["record"])
        model.stt_ready = True
        self.assertTrue(model.buttons()["record"])
        model.status.update(state="recording", phase="recording", busy=True)
        self.assertEqual(model.buttons()["record_text"], "Finish recording")
        self.assertFalse(model.buttons()["send"])
        model.status.update(state="thinking", phase="thinking")
        self.assertFalse(model.buttons()["record"])
        self.assertTrue(model.buttons()["stop"])
        model.status.update(state="paused", paused=True)
        self.assertEqual(model.buttons()["pause_text"], "Resume")
        model.closing = True
        self.assertFalse(model.buttons()["pause"])
        self.assertIn("waiting", model.status_text)

    def test_reset_clears_visible_chat_but_preserves_session_spend(self):
        model = self.model()
        model.apply("user", {"text": "Hello", "mode": "typed"})
        model.apply("metrics", {"row": {}, "session_cost": 0.02, "cost_unknown": True})
        model.apply("reset", {})
        self.assertEqual(len(model.messages), 0)
        self.assertEqual(model.session_cost, 0.02)
        self.assertIn("At least", model.cost_text)

    def test_visible_history_is_bounded(self):
        model = self.model()
        for index in range(105):
            model.apply("user", {"text": str(index), "mode": "typed"})
        self.assertEqual(len(model.messages), 100)
        self.assertEqual(model.messages[0]["text"], "5")


class SessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        capture = redirect_stdout(io.StringIO())
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)

    def session(self, reply=fake_reply, *, initialize=None, record=None, **options):
        processor = TurnProcessor(self.root, "personality", reply, tts_enabled=False,
                                  synthesize=Mock(), play=Mock(), write_caption=write_subtitle)
        view = FakeView()
        session = WindowSession(processor, record or Mock(), view, initialize_stt=initialize, **options)
        self.addCleanup(session.cleanup)
        return session, view

    def pump(self, session, predicate, timeout=3):
        deadline = monotonic() + timeout
        while not predicate() and monotonic() < deadline:
            session.tick()
            sleep(0.002)
        self.assertTrue(predicate(), "Window session failed to reach the expected state")

    def test_typed_turn_shows_both_languages_without_stt_or_playback(self):
        session, view = self.session()
        session.start()
        self.assertFalse(session.request("text", "  "))
        self.assertTrue(session.request("text", "Hello Akane"))
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        self.assertEqual([message["role"] for message in session.model.messages], ["user", "assistant"])
        self.assertIn("Hello Akane", session.model.messages[-1]["ja"])
        self.assertIn("Hello Akane", session.model.messages[-1]["en"])
        session.processor.synthesize.assert_not_called()
        self.assertEqual(set(view.render_threads), {threading.get_ident()})

    def test_overlay_status_and_startup_warning_survive_no_stt_start(self):
        session, view = self.session()
        session.cleanup()
        processor = session.processor
        processor.overlay_url = None
        processor.overlay_status = "Unavailable"
        processor.overlay_warning = "Overlay port is occupied. Text-file captions still work."
        replacement = WindowSession(processor, Mock(), FakeView())
        self.addCleanup(replacement.cleanup)
        replacement.start()
        self.assertIsNone(replacement.model.overlay_url)
        self.assertEqual(replacement.model.overlay_status, "Unavailable")
        self.assertIn("port is occupied", replacement.model.notice)

    def test_provider_failure_is_visible_and_another_turn_can_run(self):
        count = []
        def reply(messages):
            count.append(1)
            if len(count) == 1:
                raise BackendError("Simulated failure")
            return fake_reply(messages)
        session, view = self.session(reply)
        session.start()
        session.request("text", "first")
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        self.assertIn("Simulated failure", session.model.notice)
        session.request("text", "second")
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        self.assertEqual(session.model.messages[-1]["role"], "assistant")

    def test_stt_initialization_is_async_and_typed_chat_remains_available(self):
        entered, release = threading.Event(), threading.Event()
        client = Mock()
        def initialize():
            entered.set()
            release.wait(2)
            return Mock(), client
        session, view = self.session(initialize=initialize)
        self.addCleanup(release.set)
        started = monotonic()
        session.start()
        self.assertLess(monotonic() - started, 1)
        self.assertTrue(entered.wait(1))
        self.assertFalse(session.model.stt_ready)
        session.request("text", "Hello while loading")
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        release.set()
        self.pump(session, lambda: session.model.stt_ready)
        session.request("exit")
        self.pump(session, lambda: session.finished)
        client.close.assert_called_once()

    def test_stt_failure_is_visible_with_typed_input_still_enabled(self):
        initialize = Mock(side_effect=RuntimeError("missing model"))
        session, view = self.session(initialize=initialize)
        session.start()
        self.pump(session, lambda: session.init_finished)
        self.assertFalse(session.model.stt_ready)
        self.assertIn("missing model", session.model.notice)
        self.assertTrue(session.model.buttons()["send"])

    def test_gui_without_hotkey_flag_never_constructs_listener(self):
        factory = Mock(side_effect=AssertionError("Must not register keys"))
        session, view = self.session(hotkeys_factory=factory)
        session.start()
        factory.assert_not_called()

    def test_hotkey_conflict_leaves_buttons_and_chat_working(self):
        listener = Mock()
        listener.start.side_effect = HotkeyError("occupied")
        session, view = self.session(hotkeys_enabled=True, hotkeys_factory=Mock(return_value=listener))
        session.start()
        self.assertIn("Unavailable", session.model.hotkeys_status)
        self.assertIn("occupied", session.model.notice)
        self.assertTrue(session.request("text", "Buttons still work"))
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        self.assertEqual(session.model.messages[-1]["role"], "assistant")

    def test_pause_discards_late_reply_and_retains_client_until_exit(self):
        entered, release = threading.Event(), threading.Event()
        def reply(messages):
            entered.set()
            release.wait(2)
            return fake_reply(messages)
        client = Mock()
        session, view = self.session(reply, initialize=lambda: (Mock(), client))
        self.addCleanup(release.set)
        session.start()
        self.pump(session, lambda: session.init_finished)
        session.request("text", "Will be cancelled")
        self.assertTrue(entered.wait(1))
        session.request("pause")
        self.assertFalse(session.request("text", "Should not queue"))
        self.assertFalse(any(message["role"] == "assistant" for message in session.model.messages))
        client.close.assert_not_called()
        release.set()
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        self.assertFalse(any(message["role"] == "assistant" for message in session.model.messages))
        session.request("exit")
        self.pump(session, lambda: session.finished)
        client.close.assert_called_once()

    def test_close_waits_for_request_and_keeps_window_alive(self):
        entered, release = threading.Event(), threading.Event()
        def reply(messages):
            entered.set()
            release.wait(2)
            return fake_reply(messages)
        session, view = self.session(reply)
        self.addCleanup(release.set)
        session.start()
        session.request("text", "Hello")
        self.assertTrue(entered.wait(1))
        session.request("exit")
        session.tick()
        self.assertTrue(session.model.closing)
        self.assertFalse(view.destroyed)
        self.assertFalse(session.request("text", "Late input"))
        release.set()
        self.pump(session, lambda: session.finished)
        self.assertTrue(view.destroyed)
        self.assertFalse(session.controller.worker.is_alive())

    def test_close_during_initialization_cleans_up_returned_client(self):
        release, client = threading.Event(), Mock()
        def initialize():
            release.wait(2)
            return Mock(), client
        session, view = self.session(initialize=initialize)
        self.addCleanup(release.set)
        session.start()
        session.request("exit")
        session.tick()
        self.assertFalse(view.destroyed)
        release.set()
        self.pump(session, lambda: session.finished)
        client.close.assert_called_once()

    def test_window_record_button_uses_existing_microphone_toggle(self):
        entered = threading.Event()
        def record(path, **options):
            options["on_started"]()
            entered.set()
            if not options["stop_event"].wait(2):
                raise RuntimeError("Recording was not stopped")
            path.write_bytes(b"fake audio")
            return path
        recognizer = Mock(return_value={"text": "Recognized input", "languages": ["en"], "elapsed_seconds": 0.1})
        session, view = self.session(initialize=lambda: (recognizer, Mock()), record=record)
        session.start()
        self.pump(session, lambda: session.model.stt_ready)
        session.request("record")
        self.assertTrue(entered.wait(1))
        session.tick()
        self.assertEqual(session.model.buttons()["record_text"], "Finish recording")
        session.request("record")
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        self.assertEqual(session.model.messages[0]["text"], "Recognized input")
        self.assertEqual(session.model.transcription["languages"], ["en"])
        self.assertFalse(Path(recognizer.call_args.args[0]).exists())

    def test_stop_during_capture_skips_stt_and_preserves_chat(self):
        entered = threading.Event()
        def record(path, **options):
            options["on_started"]()
            entered.set()
            if options["cancel_event"].wait(2):
                raise TurnCancelled()
            raise AssertionError("Capture was not cancelled")
        recognizer = Mock()
        session, view = self.session(initialize=lambda: (recognizer, Mock()), record=record)
        session.start()
        self.pump(session, lambda: session.model.stt_ready)
        session.request("record")
        self.assertTrue(entered.wait(1))
        session.request("stop")
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        recognizer.assert_not_called()
        self.assertEqual(len(session.model.messages), 0)

    def test_reset_during_turn_clears_display_after_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        def reply(messages):
            entered.set()
            release.wait(2)
            return fake_reply(messages)
        session, view = self.session(reply)
        self.addCleanup(release.set)
        session.start()
        session.request("text", "Reset this")
        self.assertTrue(entered.wait(1))
        session.tick()
        self.assertEqual(len(session.model.messages), 1)
        session.request("reset")
        self.assertTrue(session.controller.pending_reset)
        release.set()
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        self.assertEqual(len(session.model.messages), 0)
        self.assertEqual(len(session.processor.messages), 1)


class WindowInteractionTests(unittest.TestCase):
    def make_view(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name in ("Frame", "Label", "Style", "Panedwindow", "Button", "Entry", "Checkbutton"):
            stack.enter_context(patch.object(control_window.ttk, name))
        stack.enter_context(patch.object(control_window.tk, "StringVar", side_effect=lambda *args, **kwargs: Mock()))
        stack.enter_context(patch.object(control_window.tk, "BooleanVar", side_effect=lambda *args, **kwargs: Mock()))
        def text(*args, **kwargs):
            widget = Mock()
            widget.yview.return_value = (0.0, 1.0)
            widget.index.return_value = "1.0"
            widget.tag_names.return_value = ()
            return widget
        stack.enter_context(patch.object(control_window, "ScrolledText", side_effect=text))
        self.root = Mock()
        return control_window.ControlWindow(self.root)

    def test_render_shows_ja_en_and_citations_without_opening_browser(self):
        view = self.make_view()
        model = WindowModel("Test", tts_enabled=False, budget=0.5)
        sources = [{"url": "https://example.invalid/source", "title": "Reference", "kind": "cited"}]
        model.apply("reply", {"ja": "こんにちは", "en": "Hello", "sources": sources})
        model.apply("sources", {"sources": sources})
        with patch.object(control_window.webbrowser, "open") as browser:
            view.render(model)
            browser.assert_not_called()
        inserted = "".join(call.args[1] for call in view.conversation.insert.call_args_list)
        self.assertIn("こんにちは", inserted)
        self.assertIn("Hello", inserted)
        self.assertIn("[1]", inserted)

    def test_overlay_url_copies_only_after_explicit_action(self):
        view = self.make_view()
        view.overlay_copy = Mock()  # Other mocked ttk buttons share a factory return value.
        model = WindowModel("Test", tts_enabled=False, budget=0.5)
        view.render(model)
        view.copy_overlay_url()
        self.root.clipboard_append.assert_not_called()
        model.overlay_url, model.overlay_status = "http://127.0.0.1:8765/overlay", "On"
        view.render(model)
        self.root.clipboard_append.assert_not_called()
        view.overlay_copy.configure.assert_called_with(state="normal")
        view.copy_overlay_url()
        self.root.clipboard_clear.assert_called_once()
        self.root.clipboard_append.assert_called_once_with(model.overlay_url)
        model.closing = True
        view.render(model)
        view.overlay_copy.configure.assert_called_with(state="disabled")

    def test_source_click_accepts_only_http_urls(self):
        view = self.make_view()
        with patch.object(control_window.webbrowser, "open") as browser:
            view.open_link("javascript:alert(1)")
            browser.assert_not_called()
            view.open_link("https://example.invalid/source")
            browser.assert_called_once_with("https://example.invalid/source", new=2)

    def test_send_clears_input_only_when_accepted(self):
        view = self.make_view()
        view.input_var.get.return_value = "Hello"
        view.request = Mock(return_value=False)
        view.input_var.set.reset_mock()
        view.send()
        view.input_var.set.assert_not_called()
        view.request.return_value = True
        view.send()
        view.input_var.set.assert_called_once_with("")

    def test_audio_picker_runs_only_after_explicit_button_action(self):
        view = self.make_view()
        view.request = Mock()
        with patch.object(control_window.filedialog, "askopenfilename", return_value="C:/sample.wav") as picker:
            view.render(WindowModel("Test", tts_enabled=False, budget=0.5))
            picker.assert_not_called()
            view.choose_audio()
        view.request.assert_called_once_with("audio", "C:/sample.wav")

    def test_unknown_search_count_is_not_displayed_as_zero(self):
        view = self.make_view()
        model = WindowModel("Test", tts_enabled=False, budget=0.5)
        model.apply("metrics", {"row": {"status": "cancelled", "web_search_calls": None},
                                "session_cost": 0.01, "cost_unknown": True})
        view.render(model)
        self.assertIn("Search requests   —", view.metrics_var.set.call_args.args[0])


class EntryPointTests(unittest.TestCase):
    def test_window_entry_runs_session_and_closes_without_real_tk(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            processor = TurnProcessor(Path(directory), "personality", fake_reply, tts_enabled=False,
                                      synthesize=Mock(), play=Mock(), write_caption=write_subtitle)
            class Root:
                def __init__(self):
                    self.jobs, self.destroyed = [], False
                def destroy(self):
                    self.destroyed = True
                def mainloop(self):
                    self.view.request("text", "Hello from a fake window")
                    deadline, closing = monotonic() + 3, False
                    while not self.destroyed and monotonic() < deadline:
                        if self.jobs:
                            self.jobs.pop(0)()
                        if any(m["role"] == "assistant" for m in self.view.model.messages) and not closing:
                            closing = True
                            self.view.request("exit")
                        sleep(0.002)
                    if not self.destroyed:
                        raise AssertionError("Window session did not shut down")
            class View(FakeView):
                def __init__(self, root):
                    super().__init__()
                    self.root = root
                    root.view = self
                def after(self, delay, callback):
                    self.root.jobs.append(callback)
                def render(self, model):
                    super().render(model)
                    self.model = model
                def destroy(self):
                    super().destroy()
                    self.root.destroy()
            root = Root()
            with patch.object(control_window.tk, "Tk", return_value=root), \
                 patch.object(control_window, "ControlWindow", View):
                self.assertEqual(control_window.run_control_window(processor, Mock()), 0)
            self.assertTrue(root.destroyed)
            self.assertTrue(any(m["role"] == "assistant" for m in root.view.model.messages))

    def test_gui_branch_defers_stt_initialization_to_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "personality.txt").write_text("personality", encoding="utf-8")
            with patch.object(app, "initialize_stt") as initialize, \
                 patch.object(control_window, "run_control_window", return_value=0) as run, \
                 patch.object(control_window.tk, "Tk", side_effect=AssertionError("No real window")):
                self.assertEqual(app.main(fake_reply, gui_mode=True, project_dir=root, tts_enabled=False), 0)
                initialize.assert_not_called()
                self.assertTrue(callable(run.call_args.kwargs["initialize_stt"]))

    def test_cli_gui_and_fake_flags_pass_through_without_services(self):
        with patch("sys.argv", ["main.py", "--gui", "--fake-backend", "--no-stt", "--no-tts"]), \
             patch.object(app, "main", return_value=0) as run:
            self.assertEqual(app.cli(), 0)
        self.assertTrue(run.call_args.kwargs["gui_mode"])
        self.assertFalse(run.call_args.kwargs["stt_enabled"])
        self.assertFalse(run.call_args.kwargs["tts_enabled"])


if __name__ == "__main__":
    unittest.main()
