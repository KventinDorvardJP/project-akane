"""Mock transport/files only: never touch running models, devices or Gradio."""
from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import benchmark
import config
import control_window
import gpt_sovits_backend as sovits
import main as app
from speech_audio import SpeechSynthesisError, validate_wav
import text_to_speech as tts
from test_fake_backend import get_llm_reply as fake_reply


def wav_bytes(*, frames=800, width=2, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * frames * width * channels)
    return buffer.getvalue()


class WavContractTests(unittest.TestCase):
    def test_valid_pcm_sizes_and_channels(self):
        for width, channels in ((1, 1), (2, 1), (2, 2), (3, 1), (4, 1)):
            data = wav_bytes(width=width, channels=channels)
            self.assertEqual(validate_wav(data), data)

    def test_empty_non_audio_and_truncated_wav_are_rejected(self):
        for data in (b"", b'{"error":"failed"}', wav_bytes(frames=0), wav_bytes()[:-20]):
            with self.subTest(size=len(data)), self.assertRaises((ValueError, EOFError, wave.Error)):
                validate_wav(data)


class SelectionTests(unittest.TestCase):
    def test_voicevox_default_never_imports_optional_client(self):
        with patch.object(config, "TTS_BACKEND", "voicevox"), \
             patch.object(tts, "synthesize_voicevox", return_value=b"WAV") as voicevox, \
             patch.dict(sys.modules, {"gradio_client": None}):
            self.assertEqual(tts.synthesize_speech("やあ"), b"WAV")
        voicevox.assert_called_once_with("やあ")

    def test_gpt_override_and_config_selection_do_not_call_voicevox(self):
        with patch.object(tts, "synthesize_voicevox") as voicevox, \
             patch.object(sovits, "synthesize_speech", return_value=b"GPT WAV") as gpt, \
             patch.object(config, "TTS_BACKEND", "voicevox"):
            self.assertEqual(tts.synthesize_speech("やあ", backend="gpt_sovits"), b"GPT WAV")
            self.assertEqual(config.TTS_BACKEND, "voicevox")
            with patch.object(config, "TTS_BACKEND", "gpt_sovits"):
                self.assertEqual(tts.synthesize_speech("こんにちは"), b"GPT WAV")
        voicevox.assert_not_called()
        self.assertEqual(gpt.call_count, 2)

    def test_unknown_provider_and_gpt_failure_do_not_fall_back(self):
        with patch.object(tts, "synthesize_voicevox") as voicevox:
            with self.assertRaisesRegex(SpeechSynthesisError, "Unsupported TTS_BACKEND"):
                tts.synthesize_speech("やあ", backend="unknown")
            with patch.object(sovits, "synthesize_speech", side_effect=SpeechSynthesisError("offline")):
                with self.assertRaisesRegex(SpeechSynthesisError, "offline"):
                    tts.synthesize_speech("やあ", backend="gpt_sovits")
            voicevox.assert_not_called()

    def test_cli_and_main_override_reach_the_synthesis_function(self):
        with patch("sys.argv", ["main.py", "--fake-backend", "--tts-backend", "gpt_sovits", "--no-stt"]), \
             patch.object(app, "main", return_value=0) as run:
            self.assertEqual(app.cli(), 0)
        self.assertEqual(run.call_args.kwargs["tts_backend"], "gpt_sovits")
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            (root / "personality.txt").write_text("test", encoding="utf-8")
            def window(processor, *args, **kwargs):
                self.assertEqual(processor.tts_backend, "gpt_sovits")
                self.assertEqual(processor.synthesize("やあ"), b"WAV")
                return 0
            with patch.object(app, "synthesize_speech", return_value=b"WAV") as synthesize, \
                 patch.object(control_window, "run_control_window", side_effect=window):
                app.main(fake_reply, project_dir=root, gui_mode=True, stt_enabled=False,
                         tts_backend="gpt_sovits", overlay_enabled=False)
            synthesize.assert_called_once_with("やあ", backend="gpt_sovits")

    def test_benchmark_labels_selected_engine_and_skips_unused_voicevox_probe(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            root = Path(folder)
            (root / "personality.txt").write_text("test", encoding="utf-8")
            with patch.object(config, "TTS_BACKEND", "gpt_sovits"), \
                 patch.object(benchmark, "service_version", return_value="test") as version, \
                 patch.object(benchmark, "resource_snapshot", return_value={}), \
                 patch.object(benchmark, "get_llm_reply", side_effect=fake_reply), \
                 patch.object(benchmark, "synthesize_speech", return_value=wav_bytes()):
                report = benchmark.run_benchmark(root, runs=1)
            self.assertEqual(report["settings"]["tts_backend"], "gpt_sovits")
            self.assertIsNone(report["settings"]["voice_style"])
            self.assertEqual(version.call_count, 1)


class SovitsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.reference = self.folder / "reference.wav"
        self.extra = self.folder / "extra.wav"
        self.reference.write_bytes(wav_bytes())
        self.extra.write_bytes(wav_bytes())
        values = {"GPT_SOVITS_URL": "http://127.0.0.1:9872/", "GPT_SOVITS_REFERENCE_AUDIO": str(self.reference),
                  "GPT_SOVITS_AUX_REFERENCE_AUDIO": [str(self.extra)], "GPT_SOVITS_REFERENCE_TEXT": "参照の文章です。",
                  "GPT_SOVITS_PROMPT_LANGUAGE": "Japanese", "GPT_SOVITS_TEXT_LANGUAGE": "Japanese",
                  "GPT_SOVITS_TEXT_SPLIT_METHOD": "Slice by every punct", "GPT_SOVITS_SPEED": 1.0,
                  "GPT_SOVITS_TIMEOUT": 30}
        for name, value in values.items():
            self.stack.enter_context(patch.object(config, name, value))
        self.client = Mock()
        self.job = self.client.submit.return_value
        def connect(*args, **kwargs):
            self.download = Path(kwargs["download_files"])
            output = self.download / "generated.wav"
            output.write_bytes(wav_bytes())
            self.job.result.return_value = str(output)
            return self.client
        self.client_factory = Mock(side_effect=connect)
        self.handle = Mock(side_effect=lambda path: {"path": path, "meta": {"_type": "gradio.FileData"}})
        module = SimpleNamespace(Client=self.client_factory, handle_file=self.handle)
        self.stack.enter_context(patch.dict(sys.modules, {"gradio_client": module}))

    def test_japanese_generation_uploads_references_and_cleans_downloads(self):
        self.assertEqual(sovits.synthesize_speech("合成のテスト。"), wav_bytes())
        self.assertEqual(self.client_factory.call_args.args, ("http://127.0.0.1:9872",))
        self.assertFalse(self.client_factory.call_args.kwargs["analytics_enabled"])
        params = self.client.submit.call_args.kwargs
        self.assertEqual(params["api_name"], "/get_tts_wav")
        self.assertEqual(params["text"], "合成のテスト。")
        self.assertEqual(params["prompt_text"], "参照の文章です。")
        self.assertEqual(params["text_language"], "Japanese")
        self.assertEqual(params["prompt_language"], "Japanese")
        self.assertEqual(params["ref_wav_path"]["path"], str(self.reference))
        self.assertEqual(params["inp_refs"][0]["path"], str(self.extra))
        self.assertFalse(params["ref_free"])
        self.assertFalse(params["if_freeze"])
        self.assertTrue(self.reference.exists())
        self.assertTrue(self.extra.exists())
        self.assertFalse(self.download.exists())
        self.job.result.assert_called_once_with(timeout=30)
        self.client.close.assert_called_once()

    def test_optional_references_can_be_disabled(self):
        with patch.object(config, "GPT_SOVITS_AUX_REFERENCE_AUDIO", []):
            sovits.synthesize_speech("テスト。")
        self.assertIsNone(self.client.submit.call_args.kwargs["inp_refs"])

    def test_invalid_settings_fail_before_connecting(self):
        cases = {"GPT_SOVITS_REFERENCE_AUDIO": "missing.wav", "GPT_SOVITS_REFERENCE_TEXT": " ",
                 "GPT_SOVITS_AUX_REFERENCE_AUDIO": "not a list", "GPT_SOVITS_URL": "https://example.invalid",
                 "GPT_SOVITS_SPEED": float("nan"), "GPT_SOVITS_TIMEOUT": 0}
        for name, value in cases.items():
            with self.subTest(name=name), patch.object(config, name, value):
                with self.assertRaises(SpeechSynthesisError):
                    sovits.synthesize_speech("テスト。")
        self.client_factory.assert_not_called()

    def test_empty_text_and_missing_optional_dependency_are_recoverable(self):
        with self.assertRaises(SpeechSynthesisError):
            sovits.synthesize_speech(" ")
        with patch.dict(sys.modules, {"gradio_client": None}):
            with self.assertRaisesRegex(SpeechSynthesisError, "requirements-gpt-sovits.txt"):
                sovits.synthesize_speech("テスト。")
        self.client_factory.assert_not_called()

    def test_timeout_requests_cancellation_and_closes_client(self):
        self.job.result.side_effect = TimeoutError()
        with self.assertRaisesRegex(SpeechSynthesisError, "timed out"):
            sovits.synthesize_speech("テスト。")
        self.job.cancel.assert_called_once()
        self.client.close.assert_called_once()
        self.assertFalse(self.download.exists())

    def test_server_failure_is_wrapped_and_closes_client(self):
        self.job.result.side_effect = RuntimeError("inference error")
        with self.assertRaisesRegex(SpeechSynthesisError, "inference error"):
            sovits.synthesize_speech("テスト。")
        self.client.close.assert_called_once()
        self.assertFalse(self.download.exists())

    def test_malformed_and_external_results_are_rejected(self):
        for result in (None, ["wrong shape"], str(self.reference)):
            with self.subTest(result=result):
                self.job.result.side_effect = None
                # The mocked client factory creates the download first.
                self.job.result.side_effect = lambda **kwargs: result
                with self.assertRaises(SpeechSynthesisError):
                    sovits.synthesize_speech("テスト。")
        self.assertTrue(self.reference.exists())

    def test_invalid_generated_audio_is_rejected(self):
        def result(**kwargs):
            path = self.download / "bad.wav"
            path.write_bytes(b"not WAV")
            return str(path)
        self.job.result.side_effect = result
        with self.assertRaises(SpeechSynthesisError):
            sovits.synthesize_speech("テスト。")
        self.assertFalse(self.download.exists())


if __name__ == "__main__":
    unittest.main()
