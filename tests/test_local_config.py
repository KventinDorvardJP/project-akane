"""Check public defaults and personal JSON settings without touching real config."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest

import config


class LocalConfigTests(unittest.TestCase):
    def load(self, local=None, *, raw=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.py"
            path.write_text(Path(config.__file__).read_text(encoding="utf-8"), encoding="utf-8")
            if raw is not None or local is not None:
                path.with_name("config.local.json").write_text(raw if raw is not None else json.dumps(local), encoding="utf-8")
            return runpy.run_path(str(path))

    def test_public_checkout_uses_default_microphone(self):
        self.assertIsNone(self.load()["MIC_INPUT_DEVICE"])

    def test_personal_settings_override_only_requested_values(self):
        settings = self.load({"MIC_INPUT_DEVICE": 7, "LLM_BACKEND": "openai"})
        self.assertEqual(settings["MIC_INPUT_DEVICE"], 7)
        self.assertEqual(settings["LLM_BACKEND"], "openai")
        self.assertEqual(settings["OPENAI_MODEL"], "gpt-6-luna")

    def test_null_can_restore_optional_default(self):
        self.assertIsNone(self.load({"MIC_INPUT_DEVICE": None})["MIC_INPUT_DEVICE"])

    def test_unknown_names_and_credentials_are_not_accepted(self):
        for local in ({"MIC_INPUT_DEVCE": 1}, {"OPENAI_API_KEY": "placeholder"}):
            with self.subTest(local=local), self.assertRaisesRegex(ValueError, "unknown setting"):
                self.load(local)

    def test_invalid_json_fails_with_a_clear_message(self):
        with self.assertRaisesRegex(ValueError, "JSON syntax"):
            self.load(raw="not JSON")

    def test_non_object_settings_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            self.load(raw="[]")


if __name__ == "__main__":
    unittest.main()
