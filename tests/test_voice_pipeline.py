# Service-free regression tests: no microphone capture, audio playback, or API charges.
import copy
from contextlib import ExitStack, redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np
import requests

import main as app
import cloud_speech_to_text as cloud
import config
from backend_errors import BackendError
import microphone
import ollama_backend
import speech_to_text as local
import subtitles
from test_fake_backend import get_llm_reply as fake_reply
import text_to_speech as tts


def wav_bytes(samples=800, level=1000, rate=16000):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(np.full(samples, level, dtype="<i2").tobytes())
    return output.getvalue()


def stt_result(text="recognized words"):
    return {"text": text, "languages": ["en"], "language_probability": None,
            "elapsed_seconds": 0.1}


class ReplyTests(unittest.TestCase):
    def test_accepts_reversed_order_and_trims_values(self):
        self.assertEqual(app.parse_bilingual_reply('{"en":" Hello ","ja":" やあ "}'), ("やあ", "Hello"))

    def test_rejects_invalid_or_empty_fields(self):
        for value in (None, "not JSON", "null", "[]", '{"ja":"やあ"}',
                      '{"ja":null,"en":"hi"}', '{"ja":12,"en":"hi"}',
                      '{"ja":" ","en":"hi"}', '{"ja":"やあ","en":"hi","other":1}'):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                app.parse_bilingual_reply(value)

    def test_fake_backend_obeys_current_contract(self):
        text = 'Hello "Akane" 日本語'
        ja, en = app.parse_bilingual_reply(fake_reply([{"role": "user", "content": text}]))
        self.assertIn(text, ja)
        self.assertIn(text, en)


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "personality.txt").write_text("system personality", encoding="utf-8")
        self.caption = self.root / "runtime" / "subtitle_en.txt"

    def run_loop(self, inputs, reply=None, recognize=None, tts_enabled=False, patches=(), client=None):
        self.seen = []
        if reply is None:
            def reply(messages):
                self.seen.append(copy.deepcopy(messages))
                return fake_reply(messages)
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(output))
            stack.enter_context(patch("builtins.input", side_effect=inputs))
            stack.enter_context(patch.object(config, "SUBTITLE_MIN_SECONDS", 0))
            stack.enter_context(patch.object(app, "initialize_stt", return_value=(recognize, client)))
            for item in patches:
                stack.enter_context(item)
            result = app.main(reply, stt_enabled=recognize is not None or client is not None,
                              tts_enabled=tts_enabled, project_dir=self.root)
        return result, output.getvalue()

    def test_blank_commands_reset_and_history(self):
        result, output = self.run_loop(["  ", "HeLLo", "second", "RESET", "third", "EXIT"])
        self.assertEqual(result, 0)
        self.assertEqual([len(messages) for messages in self.seen], [2, 4, 2])
        self.assertEqual(self.seen[0][-1]["content"], "HeLLo")
        self.assertEqual(self.seen[-1][0]["content"], "system personality")
        self.assertEqual(self.caption.read_text(encoding="utf-8"), "")
        self.assertIn("Conversation history reset", output)

    def test_history_trims_whole_pairs_and_keeps_system(self):
        self.run_loop(["one", "two", "three", "exit"], patches=[patch.object(config, "MAX_HISTORY_TURNS", 1)])
        self.assertEqual([entry["role"] for entry in self.seen[2]], ["system", "user", "assistant", "user"])
        self.assertEqual(self.seen[2][1]["content"], "two")

    def test_zero_history_limit_keeps_only_current_user_and_system(self):
        self.run_loop(["one", "two", "exit"], patches=[patch.object(config, "MAX_HISTORY_TURNS", 0)])
        self.assertEqual([len(messages) for messages in self.seen], [2, 2])

    def test_failed_and_invalid_replies_roll_back_user_turn(self):
        seen = []
        def reply(messages):
            seen.append(copy.deepcopy(messages))
            if len(seen) == 1:
                raise BackendError("offline")
            if len(seen) == 2:
                return '{"ja":null,"en":"broken"}'
            return fake_reply(messages)
        self.run_loop(["failed", "invalid", "valid", "exit"], reply=reply)
        self.assertEqual([len(messages) for messages in seen], [2, 2, 2])

    def test_missing_sample_and_empty_stt_do_not_call_llm(self):
        (self.root / "audio_samples").mkdir()
        (self.root / "audio_samples" / "sample.wav").write_bytes(wav_bytes())
        recognize, reply = Mock(return_value=stt_result("  ")), Mock()
        self.run_loop(["audio", "missing.wav", "audio", "sample.wav", "exit"], reply=reply, recognize=recognize)
        reply.assert_not_called()
        recognize.assert_called_once()

    def test_microphone_temp_file_removed_before_speech(self):
        captured = []
        def record(path, **kwargs):
            captured.append(path)
            path.write_bytes(wav_bytes())
            return path
        def recognize(path):
            self.assertTrue(path.exists())
            return stt_result("spoken input")
        def play(data, flags):
            self.assertFalse(captured[0].exists())
            self.assertEqual(self.caption.read_text(encoding="utf-8"), "Received message: spoken input")
        self.run_loop(["MIC", "exit"], recognize=recognize, tts_enabled=True, patches=[
            patch.object(app, "record_microphone", side_effect=record),
            patch.object(app, "synthesize_speech", return_value=wav_bytes()),
            patch.object(app.winsound, "PlaySound", side_effect=play),
        ])
        self.assertEqual(self.seen[0][-1]["content"], "spoken input")
        self.assertFalse(captured[0].parent.exists())

    def test_temp_cleanup_on_stt_failure(self):
        captured = []
        def record(path, **kwargs):
            captured.append(path)
            path.write_bytes(wav_bytes())
            return path
        recognize = Mock(side_effect=local.TranscriptionError("bad audio"))
        reply = Mock()
        self.run_loop(["mic", "exit"], reply=reply, recognize=recognize,
                      patches=[patch.object(app, "record_microphone", side_effect=record)])
        reply.assert_not_called()
        self.assertFalse(captured[0].parent.exists())

    def test_recording_error_skips_stt_and_preserves_typed_chat(self):
        recognize = Mock()
        self.run_loop(["mic", "typed", "exit"], recognize=recognize, patches=[
            patch.object(app, "record_microphone", side_effect=microphone.RecordingError("no microphone"))])
        recognize.assert_not_called()
        self.assertEqual(self.seen[0][-1]["content"], "typed")

    def test_transcribed_command_word_is_conversation_text(self):
        path = self.root / "audio_samples" / "sample.wav"
        path.parent.mkdir()
        path.write_bytes(wav_bytes())
        self.run_loop(["audio", "sample.wav", "exit"], recognize=Mock(return_value=stt_result("exit")))
        self.assertEqual(self.seen[0][-1]["content"], "exit")
        self.assertTrue(path.exists())  # Cleanup is limited to our own temporary recordings.

    def test_synthesis_failure_keeps_reply_in_history_and_text_until_exit(self):
        def next_input(prompt):
            if not self.seen:
                return "hello"
            self.assertEqual(self.caption.read_text(encoding="utf-8"), "Received message: hello")
            return "exit"
        self.run_loop(next_input, tts_enabled=True, patches=[
            patch.object(app, "synthesize_speech", side_effect=tts.SpeechSynthesisError("offline"))])
        self.assertEqual(self.caption.read_text(encoding="utf-8"), "")
        with (self.root / "runtime" / "timings.csv").open(encoding="utf-8", newline="") as stream:
            self.assertEqual(next(csv.DictReader(stream))["status"], "speech_unavailable")

    def test_playback_failure_is_recoverable(self):
        _, output = self.run_loop(["one", "two", "exit"], tts_enabled=True, patches=[
            patch.object(app, "synthesize_speech", return_value=wav_bytes()),
            patch.object(app.winsound, "PlaySound", side_effect=RuntimeError("output device unavailable")),
        ])
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(len(self.seen[1]), 4)
        self.assertIn("Speech unavailable", output)

    def test_caption_appears_only_after_synthesis_and_short_speech_gets_hold(self):
        events = []
        def synthesize(text):
            self.assertEqual(self.caption.read_text(encoding="utf-8"), "")
            events.append("synthesis")
            return wav_bytes()
        def play(data, flags):
            self.assertEqual(events, ["synthesis"])
            self.assertEqual(self.caption.read_text(encoding="utf-8"), "Received message: hello")
            events.append("playback")
        hold = Mock()
        self.run_loop(["hello", "exit"], tts_enabled=True, patches=[
            patch.object(config, "SUBTITLE_MIN_SECONDS", 2.0),
            patch.object(app, "synthesize_speech", side_effect=synthesize),
            patch.object(app.winsound, "PlaySound", side_effect=play),
            patch.object(app, "sleep", hold),
        ])
        hold.assert_called_once()
        self.assertGreater(hold.call_args.args[0], 1.5)
        self.assertEqual(self.caption.read_text(encoding="utf-8"), "")

    def test_subtitle_failure_does_not_block_speech(self):
        play = Mock()
        self.run_loop(["hello", "exit"], tts_enabled=True, patches=[
            patch.object(app, "write_subtitle", side_effect=OSError("read-only")),
            patch.object(app, "synthesize_speech", return_value=wav_bytes()),
            patch.object(app.winsound, "PlaySound", play),
        ])
        play.assert_called_once()

    def test_shutdown_clears_caption_and_closes_client_on_eof_or_interrupt(self):
        for ending in (EOFError(), KeyboardInterrupt()):
            with self.subTest(ending=type(ending).__name__):
                client = Mock()
                self.run_loop(["hello", ending], client=client)
                client.close.assert_called_once()
                self.assertEqual(self.caption.read_text(encoding="utf-8"), "")

    def test_stt_startup_failure_leaves_typed_chat_available(self):
        client = Mock()
        _, output = self.run_loop(["mic", "hello", "exit"], client=client, patches=[
            patch.object(app, "initialize_stt", side_effect=RuntimeError("STT unavailable"))])
        self.assertEqual(len(self.seen), 1)
        self.assertIn("Typed chat is still available", output)

    def test_missing_personality_clears_stale_caption(self):
        self.caption.parent.mkdir()
        self.caption.write_text("old text", encoding="utf-8")
        (self.root / "personality.txt").unlink()
        result, _ = self.run_loop([])
        self.assertEqual(result, 1)
        self.assertEqual(self.caption.read_text(encoding="utf-8"), "")

    def test_timing_csv_has_one_header_and_no_conversation_text(self):
        self.run_loop(["private words", "another message", "exit"])
        data = (self.root / "runtime" / "timings.csv").read_text(encoding="utf-8")
        self.assertNotIn("private words", data)
        rows = list(csv.DictReader(io.StringIO(data)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["input_mode"], "typed")


class AdapterTests(unittest.TestCase):
    def test_ollama_handles_unavailable_timeout_http_and_invalid_payloads(self):
        for error in (requests.ConnectionError("offline"), requests.Timeout("slow"), requests.HTTPError("404")):
            with self.subTest(error=type(error).__name__), patch.object(ollama_backend.requests, "post", side_effect=error):
                with self.assertRaises(BackendError):
                    ollama_backend.get_llm_reply([])
        for body in (None, [], {}, {"message": {"content": None}}, {"done_reason": "length"}):
            with self.subTest(body=body), patch.object(ollama_backend.requests, "post", return_value=Mock(json=Mock(return_value=body))):
                with self.assertRaises(BackendError):
                    ollama_backend.get_llm_reply([])

    def test_ollama_unwraps_response_string(self):
        text = '{"ja":"やあ","en":"hello"}'
        response = Mock(json=Mock(return_value={"message": {"content": text}}))
        with patch.object(ollama_backend.requests, "post", return_value=response):
            self.assertEqual(ollama_backend.get_llm_reply([]), text)

    def test_local_lazy_generator_failure_is_wrapped(self):
        def segments():
            yield SimpleNamespace(text="first segment")
            raise RuntimeError("decoder failed later")
        model = Mock(transcribe=Mock(return_value=(segments(), SimpleNamespace(language="en", language_probability=1.0))))
        with self.assertRaises(local.TranscriptionError):
            local.transcribe_audio(model, Path("sample.wav"))

    def test_local_result_contract(self):
        model = Mock(transcribe=Mock(return_value=(iter([SimpleNamespace(text=" hello ")]),
                    SimpleNamespace(language="en", language_probability=0.9))))
        result = local.transcribe_audio(model, Path("sample.wav"))
        self.assertEqual(set(result), set(stt_result()))
        self.assertEqual(result["text"], "hello")

    def test_cloud_handles_absent_metadata_and_closes_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            path.write_bytes(wav_bytes())
            response = Mock(text=" hello ", model_dump=Mock(return_value={}))
            client = Mock()
            client.audio.transcriptions.create.return_value = response
            result = cloud.transcribe_audio(client, path)
            upload = client.audio.transcriptions.create.call_args.kwargs["file"]
            self.assertTrue(upload.closed)
            self.assertEqual(result["text"], "hello")
            self.assertEqual(result["languages"], [])
            self.assertIsNone(result["language_probability"])

    def test_cloud_normalizes_bad_metadata_and_rejects_missing_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.wav"
            path.write_bytes(wav_bytes())
            client = Mock()
            response = Mock(text="hello", model_dump=Mock(return_value={"languages": [None, "en", {"code": "ja"}]}))
            client.audio.transcriptions.create.return_value = response
            self.assertEqual(cloud.transcribe_audio(client, path)["languages"], ["ja"])
            response.text = None
            with self.assertRaises(local.TranscriptionError):
                cloud.transcribe_audio(client, path)

    def test_tts_two_stage_request_and_valid_wav(self):
        data = wav_bytes()
        query, audio = Mock(json=Mock(return_value={"speedScale": 1.0, "volumeScale": 1.0})), Mock(content=data)
        with patch.object(config, "VOICEVOX_VOLUME_SCALE", 1.5), \
             patch.object(tts.requests, "post", side_effect=[query, audio]) as post:
            self.assertEqual(tts.synthesize_speech("こんにちは"), data)
            self.assertEqual(post.call_args_list[1].kwargs["json"], {"speedScale": 1.0, "volumeScale": 1.5})

    def test_tts_volume_accepts_mute_normal_and_upper_limit(self):
        for volume in (0, 1.0, 2.0):
            query = Mock(json=Mock(return_value={"volumeScale": 1.0}))
            with self.subTest(volume=volume), patch.object(config, "VOICEVOX_VOLUME_SCALE", volume), \
                 patch.object(tts.requests, "post", side_effect=[query, Mock(content=wav_bytes())]) as post:
                tts.synthesize_speech("こんにちは")
                self.assertEqual(post.call_args_list[1].kwargs["json"]["volumeScale"], volume)

    def test_tts_rejects_invalid_volume_before_making_requests(self):
        for volume in (-1, 2.1, "1.5", None, True, float("nan"), float("inf")):
            with self.subTest(volume=volume), patch.object(config, "VOICEVOX_VOLUME_SCALE", volume), \
                 patch.object(tts.requests, "post") as post:
                with self.assertRaisesRegex(tts.SpeechSynthesisError, "VOICEVOX_VOLUME_SCALE"):
                    tts.synthesize_speech("こんにちは")
                post.assert_not_called()

    def test_tts_rejects_invalid_audio_and_empty_input(self):
        with patch.object(tts.requests, "post", side_effect=[Mock(json=Mock(return_value={})), Mock(content=b"not WAV")]):
            with self.assertRaises(tts.SpeechSynthesisError):
                tts.synthesize_speech("こんにちは")
        with patch.object(tts.requests, "post") as post:
            with self.assertRaises(tts.SpeechSynthesisError):
                tts.synthesize_speech(" ")
            post.assert_not_called()

    def test_tts_timeout_is_wrapped(self):
        with patch.object(tts.requests, "post", side_effect=requests.Timeout("offline")):
            with self.assertRaises(tts.SpeechSynthesisError):
                tts.synthesize_speech("こんにちは")


class MicrophoneTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "input.wav"

    def record(self, samples, *, stop=(), limit=30):
        closed = []
        class Stream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]
            def __enter__(self):
                for data in samples:
                    try:
                        self.callback(data, len(data) // 2, None, None)
                    except microphone.sd.CallbackStop:
                        break
                return self
            def __exit__(self, *args):
                closed.append(True)
        self.closed = closed
        with patch.object(microphone.sd, "query_devices", return_value={"default_samplerate": 16000}), \
             patch.object(microphone.sd, "RawInputStream", Stream), \
             patch.object(config, "MIC_MAX_SECONDS", limit), \
             patch("builtins.input", side_effect=["", *stop, ""]), redirect_stdout(io.StringIO()):
            return microphone.record_microphone(self.path, input_device=1)

    def test_valid_recording_has_correct_wav_format(self):
        self.record([np.full(8000, 1000, dtype="<i2").tobytes()])
        with wave.open(str(self.path), "rb") as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 16000))
            self.assertEqual(audio.getnframes(), 8000)
        self.assertEqual(self.closed, [True])

    def test_no_audio_short_audio_and_silence_preserve_existing_file(self):
        self.path.write_bytes(b"previous recording")
        for samples in ([], [b"\0\0" * 100], [b"\0\0" * 8000]):
            with self.subTest(length=len(samples)), self.assertRaises(microphone.RecordingError):
                self.record(samples)
            self.assertEqual(self.path.read_bytes(), b"previous recording")

    def test_limit_bounds_frames_and_high_level_does_not_overflow(self):
        self.record([np.full(16000, 32767, dtype="<i2").tobytes()], limit=0.5)
        with wave.open(str(self.path), "rb") as audio:
            self.assertEqual(audio.getnframes(), 8000)

    def test_capture_closes_on_keyboard_interrupt(self):
        with self.assertRaises(KeyboardInterrupt):
            self.record([b"\0\0" * 8000], stop=[KeyboardInterrupt()])
        self.assertEqual(self.closed, [True])
        self.assertFalse(self.path.exists())

    def test_missing_device_is_wrapped(self):
        with patch.object(microphone.sd, "query_devices", side_effect=microphone.sd.PortAudioError("no device")):
            with self.assertRaises(microphone.RecordingError):
                microphone.record_microphone(self.path)


class SubtitleTests(unittest.TestCase):
    def test_unicode_replace_clear_and_failed_replace_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "subtitle.txt"
            subtitles.write_subtitle(path, "Hello — 日本語")
            self.assertEqual(path.read_text(encoding="utf-8"), "Hello — 日本語")
            with patch.object(subtitles.os, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    subtitles.write_subtitle(path, "replacement")
            self.assertEqual(path.read_text(encoding="utf-8"), "Hello — 日本語")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            subtitles.write_subtitle(path, "")
            self.assertEqual(path.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
