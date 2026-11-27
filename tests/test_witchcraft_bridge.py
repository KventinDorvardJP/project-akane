"""Temporary loopback/headless fixtures only: no game, model, microphone or sound."""
from contextlib import redirect_stdout
import io
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
from time import monotonic, sleep
import unittest
from unittest.mock import Mock, patch
import uuid
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

import config
from subtitles import write_subtitle
from test_fake_backend import get_llm_reply
from turns import TurnProcessor
from window_session import WindowSession
from witchcraft_bridge import WitchcraftBridge, MAX_TEXT, MAX_BODY
from witchcraft_terminal import TerminalClient, TerminalInput, endpoint_document, display_text


class FakeView:
    def __init__(self):
        self.threads = []
    def render(self, model):
        self.threads.append(threading.get_ident())
    def after(self, *args):
        pass
    def destroy(self):
        self.threads.append(threading.get_ident())


class BridgeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.bridge = WitchcraftBridge(self.root, port=0)
        self.bridge.start()
        self.addCleanup(self.bridge.close)
        self.opener = build_opener(ProxyHandler({}))

    def request(self, route="/events", *, document=None, token=None, headers=None):
        if route == "/events":
            route += f"?session={self.bridge.session}&after=0"
        options = {"Authorization": "Bearer " + (token or self.bridge.token), "Content-Type": "application/json"}
        options.update(headers or {})
        data = json.dumps(document).encode() if document is not None else None
        request = Request(f"http://127.0.0.1:{self.bridge.port}{route}", data=data, headers=options)
        with self.opener.open(request, timeout=2) as response:
            return json.load(response)

    def command(self, **kwargs):
        return {"session": self.bridge.session, "id": uuid.uuid4().hex,
                "action": "text", "value": "Where am I?", **kwargs}

    def test_loopback_endpoint_and_auth_host_origin_session_guards(self):
        self.assertEqual(self.bridge.server.server_address[0], "127.0.0.1")
        self.assertEqual(endpoint_document(self.bridge.path)["token"], self.bridge.token)
        for options in ({"token": "wrong"}, {"headers": {"Origin": "http://evil.example"}},
                        {"headers": {"Host": "evil.example"}}):
            with self.subTest(options=options), self.assertRaises(HTTPError) as error:
                self.request(**options)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            self.request("/command", document=self.command(session="old"))
        self.assertEqual(error.exception.code, 409)
        self.assertTrue(self.bridge.commands.empty())

    def test_commands_are_bounded_validated_deduplicated_and_only_drained_by_owner(self):
        seen = []
        command = self.command()
        self.assertTrue(self.request("/command", document=command)["queued"])
        self.assertTrue(self.request("/command", document=command)["duplicate"])
        self.assertEqual(seen, [])
        self.bridge.drain(lambda action, value: seen.append((action, value)) or True)
        self.assertEqual(seen, [("text", "Where am I?")])
        for changes in ({"action": "exit"}, {"action": "record"}, {"action": []},
                        {"value": "x" * (MAX_TEXT + 1)}, {"value": "bad\x1btext"},
                        {"action": "wow_include", "value": "yes"}):
            with self.subTest(changes=changes), self.assertRaises(HTTPError) as error:
                self.request("/command", document=self.command(**changes))
            self.assertEqual(error.exception.code, 400)
        for _ in range(8):
            self.request("/command", document=self.command())
        with self.assertRaises(HTTPError) as error:
            self.request("/command", document=self.command())
        self.assertEqual(error.exception.code, 429)

    def test_oversized_body_expired_input_and_closing_never_execute(self):
        # Oversized length must be rejected before reading the body. Sending a
        # large unread body can produce Windows TCP reset instead of an HTTP
        # response; send headers only to exercise the actual pre-read guard.
        connection = HTTPConnection("127.0.0.1", self.bridge.port, timeout=2)
        try:
            connection.putrequest("POST", "/command")
            connection.putheader("Authorization", "Bearer " + self.bridge.token)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(MAX_BODY + 1))
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 400)
        finally:
            connection.close()
        self.assertTrue(self.bridge.commands.empty())
        self.bridge.enqueue(self.command())
        self.bridge.clock = lambda: monotonic() + 10
        callback = Mock()
        self.bridge.drain(callback)
        callback.assert_not_called()
        self.assertFalse(self.bridge.read_events(0)["events"][-1]["accepted"])
        self.bridge.stop_accepting()
        self.assertEqual(self.bridge.enqueue(self.command())[0], 410)

    def test_mirror_contains_only_english_and_plain_input_not_snapshot_or_ja(self):
        self.bridge.publish("reply", en="Full English answer", ja="秘密", segments=[{"ja": "秘密"}])
        self.bridge.publish("user", text="Question", wow_context={"secret": "snapshot"}, japanese_context="秘密")
        self.bridge.publish("wow_context", snapshot={"secret": "snapshot"})
        self.bridge.publish("metrics", row={"secret": "cost"})
        result = self.request()
        self.assertEqual(len(result["events"]), 2)
        raw = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("秘密", raw)
        self.assertNotIn("snapshot", raw)
        self.assertIn("Full English answer", raw)

    def test_client_reconnect_cursor_and_no_automatic_resubmission(self):
        output = []
        client = TerminalClient(self.bridge.path, output=output.append)
        client.poll()
        self.assertTrue(client.connected)
        client.command("A question")
        self.assertEqual(self.bridge.commands.qsize(), 1)
        self.bridge.drain(lambda action, value: True)
        self.bridge.publish("reply", en="English reply", ja="秘密")
        client.poll()
        client.poll()
        self.assertEqual(sum("English reply" in line for line in output), 1)
        self.assertFalse(client.pending)
        with patch.object(client, "exchange", side_effect=OSError):
            client.command("An uncertain delivery")
        client.poll()
        self.assertTrue(client.connected)
        self.assertTrue(self.bridge.commands.empty())
        self.assertTrue(any("no automatic retry" in line for line in output))

    def test_bounded_event_ring_paginates_without_skipping_or_repeating(self):
        for index in range(140):
            self.bridge.publish("notice", message=str(index))
        self.assertEqual(len(self.bridge.events), 128)
        client = TerminalClient(self.bridge.path, output=lambda text: None)
        for _ in range(20):
            client.poll()
        self.assertEqual(client.cursor, 140)
        self.assertEqual(self.bridge.read_events(client.cursor)["events"], [])

    def test_old_owner_does_not_delete_replacement_endpoint(self):
        replacement = {"protocol": 1, "port": 12345, "session": "a" * 64, "token": "b" * 64}
        self.bridge.path.write_text(json.dumps(replacement), encoding="utf-8")
        self.bridge.close()
        self.assertEqual(endpoint_document(self.bridge.path), replacement)


class InputTests(unittest.TestCase):
    def test_paste_is_one_question_arrows_are_ignored_and_interrupt_means_stop(self):
        seen = []
        keyboard = TerminalInput(seen.append)
        for char in "\x1b[1;1R\x1b[200~First line\rSecond line\x1b[201~\r":
            keyboard.feed(char)
        for char in "\x1b[AHello\x03":
            keyboard.feed(char)
        self.assertEqual(seen, ["First line Second line", "/stop"])
        self.assertEqual(keyboard.buffer, "")

    def test_oversized_input_is_rejected_whole_and_control_text_cannot_escape_terminal(self):
        seen, output = [], []
        keyboard = TerminalInput(seen.append, output=output.append)
        for char in "x" * (MAX_TEXT + 1) + "\r":
            keyboard.feed(char)
        self.assertFalse(seen)
        self.assertIn("not sent", output[-1])
        self.assertNotIn("\x1b", display_text("test\x1b[2J\nEnglish"))

    def test_disconnected_input_is_not_saved_and_context_command_is_explicit(self):
        client = TerminalClient("unused", output=lambda text: None)
        client.command("Question")
        self.assertFalse(client.pending)
        client.connected, client.endpoint = True, {"session": "test"}
        with patch.object(client, "exchange", return_value={"queued": True}) as send:
            client.command("/context on")
            self.assertEqual(send.call_args.args[1]["action"], "wow_include")
            self.assertIs(send.call_args.args[1]["value"], True)
            client.command("/search Kobold Camp Cleanup")
            self.assertEqual(send.call_args.args[1]["action"], "text")


class WindowIntegrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        stdout = redirect_stdout(io.StringIO())
        stdout.__enter__()
        self.addCleanup(stdout.__exit__, None, None, None)
        for name in ("SUBTITLE_MIN_SECONDS", "SUBTITLE_READING_CHARS_PER_SECOND"):
            setting = patch.object(config, name, 0)
            setting.start()
            self.addCleanup(setting.stop)

    def make_session(self, reply=get_llm_reply, *, enabled=True, wow_factory=None):
        processor = TurnProcessor(self.root, "personality", reply, tts_enabled=False,
                                  synthesize=Mock(), play=Mock(), write_caption=write_subtitle)
        view = FakeView()
        factory = lambda root: WitchcraftBridge(root, port=0)
        session = WindowSession(processor, Mock(), view, witchcraft_enabled=enabled,
                                bridge_factory=factory, wow_factory=wow_factory or Mock())
        self.addCleanup(session.cleanup)
        session.start()
        return session, view

    def pump(self, session, predicate):
        deadline = monotonic() + 3
        while monotonic() < deadline:
            session.tick()
            if predicate():
                return
            sleep(0.002)
        self.fail("Session did not reach expected state")

    def test_disabled_by_default_and_in_game_and_gui_share_one_history_and_no_audio(self):
        disabled, _ = self.make_session(enabled=False)
        self.assertIsNone(disabled.witchcraft)
        disabled.cleanup()
        session, view = self.make_session()
        output = []
        client = TerminalClient(session.witchcraft.path, output=output.append)
        client.poll()
        client.command("Question from WoW")
        self.pump(session, lambda: len(session.model.messages) == 2 and not session.model.status["busy"])
        session.request("text", "Follow-up from GUI")
        self.pump(session, lambda: len(session.model.messages) == 4 and not session.model.status["busy"])
        for _ in range(5):
            client.poll()
        self.assertTrue(any("AKANE:" in line and "Question from WoW" in line for line in output))
        self.assertTrue(any("AKANE:" in line and "Follow-up from GUI" in line for line in output))
        self.assertFalse(session.model.include_wow_context)
        self.assertEqual(len(session.processor.messages), 5)
        session.processor.synthesize.assert_not_called()
        session.processor.play.assert_not_called()
        self.assertEqual(set(view.threads), {threading.get_ident()})

    def test_busy_is_rejected_and_stop_hides_late_reply(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def reply(messages):
            calls.append(messages[-1]["content"])
            entered.set()
            release.wait(2)
            return get_llm_reply(messages)
        session, view = self.make_session(reply)
        client = TerminalClient(session.witchcraft.path, output=lambda text: None)
        client.poll()
        client.command("First question")
        session.tick()
        self.assertTrue(entered.wait(1))
        client.command("Do not queue this")
        session.tick()
        client.command("/stop")
        session.tick()
        release.set()
        self.pump(session, lambda: not session.controller.state.snapshot()["busy"])
        self.assertEqual(len(calls), 1)
        self.assertFalse(any(message["role"] == "assistant" for message in session.model.messages))
        self.assertFalse(any(event["kind"] == "reply" for event in session.witchcraft.events))
        self.assertTrue(any(event["kind"] == "ack" and not event["accepted"] for event in session.witchcraft.events))
        self.assertEqual(set(view.threads), {threading.get_ident()})

    def test_context_opt_in_collects_in_worker_and_preserves_full_pair(self):
        snapshot = {"source": "fixture", "status": "ready", "requiredDomains": ["player"]}
        threads = []
        def collect(control):
            threads.append(threading.get_ident())
            return snapshot
        factory = Mock(return_value=Mock(collect=collect))
        session, _ = self.make_session(wow_factory=factory)
        client = TerminalClient(session.witchcraft.path, output=lambda text: None)
        client.poll()
        client.command("/context on")
        session.tick()
        self.assertTrue(session.model.include_wow_context)
        client.command("Where am I?")
        self.pump(session, lambda: len(session.model.messages) == 2 and not session.model.status["busy"])
        self.assertNotEqual(threads, [threading.get_ident()])
        self.assertEqual(session.model.messages[0]["wow_context"], snapshot)
        self.assertTrue(session.model.messages[1]["ja"])
        self.assertTrue(session.model.messages[1]["en"])
        mirrored = next(event for event in session.witchcraft.events if event["kind"] == "reply")
        self.assertEqual(mirrored["en"], session.model.messages[1]["en"])
        self.assertNotIn("ja", mirrored)
        client.command("/reset")
        self.pump(session, lambda: not session.model.messages)
        self.assertFalse(session.model.include_wow_context)
        endpoint = session.witchcraft.path
        session.cleanup()
        self.assertFalse(endpoint.exists())

    def test_listener_failure_leaves_gui_operational(self):
        processor = TurnProcessor(self.root, "personality", get_llm_reply, tts_enabled=False,
                                  synthesize=Mock(), play=Mock(), write_caption=write_subtitle)
        bridge = Mock()
        bridge.start.side_effect = OSError("Port already in use")
        session = WindowSession(processor, Mock(), FakeView(), witchcraft_enabled=True,
                                bridge_factory=lambda root: bridge)
        self.addCleanup(session.cleanup)
        session.start()
        self.assertIsNone(session.witchcraft)
        self.assertTrue(session.request("text", "GUI still works"))
        self.pump(session, lambda: len(session.model.messages) == 2 and not session.model.status["busy"])
        bridge.close.assert_called_once()

    def test_addon_input_keeps_japanese_speech_and_english_caption_contract(self):
        ja, en = "北へ行こう。", "Let's go north."
        session, _ = self.make_session(lambda messages: json.dumps({"ja": ja, "en": en}))
        session.processor.tts_enabled = True
        session.processor.synthesize.return_value = b"mock audio; never played"
        session.processor.play.return_value = True
        captions = []
        session.processor.write_caption = lambda path, text: captions.append(text)
        client = TerminalClient(session.witchcraft.path, output=lambda text: None)
        client.poll()
        client.command("Where next?")
        self.pump(session, lambda: len(session.model.messages) == 2 and not session.model.status["busy"])
        session.processor.synthesize.assert_called_once_with(ja)
        session.processor.play.assert_called_once()
        self.assertIn(en, captions)
        self.assertNotIn(ja, captions)
        self.assertEqual(session.model.metrics["status"], "ok")
        self.assertEqual(next(event["en"] for event in session.witchcraft.events if event["kind"] == "reply"), en)


if __name__ == "__main__":
    unittest.main()
