"""Memory persistence and provider requests with no live API or audio."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import httpx2
from openai import OpenAI

import config
import main as app
from context_tools import TurnContextTools
from memory_commands import memory_command
from memory_store import MAX_RESULTS, MemoryStore
from openai_backend import OpenAIBackend
from subtitles import write_subtitle
from test_context_tools import call, response
from turns import TurnProcessor
from window_session import WindowSession


class MemoryTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "runtime" / "memory.sqlite3"

    def test_memory_survives_reopen_and_updates_the_search_index(self):
        store = MemoryStore(self.path)
        identifier = store.add("event", "Wiped on Rufus.", game="Cold Steel III", event_date="2026-11-25",
                               source_session="local-log-identifier")
        store.add("event", "Rufus mentioned over voice chat.", game="Other", event_date="2026-11-26")
        store.add("fact", "スポイラーはなし。")
        store = MemoryStore(self.path)
        match, = store.search("rufus", game="COLD STEEL III", since="2026-11-24")
        self.assertEqual((match["id"], match["kind"], match["event_date"]), (identifier, "event", "2026-11-25"))
        self.assertEqual(match["source"], "user-entered")
        self.assertEqual(match["source_session"], "local-log-identifier")
        self.assertEqual(store.search("rufus", game="Cold Steel III", since="2026-11-26"), [])
        self.assertEqual(store.search("スポイラー")[0]["text"], "スポイラーはなし。")
        self.assertTrue(store.correct(identifier, "Wiped on Arianrhod."))
        self.assertEqual(store.search("Rufus", game="Cold Steel III"), [])
        self.assertEqual(store.search("Arianrhod")[0]["id"], identifier)
        self.assertTrue(store.forget(identifier))
        self.assertEqual(MemoryStore(self.path).search("Arianrhod"), [])

    def test_user_can_browse_and_edit_entries_beyond_lunas_result_limit(self):
        store = MemoryStore(self.path)
        for index in range(12):
            memory_command(store, f"/remember fact Boss note {index}")
        first = memory_command(store, "/memories boss")
        self.assertIn("/memories --page 2 boss", first)
        second = memory_command(store, "/memories --page 2 boss")
        self.assertIn("#1 · fact", second)
        self.assertIn("Corrected #1", memory_command(store, "/correct 1 Boss note corrected"))
        self.assertIn("Forgot #1", memory_command(store, "/forget 1"))
        self.assertEqual(len(store.search("boss")), MAX_RESULTS)

    def test_tool_is_read_only_bounded_and_ignores_match_operators(self):
        store = MemoryStore(self.path)
        for index in range(6):
            store.add("fact", f"Встреча {index}", source_session="private-session")
        arguments = json.dumps({"query": 'ВСТРЕЧА " (', "game": "", "since": ""})
        self.assertEqual(TurnContextTools().execute("search_memory", arguments)[1], "disabled")
        tools = TurnContextTools(memory=store)
        self.assertEqual([tool["name"] for tool in tools.definitions()], ["search_memory"])
        raw, status = tools.execute("search_memory", arguments)
        self.assertEqual(status, "available")
        self.assertEqual(len(json.loads(raw)["matches"]), MAX_RESULTS)
        self.assertNotIn("private-session", raw)
        self.assertEqual(tools.execute("search_memory", arguments)[1], "limit_reached")
        for bad in ({"query": "", "game": "", "since": "yesterday"}, {"query": "", "path": ".env"}):
            self.assertEqual(TurnContextTools(memory=store).execute("search_memory", json.dumps(bad))[1], "invalid_request")
        self.assertEqual(tools.execute("forget", "{\"id\":1}")[1], "invalid_request")

    def test_commands_are_local_and_only_the_control_window_can_write_from_gui(self):
        reply = Mock(side_effect=AssertionError("No model call expected"))
        processor = TurnProcessor(self.root, "persona", reply, tts_enabled=False,
                                  synthesize=Mock(), play=Mock(), write_caption=write_subtitle)
        processor.memory = MemoryStore(self.path)
        session = WindowSession(processor, Mock(), Mock())
        self.addCleanup(session.cleanup)
        with redirect_stdout(io.StringIO()):
            self.assertTrue(session.request("text", "/remember event [Cold Steel III] Left the campus."))
            session.tick()
            self.assertFalse(session.bridge_request("text", "/forget 1"))
            self.assertTrue(session.request("text", "/memories campus"))
            session.tick()
        self.assertEqual(len(processor.memory.search("campus")), 1)
        self.assertEqual(session.model.messages[-1]["role"], "memory")
        self.assertIn("Left the campus.", session.model.messages[-1]["text"])
        self.assertEqual(len(processor.messages), 1)
        reply.assert_not_called()

    def test_terminal_can_save_and_reopen_without_calling_provider(self):
        (self.root / "personality.txt").write_text("persona", encoding="utf-8")
        backend = Mock(side_effect=AssertionError("No model call expected"))
        for enabled, prompts in ((False, ["/remember Hidden note", "exit"]),
                                 (True, ["/remember Avoid spoilers", "exit"]),
                                 (True, ["/memories", "exit"])):
            with self.subTest(enabled=enabled, command=prompts[0]):
                with patch.object(config, "MEMORY_ENABLED", enabled), patch("builtins.input", side_effect=prompts), \
                        redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(app.main(backend, project_dir=self.root, stt_enabled=False, tts_enabled=False), 0)
                if not enabled:
                    self.assertFalse(self.path.exists())
        self.assertIn("Avoid spoilers", output.getvalue())
        backend.assert_not_called()

    def test_luna_only_receives_matching_memory_after_requesting_the_tool(self):
        store = MemoryStore(self.path)
        store.add("event", "Rufus wiped the party at the branch campus.", game="Cold Steel III",
                  event_date="2026-11-26", source_session="private-session")
        store.add("fact", "Private unrelated preference.")
        arguments = json.dumps({"query": "Rufus", "game": "Cold Steel III", "since": ""})
        tool_call = call("search_memory", arguments=arguments, identifier="call_memory")
        requests = []
        answer = json.dumps({"segments": [{"ja": "ルーファスだよ。", "en": "It was Rufus."}]})
        replies = [response(text=answer), response(output=[tool_call]), response(text=answer)]

        def transport(request):
            requests.append(json.loads(request.content))
            return httpx2.Response(200, json=replies[len(requests) - 1])

        log = Mock()
        with patch.object(config, "SUBTITLE_LANGUAGE", "en"), patch.object(config, "OPENAI_WEB_SEARCH_ENABLED", True), \
                patch.object(config, "OPENAI_SESSION_BUDGET_USD", None), \
                OpenAI(api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(transport))) as client:
            backend = OpenAIBackend(client, model="gpt-6-luna")
            backend.reply_with_context([{"role": "user", "content": "Hey"}], memory=store)
            self.assertEqual(backend.last_usage["provider_requests"], 1)
            backend.reply_with_context([{"role": "user", "content": "Who was that boss we fought last stream?"}],
                                       memory=store, log=log)
            self.assertEqual(backend.last_usage["provider_requests"], 2)
            self.assertEqual(backend.last_usage["local_tool_calls"], 1)
        for request in requests[:2]:
            self.assertEqual([tool["name"] for tool in request["tools"] if tool["type"] == "function"], ["search_memory"])
            self.assertNotIn("wiped the party", json.dumps(request))
        result, = [item for item in requests[2]["input"] if item.get("type") == "function_call_output"]
        self.assertIn("wiped the party", result["output"])
        self.assertNotIn("Private unrelated", result["output"])
        self.assertNotIn("private-session", result["output"])
        self.assertEqual(requests[1]["tool_choice"], "auto")
        self.assertFalse(any(tool.get("name") == "search_memory" for tool in requests[2].get("tools", [])))
        tool_event = next(entry for entry in log.call_args_list if entry.args == ("context_tool",))
        self.assertIsNone(tool_event.kwargs["selection"])
