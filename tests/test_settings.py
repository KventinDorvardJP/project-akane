"""Settings persistence/restart tests. No actual UI, services, devices or keys."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import config
import control_window
from backend_errors import BackendError
from llm_backends import UnavailableBackend
import main as app
from settings_store import (SettingsStore, RESTART_SESSION, format_value, group_for,
                            parse_value, validate_settings)
import settings_window
from settings_window import SettingsDialog
from subtitles import write_subtitle
from test_fake_backend import get_llm_reply as fake_reply
from turns import TurnProcessor
from window_session import WindowSession


class Value:
    def __init__(self, parent=None, value=None):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class Text:
    def __init__(self, *args, **kwargs):
        self.value = ""
    def get(self, *args):
        return self.value
    def insert(self, _, text):
        self.value += text
    def delete(self, *args):
        self.value = ""
    def configure(self, *args, **kwargs):
        pass
    def grid(self, *args, **kwargs):
        pass


class StoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = SettingsStore(self.root)
        self.values = self.store.load()

    def test_every_public_setting_roundtrips_through_form_values(self):
        for name, value in self.values.items():
            raw = value if isinstance(value, bool) else format_value(name, value)
            parsed = parse_value(name, raw, self.store.defaults[name])
            self.assertEqual(list(parsed) if isinstance(parsed, tuple) else parsed,
                             list(value) if isinstance(value, tuple) else value, name)
        validate_settings(self.values, self.store.defaults, self.root)

    def test_preserves_reference_profile_and_saves_only_overrides(self):
        profile = {"MIC_INPUT_DEVICE": 7, "GPT_SOVITS_REFERENCE_AUDIO": "audio_samples/test.wav",
                   "GPT_SOVITS_REFERENCE_TEXT": "日本語の参照です。", "GPT_SOVITS_AUX_REFERENCE_AUDIO": ["extra.wav"]}
        self.store.path.write_text(json.dumps(profile), encoding="utf-8")
        values = self.store.load()
        values["VOICEVOX_VOLUME_SCALE"] = 1.25
        active_volume = config.VOICEVOX_VOLUME_SCALE
        self.store.save(values)
        saved = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["MIC_INPUT_DEVICE"], 7)
        self.assertEqual(saved["GPT_SOVITS_REFERENCE_TEXT"], "日本語の参照です。")
        self.assertNotIn("TTS_BACKEND", saved)
        self.assertEqual(config.VOICEVOX_VOLUME_SCALE, active_volume)
        self.assertEqual(self.store.load()["VOICEVOX_VOLUME_SCALE"], 1.25)

    def test_concurrent_changes_are_not_overwritten(self):
        self.store.path.write_text('{"MIC_INPUT_DEVICE": 9}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed while"):
            self.store.save(self.values)
        self.assertEqual(json.loads(self.store.path.read_text())["MIC_INPUT_DEVICE"], 9)

    def test_unknown_keys_and_credentials_are_rejected(self):
        for content in ('{"OPENAI_API_KEY":"placeholder"}', '[]'):
            self.store.path.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                self.store.load()

    def test_invalid_values_fail_without_creating_a_file(self):
        cases = {"VOICEVOX_VOLUME_SCALE": 3, "MAX_HISTORY_TURNS": -1,
                 "REQUEST_TIMEOUT": 0, "OPENAI_SESSION_BUDGET_USD": float("nan"),
                 "SUBTITLE_OVERLAY_PORT": 70000, "MIC_INPUT_DEVICE": -1,
                 "LLM_BACKEND": "guess", "TTS_ENABLED": "yes", "HOTKEY_STOP": "ctrl+f12",
                 "GPT_SOVITS_URL": "https://example.invalid", "OPENAI_WEB_SEARCH_MAX_CALLS": 11,
                 "OPENAI_PRICING_USD_PER_MILLION": {"test": {"input": -1}}}
        for name, value in cases.items():
            values = {**self.values, name: value}
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.save(values)
        self.assertFalse(self.store.path.exists())

    def test_cross_field_recording_and_hotkey_validation(self):
        for change in ({"MIC_MIN_SECONDS": 1000}, {"HOTKEY_STOP": self.values["HOTKEY_RECORD"]}):
            with self.assertRaises(ValueError):
                self.store.save({**self.values, **change})

    def test_optional_blank_fields_and_device_names_are_supported(self):
        for name in ("STT_LANGUAGE", "OPENAI_SESSION_BUDGET_USD", "OPENAI_REASONING_EFFORT", "MIC_INPUT_DEVICE"):
            self.assertIsNone(parse_value(name, "", self.store.defaults[name]))
        self.assertEqual(parse_value("MIC_INPUT_DEVICE", "My microphone", None), "My microphone")
        self.assertEqual(parse_value("MIC_INPUT_DEVICE", "2", None), 2)
        self.assertEqual(parse_value("OPENAI_REASONING_EFFORT", "none", "low"), "none")
        self.assertEqual(parse_value("VOICEVOX_VOLUME_SCALE", "1.5", 2), 1.5)

    def test_selected_sovits_requires_usable_reference_files(self):
        with self.assertRaisesRegex(ValueError, "transcript"):
            self.store.save({**self.values, "TTS_BACKEND": "gpt_sovits"})
        reference = self.root / "ref.wav"
        reference.write_bytes(b"test reference")
        values = {**self.values, "TTS_BACKEND": "gpt_sovits", "GPT_SOVITS_REFERENCE_AUDIO": "ref.wav",
                  "GPT_SOVITS_REFERENCE_TEXT": "参照です。"}
        self.store.save(values)
        self.assertEqual(self.store.load()["TTS_BACKEND"], "gpt_sovits")


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in ("Frame", "Label", "Notebook", "Scrollbar", "Button", "Checkbutton", "Combobox", "Entry"):
            self.stack.enter_context(patch.object(settings_window.ttk, name))
        for name in ("Toplevel", "Canvas"):
            self.stack.enter_context(patch.object(settings_window.tk, name))
        self.stack.enter_context(patch.object(settings_window.tk, "StringVar", Value))
        self.stack.enter_context(patch.object(settings_window.tk, "BooleanVar", Value))
        self.stack.enter_context(patch.object(settings_window, "ScrolledText", Text))
        self.restart, self.saved = Mock(return_value=True), Mock()
        self.dialog = SettingsDialog(Mock(), self.root, on_restart=self.restart, on_saved=self.saved)

    def test_all_settings_have_controls_and_initialization_has_no_side_effects(self):
        self.assertEqual(set(self.dialog.fields), set(config._defaults))
        self.assertEqual(set(self.dialog.collect()), set(config._defaults))
        self.assertTrue(all(group_for(name) in self.dialog.pages for name in config._defaults))
        self.restart.assert_not_called()
        self.saved.assert_not_called()
        self.assertFalse((self.root / "config.local.json").exists())

    def test_save_persists_without_restarting(self):
        self.dialog.fields["HOTKEYS_ENABLED"][0].set(True)
        self.assertTrue(self.dialog.save())
        self.restart.assert_not_called()
        self.saved.assert_called_once()
        self.assertTrue(json.loads((self.root / "config.local.json").read_text())["HOTKEYS_ENABLED"])

    def test_invalid_input_is_visible_without_saving_or_restarting(self):
        self.dialog.fields["VOICEVOX_VOLUME_SCALE"][0].set("bad number")
        self.assertFalse(self.dialog.save(restart=True))
        self.assertIn("Invalid value", self.dialog.status.get())
        self.restart.assert_not_called()
        self.assertFalse((self.root / "config.local.json").exists())

    def test_restart_only_after_successful_save_and_rejection_keeps_dialog(self):
        self.restart.return_value = False
        self.assertTrue(self.dialog.save(restart=True))
        self.assertIn("Wait for", self.dialog.status.get())
        self.dialog.window.destroy.assert_not_called()
        self.restart.return_value = True
        self.assertTrue(self.dialog.save(restart=True))
        self.dialog.window.destroy.assert_called_once()

    def test_restoring_form_defaults_does_not_save(self):
        self.dialog.fields["VOICEVOX_VOLUME_SCALE"][0].set("0.5")
        self.dialog.restore_defaults()
        self.assertEqual(self.dialog.collect()["VOICEVOX_VOLUME_SCALE"], config._defaults["VOICEVOX_VOLUME_SCALE"])
        self.assertFalse((self.root / "config.local.json").exists())

    def test_reference_pickers_run_only_on_request(self):
        with patch.object(settings_window.filedialog, "askopenfilename", return_value="C:/ref.wav") as single, \
             patch.object(settings_window.filedialog, "askopenfilenames", return_value=("C:/one.wav", "C:/two.wav")) as multiple:
            single.assert_not_called()
            multiple.assert_not_called()
            self.dialog.browse("GPT_SOVITS_REFERENCE_AUDIO")
            self.dialog.browse("GPT_SOVITS_AUX_REFERENCE_AUDIO")
        values = self.dialog.collect()
        self.assertEqual(values["GPT_SOVITS_REFERENCE_AUDIO"], "C:/ref.wav")
        self.assertEqual(values["GPT_SOVITS_AUX_REFERENCE_AUDIO"], ["C:/one.wav", "C:/two.wav"])

    def test_device_listing_is_explicit_and_never_opens_streams(self):
        import sounddevice as sd
        with patch.object(sd, "query_devices", return_value=[{"name": "Test mic", "max_input_channels": 1, "max_output_channels": 0}]) as query, \
             patch.object(sd, "InputStream") as recording, patch.object(sd, "RawOutputStream") as playback:
            query.assert_not_called()
            self.dialog.list_devices()
        self.assertIn("0: Test mic", self.dialog.devices.value)
        recording.assert_not_called()
        playback.assert_not_called()


class RestartTests(unittest.TestCase):
    def test_restart_request_waits_for_idle_and_initialization(self):
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            processor = TurnProcessor(Path(folder), "test", fake_reply, tts_enabled=False,
                                      synthesize=Mock(), play=Mock(), write_caption=write_subtitle)
            view = Mock()
            session = WindowSession(processor, Mock(), view)
            try:
                session.init_finished = False
                self.assertFalse(session.request("restart"))
                session.init_finished = True
                token = session.controller.state.begin(None)
                self.assertFalse(session.request("restart"))
                self.assertFalse(session.restart_requested)
                session.controller.state.finish(token)
                self.assertTrue(session.request("restart"))
                self.assertTrue(session.restart_requested)
                self.assertTrue(session.model.closing)
            finally:
                session.cleanup()

    def test_cli_closes_client_before_reload_and_drops_stale_launch_overrides(self):
        first, second = Mock(), Mock()
        order, calls = [], []
        first.close.side_effect = lambda: order.append("close first")
        second.close.side_effect = lambda: order.append("close second")
        def run(*args, **kwargs):
            calls.append(kwargs)
            order.append("run")
            return RESTART_SESSION if len(calls) == 1 else 0
        def reload(module):
            order.append("reload")
            self.assertIn("close first", order)
            return module
        with patch("sys.argv", ["main.py", "--gui", "--witchcraft", "--hotkeys", "--overlay", "--tts-backend", "gpt_sovits"]), \
             patch.object(config, "FAKE_BACKEND_ENABLED", False), patch.object(config, "HOTKEYS_ENABLED", False), \
             patch.object(config, "WITCHCRAFT_ENABLED", False), \
             patch.object(app, "select_reply_backend", side_effect=[first, second]), \
             patch.object(app, "main", side_effect=run), patch("importlib.reload", side_effect=reload):
            self.assertEqual(app.cli(), 0)
        self.assertTrue(calls[0]["hotkey_mode"])
        self.assertFalse(calls[1]["hotkey_mode"])
        self.assertTrue(calls[0]["witchcraft_enabled"])
        self.assertFalse(calls[1]["witchcraft_enabled"])
        self.assertEqual(calls[0]["tts_backend"], "gpt_sovits")
        self.assertIsNone(calls[1]["tts_backend"])
        self.assertIsNone(calls[1]["overlay_enabled"])
        self.assertEqual(order, ["run", "close first", "reload", "run", "close second"])

    def test_gui_stays_available_after_backend_startup_failure(self):
        with patch("sys.argv", ["main.py", "--gui"]), \
             patch.object(config, "FAKE_BACKEND_ENABLED", False), \
             patch.object(app, "select_reply_backend", side_effect=BackendError("missing key")), \
             patch.object(app, "main", return_value=0) as run:
            self.assertEqual(app.cli(), 0)
        backend = run.call_args.args[0]
        self.assertIsInstance(backend, UnavailableBackend)
        with self.assertRaisesRegex(BackendError, "missing key"):
            backend([])

    def test_window_reports_restart_after_session_cleanup_without_real_tk(self):
        processor, root, view, session = Mock(), Mock(), Mock(), Mock()
        session.finished = session.restart_requested = True
        with patch.object(control_window.tk, "Tk", return_value=root), \
             patch.object(control_window, "ControlWindow", return_value=view), \
             patch.object(control_window, "WindowSession", return_value=session):
            self.assertEqual(control_window.run_control_window(processor, Mock()), RESTART_SESSION)
        session.cleanup.assert_called_once()
        self.assertEqual(view.project_dir, processor.root)

    def test_saved_session_toggles_work_without_cli_flags(self):
        with patch("sys.argv", ["main.py", "--gui"]), \
             patch.object(config, "FAKE_BACKEND_ENABLED", True), patch.object(config, "HOTKEYS_ENABLED", True), \
             patch.object(config, "STT_ENABLED", False), patch.object(config, "TTS_ENABLED", False), \
             patch.object(config, "WITCHCRAFT_ENABLED", True), \
             patch.object(app, "select_reply_backend") as select, patch.object(app, "main", return_value=0) as run:
            self.assertEqual(app.cli(), 0)
        select.assert_not_called()
        self.assertTrue(run.call_args.kwargs["hotkey_mode"])
        self.assertFalse(run.call_args.kwargs["stt_enabled"])
        self.assertFalse(run.call_args.kwargs["tts_enabled"])
        self.assertTrue(run.call_args.kwargs["witchcraft_enabled"])


if __name__ == "__main__":
    unittest.main()
