"""Hold/release through the real mailbox/GUI worker, with fake capture/STT/audio."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch
import uuid

import config
from controls import TurnCancelled
from subtitles import write_subtitle
from turns import TurnProcessor
from window_session import WindowSession
from witchcraft_bridge import WitchcraftBridge


class View:
    def __init__(self): self.threads = []
    def render(self, model): self.threads.append(threading.get_ident())
    def destroy(self): self.threads.append(threading.get_ident())
    def after(self, *args): pass


class PushToTalkTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        output = redirect_stdout(io.StringIO()); output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        for name in ("SUBTITLE_MIN_SECONDS", "SUBTITLE_READING_CHARS_PER_SECOND"):
            setting = patch.object(config, name, 0); setting.start(); self.addCleanup(setting.stop)
        self.recording = threading.Event()
        self.paths = []
        self.stt = Mock(return_value={"text": "Where should I go next?", "languages": ["en"], "elapsed_seconds": 0.01})
        self.reply = Mock(return_value=json.dumps({"ja": "北へ行こう。", "en": "Go north."}))

    def fake_record(self, path, *, stop_event, cancel_event, on_started, **options):
        if cancel_event.is_set(): raise TurnCancelled()
        on_started(); self.recording.set(); self.paths.append(path)
        deadline = monotonic() + 2
        while not stop_event.wait(0.002):
            if cancel_event.is_set(): raise TurnCancelled()
            if monotonic() > deadline: raise AssertionError("Test capture did not stop")
        if cancel_event.is_set(): raise TurnCancelled()
        path.write_bytes(b"fake recorded data")
        return path

    def session(self, *, record=None, stt=True, wow_factory=None):
        processor = TurnProcessor(self.root, "personality", self.reply, tts_enabled=False,
                                  synthesize=Mock(), play=Mock(), write_caption=write_subtitle)
        self.record = Mock(side_effect=record or self.fake_record)
        session = WindowSession(processor, self.record, View(), initialize_stt=(lambda: (self.stt, Mock())) if stt else None,
                                witchcraft_enabled=True, bridge_factory=lambda root: WitchcraftBridge(root, port=0),
                                wow_factory=wow_factory or Mock())
        self.addCleanup(session.cleanup)
        session.start()
        if stt: self.pump(session, lambda: session.model.stt_ready)
        return session

    def pump(self, session, predicate):
        deadline = monotonic() + 3
        while monotonic() < deadline:
            session.tick()
            if predicate(): return
            sleep(0.002)
        self.fail("Session did not reach expected state")

    def send(self, session, action, token="abcdef12:1"):
        status, result = session.witchcraft.enqueue({"session": session.witchcraft.session, "id": uuid.uuid4().hex,
                                                   "action": action, "value": token})
        self.assertEqual(status, 202)
        session.tick()

    def finish(self, session):
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])

    def test_release_closes_capture_then_runs_normal_stt_and_keeps_shared_reply(self):
        session = self.session()
        self.send(session, "ptt_down")
        self.assertTrue(self.recording.wait(1))
        self.stt.assert_not_called()
        self.send(session, "ptt_up")
        self.finish(session)
        self.stt.assert_called_once()
        self.reply.assert_called_once()
        self.assertEqual(session.model.messages[0]["text"], "Where should I go next?")
        self.assertEqual(session.model.messages[-1]["en"], "Go north.")
        self.assertTrue(all(not path.exists() for path in self.paths))
        self.assertEqual(set(session.view.threads), {threading.get_ident()})
        session.processor.play.assert_not_called()

    def test_disabled_stt_and_release_before_press_never_start_a_mic_later(self):
        session = self.session(stt=False)
        self.send(session, "ptt_down")
        self.record.assert_not_called()
        session.controller.recognize_audio = self.stt
        self.send(session, "ptt_down")  # Same refused gesture stays closed.
        self.send(session, "ptt_up", "abcdef12:2")
        self.send(session, "ptt_down", "abcdef12:2")
        self.record.assert_not_called()
        self.assertFalse(session.model.status["busy"])

    def test_release_during_context_wait_never_starts_capture_or_stt(self):
        entered = threading.Event()
        def collect(control):
            entered.set()
            if control.cancel.wait(2): raise TurnCancelled()
            raise AssertionError("Context was not cancelled")
        session = self.session(wow_factory=Mock(return_value=Mock(collect=collect)))
        session.request("wow_include", True)
        self.send(session, "ptt_down")
        self.assertTrue(entered.wait(1))
        self.send(session, "ptt_up")
        self.finish(session)
        self.record.assert_not_called(); self.stt.assert_not_called(); self.reply.assert_not_called()

    def test_lost_release_lease_cancels_and_discards_audio_without_stt(self):
        session = self.session()
        now = [0]
        session.controller.ptt_clock = lambda: now[0]
        self.send(session, "ptt_down")
        self.assertTrue(self.recording.wait(1))
        now[0] = 9
        session.tick()
        self.finish(session)
        self.stt.assert_not_called(); self.reply.assert_not_called()
        self.assertTrue(all(not path.exists() for path in self.paths))
        self.send(session, "ptt_down")  # An old press cannot restart recording.
        self.assertEqual(self.record.call_count, 1)

    def test_only_physical_hold_messages_extend_lease_and_wrong_release_cannot_stop_capture(self):
        session = self.session()
        now = [0]
        session.controller.ptt_clock = lambda: now[0]
        self.send(session, "ptt_down")
        self.assertTrue(self.recording.wait(1))
        now[0] = 7
        self.send(session, "ptt_hold")
        now[0] = 9
        self.send(session, "ptt_up", "abcdef12:99")
        self.assertFalse(session.controller.ptt["control"].stop_recording.is_set())
        self.send(session, "ptt_up")
        self.finish(session)
        self.stt.assert_called_once()

    def test_limit_without_release_discards_instead_of_transcribing(self):
        def reached_limit(path, **options):
            options["on_started"]()
            path.write_bytes(b"bounded fake audio")
            self.paths.append(path)
            return path
        session = self.session(record=reached_limit)
        self.send(session, "ptt_down")
        self.finish(session)
        self.stt.assert_not_called(); self.reply.assert_not_called()
        self.assertTrue(all(not path.exists() for path in self.paths))

    def test_busy_press_is_not_queued_and_cancel_stop_pause_reset_exit_discard_capture(self):
        for action in ("ptt_cancel", "stop", "pause", "reset", "exit"):
            with self.subTest(action=action):
                self.recording.clear()
                session = self.session()
                self.send(session, "ptt_down")
                self.assertTrue(self.recording.wait(1))
                self.send(session, "ptt_down", "abcdef12:2")
                self.assertEqual(self.record.call_count, 1)
                if action == "ptt_cancel": self.send(session, action)
                else: session.request(action)
                self.finish(session)
                self.stt.assert_not_called(); self.reply.assert_not_called()
                session.cleanup()

    def test_capabilities_survive_event_rollover_and_bad_gestures_are_refused(self):
        session = self.session()
        for _ in range(140): session.witchcraft.publish("notice", message="test")
        result = session.witchcraft.read_events(0)
        self.assertTrue(result["capabilities"]["sttReady"])
        for value in (None, True, "abc", "../file", "abcdef12:1\n"):
            status, _ = session.witchcraft.enqueue({"session": session.witchcraft.session, "id": uuid.uuid4().hex,
                                                   "action": "ptt_down", "value": value})
            self.assertEqual(status, 400)
        self.record.assert_not_called()


if __name__ == "__main__": unittest.main()
