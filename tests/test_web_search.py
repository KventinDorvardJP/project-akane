"""Service-free search checks: real SDK serialization, in-memory HTTP only."""
from contextlib import ExitStack, redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx2
from openai import OpenAI

import config
from backend_errors import BackendError
from controls import ControlState, TurnControl, TurnCancelled
import metrics
from openai_backend import OpenAIBackend
from source_output import write_sources
from subtitles import write_subtitle
from turns import TurnProcessor
from web_search import force_search, response_sources, search_requests, search_actions


URL = "https://example.invalid/character"


def payload(*, search=True, citation=True, consulted=True, status="completed", calls=None):
    content = {"type": "output_text", "text": json.dumps({"ja": "確認しました。", "en": "The identity was checked."}),
               "annotations": ([{"type": "url_citation", "url": URL, "title": "Character reference",
                                  "start_index": 0, "end_index": 5}] if citation and search else [])}
    result = {"id": "resp_search", "object": "response", "created_at": 0,
              "model": "gpt-6-luna", "status": status,
              "output": [{"type": "message", "id": "msg_search", "role": "assistant",
                          "status": "completed", "content": [content]}],
              "usage": {"input_tokens": 200, "input_tokens_details": {"cached_tokens": 100},
                        "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 10}, "total_tokens": 250}}
    if search:
        result["output"].insert(0, {"type": "web_search_call", "id": "ws_test", "status": "completed",
                                    "action": {"type": "search", "query": "character identity",
                                               "sources": [{"type": "url", "url": URL}] if consulted else []}})
    if calls is not None:
        result["tool_usage"] = {"web_search": {"num_requests": calls}}
    return result


class SearchAdapterTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in {"OPENAI_MODEL": "gpt-6-luna", "OPENAI_WEB_SEARCH_ENABLED": True,
                            "OPENAI_WEB_SEARCH_MAX_CALLS": 2, "OPENAI_WEB_SEARCH_USD_PER_CALL": 0.01,
                            "OPENAI_SESSION_BUDGET_USD": 0.50}.items():
            stack.enter_context(patch.object(config, name, value))

    def backend(self, handler):
        self.requests = []
        def transport(request):
            self.requests.append(json.loads(request.content))
            body = handler(self.requests[-1]) if callable(handler) else handler
            return httpx2.Response(200, json=body)
        client = OpenAI(api_key="test-key", max_retries=0,
                        http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))
        self.addCleanup(client.close)
        return OpenAIBackend(client)

    def test_identity_typo_forces_hosted_lookup_and_preserves_schema(self):
        backend = self.backend(payload())
        messages = [{"role": "user", "content": "Who is Rean Schwarzerr?"}]
        result = json.loads(backend(messages))
        request = self.requests[0]
        self.assertEqual(request["model"], "gpt-6-luna")
        self.assertEqual(request["tool_choice"], {"type": "web_search"})
        self.assertEqual(request["tools"], [{"type": "web_search", "search_context_size": "low"}])
        self.assertEqual(request["max_tool_calls"], 2)
        self.assertIn("web_search_call.action.sources", request["include"])
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertEqual(set(result), {"ja", "en"})
        self.assertEqual(messages, request["input"])
        self.assertIn("misspellings", request["instructions"])
        self.assertEqual(backend.last_sources[0]["kind"], "cited")
        self.assertAlmostEqual(backend.last_usage["estimated_api_cost_usd"], 0.010036)

    def test_ordinary_conversation_can_skip_search_at_no_tool_fee(self):
        backend = self.backend(payload(search=False))
        backend([{"role": "user", "content": "Hello Akane"}])
        self.assertEqual(self.requests[0]["tool_choice"], "auto")
        self.assertEqual(backend.last_sources, [])
        self.assertEqual(backend.last_usage["web_search_calls"], 0)
        self.assertEqual(backend.last_usage["estimated_search_cost_usd"], 0)

    def test_required_search_without_tool_execution_withholds_answer(self):
        backend = self.backend(payload(search=False))
        with self.assertRaisesRegex(BackendError, "not performed"):
            backend([{"role": "user", "content": "/search character identity"}])
        self.assertGreater(backend.estimated_session_usd, 0)  # A withheld response can still bill tokens.

    def test_search_without_any_links_withholds_answer(self):
        backend = self.backend(payload(citation=False, consulted=False))
        with self.assertRaisesRegex(BackendError, "no usable source"):
            backend([{"role": "user", "content": "Who is this character?"}])
        self.assertEqual(backend.last_sources, [])
        self.assertEqual(backend.last_usage["web_search_calls"], 1)

    def test_retrieved_pages_are_not_mislabelled_as_citations(self):
        backend = self.backend(payload(citation=False))
        backend([{"role": "user", "content": "Who is this character?"}])
        self.assertEqual(backend.last_sources[0]["kind"], "consulted")

    def test_reported_request_count_is_used_for_fee(self):
        backend = self.backend(payload(calls=2))
        backend([])
        self.assertEqual(backend.last_usage["search_usage_source"], "reported")
        self.assertEqual(backend.last_usage["web_search_calls"], 2)
        self.assertAlmostEqual(backend.last_usage["estimated_search_cost_usd"], 0.02)

    def test_open_and_find_are_not_counted_as_new_searches(self):
        body = payload()
        for action in ("open_page", "find_in_page"):
            body["output"].insert(1, {"type": "web_search_call", "id": "ws_" + action,
                                      "status": "completed", "action": {"type": action, "url": URL, "pattern": "name"}})
        backend = self.backend(body)
        backend([])
        self.assertEqual(backend.last_usage["web_search_calls"], 1)

    def test_incomplete_output_retains_tool_fee_and_blocks_next_request(self):
        backend = self.backend(payload(status="incomplete"))
        with patch.object(config, "OPENAI_SESSION_BUDGET_USD", 0.01):
            with self.assertRaisesRegex(BackendError, "did not complete"):
                backend([])
            with self.assertRaisesRegex(BackendError, "budget has been reached"):
                backend([])
        self.assertEqual(len(self.requests), 1)
        self.assertGreater(backend.estimated_session_usd, 0.01)

    def test_remaining_budget_reduces_tool_limit_and_rejects_when_too_small(self):
        backend = self.backend(payload(search=False))
        with patch.object(config, "OPENAI_SESSION_BUDGET_USD", 0.015):
            backend([])
        self.assertEqual(self.requests[0]["max_tool_calls"], 1)
        backend.estimated_session_usd = 0.495
        with self.assertRaisesRegex(BackendError, "budget remains"):
            backend([])
        self.assertEqual(len(self.requests), 1)

    def test_disabled_search_omits_tools_and_rejects_explicit_command(self):
        backend = self.backend(payload(search=False))
        with patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", False):
            backend([])
            self.assertNotIn("tools", self.requests[0])
            with self.assertRaisesRegex(BackendError, "disabled"):
                backend([{"role": "user", "content": "/search something"}])
        self.assertEqual(len(self.requests), 1)

    def test_invalid_search_config_rejects_before_network(self):
        backend = self.backend(payload())
        for key, value in (("OPENAI_WEB_SEARCH_MAX_CALLS", 0), ("OPENAI_WEB_SEARCH_MAX_CALLS", True),
                           ("OPENAI_WEB_SEARCH_USD_PER_CALL", -1), ("OPENAI_WEB_SEARCH_USD_PER_CALL", float("nan"))):
            with self.subTest(key=key, value=value), patch.object(config, key, value):
                with self.assertRaises(BackendError):
                    backend([])
        self.assertEqual(self.requests, [])

    def test_sources_do_not_leak_into_a_later_failed_turn(self):
        count = []
        def handler(request):
            count.append(1)
            return payload() if len(count) == 1 else payload(status="incomplete")
        backend = self.backend(handler)
        backend([])
        self.assertTrue(backend.last_sources)
        with self.assertRaises(BackendError):
            backend([])
        self.assertEqual(backend.last_sources, [])

    def test_citation_markup_is_not_sent_to_speech(self):
        body = payload()
        body["output"][-1]["content"][0]["text"] = json.dumps({
            "ja": "確認済み。citeturn0search0", "en": f"See [the reference]({URL})."})
        result = json.loads(self.backend(body)([]))
        self.assertEqual(result, {"ja": "確認済み。", "en": "See the reference."})


class SearchMetadataTests(unittest.TestCase):
    def test_query_metadata_supports_singular_plural_and_missing_without_inventing_queries(self):
        bodies = [{"type": "search", "query": "WoW Forever release date"},
                  {"type": "search", "queries": ["WoW Forever launch", "WoW Forever release"]},
                  {"type": "search"}]
        result = search_actions({"output": [{"type": "web_search_call", "action": action} for action in bodies]})
        self.assertEqual(result, [
            {"queries": ["WoW Forever release date"], "truncated": False},
            {"queries": ["WoW Forever launch", "WoW Forever release"], "truncated": False},
            {"queries": None, "truncated": False}])

    def test_query_metadata_bounds_and_cleans_provider_strings_without_dumping_pages(self):
        action = {"type": "search", "query": "  ゲーム\n発売\x00日  ",
                  "queries": ["ゲーム 発売 日", "x" * 2048, None, {"page": "PRIVATE PAGE"},
                              *[f"query {i}" for i in range(20)]],
                  "sources": [{"text": "PRIVATE PAGE"}]}
        result = search_actions({"output": [{"type": "web_search_call", "action": action}]})
        self.assertTrue(result[0]["truncated"])
        self.assertLessEqual(len(result[0]["queries"]), 8)
        self.assertEqual(result[0]["queries"][0], "ゲーム 発売 日")
        self.assertEqual(len(result[0]["queries"][1]), 1024)
        self.assertNotIn("PRIVATE PAGE", json.dumps(result))
        self.assertNotIn("\\u0000", json.dumps(result))

    def test_query_metadata_skips_nonsearch_actions_and_limits_actions(self):
        output = [{"type": "web_search_call", "action": {"type": kind, "query": "not a search"}}
                  for kind in ("open_page", "find_in_page", "unknown")]
        output.append({"type": "message", "query": "also not a search"})
        self.assertEqual(search_actions({"output": output}), [])
        output += [{"type": "web_search_call", "action": {"type": "search"}} for _ in range(100)]
        self.assertEqual(len(search_actions({"output": output})), 10)

    def test_identity_policy_uses_latest_user_message_only(self):
        for text in ("Who's Rean?", "Who is Rean Schwarzerr?", "Кто такой Рин?", "リィンって誰？", "/search Rean"):
            self.assertTrue(force_search([{"role": "user", "content": text}]), text)
        self.assertFalse(force_search([{"role": "user", "content": "Who is Rean?"},
                                       {"role": "assistant", "content": "Who is Rean?"},
                                       {"role": "user", "content": "Hi again"}]))
        self.assertFalse(force_search([{"role": "user", "content": "Who are you?"}]))
        self.assertTrue(force_search([{"role": "user", "content": "/search Who are you?"}]))

    def test_unknown_tool_accounting_is_not_invented(self):
        self.assertEqual(search_requests({"output": [{"type": "web_search_call", "action": {"type": "new_action"}}]}, True),
                         (None, "unknown"))
        self.assertEqual(search_requests({}, True), (None, "unknown"))

    def test_reported_fee_count_cannot_undercut_observed_searches(self):
        self.assertEqual(search_requests(payload(calls=0), True), (1, "reported"))

    def test_url_extraction_deduplicates_and_rejects_non_web_links(self):
        body = payload()
        annotations = body["output"][-1]["content"][0]["annotations"]
        for url in (URL, "javascript:alert(1)", "file:///secret", "https://user:pass@example.com", "https://bad.invalid/\nheader"):
            annotations.append({"type": "url_citation", "url": url, "title": "bad"})
        self.assertEqual(len(response_sources(body)), 1)


class SearchOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = [{"url": URL, "title": "Reference <script>bad</script>", "kind": "cited"}]

    def processor(self, backend, enabled=False):
        return TurnProcessor(self.root, "personality", backend, tts_enabled=enabled,
                             synthesize=Mock(return_value=b"wav"), play=Mock(return_value=True),
                             write_caption=write_subtitle)

    def test_citation_page_has_inline_clickable_links_and_escaped_content(self):
        write_sources(self.root, {"en": "Answer <script>bad</script>", "sources": self.sources})
        page = (self.root / "runtime" / "sources.html").read_text(encoding="utf-8")
        self.assertIn(f'href="{URL}"', page)
        self.assertIn(">[1]</a>", page)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_only_plain_japanese_reaches_tts_and_normal_playback_keeps_sources(self):
        def backend(messages):
            return json.dumps({"ja": "確認済み。", "en": "Checked."})
        backend.last_sources = self.sources
        processor = self.processor(backend, enabled=True)
        with redirect_stdout(io.StringIO()), patch.object(config, "SUBTITLE_MIN_SECONDS", 0):
            self.assertTrue(processor.respond("Who is this?"))
        processor.synthesize.assert_called_once_with("確認済み。")
        self.assertEqual((self.root / "runtime" / "subtitle_en.txt").read_text(), "")
        self.assertTrue(json.loads((self.root / "runtime" / "sources.json").read_text())["sources"])
        with redirect_stdout(io.StringIO()):
            processor.reset()
        self.assertEqual(json.loads((self.root / "runtime" / "sources.json").read_text())["sources"], [])

    def test_stop_clears_sources_and_prevents_late_publication(self):
        processor = self.processor(Mock())
        state = ControlState()
        control = state.begin(Mock())
        control.show_caption(processor.update_sources, {"en": "Checked", "sources": self.sources})
        state.stop(processor.update_subtitle)
        with self.assertRaises(TurnCancelled):
            control.show_caption(processor.update_sources, {"en": "Late", "sources": self.sources})
        self.assertEqual(json.loads((self.root / "runtime" / "sources.json").read_text())["sources"], [])

    def test_cancelled_search_records_fee_without_publishing_sources(self):
        control = TurnControl()
        def backend(messages):
            control.cancel.set()
            return '{"ja":"確認済み","en":"Checked"}'
        backend.last_sources = self.sources
        backend.last_usage = {"provider": "openai", "web_search_calls": 1,
                              "estimated_search_cost_usd": 0.01, "estimated_api_cost_usd": 0.011}
        processor = self.processor(backend)
        with redirect_stdout(io.StringIO()):
            self.assertFalse(processor.respond("Who is this?", control=control))
        self.assertEqual(json.loads((self.root / "runtime" / "sources.json").read_text())["sources"], [])
        with (self.root / "runtime" / "timings.csv").open(newline="") as source:
            row = next(csv.DictReader(source))
        self.assertEqual(row["web_search_calls"], "1")
        self.assertEqual(row["estimated_search_cost_usd"], "0.01")

    def test_explicit_search_cannot_silently_use_local_backend(self):
        def local_reply(messages):
            raise AssertionError("Local LLM must not pretend to search")
        processor = self.processor(local_reply)
        with redirect_stdout(io.StringIO()) as output:
            self.assertFalse(processor.respond("/search Who is Rean?"))
        self.assertIn("OpenAI conversation backend", output.getvalue())
        self.assertEqual(len(processor.messages), 1)

    def test_previous_usage_csv_migrates_with_original_backup(self):
        path = self.root / "timings.csv"
        with path.open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=metrics.PRE_SEARCH_FIELDS)
            writer.writeheader()
            writer.writerow({"status": "ok", "estimated_api_cost_usd": 0.001})
        original = path.read_bytes()
        metrics.append_timing(path, {"status": "ok", "web_search_calls": 1})
        with path.open(newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["estimated_api_cost_usd"], "0.001")
        self.assertEqual(rows[1]["web_search_calls"], "1")
        self.assertEqual(next(self.root.glob("timings.legacy.*.csv")).read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
