"""No live capture, gameplay, audio or paid services. Binary fixtures + fake Lua APIs."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zlib

import config
from context_tools import TurnContextTools
from controls import TurnControl, TurnCancelled
import main as app
from stream_controls import StreamController
from subtitles import write_subtitle
from turns import TurnProcessor
from window_session import WindowSession
from wow_module import (WoWModule, DOMAINS, HEADER, PAYLOAD_BYTES, FRAME_BYTES,
                        decode_frame, pixels_to_frame, validate_record)


def sample(domain, **changes):
    data = {
        "player": {"name": "Quentin", "class": "Paladin", "level": 8,
                   "location": {"zone": "Elwynn Forest", "subzone": "Goldshire", "mapId": 1429}},
        "progress": {"money": 100, "xp": 10, "xpMax": 400, "rested": None, "situation": {
            "inInstance": False, "instanceType": None, "instanceName": None, "difficulty": None,
            "groupSize": 0, "role": "NONE", "dead": False, "ghost": False, "combat": False}},
        "quests": {"quests": [], "trackedCount": 0, "truncated": False},
        "professions": {"professions": [{"id": 185, "name": "Cooking", "rank": 25, "maxRank": 75}], "truncated": False},
        "inventory": {"items": [{"id": 6291, "name": "Raw Brilliant Smallfish", "count": 5, "bag": 0, "slot": 1,
                                  "equipped": False}], "truncated": False, "scope": "bags and equipment"},
        "hearthstone": {"itemId": 6948, "count": 1, "remainingAtObservation": 300, "enabled": True},
        "events": {"events": [{"id": 1, "kind": "death", "observedAt": 1000000}], "truncated": False},
    }[domain]
    return {"status": "available", "observedAt": 1000000, "data": {**data, **changes}}


def frames(domain, record=None, *, session=1, counter=1, revision=1, manifest=None):
    text = json.dumps(record if record is not None else sample(domain), ensure_ascii=False, separators=(",", ":")).encode()
    chunks = [text[i:i + PAYLOAD_BYTES] for i in range(0, len(text), PAYLOAD_BYTES)]
    manifest = manifest or (revision,) * 7
    result = []
    for index, chunk in enumerate(chunks):
        packet = HEADER.pack(b"AKW1", session, counter + index, DOMAINS.index(domain) + 1,
                             revision, index + 1, len(chunks), len(chunk), *manifest)
        packet += chunk + bytes(PAYLOAD_BYTES - len(chunk))
        result.append(packet + struct.pack(">I", zlib.adler32(packet)))
    return result


def feed(module, domain, record=None, **options):
    options.setdefault("counter", module.counter + 1)
    for frame in frames(domain, record, **options):
        module.accept(frame)


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.module = WoWModule(clock=lambda: self.now, wall_clock=lambda: 1000100)

    def ready(self, domain="player"):
        feed(self.module, domain)
        feed(self.module, domain)
        return self.module.freeze().collect()

    def test_frame_checksum_length_padding_and_duplicates_fail_closed(self):
        frame = frames("player")[0]
        self.assertEqual(len(frame), FRAME_BYTES)
        decode_frame(frame)
        for corrupted in (frame[:-1], b"x" + frame[1:], bytes(FRAME_BYTES)):
            with self.assertRaises(ValueError):
                decode_frame(corrupted)
        self.module.accept(frame)
        self.assertFalse(self.module.accept(frame))

    def test_unchanged_cooking_has_no_expiry_and_observation_is_not_retimestamped(self):
        self.ready("professions")
        self.now += 3600
        feed(self.module, "professions")
        feed(self.module, "professions")
        row = self.module.freeze().collect()["domains"]["professions"]
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["observedAt"], 1000000)
        self.assertEqual(row["data"]["professions"][0]["rank"], 25)

    def test_cached_frames_do_not_keep_disconnected_state_alive(self):
        self.ready()
        replay = frames("player", counter=self.module.counter)[0]
        self.now += 6
        self.assertFalse(self.module.accept(replay))
        row = self.module.freeze().collect()["domains"]["player"]
        self.assertEqual(row["status"], "disconnected")
        self.assertIsNone(row["data"])

    def test_missing_revision_withholds_only_that_domain_until_complete(self):
        self.ready("professions")
        feed(self.module, "player")
        manifest = [1] * 7
        manifest[3] = 2
        feed(self.module, "player", manifest=manifest)
        snapshot = self.module.freeze().collect()
        self.assertEqual(snapshot["domains"]["player"]["status"], "available")
        self.assertEqual(snapshot["domains"]["professions"]["status"], "updating")
        self.assertIsNone(snapshot["domains"]["professions"]["data"])
        feed(self.module, "professions", sample("professions", professions=[]), revision=2, manifest=manifest)
        self.assertEqual(self.module.freeze().collect()["domains"]["professions"]["data"]["professions"], [])

    def test_new_session_clears_records_and_retired_session_cannot_return(self):
        self.ready("professions")
        feed(self.module, "player", session=2)
        self.assertEqual(self.module.freeze().collect()["domains"]["professions"]["status"], "missing")
        old = frames("professions", session=1, counter=500)[0]
        self.assertFalse(self.module.accept(old))
        self.assertEqual(self.module.session, 2)

    def test_manifest_cannot_move_backwards_or_renew_connection(self):
        self.ready()
        feed(self.module, "player", revision=2)
        counter = self.module.counter
        self.assertFalse(self.module.accept(frames("player", counter=counter + 1, revision=1)[0]))
        self.assertEqual(self.module.counter, counter)

    def test_snapshot_is_immutable_and_missing_data_does_not_block(self):
        empty = self.module.freeze()
        self.ready()
        frozen = self.module.freeze()
        feed(self.module, "player", sample("player", level=9), revision=2)
        self.assertIsNone(empty.collect()["domains"]["player"]["data"])
        snapshot = frozen.collect()
        self.assertEqual(snapshot["domains"]["player"]["data"]["level"], 8)
        snapshot["domains"]["player"]["data"]["level"] = 999
        self.assertEqual(frozen.collect()["domains"]["player"]["data"]["level"], 8)
        control = TurnControl(); control.cancel.set()
        with self.assertRaises(TurnCancelled):
            frozen.collect(control)

    def test_incomplete_payload_never_exposes_partially_updated_inventory(self):
        self.ready("inventory")
        record = sample("inventory", items=sample("inventory")["data"]["items"] * 10)
        packets = frames("inventory", record, revision=2, counter=self.module.counter + 1)
        self.assertGreater(len(packets), 1)
        self.module.accept(packets[0])
        self.assertEqual(self.module.freeze().collect()["domains"]["inventory"]["status"], "updating")
        for packet in packets[1:]:
            self.module.accept(packet)
        self.assertEqual(len(self.module.freeze().collect()["domains"]["inventory"]["data"]["items"]), 10)

    def test_full_carried_inventory_including_reagent_bag_fits_and_remains_bounded(self):
        item = sample("inventory")["data"]["items"][0]
        items = [{**item, "bag": bag, "slot": slot} for bag in range(6) for slot in range(1, 41)]
        items += [{**item, "bag": None, "slot": slot, "equipped": True} for slot in range(1, 20)]
        self.assertEqual(len(items), 259)
        record = sample("inventory", items=items)
        validate_record("inventory", record)
        feed(self.module, "inventory", record)
        self.assertEqual(len(self.module.freeze().collect()["domains"]["inventory"]["data"]["items"]), 259)
        with self.assertRaises(ValueError):
            validate_record("inventory", sample("inventory", items=items + [item]))

    def test_unknown_fields_restricted_values_and_malformed_rows_are_rejected(self):
        for record in (sample("player", shell="bad"), sample("player", level="eight"),
                       sample("inventory", items=["bad"]), {"status": "unavailable", "observedAt": 1, "data": {"name": "secret"}},
                       {"status": [], "observedAt": 1, "data": None}):
            with self.assertRaises(ValueError):
                validate_record("inventory" if "items" in (record.get("data") or {}) else "player", record)

    def test_events_are_deduplicated_and_never_trigger_a_reply(self):
        log = Mock(); self.module.log = log
        self.ready("events")
        for _ in range(5):
            feed(self.module, "events")
        observations = [call for call in log.call_args_list if call.args[0] == "wow_game_event"]
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].kwargs["observation"]["kind"], "death")

    def test_extra_tools_only_exist_for_the_module_and_filter_inventory(self):
        self.ready("inventory")
        feed(self.module, "professions")
        tools = TurnContextTools(self.module.freeze().collect())
        self.assertIn("get_wow_details", [tool["name"] for tool in tools.definitions()])
        data, status = tools.execute("get_wow_details", '{"domain":"inventory","query":"6291"}')
        self.assertEqual(json.loads(data)["data"]["items"][0]["count"], 5)
        data, status = tools.execute("get_wow_details", '{"domain":"professions","query":""}')
        self.assertEqual(json.loads(data)["data"]["professions"][0]["rank"], 25)
        self.assertEqual(TurnContextTools().definitions(), [])
        self.assertNotIn("get_wow_details", [t["name"] for t in TurnContextTools({"domains": {}}).definitions()])

    def test_hearthstone_countdown_is_computed_at_freeze_and_never_moves_mid_turn(self):
        self.ready("hearthstone")
        tools = TurnContextTools(self.module.freeze().collect())
        feed(self.module, "hearthstone", sample("hearthstone", count=0), revision=2)
        data, status = tools.execute("get_wow_details", '{"domain":"hearthstone","query":""}')
        self.assertEqual(status, "available")
        self.assertEqual(json.loads(data)["remainingSecondsAtSnapshot"], 200)
        self.assertEqual(json.loads(data)["data"]["count"], 1)

    def test_reconnection_requires_two_advancing_packets_and_keeps_source_timestamp(self):
        self.ready()
        self.now += 6
        feed(self.module, "player")
        self.assertEqual(self.module.freeze().collect()["domains"]["player"]["status"], "disconnected")
        feed(self.module, "player")
        row = self.module.freeze().collect()["domains"]["player"]
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["observedAt"], 1000000)

    def test_conflicting_multipart_cannot_publish_data(self):
        self.ready("inventory")
        record = sample("inventory", items=sample("inventory")["data"]["items"] * 10)
        self.module.accept(frames("inventory", record, revision=2, counter=self.module.counter + 1)[0])
        changed = deepcopy(record); changed["data"]["items"][0]["id"] = 6292
        with self.assertRaises(ValueError):
            self.module.accept(frames("inventory", changed, revision=2, counter=self.module.counter + 1)[0])
        self.assertNotIn("inventory", self.module.pending)
        self.assertEqual(self.module.freeze().collect()["domains"]["inventory"]["status"], "updating")

    def test_partial_null_titles_are_searchable_without_losing_other_quests(self):
        row = {"id": 1, "title": None, "complete": False, "truncated": False,
               "objectives": [{"text": "Kobolds slain", "fulfilled": 2, "required": 10, "finished": False}]}
        record = sample("quests", quests=[row], trackedCount=1); record["status"] = "partial"
        self.ready("quests"); feed(self.module, "quests", record, revision=2)
        tools = TurnContextTools(self.module.freeze().collect())
        result, status = tools.execute("get_wow_context", '{"fields":["quests"],"quest_query":"kobolds","quest_detail":"objectives"}')
        data = json.loads(result)["domains"]["quests"]
        self.assertEqual(data["status"], "partial")
        self.assertEqual(data["observedAt"], 1000000)
        self.assertEqual(data["data"]["quests"][0]["objectives"][0]["fulfilled"], 2)

    def test_gui_flag_gates_tools_and_selected_cache_is_frozen_without_legacy_factory(self):
        with tempfile.TemporaryDirectory() as directory:
            processor = TurnProcessor(Path(directory), "Akane", Mock(), tts_enabled=False,
                                      synthesize=Mock(), play=Mock(), write_caption=write_subtitle)
            processor.wow_enabled = False
            listener = Mock(running=False, stop_event=threading.Event())
            listener.snapshot.return_value = SimpleNamespace(text="", status="Disconnected")
            legacy = Mock(side_effect=AssertionError("Legacy refresh must never run"))
            session = WindowSession(processor, Mock(), Mock(), context_factory=Mock(return_value=listener), wow_factory=legacy)
            self.assertFalse(session.model.wow_available)
            self.assertFalse(session.request("wow_include", True))
            session.model.include_wow_context = True
            self.assertIsNone(session.selected_wow_context("Hello!"))
            processor.wow_enabled, processor.wow_module = True, self.module
            session.model.wow_available = True
            self.ready()
            frozen = session.selected_wow_context("Hello!")
            feed(self.module, "player", sample("player", level=9), revision=2)
            self.assertEqual(frozen.collect()["domains"]["player"]["data"]["level"], 8)
            session.tick()
            self.assertIn("linked", session.model.wow_status)
            legacy.assert_not_called()

    def test_enabled_main_owns_receiver_lifecycle_without_starting_native_capture(self):
        with tempfile.TemporaryDirectory() as directory, patch("wow_module.WoWModule") as receiver, \
                patch("control_window.run_control_window", return_value=0) as window, redirect_stdout(io.StringIO()):
            root = Path(directory); (root / "personality.txt").write_text("Akane")
            self.assertEqual(app.main(Mock(), project_dir=root, stt_enabled=False, tts_enabled=False,
                                      gui_mode=True, wow_enabled=True), 0)
            receiver.return_value.start.assert_called_once_with()
            receiver.return_value.close.assert_called_once_with()
            self.assertTrue(window.call_args.args[0].wow_enabled)

    def test_receiver_with_fake_source_closes_without_any_native_capture(self):
        finished = threading.Event()
        module = WoWModule(capture_factory=lambda: source)
        class Source:
            def __enter__(self): return self
            def __exit__(self, *args): finished.set()
            def read(self): module.stop.set(); return None
        source = Source()
        module.start()
        self.assertTrue(finished.wait(1))
        module.close()
        self.assertFalse(module.worker.is_alive())

    def test_disabled_startup_never_constructs_a_receiver_and_flag_is_forwarded(self):
        with patch("main.main", return_value=0) as run, patch("main.select_reply_backend", return_value=Mock()), \
                patch("sys.argv", ["main.py", "--wow", "--gui", "--no-stt", "--no-tts"]):
            self.assertEqual(app.cli(), 0)
        self.assertTrue(run.call_args.kwargs["wow_enabled"])
        with tempfile.TemporaryDirectory() as directory, patch("wow_module.WoWModule") as receiver, \
                patch("builtins.input", return_value="exit"), redirect_stdout(io.StringIO()):
            root = Path(directory); (root / "personality.txt").write_text("Akane")
            self.assertEqual(app.main(Mock(), project_dir=root, stt_enabled=False, tts_enabled=False), 0)
            receiver.assert_not_called()

    def test_acceptance_freezes_before_fake_recording_and_never_reports_refresh_wait(self):
        self.ready()
        entered = threading.Event()
        received = []
        class Backend:
            supports_context_tools = True
            def reply_with_context(self, messages, **kwargs):
                received.append(kwargs["wow_context"])
                return '{"segments":[{"ja":"了解。","en":"Understood."}]}'
        def record(path, **options):
            feed(self.module, "player", sample("player", level=9), revision=2)
            options["on_started"]()
            entered.set()
            path.write_bytes(b"fake")
            return path
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as output, \
                patch.object(config, "SUBTITLE_MIN_SECONDS", 0), patch.object(config, "SUBTITLE_READING_CHARS_PER_SECOND", 0):
            processor = TurnProcessor(Path(directory), "Akane", Backend(), tts_enabled=False,
                                      synthesize=Mock(), play=Mock(), write_caption=write_subtitle)
            stt = Mock(return_value={"text": "Hello", "languages": ["en"], "elapsed_seconds": 0})
            controller = StreamController(processor, stt, record, wow_provider=lambda question: self.module.freeze())
            self.assertTrue(controller.start_turn("mic"))
            self.assertTrue(entered.wait(1))
            controller.worker.join(2)
            self.assertFalse(controller.worker.is_alive())
            self.assertEqual(received[0]["domains"]["player"]["data"]["level"], 8)
            self.assertNotIn("Getting WoW context", output.getvalue())
            processor.synthesize.assert_not_called()


class LuaCollectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        package = Path(os.environ.get("AKANE_TEST_FENGARI", root.parent / "witchcraft/tools/witchcraft-daemon/node_modules/fengari"))
        node = shutil.which("node")
        if not node or not package.is_dir():
            raise unittest.SkipTest("Lua integration needs Node and existing Fengari; no dependencies are downloaded")
        result = subprocess.run([node, str(root / "tests/wow_lua_runner.cjs"), str(root / "wow_addon/AkaneData"), str(package)],
                                check=True, capture_output=True, text=True, encoding="utf-8", timeout=20)
        cls.output = json.loads(result.stdout)

    def test_real_lua_collector_packets_round_trip_to_python_during_fake_combat(self):
        module = WoWModule(wall_clock=lambda: 1000001)
        for packet in self.output["packets"]:
            module.accept(bytes.fromhex(packet))
        snapshot = module.freeze().collect()
        self.assertTrue(snapshot["connected"])
        self.assertEqual(snapshot["domains"]["professions"]["data"]["professions"][0]["rank"], 25)
        self.assertEqual(snapshot["domains"]["inventory"]["data"]["items"][0]["count"], 5)
        self.assertTrue(self.output["combatCounterAdvanced"])
        self.assertEqual(self.output["eventCount"], 1)

    def test_lua_optical_colours_decode_to_the_identical_checksum_valid_packet(self):
        packet = pixels_to_frame(bytes(self.output["colours"]))
        self.assertEqual(packet.hex(), self.output["packets"][0])
        decode_frame(packet)

    def test_idle_lua_retransmission_does_not_allocate_per_cell_tables_or_grow_state(self):
        # 500 actual sender ticks, after warm-up. The original implementation
        # created roughly 200,500 tables; no native game-memory claim is made.
        self.assertLessEqual(self.output["idleTableAllocations"], 20)
        self.assertEqual(self.output["retainedIdleBefore"], self.output["retainedIdleAfter"])

    def test_reagent_only_linen_reaches_inventory_tool_and_bag_events_update_its_count(self):
        module = WoWModule(wall_clock=lambda: 1000001)
        for packet in self.output["packets"]:
            module.accept(bytes.fromhex(packet))
        tools = TurnContextTools(module.freeze().collect())
        encoded, status = tools.execute("get_wow_details", '{"domain":"inventory","query":"Linen Cloth"}')
        result = json.loads(encoded)
        self.assertIn(status, {"available", "partial"})
        self.assertEqual(len(result["data"]["items"]), 1)
        self.assertEqual(result["data"]["items"][0]["bag"], 5)
        self.assertEqual(result["data"]["items"][0]["count"], 38)
        self.assertIn("Reagent Bag", result["data"]["scope"])
        updated = json.loads(self.output["updatedReagentInventory"])
        feed(module, "inventory", updated, revision=2)
        encoded, _ = TurnContextTools(module.freeze().collect()).execute(
            "get_wow_details", '{"domain":"inventory","query":"2589"}')
        self.assertEqual(json.loads(encoded)["data"]["items"][0]["count"], 41)


if __name__ == "__main__":
    unittest.main()
