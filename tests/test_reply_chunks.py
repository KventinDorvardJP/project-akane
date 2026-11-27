"""Paired chunk delivery with fake speech/audio and bounded synchronization."""
from contextlib import redirect_stdout
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import httpx2
from openai import OpenAI

import config
from controls import ControlState, TurnControl
from conversation import parse_bilingual_reply, parse_reply_segments, reply_schema, with_reply_instructions
import metrics
import ollama_backend
from openai_backend import OpenAIBackend
from reply_delivery import split_speech_text
from session_log import SessionLog
from subtitles import write_subtitle
from text_to_speech import SpeechSynthesisError
from turns import TurnProcessor


PARTS = [{"ja": "最初の話だよ。", "en": "Here is the first thought."},
         {"ja": "次の話もある。", "en": "Here is the second thought."},
         {"ja": "これで終わり。", "en": "This is the final thought."}]


class ContractTests(unittest.TestCase):
    def test_ollama_requests_russian_subtitles_without_switching_the_voice_language(self):
        part = {"ja": "やあ。", "ru": "Привет."}
        reply = json.dumps({"segments": [part]})
        response = Mock()
        response.json.return_value = {"message": {"content": reply}}
        with patch.object(config, "SUBTITLE_LANGUAGE", "ru"), patch.object(ollama_backend.requests, "post", return_value=response) as post:
            self.assertEqual(parse_reply_segments(ollama_backend.get_llm_reply([{"role": "user", "content": "Привет!"}])), [part])
            payload = post.call_args.kwargs["json"]
            pair = payload["format"]["properties"]["segments"]["items"]
            self.assertEqual(pair["required"], ["ja", "ru"])
            self.assertEqual(set(pair["properties"]), {"ja", "ru"})
            self.assertIn("Always write ja in natural Japanese", payload["messages"][0]["content"])
            self.assertIn("idiomatic spoken Russian", payload["messages"][0]["content"])
            self.assertEqual(parse_bilingual_reply(reply), ("やあ。", "Привет."))
            for invalid in (PARTS, [{**part, "en": "Hey."}]):
                with self.assertRaisesRegex(ValueError, "exactly ja, ru"):
                    parse_reply_segments(json.dumps({"segments": invalid}))

    def test_local_speech_split_preserves_text_and_avoids_breaking_latin_words(self):
        text = "まずクエストを確認するね。" * 6 + "「Brotherhood of Thieves」を続けよう。"
        pieces = split_speech_text(text, 40)
        self.assertEqual("".join(pieces), text)
        self.assertTrue(all(0 < len(piece) <= 40 for piece in pieces))
        self.assertGreater(len(pieces), 1)
        words = "あ" * 36 + " KoboldVermin を倒す。"
        pieces = split_speech_text(words, 40)
        self.assertEqual("".join(pieces), words)
        self.assertTrue(any("KoboldVermin" in piece for piece in pieces))
        punctuation_tail = "あ" * 80 + "。"
        pieces = split_speech_text(punctuation_tail, 80)
        self.assertEqual("".join(pieces), punctuation_tail)
        self.assertTrue(all(piece.strip("。！？、 .!?,") for piece in pieces))
        self.assertTrue(all(len(piece) <= 80 for piece in pieces))

    def test_pairs_and_full_reply_preserve_order(self):
        raw = json.dumps({"segments": PARTS}, ensure_ascii=False)
        self.assertEqual(parse_reply_segments(raw), PARTS)
        self.assertEqual(parse_bilingual_reply(raw), ("".join(p["ja"] for p in PARTS), " ".join(p["en"] for p in PARTS)))
        self.assertEqual(parse_reply_segments('{"ja":"やあ","en":"Hi"}'), [{"ja": "やあ", "en": "Hi"}])

    def test_rejects_empty_malformed_or_excessive_segments(self):
        for data in ({"segments": []}, {"segments": "bad"}, {"segments": [None]},
                     {"segments": [{"ja": "やあ"}]}, {"segments": [{"ja": " ", "en": "Hi"}]},
                     {"segments": [{"ja": "やあ", "en": "Hi", "other": 1}]},
                     {"segments": PARTS, "ja": "duplicate"}, {"segments": [PARTS[0]] * (config.REPLY_MAX_CHUNKS + 1)}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_reply_segments(json.dumps(data))

    def test_schema_bounds_and_personality_injection_do_not_mutate_history(self):
        messages = [{"role": "system", "content": "My existing persona"}, {"role": "user", "content": "Question"}]
        before = copy.deepcopy(messages)
        modified = with_reply_instructions(messages)
        self.assertEqual(messages, before)
        self.assertIn("My existing persona", modified[0]["content"])
        self.assertIn("matching English", modified[0]["content"])
        schema = reply_schema()
        self.assertEqual(schema["required"], ["segments"])
        self.assertEqual(schema["properties"]["segments"]["maxItems"], config.REPLY_MAX_CHUNKS)
        properties = schema["properties"]["segments"]["items"]["properties"]
        for language in ("ja", "en"):
            self.assertNotIn("maxLength", properties[language])
            self.assertIn("complete", properties[language]["description"].lower())
        self.assertIn("Never stop mid-name", modified[0]["content"])


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.capture = redirect_stdout(io.StringIO())
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)
        for name in ("SUBTITLE_MIN_SECONDS", "SUBTITLE_READING_CHARS_PER_SECOND"):
            setting = patch.object(config, name, 0)
            setting.start()
            self.addCleanup(setting.stop)
        self.captions, self.events = [], []

    def processor(self, *, parts=PARTS, synthesize=None, play=None, enabled=True, writer=None):
        def write(path, text):
            self.captions.append(text)
            write_subtitle(path, text)
        processor = TurnProcessor(self.root, "persona", lambda messages: json.dumps({"segments": parts}, ensure_ascii=False),
                                  tts_enabled=enabled, synthesize=synthesize or Mock(return_value=b"fake WAV"),
                                  play=play or Mock(return_value=True), write_caption=writer or write,
                                  sleep_function=Mock(), event_callback=lambda kind, **data: self.events.append((kind, data)))
        return processor

    def row(self):
        with (self.root / "runtime" / "timings.csv").open(encoding="utf-8", newline="") as source:
            return list(csv.DictReader(source))[-1]

    def test_synthesis_receives_parts_and_full_reply_is_retained_once(self):
        synthesize, play = Mock(return_value=b"fake WAV"), Mock(return_value=True)
        processor = self.processor(synthesize=synthesize, play=play)
        self.assertTrue(processor.respond("Question"))
        self.assertEqual([call.args[0] for call in synthesize.call_args_list], [p["ja"] for p in PARTS])
        self.assertEqual([text for text in self.captions if text], [p["en"] for p in PARTS])
        self.assertEqual(self.captions[-1], "")
        self.assertEqual(play.call_count, 3)
        replies = [data for kind, data in self.events if kind == "reply"]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["en"], " ".join(p["en"] for p in PARTS))
        self.assertEqual(json.loads(processor.messages[-1]["content"])["segments"], PARTS)
        row = self.row()
        self.assertEqual(row["spoken_chunks"], "3")
        self.assertEqual(row["generated_chunks"], "3")
        self.assertEqual(row["subtitle_pages"], "3")
        self.assertGreaterEqual(float(row["tts_seconds"]), float(row["first_tts_seconds"]))
        self.assertGreaterEqual(float(row["turn_seconds"]), float(row["ready_seconds"]))

    def test_prefetch_overlaps_playback_but_never_starts_part_three_early(self):
        playing_first, generating_second = threading.Event(), threading.Event()
        order = []
        def synthesize(text):
            order.append(("generate", text))
            if text == PARTS[1]["ja"]:
                generating_second.set()
                self.assertTrue(playing_first.wait(2))
            return text.encode("utf-8")
        def play(audio, control):
            text = audio.decode("utf-8")
            order.append(("play", text))
            if text == PARTS[0]["ja"]:
                playing_first.set()
                self.assertTrue(generating_second.wait(2))
                self.assertNotIn(("generate", PARTS[2]["ja"]), order)
            return True
        with patch.object(config, "TTS_PREFETCH_NEXT", True):
            self.assertTrue(self.processor(synthesize=synthesize, play=play).respond("test"))
        self.assertEqual([value for kind, value in order if kind == "play"], [p["ja"] for p in PARTS])

    def test_disabling_prefetch_makes_generation_and_playback_strictly_sequential(self):
        order = []
        def synthesize(text):
            order.append(("generate", text))
            return text
        def play(audio, control):
            order.append(("play", audio))
            return True
        with patch.object(config, "TTS_PREFETCH_NEXT", False):
            self.assertTrue(self.processor(synthesize=synthesize, play=play).respond("test"))
        self.assertEqual(order, [item for p in PARTS for item in (("generate", p["ja"]), ("play", p["ja"]))])

    def test_stop_during_prefetch_discards_next_audio_and_never_requests_third(self):
        state = ControlState()
        control = state.begin(None)
        second_entered, release = threading.Event(), threading.Event()
        called, played = [], []
        def synthesize(text):
            called.append(text)
            if text == PARTS[1]["ja"]:
                second_entered.set()
                self.assertTrue(release.wait(2))
            return text
        processor = None
        def play(audio, token):
            played.append(audio)
            self.assertTrue(second_entered.wait(2))
            state.stop(processor.update_subtitle)
            release.set()
            return True
        processor = self.processor(synthesize=synthesize, play=play)
        with patch.object(config, "TTS_PREFETCH_NEXT", True):
            self.assertFalse(processor.respond("test", control=control))
        self.assertEqual(called, [p["ja"] for p in PARTS[:2]])
        self.assertEqual(played, [PARTS[0]["ja"]])
        self.assertNotIn(PARTS[1]["en"], self.captions)
        self.assertEqual(self.captions[-1], "")
        self.assertEqual(self.row()["status"], "cancelled")
        self.assertFalse(any(t.name.startswith("Akane speech part") and t.is_alive() for t in threading.enumerate()))

    def test_second_part_failure_pages_only_remaining_text(self):
        def synthesize(text):
            if text == PARTS[1]["ja"]:
                raise SpeechSynthesisError("engine stopped")
            return text
        play = Mock(return_value=True)
        processor = self.processor(synthesize=synthesize, play=play)
        self.assertTrue(processor.respond("test"))
        self.assertEqual(play.call_count, 1)
        self.assertEqual([text for text in self.captions if text], [p["en"] for p in PARTS])
        self.assertEqual(self.captions[-1], PARTS[-1]["en"])
        self.assertEqual(self.row()["status"], "speech_unavailable")
        self.assertEqual(self.row()["spoken_chunks"], "1")

    def test_russian_subtitles_work_during_japanese_playback_and_wrong_language_fallback(self):
        parts = [
            {"ja": "VOICEVOXで話すよ。", "ru": "Сейчас скажу."},
            {"ja": "Я отвечаю по-русски.", "ru": "Давай поиграем."},
        ]
        synthesize = Mock(return_value=b"fake WAV")
        def play(audio, control):
            path = self.root / "runtime" / "subtitle_en.txt"
            self.assertEqual(path.read_text(encoding="utf-8"), parts[0]["ru"])
            return True
        processor = self.processor(parts=parts, synthesize=synthesize, play=play)
        processor.session_log = SessionLog(self.root)
        self.addCleanup(processor.session_log.close)
        processor.reply_function.last_sources = [{"url": "https://example.invalid", "title": "Example", "kind": "cited"}]
        with patch.object(config, "SUBTITLE_LANGUAGE", "ru"):
            self.assertTrue(processor.respond("Что будем делать?"))
        synthesize.assert_called_once_with(parts[0]["ja"])
        self.assertEqual([text for text in self.captions if text], [part["ru"] for part in parts])
        reply = next(data for kind, data in self.events if kind == "reply")
        self.assertNotIn("en", reply)
        self.assertEqual(reply["ru"], "Сейчас скажу. Давай поиграем.")
        self.assertEqual(self.row()["status"], "speech_unavailable")
        sources = json.loads((self.root / "runtime" / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(sources["ru"], reply["ru"])
        self.assertNotIn("en", sources)
        self.assertIn("RU: " + reply["ru"], processor.session_log.text_path.read_text(encoding="utf-8"))

    def test_cyrillic_in_japanese_field_is_blocked_with_default_english_subtitles(self):
        parts = [{"ja": "こんにちは。Привет!", "en": "Hello!"}]
        synthesize = Mock()
        self.assertTrue(self.processor(parts=parts, synthesize=synthesize).respond("Привет!"))
        synthesize.assert_not_called()
        self.assertEqual([text for text in self.captions if text], ["Hello!"])

    def test_no_speech_pages_the_reply_and_keeps_last_page(self):
        synthesize = Mock()
        processor = self.processor(synthesize=synthesize, enabled=False)
        self.assertTrue(processor.respond("test"))
        synthesize.assert_not_called()
        self.assertEqual([text for text in self.captions if text], [p["en"] for p in PARTS])
        self.assertEqual(self.captions[-1], PARTS[-1]["en"])

    def test_oversized_pair_is_spoken_in_bounded_pieces_under_one_complete_caption(self):
        parts = [{"ja": "あ" * (config.REPLY_CHUNK_JA_CHARS + 1), "en": "Word " * 100}]
        synthesize = Mock(return_value=b"fake WAV")
        processor = self.processor(parts=parts, synthesize=synthesize)
        self.assertTrue(processor.respond("test"))
        spoken = [call.args[0] for call in synthesize.call_args_list]
        self.assertEqual("".join(spoken), parts[0]["ja"])
        self.assertTrue(all(len(text) <= config.REPLY_CHUNK_JA_CHARS for text in spoken))
        self.assertEqual([text for text in self.captions if text], [parts[0]["en"].strip()])
        self.assertEqual(self.row()["status"], "ok")
        self.assertEqual(self.row()["reply_chunks"], "1")
        self.assertEqual(self.row()["speech_chunks"], "2")
        self.assertEqual(self.row()["spoken_chunks"], "2")
        self.assertEqual(len(processor.messages), 3)

    def test_long_english_caption_does_not_disable_short_japanese_speech(self):
        parts = [{"ja": "今は盗まれた本を集めよう。", "en": "Here is the full translated explanation. " * 8}]
        synthesize = Mock(return_value=b"fake WAV")
        processor = self.processor(parts=parts, synthesize=synthesize)
        self.assertTrue(processor.respond("test"))
        synthesize.assert_called_once_with(parts[0]["ja"])
        self.assertIn(parts[0]["en"].strip(), self.captions)
        self.assertEqual(self.row()["status"], "ok")

    def test_oversized_later_part_does_not_prevent_valid_earlier_pairs_from_playing(self):
        parts = PARTS[:1] + [{"ja": "次の文章を読むよ。" * 20, "en": "The next complete translation."}]
        synthesize = Mock(side_effect=lambda text: text)
        play = Mock(return_value=True)
        processor = self.processor(parts=parts, synthesize=synthesize, play=play)
        self.assertTrue(processor.respond("test"))
        self.assertEqual(play.call_args_list[0].args[0], PARTS[0]["ja"])
        self.assertEqual("".join(call.args[0] for call in play.call_args_list), "".join(part["ja"] for part in parts))
        self.assertEqual([text for text in self.captions if text], [part["en"] for part in parts])

    def test_failure_within_a_pair_keeps_its_full_caption_without_replaying_prior_audio(self):
        parts = [{"ja": "あ" * (config.REPLY_CHUNK_JA_CHARS * 2 + 1), "en": "One complete parent translation."}]
        requests, played = [], []
        def synthesize(text):
            requests.append(text)
            if len(requests) == 2:
                raise SpeechSynthesisError("engine stopped")
            return text
        processor = self.processor(parts=parts, synthesize=synthesize, play=lambda audio, token: played.append(audio) or True)
        self.assertTrue(processor.respond("test"))
        self.assertEqual(len(played), 1)
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.row()["status"], "speech_unavailable")
        self.assertEqual(self.row()["spoken_chunks"], "1")
        self.assertEqual(self.captions[-1], parts[0]["en"])

    def test_unbreakable_token_falls_back_at_its_parent_without_blocking_earlier_pairs(self):
        parts = PARTS[:1] + [{"ja": "A" * (config.REPLY_CHUNK_JA_CHARS + 1), "en": "An unbreakable name."}]
        synthesize = Mock(return_value=b"fake WAV")
        processor = self.processor(parts=parts, synthesize=synthesize)
        self.assertTrue(processor.respond("test"))
        synthesize.assert_called_once_with(PARTS[0]["ja"])
        self.assertEqual(self.row()["status"], "speech_unavailable")
        self.assertEqual(self.captions[-1], parts[1]["en"])

    def test_minimum_caption_hold_applies_once_per_parent_not_per_audio_piece(self):
        parts = [{"ja": "あ" * (config.REPLY_CHUNK_JA_CHARS * 2 + 1), "en": "One parent translation."}]
        processor = self.processor(parts=parts)
        with patch.object(config, "SUBTITLE_MIN_SECONDS", 2):
            self.assertTrue(processor.respond("test"))
        processor.sleep.assert_called_once()
        self.assertLessEqual(processor.sleep.call_args.args[0], 2)
        self.assertEqual(self.row()["spoken_chunks"], "3")

    def test_piece_logs_keep_parent_identity_and_unique_audio_piece_indices(self):
        parts = [{"ja": "あ" * (config.REPLY_CHUNK_JA_CHARS + 1), "en": "One translation."}, PARTS[1]]
        processor = self.processor(parts=parts)
        log = Mock()
        processor.log = log
        self.assertTrue(processor.respond("test"))
        playback = [call.kwargs for call in log.call_args_list if call.args[0] == "playback_start"]
        self.assertEqual([data["part"] for data in playback], [1, 1, 2])
        self.assertEqual([data["speech_piece"] for data in playback], [1, 2, 3])

    def test_cancel_during_split_pair_discards_prefetch_and_keeps_parent_translation_alignment(self):
        parts = [{"ja": "あ" * (config.REPLY_CHUNK_JA_CHARS * 2 + 1), "en": "One complete translation."}, PARTS[1]]
        state = ControlState()
        control = state.begin(None)
        requests, played = [], []
        entered, release = threading.Event(), threading.Event()
        def synthesize(text):
            requests.append(text)
            if len(requests) == 2:
                entered.set()
                self.assertTrue(release.wait(2))
            return text
        processor = None
        def play(audio, token):
            played.append(audio)
            self.assertTrue(entered.wait(2))
            state.stop(processor.update_subtitle)
            release.set()
            return True
        processor = self.processor(parts=parts, synthesize=synthesize, play=play)
        self.assertFalse(processor.respond("test", control=control))
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(played), 1)
        self.assertNotIn(parts[1]["en"], self.captions)
        self.assertEqual(self.captions[-1], "")
        self.assertFalse(any(t.name.startswith("Akane speech part") and t.is_alive() for t in threading.enumerate()))

    def test_text_page_reading_hold_is_configured_and_cancellable(self):
        processor = self.processor(parts=PARTS[:2], enabled=False)
        with patch.object(config, "SUBTITLE_READING_CHARS_PER_SECOND", 10.0):
            self.assertTrue(processor.respond("test"))
        processor.sleep.assert_called_once_with(len(PARTS[0]["en"]) / 10.0)
        control = TurnControl()
        def write(path, text):
            write_subtitle(path, text)
            if text == PARTS[0]["en"]:
                control.cancel.set()
        processor = self.processor(enabled=False, writer=write)
        self.assertFalse(processor.respond("cancel", control=control))
        self.assertEqual((self.root / "runtime" / "subtitle_en.txt").read_text(), "")


class AdapterAndMetricsTests(unittest.TestCase):
    def test_openai_retains_russian_subtitles_through_citation_cleanup(self):
        part = {"ja": "資料を見てね。", "ru": "См. [источник](https://example.invalid)."}
        requests = []
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json={"id": "resp_ru", "object": "response", "created_at": 0,
                "model": "gpt-6-luna", "status": "completed", "output": [{"type": "message", "role": "assistant",
                "id": "msg_ru", "status": "completed", "content": [{"type": "output_text",
                "text": json.dumps({"segments": [part]}), "annotations": []}]}],
                "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}})
        with patch.object(config, "SUBTITLE_LANGUAGE", "ru"), patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", False), patch.object(config, "OPENAI_SESSION_BUDGET_USD", None):
            with OpenAI(api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(transport))) as client:
                reply = OpenAIBackend(client, model="gpt-6-luna")([{"role": "user", "content": "Есть источник?"}])
            self.assertEqual(parse_reply_segments(reply), [{**part, "ru": "См. источник."}])
        items = requests[0]["text"]["format"]["schema"]["properties"]["segments"]["items"]
        self.assertEqual(items["required"], ["ja", "ru"])
        self.assertEqual(set(items["properties"]), {"ja", "ru"})
        self.assertNotIn("English translation", requests[0]["instructions"])
        self.assertEqual(len(requests), 1)

    def test_previous_context_csv_migrates_with_unknown_historical_piece_count_blank(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "timings.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=metrics.PRE_SPEECH_FIELDS)
                writer.writeheader()
                writer.writerow({"status": "ok", "context_seconds": 0.01, "reply_chunks": 2, "spoken_chunks": 2})
            original = path.read_bytes()
            metrics.append_timing(path, {"status": "ok", "reply_chunks": 2, "speech_chunks": 4, "spoken_chunks": 4})
            with path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(rows[0]["speech_chunks"], "")
            self.assertEqual(rows[0]["context_seconds"], "0.01")
            self.assertEqual(rows[1]["speech_chunks"], "4")
            self.assertEqual(next(Path(folder).glob("timings.legacy.*.csv")).read_bytes(), original)

    def test_full_quest_names_survive_generation_even_when_a_part_exceeds_speech_target(self):
        parts = [{"ja": "前の返事は古い情報についてだよ。今の情報では「Nibbled-On Book」は目標達成済みで、"
                         "「Kobold Camp Cleanup」はコボルトを10匹中1匹倒しているよ。",
                  "en": 'My previous reply used old data. The current snapshot shows "Nibbled-On Book" complete, '
                        'and "Kobold Camp Cleanup" at 1/10 Kobold Vermin slain.'}]
        self.assertGreater(len(parts[0]["ja"]), config.REPLY_CHUNK_JA_CHARS)
        requests = []
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json={"id": "resp_full_names", "object": "response", "created_at": 0,
                "model": "gpt-6-luna", "status": "completed", "output": [{"type": "message", "role": "assistant",
                "id": "msg_names", "status": "completed", "content": [{"type": "output_text",
                "text": json.dumps({"segments": parts}), "annotations": []}]}],
                "usage": {"input_tokens": 100, "output_tokens": 150, "total_tokens": 250}})
        with patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", False), patch.object(config, "OPENAI_SESSION_BUDGET_USD", None):
            with OpenAI(api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(transport))) as client:
                reply = OpenAIBackend(client, model="gpt-6-luna")([{"role": "user", "content": "Please elaborate."}])
        self.assertEqual(parse_reply_segments(reply), parts)
        properties = requests[0]["text"]["format"]["schema"]["properties"]["segments"]["items"]["properties"]
        self.assertNotIn("maxLength", properties["ja"])
        self.assertNotIn("maxLength", properties["en"])
        self.assertEqual(len(requests), 1)

    def test_real_sdk_keeps_segment_boundaries_after_citation_cleanup(self):
        requests = []
        parts = copy.deepcopy(PARTS[:2])
        parts[0]["ja"] += "citetest"
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json={"id": "resp_parts", "object": "response", "created_at": 0,
                "model": "gpt-6-luna", "status": "completed", "output": [{"type": "message", "role": "assistant",
                "id": "msg_parts", "status": "completed", "content": [{"type": "output_text", "text": json.dumps({"segments": parts}), "annotations": []}]}],
                "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}})
        with patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", False), patch.object(config, "OPENAI_SESSION_BUDGET_USD", None):
            with OpenAI(api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(transport))) as client:
                reply = OpenAIBackend(client, model="gpt-6-luna")([{"role": "user", "content": "Explain"}])
        self.assertEqual(parse_reply_segments(reply), PARTS[:2])
        self.assertEqual(len(requests), 1)
        self.assertIn("matching English", requests[0]["instructions"])
        self.assertEqual(requests[0]["text"]["format"]["schema"]["required"], ["segments"])

    def test_previous_search_csv_is_migrated_without_losing_old_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "timings.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=metrics.PRE_CHUNK_FIELDS)
                writer.writeheader()
                writer.writerow({"status": "ok", "ready_seconds": 17.7, "web_search_calls": 1})
            before = path.read_bytes()
            metrics.append_timing(path, {"status": "ok", "reply_chunks": 3, "spoken_chunks": 3})
            with path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(rows[0]["ready_seconds"], "17.7")
            self.assertEqual(rows[0]["reply_chunks"], "")
            self.assertEqual(rows[1]["spoken_chunks"], "3")
            self.assertEqual(next(Path(folder).glob("timings.legacy.*.csv")).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
