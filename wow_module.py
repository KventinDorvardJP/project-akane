"""Opt-in WoW telemetry: bounded optical protocol, revision cache and receiver.

No per-field TTL, question-time I/O, refresh requests, LLM calls or gameplay input.
Native capture is imported only when explicitly starting the --wow module.
"""
from copy import deepcopy
import json
import math
import struct
import threading
from time import monotonic, time
import zlib

DOMAINS = ("player", "progress", "quests", "professions", "inventory", "hearthstone", "events")
MAX_CARRIED_ITEMS = 6 * 40 + 19  # Backpack + four bags + Reagent Bag + equipment.
HEADER = struct.Struct(">4sIIBIHHH7I")
FRAME_BYTES = 288
PAYLOAD_BYTES = FRAME_BYTES - HEADER.size - 4
MAX_DOMAIN_BYTES = 48 * 1024
MAX_PARTS = math.ceil(MAX_DOMAIN_BYTES / PAYLOAD_BYTES)
CONNECTION_TIMEOUT = 5.0
STATUSES = {"available", "partial", "unsupported", "unavailable"}


def _plain(value, depth=0):
    if depth > 9:
        raise ValueError("telemetry nesting")
    if value is None or type(value) is bool:
        return
    if type(value) in (int, float):
        if abs(value) > 2 ** 53 or not math.isfinite(value):
            raise ValueError("telemetry number")
    elif isinstance(value, str):
        if len(value.encode("utf-8")) > 2048 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
            raise ValueError("telemetry text")
    elif isinstance(value, list):
        if len(value) > MAX_CARRIED_ITEMS:
            raise ValueError("telemetry list")
        for child in value:
            _plain(child, depth + 1)
    elif isinstance(value, dict):
        if len(value) > 40 or any(not isinstance(key, str) or len(key) > 64 for key in value):
            raise ValueError("telemetry object")
        for child in value.values():
            _plain(child, depth + 1)
    else:
        raise ValueError("telemetry type")


def _shape(value, spec):
    if value is None:
        if isinstance(spec, (dict, list)):
            raise ValueError("missing telemetry structure")
        return  # Explicit unavailable field, never a fabricated zero.
    if isinstance(spec, dict):
        if not isinstance(value, dict) or set(value) != set(spec):
            raise ValueError("telemetry field shape")
        for key, child in spec.items():
            _shape(value[key], child)
    elif isinstance(spec, list):
        if not isinstance(value, list) or len(value) > spec[1]:
            raise ValueError("telemetry row count")
        for child in value:
            if child is None:
                raise ValueError("null telemetry row")
            _shape(child, spec[0])
    elif spec == "number":
        if type(value) not in (int, float) or value < 0:
            raise ValueError("telemetry unsigned number")
    elif type(value) is not spec or (spec is int and value < 0):
        raise ValueError("telemetry field type")


def validate_record(domain, record):
    if not isinstance(record, dict) or set(record) != {"status", "observedAt", "data"}:
        raise ValueError("telemetry record")
    _plain(record)
    if not isinstance(record["status"], str) or record["status"] not in STATUSES or type(record["observedAt"]) not in (float, int) or record["observedAt"] < 0:
        raise ValueError("telemetry status/time")
    data = record["data"]
    if record["status"] in {"unsupported", "unavailable"}:
        if data is not None:
            raise ValueError("unavailable telemetry values")
        return
    if not isinstance(data, dict):
        raise ValueError("telemetry data")
    keys = {
        "player": {"name", "class", "level", "location"},
        "progress": {"money", "xp", "xpMax", "rested", "situation"},
        "quests": {"trackedCount", "truncated", "quests"},
        "professions": {"professions", "truncated"},
        "inventory": {"items", "truncated", "scope"},
        "hearthstone": {"itemId", "count", "remainingAtObservation", "enabled"},
        "events": {"events", "truncated"},
    }[domain]
    if set(data) != keys:
        raise ValueError("unexpected telemetry fields")
    list_key = {"quests": "quests", "professions": "professions", "inventory": "items", "events": "events"}.get(domain)
    if list_key and not isinstance(data[list_key], list):
        raise ValueError("telemetry rows")
    if list_key and any(not isinstance(row, dict) for row in data[list_key]):
        raise ValueError("telemetry row")
    schemas = {
        "player": {"name": str, "class": str, "level": int, "location": {"zone": str, "subzone": str, "mapId": int}},
        "progress": {"money": int, "xp": int, "xpMax": int, "rested": int, "situation": {
            "inInstance": bool, "instanceType": str, "instanceName": str, "difficulty": str,
            "groupSize": int, "role": str, "dead": bool, "ghost": bool, "combat": bool}},
        "quests": {"trackedCount": int, "truncated": bool, "quests": [{"id": int, "title": str, "complete": bool,
            "truncated": bool, "objectives": [{"text": str, "fulfilled": int, "required": int, "finished": bool}, 4]}, 25]},
        "professions": {"professions": [{"id": int, "name": str, "rank": int, "maxRank": int}, 12], "truncated": bool},
        "inventory": {"items": [{"id": int, "name": str, "count": int, "bag": int, "slot": int, "equipped": bool}, MAX_CARRIED_ITEMS],
            "truncated": bool, "scope": str},
        "hearthstone": {"itemId": int, "count": int, "remainingAtObservation": "number", "enabled": bool},
        "events": {"events": [{"id": int, "kind": str, "observedAt": "number"}, 32], "truncated": bool},
    }
    _shape(data, schemas[domain])
    if domain == "events" and any(type(event["id"]) is not int or event["id"] < 1
            or event["kind"] not in {"death", "level_up", "zone_changed"} for event in data["events"]):
        raise ValueError("telemetry event")


def decode_frame(raw):
    if not isinstance(raw, bytes) or len(raw) != FRAME_BYTES:
        raise ValueError("telemetry frame size")
    if zlib.adler32(raw[:-4]) != struct.unpack(">I", raw[-4:])[0]:
        raise ValueError("telemetry checksum")
    magic, session, counter, code, revision, part, total, length, *manifest = HEADER.unpack(raw[:HEADER.size])
    if (magic != b"AKW1" or not session or not counter or not 1 <= code <= len(DOMAINS)
            or not 1 <= part <= total <= MAX_PARTS or not 1 <= length <= PAYLOAD_BYTES
            or revision < 1 or revision > manifest[code - 1]
            or any(raw[HEADER.size + length:-4])):
        raise ValueError("telemetry envelope")
    return session, counter, DOMAINS[code - 1], revision, part, total, tuple(manifest), raw[HEADER.size:HEADER.size + length]


def pixels_to_frame(rgb):
    """384 RGB cell centres, four robust intensity levels per component."""
    if len(rgb) != 384 * 3:
        raise ValueError("telemetry cell count")
    accumulator = bits = 0
    output = bytearray()
    for offset in range(0, len(rgb), 3):
        value = 0
        for channel in rgb[offset:offset + 3]:
            level = min(3, max(0, round(channel / 85)))
            if abs(channel - level * 85) > 22:
                raise ValueError("telemetry colour")
            value = value * 4 + level
        accumulator = (accumulator << 6) | value
        bits += 6
        if bits >= 8:
            bits -= 8
            output.append((accumulator >> bits) & 255)
            accumulator &= (1 << bits) - 1
    return bytes(output)


class FrozenWoW:
    """The worker consumes this interface without waiting or touching the bridge."""
    immediate = True

    def __init__(self, snapshot):
        self.snapshot = deepcopy(snapshot)

    def collect(self, control=None):
        if control:
            control.check()
        return deepcopy(self.snapshot)


class WoWModule:
    def __init__(self, *, clock=monotonic, wall_clock=time, capture_factory=None, log=None):
        self.clock, self.wall_clock = clock, wall_clock
        self.capture_factory, self.log = capture_factory, log
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.worker = None
        self.session = None
        self.retired = []
        self.counter = 0
        self.manifest = (0,) * len(DOMAINS)
        self.records, self.pending = {}, {}
        self.last_advance = None
        self.confirmed = False
        self.error = None
        self.seen_events = set()

    def accept(self, raw):
        parsed = decode_frame(raw)
        session, counter, domain, revision, part, total, manifest, payload = parsed
        with self.lock:
            if session in self.retired:
                return False
            if self.session != session:
                if self.session is not None:
                    self.retired = (self.retired + [self.session])[-32:]
                self.session, self.counter = session, 0
                self.manifest = (0,) * len(DOMAINS)
                self.records, self.pending = {}, {}
                self.confirmed = False
                self.seen_events.clear()
            if counter <= self.counter:
                return False  # Replayed/cached frames never renew connection state.
            if any(new < old for new, old in zip(manifest, self.manifest)):
                return False
            now = self.clock()
            self.confirmed = self.counter > 0 and self.last_advance is not None and now - self.last_advance <= CONNECTION_TIMEOUT
            self.counter, self.last_advance, self.manifest = counter, now, manifest
            self.error = None
            index = DOMAINS.index(domain)
            held = self.records.get(domain)
            if held and revision <= held["revision"]:
                return True
            if revision != manifest[index]:
                return True
            pending = self.pending.get(domain)
            if not pending or revision != pending["revision"]:
                pending = {"revision": revision, "total": total, "parts": {}}
                self.pending[domain] = pending
            if total != pending["total"] or (part in pending["parts"] and pending["parts"][part] != payload):
                self.pending.pop(domain, None)
                raise ValueError("conflicting telemetry parts")
            pending["parts"][part] = payload
            if len(pending["parts"]) != total:
                return True
            encoded = b"".join(pending["parts"][i] for i in range(1, total + 1))
            self.pending.pop(domain, None)
            if len(encoded) > MAX_DOMAIN_BYTES:
                raise ValueError("telemetry domain size")
            record = json.loads(encoded.decode("utf-8"))
            validate_record(domain, record)
            self.records[domain] = {**record, "revision": revision, "receivedAt": self.wall_clock()}
            if domain == "events" and record["status"] in {"available", "partial"}:
                for event in record["data"]["events"]:
                    identifier = event.get("id")
                    if identifier not in self.seen_events:
                        self.seen_events.add(identifier)
                        if self.log:
                            self.log("wow_game_event", session=session, observation=event)
                # IDs only increase in a session; bound deduplication memory.
                self.seen_events = set(sorted(self.seen_events)[-128:])
            return True

    def freeze(self):
        with self.lock:
            connected = (self.confirmed and self.last_advance is not None
                         and self.clock() - self.last_advance <= CONNECTION_TIMEOUT)
            domains = {}
            for index, domain in enumerate(DOMAINS):
                record = self.records.get(domain)
                status = "missing"
                data = None
                if record:
                    if not connected:
                        status = "disconnected"
                    elif record["revision"] != self.manifest[index]:
                        status = "updating"
                    else:
                        status = record["status"]
                        data = deepcopy(record["data"])
                domains[domain] = {"status": status, "data": data,
                    "observedAt": record.get("observedAt") if record else None,
                    "revision": record.get("revision") if record else None}
            available = sum(row["status"] in {"available", "partial"} for row in domains.values())
            snapshot = {"source": "AkaneData / WoW Forever", "module": "wow", "snapshotAt": int(self.wall_clock() * 1000),
                "session": self.session, "connected": connected, "requiredDomains": [],
                "status": "ready" if available == len(DOMAINS) else "partial",
                "requestedStatus": "ready" if available else "unavailable", "domains": domains}
        return FrozenWoW(snapshot)

    def start(self):
        if self.worker is not None:
            raise RuntimeError("WoW receiver already started")
        self.worker = threading.Thread(target=self._receive, name="akane-wow", daemon=True)
        self.worker.start()

    def status_text(self):
        with self.lock:
            if self.error:
                return "WoW receiver unavailable: " + self.error
            if not self.confirmed or self.last_advance is None or self.clock() - self.last_advance > CONNECTION_TIMEOUT:
                return "WoW module: waiting for AkaneData — keep its strip visible in foreground WoW"
            count = sum(record["revision"] == self.manifest[DOMAINS.index(name)]
                        and record["status"] in {"available", "partial"} for name, record in self.records.items())
            return f"WoW module linked · {count}/{len(DOMAINS)} data domains · no question-time refresh"

    def _receive(self):
        try:
            if self.capture_factory is None:
                from wow_capture import WindowsWoWCapture
                factory = WindowsWoWCapture
            else:
                factory = self.capture_factory
            with factory() as source:
                while not self.stop.is_set():
                    try:
                        cells = source.read()
                        if cells is not None:
                            self.accept(pixels_to_frame(cells))
                    except (ValueError, UnicodeError, RecursionError):
                        pass  # Occlusion/tearing is not a valid heartbeat.
                    self.stop.wait(0.1)
        except (OSError, RuntimeError, AttributeError) as error:
            self.error = str(error)
            if self.log:
                self.log("wow_receiver_error", message=str(error))

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join(timeout=2)
        with self.lock:
            self.confirmed = False
