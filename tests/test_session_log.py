"""Transcript tests use temporary files, fake providers/devices and mocked widgets."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import config
import main as app
from controls import TurnControl
from session_log import SessionLog
from speech_to_text import TranscriptionError
from stream_controls import StreamController
from subtitles import write_subtitle
from turns import TurnProcessor
from window_model import WindowModel
from window_session import WindowSession


PARTS = [{"ja": "やあ、朱音だよ。", "en": "Hi, I'm Akane."},
         {"ja": "次の話だよ。", "en": "Here is another thought."}]


def read_events(log):
    return [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        setting = patch.object(config, "SUBTITLE_MIN_SECONDS", 0)
        setting.start()
        self.addCleanup(setting.stop)

    def logger(self, **options):
        log = SessionLog(self.root, **options)
        self.addCleanup(log.close)
        return log

    def processor(self, log=None, *, reply=None, play=None, synthesize=None, tts=True):
        if reply is None:
            def reply(messages):
                return json.dumps({"segments": PARTS}, ensure_ascii=False)
        return TurnProcessor(self.root, "PRIVATE SYSTEM PROMPT", reply, tts_enabled=tts,
                             synthesize=synthesize or Mock(return_value=b"PRIVATE AUDIO DATA"),
                             play=play or Mock(return_value=True), write_caption=write_subtitle,
                             sleep_function=Mock(), session_log=log)

    def test_unicode_clocks_and_flushed_readable_companion(self):
        ticks = iter([100.0, 100.0, 101.25, 101.3])
        log = self.logger(clock=lambda: next(ticks), utc_now=lambda: datetime(2026, 11, 10, tzinfo=timezone.utc))
        turn_id = log.begin_turn("mic")
        log.record("user", turn_id=turn_id, text="Акане、日本語", mode="mic")
        events = read_events(log)  # Read before close: per-event flush is observable.
        self.assertEqual(events[-1]["elapsed_seconds"], 1.3)
        self.assertEqual(events[-1]["timestamp_utc"], "2026-11-10T00:00:00.000+00:00")
        self.assertEqual(events[-1]["turn_id"], "turn_0001")
        self.assertIn("Акане、日本語", log.text_path.read_text(encoding="utf-8"))
        # Supply a non-exhausted clock for cleanup.
        log.clock = lambda: 102.0

    def test_unique_sessions_turns_markers_and_idempotent_close(self):
        first, second = self.logger(), self.logger()
        self.assertNotEqual(first.path, second.path)
        self.assertNotEqual(first.begin_turn("typed"), first.begin_turn("typed"))
        self.assertEqual(first.mark()["data"]["number"], 1)
        self.assertEqual(first.mark()["data"]["number"], 2)
        first.close()
        before = first.path.read_bytes()
        first.close()
        self.assertIsNone(first.record("late"))
        self.assertEqual(first.path.read_bytes(), before)
        self.assertEqual(read_events(first)[-1]["event"], "session_end")

    def test_threads_keep_complete_ordered_json_lines(self):
        log = self.logger()
        workers = [threading.Thread(target=lambda: [log.record("test", value="日本語") for _ in range(15)])
                   for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        events = read_events(log)
        self.assertEqual(len(events), 61)
        self.assertEqual([event["sequence"] for event in events], list(range(1, 62)))

    def test_open_failure_disables_logger_without_raising(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError("folder unavailable")):
            log = self.logger()
        self.assertFalse(log.active)
        self.assertIn("PermissionError", log.error)
        self.assertIsNone(log.record("test"))

    def test_write_failure_warns_once_and_does_not_break_chat(self):
        log = self.logger()
        log.handles[0].close()
        log.handles[0] = Mock(write=Mock(side_effect=OSError("disk full")))
        processor = self.processor(log, tts=False)
        notices = []
        processor.event_callback = lambda kind, **data: notices.append(data) if kind == "notice" else None
        self.assertTrue(processor.respond("First"))
        self.assertTrue(processor.respond("Second"))
        self.assertFalse(log.active)
        self.assertEqual(sum("transcript unavailable" in item["message"] for item in notices), 1)

    def test_serialization_failure_is_contained(self):
        log = self.logger()
        self.assertIsNone(log.record("invalid", value=object()))
        self.assertFalse(log.active)
        self.assertIn("TypeError", log.error)

    def test_full_response_parts_playback_sources_usage_and_input_are_linked(self):
        log = self.logger()
        def reply(messages):
            return json.dumps({"segments": PARTS}, ensure_ascii=False)
        reply.last_sources = [{"kind": "cited", "title": "Test reference", "url": "https://example.invalid"}]
        reply.last_usage = {"provider": "openai", "model": "test", "web_search_calls": 1, "input_tokens": 10}
        processor = self.processor(log, reply=reply)
        self.assertTrue(processor.respond("What does it mean?", input_mode="mic", stt_seconds=0.5,
                                          japanese_context="引用文。"))
        events = read_events(log)
        selected = lambda kind: [event for event in events if event["event"] == kind]
        self.assertEqual(selected("reply")[0]["data"]["segments"], PARTS)
        self.assertEqual(selected("reply")[0]["data"]["sources"], reply.last_sources)
        self.assertEqual(selected("user")[0]["data"]["japanese_context"], "引用文。")
        self.assertEqual([event["data"]["part"] for event in selected("playback_start")], [1, 2])
        self.assertEqual([event["data"]["status"] for event in selected("playback_end")], ["completed", "completed"])
        self.assertEqual(selected("metrics")[0]["data"]["row"]["web_search_calls"], 1)
        self.assertEqual(selected("turn_end")[0]["data"]["status"], "ok")
        turn_events = events[1:]
        self.assertTrue(all(event["turn_id"] == "turn_0001" for event in turn_events))
        saved = log.path.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE AUDIO DATA", saved)
        self.assertNotIn("PRIVATE SYSTEM PROMPT", saved)
        self.assertIn("GENERATED (playback recorded separately)", log.text_path.read_text(encoding="utf-8"))

    def test_text_only_reply_never_claims_playback(self):
        log = self.logger()
        self.assertTrue(self.processor(log, tts=False).respond("Text only"))
        kinds = [event["event"] for event in read_events(log)]
        self.assertIn("reply", kinds)
        self.assertIn("subtitle", kinds)
        self.assertNotIn("playback_start", kinds)
        self.assertEqual(read_events(log)[-1]["data"]["status"], "tts_disabled")

    def test_stop_during_playback_does_not_claim_next_part_was_spoken(self):
        log, control = self.logger(), TurnControl()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def synthesize(text):
            if text == PARTS[1]["ja"]:
                entered.set()
                self.assertTrue(release.wait(2))
            return b"audio"
        def play(audio, token):
            self.assertTrue(entered.wait(2))
            token.cancel.set()
            release.set()
            return False
        with patch.object(config, "TTS_PREFETCH_NEXT", True):
            self.assertFalse(self.processor(log, play=play, synthesize=synthesize).respond("Stop", control=control))
        events = read_events(log)
        starts = [event for event in events if event["event"] == "playback_start"]
        self.assertEqual([event["data"]["part"] for event in starts], [1])
        self.assertEqual(next(event for event in events if event["event"] == "playback_end")["data"]["status"], "stopped")
        self.assertTrue(any(event["event"] == "tts_end" and event["data"]["part"] == 2
                            and event["data"]["cancelled"] for event in events))
        self.assertEqual(events[-1]["data"]["status"], "cancelled")

    def test_late_cancelled_llm_result_is_not_a_generated_visible_reply(self):
        log, control = self.logger(), TurnControl()
        def reply(messages):
            control.cancel.set()
            return json.dumps({"segments": PARTS})
        self.assertFalse(self.processor(log, reply=reply).respond("Stop", control=control))
        self.assertNotIn("reply", [event["event"] for event in read_events(log)])
        self.assertEqual(read_events(log)[-1]["data"]["status"], "cancelled")

    def test_invalid_reply_is_logged_as_error_without_playback(self):
        log = self.logger()
        self.assertFalse(self.processor(log, reply=lambda messages: "invalid JSON").respond("Hello"))
        events = read_events(log)
        self.assertIn("reply_error", [event["event"] for event in events])
        self.assertNotIn("reply", [event["event"] for event in events])
        self.assertEqual(events[-1]["data"]["status"], "reply_unavailable")

    def test_audio_failure_and_empty_transcription_end_their_turn(self):
        for result, status in ((TranscriptionError("Bad sample"), "input_error"),
                               ({"text": " ", "languages": [], "elapsed_seconds": 0}, "no_speech")):
            with self.subTest(status=status):
                log = self.logger()
                sample = self.root / "sample.wav"
                sample.write_bytes(b"not real audio")
                recognizer = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
                processor = self.processor(log)
                controller = StreamController(processor, recognizer, Mock())
                self.assertTrue(controller.start_turn("audio", str(sample)))
                controller.worker.join(2)
                self.assertFalse(controller.worker.is_alive())
                ends = [event for event in read_events(log) if event["event"] == "turn_end"]
                self.assertEqual(len(ends), 1)
                self.assertEqual(ends[0]["data"]["status"], status)

    def test_fake_mic_capture_transcription_and_response_share_one_turn(self):
        log = self.logger()
        def record(path, **options):
            options["on_started"]()
            path.write_bytes(b"NOT REAL AUDIO")
            return path
        recognizer = Mock(return_value={"text": "Hello", "languages": ["en"], "elapsed_seconds": 0.1})
        controller = StreamController(self.processor(log, tts=False), recognizer, record)
        controller.start_turn("mic")
        controller.worker.join(2)
        self.assertFalse(controller.worker.is_alive())
        events = read_events(log)
        for kind in ("recording_start", "recording_end", "transcription", "user", "reply", "turn_end"):
            selected = [event for event in events if event["event"] == kind]
            self.assertEqual(len(selected), 1, kind)
            self.assertEqual(selected[0]["turn_id"], "turn_0001")
        self.assertFalse(Path(recognizer.call_args.args[0]).exists())

    def test_gui_status_and_explicit_recording_marker(self):
        processor = self.processor()
        session = WindowSession(processor, Mock(), Mock())  # No start(), Tk or devices.
        self.assertEqual(session.model.session_log_status, "Off")
        self.assertFalse(session.request("mark"))
        processor.session_log = self.logger()
        session.refresh_session_log()
        self.assertEqual(session.model.session_log_status, "On")
        self.assertEqual(session.model.session_log_path, str(processor.session_log.text_path))
        self.assertTrue(session.request("mark"))
        self.assertTrue(any(event["event"] == "recording_marker" for event in read_events(processor.session_log)))

    def test_terminal_off_and_enabled_eof_and_restart_create_new_sessions(self):
        (self.root / "personality.txt").write_text("PRIVATE SYSTEM PROMPT", encoding="utf-8")
        with patch.object(config, "SESSION_LOG_ENABLED", False), patch("builtins.input", side_effect=["exit"]):
            app.main(lambda messages: "", stt_enabled=False, tts_enabled=False, project_dir=self.root, overlay_enabled=False)
        self.assertFalse((self.root / "runtime" / "session_logs").exists())
        with patch.object(config, "SESSION_LOG_ENABLED", True):
            with patch("builtins.input", side_effect=EOFError):
                self.assertEqual(app.main(lambda messages: "", stt_enabled=False, tts_enabled=False,
                                          project_dir=self.root, overlay_enabled=False), 0)
            def fake_gui(processor, *args, **kwargs):
                self.assertTrue(processor.session_log.active)
                return 75
            with patch("control_window.run_control_window", side_effect=fake_gui):
                self.assertEqual(app.main(lambda messages: "", stt_enabled=False, tts_enabled=False,
                                          project_dir=self.root, overlay_enabled=False, gui_mode=True), 75)
        paths = list((self.root / "runtime" / "session_logs").glob("*.jsonl"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events[-1]["event"], "session_end")
            self.assertNotIn("PRIVATE SYSTEM PROMPT", path.read_text(encoding="utf-8"))

    def test_terminal_audio_uses_one_turn_and_closes_interrupted_input(self):
        (self.root / "personality.txt").write_text("persona", encoding="utf-8")
        result = {"text": "Hello", "languages": ["en"], "elapsed_seconds": 0.1, "language_probability": None}
        with patch.object(config, "SESSION_LOG_ENABLED", True), \
                patch.object(app, "initialize_stt", return_value=(Mock(), None)), \
                patch.object(app, "recognize_command", return_value=(result, 0)), \
                patch("builtins.input", side_effect=["audio", "exit"]):
            app.main(lambda messages: json.dumps({"segments": PARTS}), tts_enabled=False,
                     project_dir=self.root, overlay_enabled=False)
        path = next((self.root / "runtime" / "session_logs").glob("*.jsonl"))
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(sum(event["event"] == "turn_start" for event in events), 1)
        self.assertEqual(sum(event["event"] == "turn_end" for event in events), 1)
        with patch.object(config, "SESSION_LOG_ENABLED", True), \
                patch.object(app, "initialize_stt", return_value=(Mock(), None)), \
                patch("builtins.input", side_effect=["audio", EOFError]):
            app.main(Mock(), tts_enabled=False, project_dir=self.root, overlay_enabled=False)
        newest = sorted((self.root / "runtime" / "session_logs").glob("*.jsonl"))[-1]
        last_events = [json.loads(line) for line in newest.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(last_events[-2]["event"], "turn_end")
        self.assertEqual(last_events[-2]["data"]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
