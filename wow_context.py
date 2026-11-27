"""Bounded Witchcraft cache reader and on-demand refresh, independent of any LLM.

Only three fixed sibling want files are written. The running Witchcraft daemon
owns the cache and talks to the game; this module never launches or controls it.
"""
import json
import math
import os
from pathlib import Path
import re
import tempfile
from time import monotonic, time

from controls import TurnControl


class WoWContextError(ValueError):
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details


DOMAINS = ("player", "progress", "quests")
MAX_BYTES = 128 * 1024
HEX = re.compile(r"[0-9a-f]{8}\Z")


def question_domains(question, previous=None):
    """Small local intent hints; unknown/spoken questions conservatively use all.

    Inspect only the user's question, never game strings. Mixed requests combine
    domains. This is lookup scoping, not model/tool planning or gameplay control.
    """
    if not isinstance(question, str) or not question.strip():
        return DOMAINS
    text = question.casefold()
    selected = set()
    if re.search(r"\b(?:where|location|position|zone|subzone|map)\b|\b(?:my|your)\s+(?:character|class|level)\b"
                 r"|\bwhat\s+class\b|\bгде\b|\b(?:локаци|местоположени|зон)\w*|どこ|何処|場所|エリア|地域|クラス|レベル|キャラ", text):
        selected.add("player")
    if re.search(r"\bquests?\b|\bobjectives?\b|\btracked\b|\bhow\s+many\b.*\b(?:left|remaining)\b"
                 r"|\b(?:квест|задани)\w*|сколько.*остал|クエスト|依頼|目標|あと何|残り|何匹|何個", text):
        selected.add("quests")
    if re.search(r"\b(?:xp|experience|rested|money|gold|silver|copper|currency|coins?|combat|alive|dead|ghost|group|party|instance|dungeon|difficulty|role)\b"
                 r"|\blevel\s*up\b|\bnext\s+level\b|\b(?:опыт|золот|деньг|монет|мертв|групп|подземел)\w*"
                 r"|経験値|所持金|お金|ゴールド|戦闘|死亡|死ん|パーティ|グループ|難易度|ロール", text):
        selected.add("progress")
    if not selected and re.search(r"\bprogress(?:ion)?\b", text):
        selected.add("progress")
    if re.search(r"\b(?:advise|recommend|suggest)\b.*\b(?:next|do)\b|\bwhat\b.*\bdo\s+next\b"
                 r"|что.*(?:дальше|делать)|次に.*(?:する|したら)|次は何", text):
        selected.update(("player", "quests"))
    if selected:
        return tuple(name for name in DOMAINS if name in selected)
    followup = re.search(r"\b(?:elaborate|continue|tell me more|go on|explain that|explain more)\b"
                         r"|подробнее|продолж|詳しく|続けて", text)
    if followup and previous and all(name in DOMAINS for name in previous):
        return tuple(name for name in DOMAINS if name in previous)
    return DOMAINS


def _invalid_constant(value):
    raise ValueError("invalid JSON number")


def cache_path(value, project_dir):
    # Reject network/device paths and URLs; refresh writes must stay local.
    if (not isinstance(value, str) or not value.strip() or "://" in value
            or value.startswith(("\\\\", "//")) or any(ord(c) < 32 for c in value)):
        raise WoWContextError("Choose a local Witchcraft wow-context.json file in Settings → WoW context.")
    try:
        path = (Path(project_dir) / value).resolve()
    except (OSError, RuntimeError, ValueError):
        raise WoWContextError("Choose a valid local Witchcraft wow-context.json path.") from None
    if path.name != "wow-context.json" or str(path).startswith(("\\\\", "//")):
        raise WoWContextError("The Witchcraft cache must be a local file named wow-context.json.")
    return path


def validate_limits(max_age, wait_seconds):
    for value, low, high, label in ((max_age, 35, 180, "maximum age"), (wait_seconds, 1, 60, "refresh wait")):
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise WoWContextError(f"WoW context {label} must be between {low} and {high} seconds.")


def _text(value, limit):
    if (not isinstance(value, str) or len(value.encode("utf-8")) > limit
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)):
        raise ValueError("invalid text")
    return value


def _number(value):
    if type(value) is not int or not 0 <= value <= 2147483647:
        raise ValueError("invalid number")
    return value


def _clean(value, spec):
    """Whitelist the exported game fields, preserving nulls instead of inventing zeros."""
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            raise ValueError("invalid object")
        return {key: _clean(value[key], child) for key, child in spec.items()}
    if isinstance(spec, list):
        child, limit = spec
        if not isinstance(value, list) or len(value) > limit:
            raise ValueError("invalid list")
        return [_clean(item, child) for item in value]
    if value is None:
        return None
    if spec is bool:
        if type(value) is not bool:
            raise ValueError("invalid boolean")
        return value
    if spec is int:
        return _number(value)
    return _text(value, spec)


FIELDS = {
    "player": {"name": 160, "class": 160, "level": int,
               "location": {"mapId": int, "zone": 160, "subzone": 160}, "unavailableFields": [256, 12]},
    "progress": {"money": int, "xp": int, "xpMax": int, "rested": int,
                 "situation": {"inInstance": bool, "instanceType": 64, "instanceName": 160,
                               "difficulty": 96, "groupSize": int, "role": 32,
                               "dead": bool, "ghost": bool, "combat": bool}, "unavailableFields": [256, 14]},
    "quests": {"trackedCount": int, "truncated": bool,
               "quests": [{"id": int, "title": 256, "complete": bool, "truncated": bool,
                           "objectives": [{"text": 256, "fulfilled": int, "required": int, "finished": bool}, 4]}, 10]},
}


class WoWContextClient:
    def __init__(self, path, *, max_age=60, wait_seconds=8, clock=time, timer=monotonic, domains=DOMAINS):
        validate_limits(max_age, wait_seconds)
        if not domains or any(name not in DOMAINS for name in domains):
            raise WoWContextError("Choose supported WoW context domains.")
        self.domains = tuple(name for name in DOMAINS if name in domains)
        self.path = Path(path)
        self.max_age, self.wait_seconds = max_age, wait_seconds
        self.clock, self.timer = clock, timer

    def read(self):
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("oversized cache")
            source = json.loads(raw.decode("utf-8"), parse_constant=_invalid_constant)
            if (not isinstance(source, dict) or type(source.get("schemaVersion")) is not int
                    or source["schemaVersion"] != 1 or source.get("source") != "witchcraft-addon"):
                raise ValueError("unsupported cache format")
            session = source["session"]
            if not isinstance(session, dict) or any(not isinstance(session.get(k), str) or not HEX.fullmatch(session[k])
                                                    for k in ("epoch", "uiNonce")):
                raise WoWContextError("Witchcraft has no active game session. Start its bridge and return to WoW.")
            if source.get("enabled") is not True:
                raise WoWContextError("WoW context sharing is off. Enable it in Witchcraft first.")
            now = int(self.clock() * 1000)
            heartbeat = source.get("lastHeartbeatAt")
            if type(heartbeat) is not int or not 0 <= now - heartbeat <= 30000:
                raise WoWContextError("Witchcraft is disconnected. Return to foreground WoW with the bridge running.")
            result = {"session": {k: session[k] for k in ("epoch", "uiNonce")}, "domains": {}}
            for name in DOMAINS:
                record = source.get(name)
                if record is None:
                    result["domains"][name] = {"status": "missing", "ageSeconds": None, "data": None}
                    continue
                if not isinstance(record, dict) or type(record.get("available")) is not bool:
                    raise ValueError("invalid domain")
                sampled, received = record["sampledAt"], record["receivedAt"]
                if (type(sampled) is not int or type(received) is not int or not 0 <= sampled <= received <= now):
                    raise ValueError("invalid sample time")
                age = (now - sampled) / 1000
                data = _clean(record["data"], FIELDS[name]) if record["available"] else None
                if record["available"] != (record["data"] is not None):
                    raise ValueError("invalid availability")
                status = "stale" if age > self.max_age else "available" if record["available"] else "unavailable"
                timing = record.get("timing")
                if timing is not None:
                    if not isinstance(timing, dict) or timing.get("method") not in {"host-anchor", "transport-bound"}:
                        raise ValueError("invalid sample timing")
                    if timing["method"] == "host-anchor":
                        seq = timing.get("captureSeq")
                        if type(seq) is not int or not 1 <= seq <= 9999:
                            raise ValueError("invalid capture anchor")
                        timing = {"method": "host-anchor", "captureSeq": seq}
                    else:
                        timing = {"method": "transport-bound"}
                result["domains"][name] = {"status": status, "ageSeconds": round(age, 1),
                                           "sampledAt": sampled, "receivedAt": received,
                                           "reason": _text(record.get("reason", ""), 640),
                                           "data": data if status == "available" else None}
                if timing is not None:
                    result["domains"][name]["timing"] = timing
            return result
        except WoWContextError:
            raise
        except (OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            raise WoWContextError("Cannot read a valid Witchcraft cache. Check the cache path and running bridge.") from None

    def request_refresh(self, names):
        # This is Witchcraft's existing context-want protocol, not arbitrary Lua
        # or terminal input. Atomic replacement tolerates simultaneous MCP readers.
        for name in names:
            if name not in DOMAINS:
                raise WoWContextError("Unsupported WoW context domain.")
            target = self.path.with_name(f"wow-context.want.{name}.json")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                 prefix=".akane-want-", suffix=".tmp", delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump({"protocol": 1, "requestedAt": int(self.clock() * 1000)}, stream)
                os.replace(temporary, target)
            except OSError:
                raise WoWContextError("Cannot request WoW context. Check write access to Witchcraft's .local folder.") from None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def collect(self, control=None):
        control = control or TurnControl()
        control.check()
        snapshot = self.read()
        session = snapshot["session"]
        needed = [name for name in self.domains if snapshot["domains"][name]["status"] in {"missing", "stale"}]
        if needed:
            control.phase("context")
            self.request_refresh(needed)
            deadline = self.timer() + self.wait_seconds
            while self.timer() < deadline:
                control.check()
                if control.cancel.wait(min(0.15, max(0, deadline - self.timer()))):
                    control.check()
                snapshot = self.read()
                if snapshot["session"] != session:
                    raise WoWContextError("The WoW/bridge session changed. Submit your question again.")
                if all(snapshot["domains"][name]["status"] not in {"missing", "stale"} for name in self.domains):
                    break
        control.check()
        available = sum(row["status"] == "available" for row in snapshot["domains"].values())
        requested_available = sum(snapshot["domains"][name]["status"] == "available" for name in self.domains)
        if not requested_available:
            raise WoWContextError("No fresh WoW data for this question arrived. Keep WoW foreground, check sharing, then retry your question.",
                                  details={"requiredDomains": list(self.domains), "domains": {
                                      name: {"status": row["status"], "ageSeconds": row.get("ageSeconds"),
                                             **({"timing": row["timing"]} if "timing" in row else {})}
                                      for name, row in snapshot["domains"].items()}})
        return {"source": "Witchcraft / WoW Forever", "snapshotAt": int(self.clock() * 1000),
                "requiredDomains": list(self.domains),
                "requestedStatus": "ready" if requested_available == len(self.domains) else "partial",
                "status": "ready" if available == len(DOMAINS) else "partial", **snapshot}
