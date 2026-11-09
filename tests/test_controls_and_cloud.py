# All OS/audio/provider interactions here are fakes. Never register live hotkeys.
from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import httpx2
from openai import OpenAI, APIError
import numpy as np

import config
from backend_errors import BackendError
from controls import ControlState, TurnControl, TurnCancelled
from llm_backends import select_reply_backend
import metrics
import microphone
from openai_backend import OpenAIBackend, estimate_text_cost
import playback
from stream_controls import StreamController, ConsoleCommands
from subtitles import write_subtitle
from test_fake_backend import get_llm_reply as fake_reply
from turns import TurnProcessor
from windows_hotkeys import WindowsHotkeys, HotkeyError, parse_hotkey


def wav_data(frames=1600):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(np.full(frames, 1000, dtype="<i2").tobytes())
    return output.getvalue()


def response_payload(status="completed", text=None):
    return {
        "id": "resp_test", "created_at": 0, "object": "response", "status": status,
        "model": "gpt-6.1-sol", "output": [{"id": "msg_test", "type": "message",
            "role": "assistant", "status": "completed", "content": [{"type": "output_text",
            "text": text or '{"ja":"こんにちは","en":"Hello"}', "annotations": []}]}],
        "usage": {"input_tokens": 200, "input_tokens_details": {"cached_tokens": 100},
                  "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 10}, "total_tokens": 250},
    }


class OpenAIBackendTests(unittest.TestCase):
    def setUp(self):
        # Keep the original no-search adapter regression cases independent of defaults.
        model = patch.object(config, "OPENAI_MODEL", "gpt-6.1-sol")
        search = patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", False)
        model.start()
        search.start()
        self.addCleanup(model.stop)
        self.addCleanup(search.stop)

    def client(self, handler):
        # Run the installed SDK's real serialization/deserialization through an in-memory transport.
        client = OpenAI(api_key="test-key", max_retries=0,
                        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
        self.addCleanup(client.close)
        return client

    def test_installed_sdk_serializes_schema_messages_and_usage(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json=response_payload())
        backend = OpenAIBackend(self.client(handler))
        reply = backend([{"role": "system", "content": "personality"}, {"role": "user", "content": "hi"}])
        self.assertEqual(json.loads(reply)["ja"], "こんにちは")
        self.assertFalse(requests[0]["store"])
        self.assertTrue(requests[0]["text"]["format"]["strict"])
        self.assertEqual(requests[0]["text"]["format"]["schema"]["required"], ["ja", "en"])
        self.assertEqual(backend.last_usage["input_tokens"], 200)
        self.assertEqual(backend.last_usage["reasoning_tokens"], 10)
        self.assertAlmostEqual(backend.last_usage["estimated_api_cost_usd"], 0.00071)

    def test_missing_usage_does_not_invent_estimate_and_blocks_next_budgeted_call(self):
        body = response_payload()
        body.pop("usage")
        count = []
        def handler(request):
            count.append(1)
            return httpx2.Response(200, json=body)
        backend = OpenAIBackend(self.client(handler))
        backend([])
        self.assertIsNone(backend.last_usage["estimated_api_cost_usd"])
        with self.assertRaises(BackendError):
            backend([])
        self.assertEqual(len(count), 1)

    def test_output_truncation_still_accounts_for_usage(self):
        backend = OpenAIBackend(self.client(lambda request: httpx2.Response(200, json=response_payload("incomplete"))))
        with self.assertRaises(BackendError):
            backend([])
        self.assertEqual(backend.last_usage["output_tokens"], 50)
        self.assertGreater(backend.estimated_session_usd, 0)

    def test_refusal_without_text_is_recoverable(self):
        body = response_payload()
        body["output"][0]["content"] = [{"type": "refusal", "refusal": "test refusal"}]
        backend = OpenAIBackend(self.client(lambda request: httpx2.Response(200, json=body)))
        with self.assertRaises(BackendError):
            backend([])

    def test_http_error_and_timeout_are_wrapped_without_retry(self):
        requests = []
        def error_handler(request):
            requests.append(request)
            return httpx2.Response(429, json={"error": {"message": "test quota", "type": "rate_limit_error"}})
        backend = OpenAIBackend(self.client(error_handler))
        with self.assertRaises(BackendError):
            backend([])
        self.assertEqual(len(requests), 1)
        def timeout(request):
            raise httpx2.ReadTimeout("test timeout", request=request)
        backend = OpenAIBackend(self.client(timeout))
        with self.assertRaises(BackendError):
            backend([])

    def test_budget_unknown_model_and_bad_rates_reject_before_request(self):
        client = Mock()
        backend = OpenAIBackend(client)
        with patch.object(config, "OPENAI_SESSION_BUDGET_USD", 0):
            with self.assertRaises(BackendError):
                backend([])
        with self.assertRaises(BackendError):
            OpenAIBackend(client, "unpriced-model")([])
        with patch.object(config, "OPENAI_PRICING_USD_PER_MILLION", {"gpt-6.1-sol": {"input": -1}}):
            with self.assertRaises(BackendError):
                backend([])
        client.responses.create.assert_not_called()

    def test_budget_stops_following_request_after_small_overshoot(self):
        client = self.client(lambda request: httpx2.Response(200, json=response_payload()))
        backend = OpenAIBackend(client)
        with patch.object(config, "OPENAI_SESSION_BUDGET_USD", 0.0001):
            backend([])
            with self.assertRaises(BackendError):
                backend([])

    def test_reasoning_option_can_be_omitted_for_other_models(self):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            return httpx2.Response(200, json=response_payload())
        with patch.object(config, "OPENAI_REASONING_EFFORT", None):
            OpenAIBackend(self.client(handler))([])
        self.assertNotIn("reasoning", seen[0])

    def test_factory_requires_key_without_falling_back(self):
        with patch("llm_backends.load_dotenv"), patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(BackendError):
                select_reply_backend("openai", Path("unused"))
        with self.assertRaises(BackendError):
            select_reply_backend("unknown", Path("unused"))

    def test_cost_unknown_model_and_cached_count_bounds(self):
        self.assertIsNone(estimate_text_cost("unknown", 10, 0, 10))
        self.assertAlmostEqual(estimate_text_cost("gpt-6.1-sol", 10, 100, 0), 0.000001)


class FakeNative:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.registered, self.unregistered = [], []
        self.messages = queue.Queue()

    def thread_id(self):
        return 42

    def register(self, identifier, binding):
        if identifier == self.fail_on:
            raise HotkeyError("simulated conflict")
        self.registered.append(identifier)

    def unregister(self, identifier):
        self.unregistered.append(identifier)

    def next_action_id(self):
        return self.messages.get(timeout=2)

    def wake_stop(self, thread_id):
        self.messages.put(None)


class HotkeyTests(unittest.TestCase):
    def test_parse_modifiers_no_repeat_and_validation(self):
        binding = parse_hotkey("Ctrl+Shift+F8")
        self.assertEqual(binding.modifiers, 0x4006)
        self.assertEqual(binding.key, 0x77)
        for text in ("f8", "ctrl+f12", "ctrl+f25", "ctrl++a", "ctrl+shift", "win+a"):
            with self.subTest(text=text), self.assertRaises(HotkeyError):
                parse_hotkey(text)

    def test_registration_failure_releases_partial_bindings(self):
        native = FakeNative(fail_on=2)
        listener = WindowsHotkeys({"record": "ctrl+f8", "stop": "ctrl+f9"}, Mock(), lambda: native)
        with self.assertRaises(HotkeyError):
            listener.start()
        self.assertEqual(native.registered, [1])
        self.assertEqual(native.unregistered, [1])

    def test_dispatch_and_clean_stop_without_os_registration(self):
        native, actions = FakeNative(), []
        native.messages.put(1)
        dispatched = threading.Event()
        def receive(action):
            actions.append(action)
            dispatched.set()
        listener = WindowsHotkeys({"record": "ctrl+f8"}, receive, lambda: native)
        listener.start()
        self.assertTrue(dispatched.wait(1))
        listener.stop()
        self.assertEqual(actions, ["record"])
        self.assertEqual(native.unregistered, [1])
        self.assertFalse(listener.thread.is_alive())

    def test_duplicate_keys_rejected_before_native_factory(self):
        factory = Mock()
        listener = WindowsHotkeys({"record": "ctrl+f8", "stop": "control+f8"}, Mock(), factory)
        with self.assertRaises(HotkeyError):
            listener.start()
        factory.assert_not_called()


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.caption = self.root / "runtime" / "subtitle_en.txt"

    def processor(self, reply=fake_reply, synthesize=None, play=None, enabled=True):
        return TurnProcessor(self.root, "personality", reply, tts_enabled=enabled,
                             synthesize=synthesize or Mock(return_value=wav_data()),
                             play=play or Mock(return_value=True), write_caption=write_subtitle)

    def done(self, controller):
        action, value = controller.events.get(timeout=3)
        self.assertEqual(action, "done")
        controller.handle(action, value)

    def test_pause_cancels_and_caption_cannot_reappear(self):
        state = ControlState()
        token = state.begin(Mock())
        writer = Mock()
        token.show_caption(writer, "hello")
        state.toggle_pause(writer)
        self.assertTrue(token.cancel.is_set())
        with self.assertRaises(TurnCancelled):
            token.show_caption(writer, "late reply")
        self.assertEqual(writer.call_args.args, ("",))
        self.assertIsNone(state.begin(Mock()))
        state.finish(token)
        state.toggle_pause(writer)
        self.assertIsNotNone(state.begin(Mock()))

    def test_cancelled_cloud_reply_keeps_usage_without_speech_or_history(self):
        token = TurnControl()
        def reply(messages):
            reply.last_usage = {"provider": "openai", "model": "mock-model",
                                "input_tokens": 40, "output_tokens": 10,
                                "estimated_api_cost_usd": 0.001}
            token.cancel.set()  # Simulate cancellation while a paid request is in flight.
            return fake_reply(messages)
        synthesize = Mock()
        processor = self.processor(reply=reply, synthesize=synthesize)
        with redirect_stdout(io.StringIO()):
            self.assertFalse(processor.respond("hello", control=token))
        synthesize.assert_not_called()
        self.assertEqual(len(processor.messages), 1)
        with (self.root / "runtime" / "timings.csv").open(newline="", encoding="utf-8") as source:
            row = next(csv.DictReader(source))
        self.assertEqual(row["status"], "cancelled")
        self.assertEqual(row["input_tokens"], "40")
        self.assertEqual(row["estimated_api_cost_usd"], "0.001")

    def test_pre_cancelled_turn_does_not_copy_previous_usage(self):
        reply = Mock()
        reply.last_usage = {"provider": "openai", "estimated_api_cost_usd": 99}
        token = TurnControl()
        token.cancel.set()
        with redirect_stdout(io.StringIO()):
            self.assertFalse(self.processor(reply=reply).respond("hello", control=token))
        reply.assert_not_called()
        with (self.root / "runtime" / "timings.csv").open(newline="", encoding="utf-8") as source:
            self.assertEqual(next(csv.DictReader(source))["estimated_api_cost_usd"], "")

    def test_cancelled_llm_reply_rolls_back_and_never_synthesizes(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def reply(messages):
            entered.set()
            release.wait(2)
            return fake_reply(messages)
        synthesize, play = Mock(), Mock()
        processor = self.processor(reply=reply, synthesize=synthesize, play=play)
        controller = StreamController(processor, None, Mock())
        with redirect_stdout(io.StringIO()):
            controller.handle("text", "private text")
            self.assertTrue(entered.wait(1))
            controller.handle("pause")
            self.assertEqual(controller.state.snapshot()["state"], "paused")
            controller.handle("text", "must not queue")
            release.set()
            self.done(controller)
        synthesize.assert_not_called()
        play.assert_not_called()
        self.assertEqual(len(processor.messages), 1)
        self.assertEqual(self.caption.read_text(encoding="utf-8"), "")
        self.assertNotIn("private text", (self.root / "runtime" / "status.json").read_text())

    def test_reset_waits_for_worker_before_modifying_history(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def reply(messages):
            entered.set()
            release.wait(2)
            return fake_reply(messages)
        processor = self.processor(reply=reply, enabled=False)
        controller = StreamController(processor, None, Mock())
        with redirect_stdout(io.StringIO()):
            controller.handle("text", "test")
            self.assertTrue(entered.wait(1))
            controller.handle("reset")
            self.assertTrue(controller.pending_reset)
            release.set()
            self.done(controller)
        self.assertFalse(controller.pending_reset)
        self.assertEqual(processor.messages, [{"role": "system", "content": "personality"}])

    def test_microphone_toggle_stop_and_no_overlap_with_busy_worker(self):
        recording, transcribing, release_stt = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release_stt.set)
        paths = []
        def record(path, *, input_device, stop_event, cancel_event, on_started):
            paths.append(path)
            on_started()
            recording.set()
            if not stop_event.wait(2):
                raise AssertionError("stop toggle missing")
            path.write_bytes(wav_data())
            return path
        def recognize(path):
            self.assertTrue(path.exists())
            transcribing.set()
            release_stt.wait(2)
            return {"text": "recognized input", "languages": ["en"],
                    "elapsed_seconds": 0.1, "language_probability": None}
        recorder = Mock(side_effect=record)
        processor = self.processor(enabled=False)
        controller = StreamController(processor, recognize, recorder)
        with redirect_stdout(io.StringIO()):
            controller.handle("record")
            self.assertTrue(recording.wait(1))
            controller.handle("record")
            self.assertTrue(transcribing.wait(1))
            controller.handle("record")
            self.assertEqual(recorder.call_count, 1)
            release_stt.set()
            self.done(controller)
        self.assertFalse(paths[0].exists())
        self.assertEqual(processor.messages[-2]["content"], "recognized input")

    def test_stop_during_synthesis_discards_audio(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def synthesize(text):
            entered.set()
            release.wait(2)
            return wav_data()
        play = Mock()
        processor = self.processor(synthesize=synthesize, play=play)
        controller = StreamController(processor, None, Mock())
        with redirect_stdout(io.StringIO()):
            controller.handle("text", "test")
            self.assertTrue(entered.wait(1))
            controller.handle("stop")
            release.set()
            self.done(controller)
        play.assert_not_called()
        self.assertEqual(self.caption.read_text(encoding="utf-8"), "")
        self.assertEqual(len(processor.messages), 3)  # A valid displayed reply is retained.

    def test_cancel_interrupts_caption_hold(self):
        control = TurnControl()
        def play(data, token):
            token.cancel.set()
            return True
        processor = self.processor(play=play)
        with redirect_stdout(io.StringIO()):
            self.assertFalse(processor.respond("test", control=control))
        self.assertEqual(self.caption.read_text(encoding="utf-8"), "")

    def test_hotkey_loop_start_failure_and_shutdown_are_safe(self):
        stopped = []
        class Listener:
            def __init__(self, bindings, callback):
                self.callback = callback
            def start(self):
                self.callback("exit")
            def stop(self):
                stopped.append(True)
        controller = StreamController(self.processor(enabled=False), None, Mock())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(controller.run(hotkeys_factory=Listener, console_input=False), 0)
        self.assertEqual(stopped, [True])
        self.assertEqual(json.loads((self.root / "runtime" / "status.json").read_text())["state"], "stopped")
        class Conflict(Listener):
            def start(self):
                raise HotkeyError("occupied")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(controller.run(hotkeys_factory=Conflict, console_input=False), 1)

    def test_console_commands_are_polled_without_background_stdin_thread(self):
        characters = iter("mic\rpause\raudio sample.wav\rHello\b!\rexit\r")
        pending = []
        class Console:
            def kbhit(self):
                if not pending:
                    try:
                        pending.append(next(characters))
                    except StopIteration:
                        return False
                return True
            def getwch(self):
                return pending.pop()
        received = []
        reader = ConsoleCommands(lambda *args: received.append(args), Console())
        with redirect_stdout(io.StringIO()):
            reader.poll()
        self.assertEqual(received, [("record",), ("pause",), ("audio", "sample.wav"),
                                    ("text", "Hell!"), ("exit",)])


class PlaybackTests(unittest.TestCase):
    def test_stop_aborts_output_and_closes_stream(self):
        control, events = TurnControl(), []
        class Stream:
            def __init__(self, **kwargs):
                pass
            def __enter__(self):
                events.append("opened")
                return self
            def write(self, data):
                events.append("write")
                control.cancel.set()
            def abort(self):
                events.append("aborted")
            def __exit__(self, *args):
                events.append("closed")
        with patch.object(playback.sd, "RawOutputStream", Stream):
            self.assertFalse(playback.play_wav(wav_data(3200), control))
        self.assertEqual(events, ["opened", "write", "aborted", "closed"])

    def test_pre_cancelled_playback_never_opens_output(self):
        control = TurnControl()
        control.cancel.set()
        with patch.object(playback.sd, "RawOutputStream") as stream:
            self.assertFalse(playback.play_wav(wav_data(), control))
            stream.assert_not_called()


class ControlledMicrophoneTests(unittest.TestCase):
    def run_capture(self, *, cancel=False, capped=False):
        stop, cancelled, events = threading.Event(), threading.Event(), []
        class Stream:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def __enter__(self):
                events.append("opened")
                data = np.full(16000, 1000, dtype="<i2").tobytes()
                try:
                    self.kwargs["callback"](data, 16000, None, None)
                except microphone.sd.CallbackStop:
                    self.kwargs["finished_callback"]()
                return self
            def __exit__(self, *args):
                events.append("closed")
        def started():
            events.append("recording")
            if cancel:
                cancelled.set()
            elif not capped:
                stop.set()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.wav"
            with patch.object(microphone.sd, "query_devices", return_value={"default_samplerate": 16000}), \
                 patch.object(microphone.sd, "RawInputStream", Stream), \
                 patch.object(config, "MIC_MAX_SECONDS", 1 if capped else 30), \
                 patch("builtins.input", side_effect=AssertionError("Hotkey recording must not ask for Enter")), \
                 redirect_stdout(io.StringIO()):
                if cancel:
                    with self.assertRaises(TurnCancelled):
                        microphone.record_microphone(path, stop_event=stop, cancel_event=cancelled, on_started=started)
                    self.assertFalse(path.exists())
                else:
                    self.assertEqual(microphone.record_microphone(path, stop_event=stop, cancel_event=cancelled, on_started=started), path)
                    with wave.open(str(path), "rb") as audio:
                        self.assertEqual(audio.getnframes(), 16000)
            self.assertEqual(events, ["opened", "recording", "closed"])

    def test_hotkey_stop_saves_audio_without_enter(self):
        self.run_capture()

    def test_cancellation_closes_capture_and_does_not_save(self):
        self.run_capture(cancel=True)

    def test_recording_cap_continues_automatically(self):
        self.run_capture(capped=True)


class MetricsUpgradeTests(unittest.TestCase):
    def test_upgrade_preserves_legacy_rows_and_backup_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timings.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=metrics.LEGACY_FIELDS)
                writer.writeheader()
                writer.writerow({"timestamp_utc": "previous", "status": "ok"})
            original = path.read_bytes()
            metrics.append_timing(path, {"status": "cancelled", "input_tokens": 42})
            with path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["timestamp_utc"], "previous")
            self.assertEqual(rows[1]["input_tokens"], "42")
            backup = next(Path(directory).glob("timings.legacy.*.csv"))
            self.assertEqual(backup.read_bytes(), original)

    def test_unknown_header_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "timings.csv"
            path.write_text("unknown,header\nkeep,me\n", encoding="utf-8")
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                metrics.append_timing(path, {})
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
