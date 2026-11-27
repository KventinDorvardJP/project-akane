"""Local context selection and real SDK tool loops over in-memory HTTP only."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
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
from context_tools import TurnContextTools, context_free_messages, MAX_TOOL_CALLS
from controls import TurnControl, TurnCancelled
from conversation import with_japanese_reference, with_wow_reference
from openai_backend import OpenAIBackend
from session_log import SessionLog
from subtitles import write_subtitle
from turns import TurnProcessor
import metrics


def snapshot():
    return {"source": "Witchcraft / WoW Forever", "snapshotAt": 1000000,
            "session": {"epoch": "do-not-upload-session"}, "domains": {
        "player": {"status": "available", "ageSeconds": 4, "data": {
            "name": "PrivateCharacter", "class": "Paladin", "level": 4,
            "location": {"zone": "Elwynn Forest", "subzone": "Northshire", "mapId": 1429}}},
        "progress": {"status": "available", "ageSeconds": 6, "data": {
            "money": 12345, "xp": 10, "xpMax": 400, "rested": None,
            "situation": {"combat": False, "groupSize": 0}}},
        "quests": {"status": "available", "ageSeconds": 5, "data": {
            "trackedCount": 2, "truncated": False, "quests": [
                {"id": 7, "title": "Kobold Camp Cleanup", "complete": False, "truncated": False,
                 "objectives": [{"text": "Kobold Vermin slain", "fulfilled": 6,
                                 "required": 10, "finished": False}]},
                {"id": 8, "title": "UnrelatedQuest", "complete": True, "truncated": False,
                 "objectives": [{"text": "Secret objective", "fulfilled": 1, "required": 1}]}]}}}}


def args(fields=None, query="", detail="objectives"):
    return json.dumps({"fields": fields or ["quests"], "quest_query": query, "quest_detail": detail})


def call(name="get_wow_context", arguments=None, identifier="call_context"):
    return {"type": "function_call", "id": "fc_" + identifier, "call_id": identifier,
            "name": name, "arguments": arguments if arguments is not None else args(query="kobold")}


def response(output=None, text=None):
    if output is None:
        output = [{"id": "msg_reply", "type": "message", "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "annotations": [], "text": text or json.dumps({
                       "segments": [{"ja": "あと4匹だよ。", "en": "Four kobolds left."}]})}]}]
    return {"id": "resp_test", "created_at": 0, "object": "response", "status": "completed",
            "model": "gpt-6-luna", "output": output,
            "usage": {"input_tokens": 200, "input_tokens_details": {"cached_tokens": 100},
                      "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 10}, "total_tokens": 250}}


def search(identifier="ws_context"):
    return {"type": "web_search_call", "id": identifier, "status": "completed", "action": {
        "type": "search", "query": "WoW facts", "sources": [
            {"type": "url", "url": "https://example.invalid/reference", "title": "Reference"}]}}


class ContextSelectionTests(unittest.TestCase):
    def result(self, tools, arguments, name="get_wow_context"):
        encoded, status = tools.execute(name, arguments)
        return json.loads(encoded)

    def test_selects_only_requested_fields_and_never_bridge_metadata(self):
        result = self.result(TurnContextTools(snapshot()), args(["class", "zone"]))
        self.assertEqual(result["domains"]["player"]["data"], {"class": "Paladin", "zone": "Elwynn Forest"})
        encoded = json.dumps(result)
        for excluded in ("PrivateCharacter", "12345", "UnrelatedQuest", "do-not-upload-session"):
            self.assertNotIn(excluded, encoded)
        self.assertEqual(result["domains"]["player"]["ageSeconds"], 4)

    def test_quest_filter_matches_titles_or_objectives_and_summary_omits_counts(self):
        tools = TurnContextTools(snapshot())
        result = self.result(tools, args(query="VERMIN"))
        quests = result["domains"]["quests"]["data"]["quests"]
        self.assertEqual(len(quests), 1)
        self.assertEqual(quests[0]["objectives"][0]["fulfilled"], 6)
        result = self.result(tools, args(query="kobold", detail="summary"))
        self.assertNotIn("objectives", result["domains"]["quests"]["data"]["quests"][0])
        result = self.result(tools, args(query="no matching quest"))
        self.assertEqual(result["domains"]["quests"]["data"]["quests"], [])

    def test_stale_values_are_withheld_even_if_the_caller_supplies_them(self):
        data = snapshot()
        data["domains"]["quests"]["status"] = "stale"
        result = self.result(TurnContextTools(data), args())
        self.assertIsNone(result["domains"]["quests"]["data"])
        self.assertNotIn("Kobold", json.dumps(result))

    def test_mixed_domains_preserve_unknown_values_without_making_up_zero(self):
        result = self.result(TurnContextTools(snapshot()), args(["level", "rested", "combat"]))
        self.assertEqual(result["domains"]["player"]["data"], {"level": 4})
        self.assertEqual(result["domains"]["progress"]["data"], {"rested": None, "combat": False})

    def test_tools_are_opt_in_and_cannot_access_unselected_sources(self):
        self.assertEqual(TurnContextTools().definitions(), [])
        self.assertEqual(self.result(TurnContextTools(), args())["status"], "disabled")
        self.assertEqual(self.result(TurnContextTools(snapshot()), "{}", "get_japanese_reference")["status"], "disabled")
        tools = TurnContextTools(japanese="選んだ文。")
        self.assertEqual([tool["name"] for tool in tools.definitions()], ["get_japanese_reference"])
        self.assertEqual(self.result(tools, "{}", "get_japanese_reference")["text"], "選んだ文。")

    def test_inputs_are_frozen_and_invalid_requests_cannot_read_paths(self):
        data = snapshot()
        tools = TurnContextTools(data)
        data["domains"]["player"]["data"]["level"] = 99
        self.assertEqual(self.result(tools, args(["level"]))["domains"]["player"]["data"]["level"], 4)
        for bad in ("{", "null", "[]", args(["../../.env"]), args([True]), "x" * 4097,
                    json.dumps({"fields": ["name"], "file": ".env"})):
            with self.subTest(bad=bad[:40]):
                self.assertEqual(self.result(tools, bad)["status"], "invalid_request")
        self.assertEqual(self.result(tools, "{}", "read_file")["status"], "invalid_request")

    def test_legacy_attachments_are_removed_without_mutating_dialogue(self):
        content = with_wow_reference(with_japanese_reference("Explain", "古い文"), snapshot())
        messages = [{"role": "user", "content": content}, {"role": "assistant", "content": "Previous advice"}]
        self.assertEqual(context_free_messages(messages), [
            {"role": "user", "content": "Explain"}, {"role": "assistant", "content": "Previous advice"}])
        self.assertEqual(messages[0]["content"], content)

    def test_oversized_results_return_a_complete_error_instead_of_truncated_json(self):
        with patch("context_tools.MAX_RESULT_CHARS", 32):
            result = self.result(TurnContextTools(snapshot()), args())
        self.assertEqual(result["status"], "too_large")
        self.assertNotIn("Kobold", json.dumps(result))


class ContextLoopTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in {"OPENAI_MODEL": "gpt-6-luna", "OPENAI_WEB_SEARCH_ENABLED": False,
                            "OPENAI_SESSION_BUDGET_USD": 0.50, "OPENAI_WEB_SEARCH_MAX_CALLS": 2,
                            "OPENAI_WEB_SEARCH_USD_PER_CALL": 0.01}.items():
            stack.enter_context(patch.object(config, name, value))
        self.requests = []
        self.messages = [{"role": "system", "content": "Akane"}, {"role": "user", "content": "How many kobolds left?"}]

    def backend(self, bodies, after_request=None):
        bodies = iter(bodies)
        def handler(request):
            self.requests.append(json.loads(request.content))
            if after_request:
                after_request(len(self.requests))
            body = next(bodies)
            if isinstance(body, Exception):
                raise body
            return httpx2.Response(200, json=body)
        client = OpenAI(api_key="test-key", max_retries=0,
                        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
        self.addCleanup(client.close)
        return OpenAIBackend(client)

    def test_sdk_round_trip_sends_only_selected_data_and_sums_both_requests(self):
        reasoning = {"type": "reasoning", "id": "rs_context", "summary": [], "encrypted_content": "opaque-reasoning"}
        backend = self.backend([response([reasoning, call()]), response()])
        before = deepcopy(self.messages)
        backend.reply_with_context(self.messages, wow_context=snapshot(), japanese_context="未要求の文")
        first, second = self.requests
        self.assertNotIn("PrivateCharacter", json.dumps(first))
        self.assertNotIn("未要求の文", json.dumps(first, ensure_ascii=False))
        self.assertIn("reasoning.encrypted_content", first["include"])
        tool_result = next(item for item in second["input"] if item.get("type") == "function_call_output")
        self.assertEqual(tool_result["call_id"], "call_context")
        self.assertIn("Kobold", tool_result["output"])
        for excluded in ("UnrelatedQuest", "12345", "未要求の文", "PrivateCharacter"):
            self.assertNotIn(excluded, tool_result["output"])
        self.assertTrue(any(item.get("encrypted_content") == "opaque-reasoning" for item in second["input"]))
        self.assertNotIn("tools", second)  # No second local retrieval round.
        self.assertEqual(self.messages, before)
        self.assertEqual(backend.last_usage["provider_requests"], 2)
        self.assertEqual(backend.last_usage["local_tool_calls"], 1)
        self.assertEqual(backend.last_usage["input_tokens"], 400)
        self.assertEqual(backend.last_usage["cached_input_tokens"], 200)
        self.assertEqual(backend.last_usage["output_tokens"], 100)
        self.assertEqual(backend.last_usage["reasoning_tokens"], 20)
        self.assertAlmostEqual(backend.last_usage["estimated_api_cost_usd"], 0.000072)
        self.assertAlmostEqual(backend.estimated_session_usd, 0.000072)

    def test_no_tool_needed_is_one_request_without_uploading_either_source(self):
        backend = self.backend([response()])
        backend.reply_with_context(self.messages, wow_context=snapshot(), japanese_context="秘密の文")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(backend.last_usage["local_tool_calls"], 0)
        for private in ("PrivateCharacter", "Kobold Camp Cleanup", "秘密の文"):
            self.assertNotIn(private, json.dumps(self.requests, ensure_ascii=False))

    def test_parallel_requests_read_the_same_frozen_sources_and_disabled_tools_fail_closed(self):
        first = response([call(arguments=args(["class"])),
                          call("get_japanese_reference", "{}", "call_japanese")])
        data = snapshot()
        def mutate(number):
            data["domains"]["player"]["data"]["class"] = "ChangedLater"
        backend = self.backend([first, response()], mutate)
        backend.reply_with_context(self.messages, wow_context=data, japanese_context="最初の文。")
        results = [json.loads(item["output"]) for item in self.requests[1]["input"]
                   if item.get("type") == "function_call_output"]
        self.assertEqual(results[0]["domains"]["player"]["data"], {"class": "Paladin"})
        self.assertEqual(results[1]["text"], "最初の文。")

    def test_unadvertised_source_returns_disabled_and_no_values(self):
        backend = self.backend([response([call("get_japanese_reference", "{}")]), response()])
        backend.reply_with_context(self.messages, wow_context=snapshot())
        results = [item for item in self.requests[1]["input"] if item.get("type") == "function_call_output"]
        self.assertEqual(json.loads(results[0]["output"]), {"status": "disabled"})

    def test_cancelled_first_response_is_accounted_without_executing_or_continuing(self):
        control = TurnControl()
        backend = self.backend([response([call()])], lambda number: control.cancel.set())
        with self.assertRaises(TurnCancelled):
            backend.reply_with_context(self.messages, wow_context=snapshot(), control=control)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(backend.last_usage["input_tokens"], 200)
        self.assertEqual(backend.last_usage["local_tool_calls"], 0)
        self.assertEqual(backend.last_sources, [])

    def test_missing_usage_or_reached_budget_blocks_continuation(self):
        for missing in (True, False):
            with self.subTest(missing=missing):
                first = response([call()])
                if missing:
                    first.pop("usage")
                self.requests.clear()
                backend = self.backend([first])
                with patch.object(config, "OPENAI_SESSION_BUDGET_USD", 0.000001 if not missing else 0.5):
                    with self.assertRaises(BackendError):
                        backend.reply_with_context(self.messages, wow_context=snapshot())
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(backend.last_usage["provider_requests"], 1)
                if missing:
                    self.assertIsNone(backend.last_usage["input_tokens"])
                else:
                    self.assertGreater(backend.estimated_session_usd, 0)

    def test_excess_calls_duplicate_ids_and_a_second_round_are_rejected_without_retry(self):
        for first, bodies in ((response([call() for _ in range(MAX_TOOL_CALLS + 1)]), []),
                              (response([call(), call()]), []),
                              (response([call()]), [response([call(identifier="call_again")])])):
            self.requests.clear()
            backend = self.backend([first] + bodies)
            with self.assertRaises(BackendError):
                backend.reply_with_context(self.messages, wow_context=snapshot())
            self.assertEqual(len(self.requests), 1 + len(bodies))
            self.assertEqual(backend.last_sources, [])

    def test_search_limit_and_sources_are_shared_across_context_continuation(self):
        first = response([search(), call()])
        final = response()
        final["output"].insert(0, search("ws_second"))
        backend = self.backend([first, final])
        with patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", True):
            backend.reply_with_context(self.messages, wow_context=snapshot())
        self.assertEqual(self.requests[0]["max_tool_calls"], 2)
        self.assertEqual(self.requests[1]["max_tool_calls"], 1)
        self.assertEqual(backend.last_usage["web_search_calls"], 2)
        self.assertAlmostEqual(backend.last_usage["estimated_search_cost_usd"], 0.02)
        self.assertEqual(len(backend.last_sources), 1)

    def test_real_sdk_logs_exact_search_queries_by_request_but_not_page_contents(self):
        first = response([search(), call()])
        final = response()
        final["output"].insert(0, search("ws_second"))
        final["output"][0]["action"].pop("query")
        final["output"][0]["action"]["queries"] = ["WoW quest recipient", "モーガン ペストル"]
        final["output"][0]["action"]["sources"][0]["text"] = "PRIVATE PAGE CONTENT"
        backend = self.backend([first, final])
        log = Mock()
        with patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", True):
            backend.reply_with_context(self.messages, wow_context=snapshot(), log=log)
        searches = [item for item in log.call_args_list if item.args[0] == "web_search"]
        self.assertEqual([item.kwargs["request"] for item in searches], [1, 2])
        self.assertEqual(searches[0].kwargs["actions"][0]["queries"], ["WoW facts"])
        self.assertEqual(searches[1].kwargs["actions"][0]["queries"],
                         ["WoW quest recipient", "モーガン ペストル"])
        self.assertNotIn("PRIVATE PAGE CONTENT", str(searches))
        self.assertEqual(backend.last_usage["web_search_calls"], 2)

    def test_search_log_explicitly_marks_missing_queries_and_never_reuses_old_actions(self):
        first = response()
        first["output"].insert(0, search())
        first["output"][0]["action"].pop("query")
        backend = self.backend([first, response()])
        log = Mock()
        with patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", True):
            backend.reply_with_context(self.messages, log=log)
            backend.reply_with_context([{"role": "user", "content": "How are you?"}], log=log)
        searches = [item for item in log.call_args_list if item.args[0] == "web_search"]
        self.assertEqual(len(searches), 1)
        self.assertIsNone(searches[0].kwargs["actions"][0]["queries"])
        self.assertEqual(backend.last_usage["web_search_calls"], 0)
        self.assertEqual(backend.last_sources, [])

    def test_forced_search_is_not_repeated_and_exhausted_tool_budget_stays_exhausted(self):
        backend = self.backend([response([search(), call()]), response()])
        with patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", True), \
                patch.object(config, "OPENAI_WEB_SEARCH_MAX_CALLS", 1):
            backend.reply_with_context([{"role": "user", "content": "/search compare this quest"}], wow_context=snapshot())
        self.assertEqual(self.requests[0]["tool_choice"], {"type": "web_search"})
        self.assertNotIn("tools", self.requests[1])
        self.assertEqual(backend.last_usage["web_search_calls"], 1)

    def test_required_search_cannot_be_replaced_with_a_local_lookup(self):
        backend = self.backend([response([call()]), response()])
        with patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", True):
            with self.assertRaisesRegex(BackendError, "required web lookup"):
                backend.reply_with_context([{"role": "user", "content": "Who is this quest giver?"}], wow_context=snapshot())
        self.assertEqual(self.requests[1]["tool_choice"], {"type": "web_search"})

    def test_http_failure_on_continuation_retains_known_session_charge_and_marks_total_unknown(self):
        backend = self.backend([response([call()]), httpx2.ReadTimeout("offline test")])
        with self.assertRaises(BackendError):
            backend.reply_with_context(self.messages, wow_context=snapshot())
        self.assertEqual(len(self.requests), 2)
        self.assertGreater(backend.estimated_session_usd, 0)
        self.assertIsNone(backend.last_usage["estimated_api_cost_usd"])
        self.assertTrue(backend.unaccounted_usage)

    def test_whole_batch_result_budget_and_malformed_arguments_are_explicit(self):
        first = response([call(), call(arguments="{broken", identifier="call_bad")])
        backend = self.backend([first, response()])
        with patch("openai_backend.MAX_BATCH_RESULT_CHARS", 128):
            backend.reply_with_context(self.messages, wow_context=snapshot(), log=Mock())
        results = [json.loads(item["output"]) for item in self.requests[1]["input"]
                   if item.get("type") == "function_call_output"]
        self.assertEqual(results[0]["status"], "too_large")
        self.assertEqual(results[1]["status"], "too_large")
        self.assertNotIn("Kobold", json.dumps(results))

    def test_unknown_usage_stays_unknown_across_a_continuation_without_budget(self):
        first = response([call()])
        first.pop("usage")
        backend = self.backend([first, response()])
        with patch.object(config, "OPENAI_SESSION_BUDGET_USD", None):
            backend.reply_with_context(self.messages, wow_context=snapshot())
        self.assertEqual(len(self.requests), 2)
        self.assertIsNone(backend.last_usage["input_tokens"])
        self.assertIsNone(backend.last_usage["estimated_api_cost_usd"])
        self.assertGreater(backend.estimated_session_usd, 0)
        self.assertTrue(backend.unaccounted_usage)

    def test_turn_history_logs_metrics_and_next_question_do_not_retain_raw_tool_data(self):
        backend = self.backend([response([call()]), response(), response()])
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            root = Path(temporary)
            log = SessionLog(root)
            self.addCleanup(log.close)
            processor = TurnProcessor(root, "Akane", backend, tts_enabled=False, synthesize=Mock(),
                                      play=Mock(), write_caption=write_subtitle, session_log=log)
            self.assertTrue(processor.respond("How many kobolds?", wow_context=snapshot()))
            self.assertEqual(processor.messages[1]["content"], "How many kobolds?")
            self.assertIn("Four kobolds left", processor.messages[2]["content"])
            self.assertTrue(processor.respond("Explain your advice"))
            self.assertNotIn("Kobold Camp Cleanup", json.dumps(self.requests[2]))
            self.assertNotIn("function_call_output", json.dumps(self.requests[2]))
            processor.synthesize.assert_not_called()
            events = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
            tool = next(event for event in events if event["event"] == "context_tool")
            self.assertEqual(tool["data"]["name"], "get_wow_context")
            self.assertEqual(tool["data"]["status"], "observed")
            self.assertEqual(tool["data"]["selection"]["quest_query"], "kobold")
            self.assertTrue(tool["turn_id"])
            self.assertEqual(sum(event["event"] == "provider_usage" for event in events), 3)
            with (root / "runtime" / "timings.csv").open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(rows[0]["input_tokens"], "400")
            self.assertEqual(rows[0]["provider_requests"], "2")
            self.assertEqual(rows[0]["local_tool_calls"], "1")
            self.assertEqual(rows[1]["local_tool_calls"], "0")
            log.close()

    def test_cancelled_final_response_never_enters_history_gui_or_audio(self):
        control = TurnControl()
        backend = self.backend([response([call()]), response()],
                               lambda number: control.cancel.set() if number == 2 else None)
        with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
            events = Mock()
            processor = TurnProcessor(Path(temporary), "Akane", backend, tts_enabled=True,
                synthesize=Mock(), play=Mock(), write_caption=write_subtitle, event_callback=events)
            self.assertFalse(processor.respond("How many?", wow_context=snapshot(), control=control))
            self.assertEqual(len(processor.messages), 1)
            processor.synthesize.assert_not_called()
            processor.play.assert_not_called()
            self.assertFalse(any(item.args[0] == "reply" for item in events.call_args_list))
            self.assertEqual(backend.last_usage["input_tokens"], 400)

    def test_previous_timing_header_is_migrated_with_original_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "timings.csv"
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=metrics.PRE_TOOL_FIELDS)
                writer.writeheader()
                writer.writerow({"status": "ok", "speech_chunks": 2})
            original = path.read_bytes()
            metrics.append_timing(path, {"status": "ok", "provider_requests": 2, "local_tool_calls": 1})
            self.assertEqual(next(path.parent.glob("timings.legacy.*.csv")).read_bytes(), original)
            with path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(rows[0]["speech_chunks"], "2")
            self.assertEqual(rows[1]["provider_requests"], "2")


if __name__ == "__main__":
    unittest.main()
