from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import review_session
from session_log import SessionLog


class ReviewSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.clock = 100.0

    def logger(self, day=27):
        log = SessionLog(self.root, clock=lambda: self.clock,
                         utc_now=lambda: datetime(2026, 11, day, tzinfo=timezone.utc))
        self.addCleanup(log.close)
        return log

    def test_report_uses_recognized_text_and_capture_to_playback_timing(self):
        log = self.logger()
        log.mark()
        turn_id = log.begin_turn("mic")
        self.clock = 110.0
        log.record("recording_end", turn_id=turn_id)
        log.record("transcription", turn_id=turn_id, text="Привет, Аканэ!")
        log.record("user", turn_id=turn_id, mode="mic", text="Привет, Аканэ!", wow_context={"private": "game snapshot"})
        log.record("reply", turn_id=turn_id, ja="おそいよ。", ru="Ну наконец-то.")
        self.clock = 112.25
        log.record("playback_start", turn_id=turn_id, part=1)
        log.record("playback_end", turn_id=turn_id, status="completed", part=1)
        log.record("metrics", turn_id=turn_id, row={"stt_seconds": 0.75, "llm_seconds": 1.25})
        log.record("turn_end", turn_id=turn_id, status="ok")
        log.close()
        report = review_session.review_session(log.path)
        self.assertIn("Recording marker 1", report)
        self.assertIn("Heard: Привет, Аканэ!", report)
        self.assertIn("Akane JA (generated): おそいよ。", report)
        self.assertIn("Akane RU (generated): Ну наконец-то.", report)
        self.assertIn("STT 0.75s · Model 1.25s · capture end → first playback dispatch 2.25s", report)
        self.assertIn("Playback pieces: 1 completed", report)
        self.assertNotIn("game snapshot", report)
        self.assertNotIn("No session end recorded", report)

    def test_partial_file_keeps_fallback_failure_and_unfinished_turn_visible(self):
        log = self.logger()
        fallback = log.begin_turn("typed")
        log.record("user", turn_id=fallback, mode="typed", text="Play a game?")
        log.record("reply", turn_id=fallback, ja="いいよ。", en="Sure.")
        log.record("notice", turn_id=fallback, level="warning", message="Speech engine unavailable.")
        log.record("turn_end", turn_id=fallback, status="speech_unavailable")
        unfinished = log.begin_turn("typed")
        log.record("input_accepted", turn_id=unfinished, mode="typed", text="Next question")
        # Simulate a crash while appending the next event.
        partial = self.root / "partial.jsonl"
        partial.write_bytes(log.path.read_bytes() + b'{"truncated":')
        report = review_session.review_session(partial)
        for detail in ("subtitle fallback", "Speech engine unavailable.", "no dispatch recorded",
                       "Next question", "No turn end recorded", "No session end recorded", "Skipped 1"):
            self.assertIn(detail, report)

    def test_cli_picks_latest_and_accepts_the_companion_text_path(self):
        earlier, latest = self.logger(day=26), self.logger()
        earlier.close()
        latest.close()
        with patch.object(review_session, "SESSION_FOLDER", latest.path.parent):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(review_session.main([]), 0)
            self.assertIn(latest.path.name, output.getvalue())
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(review_session.main([str(earlier.text_path)]), 0)
            self.assertIn(earlier.path.name, output.getvalue())

    def test_cli_explains_missing_logs(self):
        with patch.object(review_session, "SESSION_FOLDER", self.root), redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as error:
                review_session.main([])
        self.assertEqual(error.exception.code, 1)
        self.assertIn("No session logs found", output.getvalue())
