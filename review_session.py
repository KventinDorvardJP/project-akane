"""Summarize a saved Akane session without starting the app or its services."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys


SESSION_FOLDER = Path(__file__).resolve().parent / "runtime" / "session_logs"
OUTCOMES = {
    "ok": "Playback completed",
    "speech_unavailable": "Speech failed; subtitle fallback",
    "tts_disabled": "Text only (speech disabled)",
    "cancelled": "Stopped",
    "interrupted": "Interrupted",
    "no_speech": "No speech recognized",
    "input_error": "Input failed",
    "reply_unavailable": "Reply unavailable",
    "unexpected_error": "Turn failed",
}


def first_event(events, *kinds):
    # Prefer earlier arguments (e.g. transcription over submitted input).
    for kind in kinds:
        for event in events:
            if event["event"] == kind:
                return event
    return None


def seconds(value):
    return value if type(value) in (int, float) and 0 <= value <= sys.float_info.max else None


def elapsed(event):
    return seconds(event.get("elapsed_seconds")) if event else None


def turn_report(turn_id, events):
    start = first_event(events, "turn_start") or events[0]
    submitted = first_event(events, "transcription", "user", "input_accepted")
    reply = first_event(events, "reply")
    end = first_event(events, "turn_end")
    mode = start["data"].get("input_mode") or (submitted or start)["data"].get("mode", "unknown input")
    if not isinstance(mode, str):
        mode = "unknown input"
    status = end["data"].get("status", "unknown outcome") if end else None
    outcome = OUTCOMES.get(status, status) if isinstance(status, str) else "No turn end recorded"
    stamp = elapsed(start)
    when = f" · +{stamp:.2f}s" if stamp is not None else ""
    lines = [f"{turn_id}{when} · {mode} · {outcome}"]
    if submitted and isinstance(submitted["data"].get("text"), str):
        label = "Heard" if submitted["event"] == "transcription" or mode in {"audio", "mic"} else "You"
        lines.append(f"  {label}: {submitted['data']['text']}")
    if reply:
        for key in ("ja", "en", "ru"):
            text = reply["data"].get(key)
            if isinstance(text, str) and text:
                lines.append(f"  Akane {key.upper()} (generated): {text}")

    timing = []
    metrics = first_event(events, "metrics")
    if metrics and isinstance(metrics["data"].get("row"), dict):
        row = metrics["data"]["row"]
        for key, label in (("stt_seconds", "STT"), ("context_seconds", "Game context"), ("llm_seconds", "Model")):
            duration = seconds(row.get(key))
            if duration:
                timing.append(f"{label} {duration:.2f}s")
    first_playback = first_event(events, "playback_start")
    # Measure from the end of capture for microphone turns, excluding time spent speaking.
    anchor = first_event(events, "recording_end") if mode == "mic" else first_event(events, "turn_start")
    anchor_seconds, playback_seconds = elapsed(anchor), elapsed(first_playback)
    if playback_seconds is not None:
        if anchor_seconds is not None and playback_seconds >= anchor_seconds:
            label = "capture end" if mode == "mic" else "turn start"
            timing.append(f"{label} → first playback dispatch {playback_seconds - anchor_seconds:.2f}s")
        else:
            timing.append(f"first playback dispatch at +{playback_seconds:.2f}s")
    if timing:
        lines.append("  Timing: " + " · ".join(timing))

    playback = Counter(event["data"].get("status") for event in events
                       if event["event"] == "playback_end" and isinstance(event["data"].get("status"), str))
    if first_playback:
        parts = [f"{playback[kind]} {kind}" for kind in ("completed", "stopped", "error") if playback[kind]]
        pending = sum(event["event"] == "playback_start" for event in events) - sum(playback.values())
        if pending > 0:
            parts.append(f"{pending} without a recorded end")
        lines.append("  Playback pieces: " + (", ".join(parts) or "end status unknown"))
    elif reply:
        lines.append("  Playback: no dispatch recorded")

    messages = []
    for event in events:
        kind, data = event["event"], event["data"]
        if (kind in {"input_error", "reply_error", "turn_error", "wow_context_failure"}
                or (kind == "notice" and data.get("level") in ("warning", "error"))):
            message = data.get("message")
            if isinstance(message, str) and message and not any(
                    message in previous or previous in message for previous in messages):
                messages.append(message)
    lines.extend(f"  Note: {message}" for message in messages)
    return lines


def review_session(path):
    turns, markers = defaultdict(list), []
    ignored = event_count = 0
    ended = False
    with Path(path).open("rb") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                ignored += 1
                continue
            if (not isinstance(event, dict) or event.get("schema_version") != 1
                    or not isinstance(event.get("event"), str) or not isinstance(event.get("data"), dict)):
                ignored += 1
                continue
            event_count += 1
            if event["event"] == "session_end":
                ended = True
            elif event["event"] == "recording_marker":
                markers.append(event)
            turn_id = event.get("turn_id")
            if isinstance(turn_id, str) and turn_id:
                turns[turn_id].append(event)

    if not event_count:
        raise ValueError("The file has no readable Akane session events (schema version 1).")
    lines = [f"Akane session review · {Path(path).name}", f"Turns: {len(turns)}"]
    if not ended:
        lines.append("No session end recorded. The session may still be running or the log may be incomplete.")
    if ignored:
        lines.append(f"Skipped {ignored} unreadable or unsupported log line(s).")
    for marker in markers:
        seconds = elapsed(marker)
        timing = f" (+{seconds:.2f}s)" if seconds is not None else ""
        lines.append(f"Recording marker {marker['data'].get('number', '?')}: {marker.get('timestamp_utc', 'unknown UTC')}{timing}")
    if any(first_event(events, "playback_start") for events in turns.values()):
        lines.append("Playback dispatch is when Akane called the player. Use the recording to check audible onset and OBS rendering.")
    for turn_id, events in turns.items():
        lines.extend(["", *turn_report(turn_id, events)])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review an Akane session transcript locally.")
    parser.add_argument("log", nargs="?", type=Path,
                        help="Path to a session .jsonl or its matching .txt. Defaults to the latest session.")
    args = parser.parse_args(argv)
    path = args.log
    if path is None:
        path = max(SESSION_FOLDER.glob("*.jsonl"), default=None)
        if path is None:
            parser.exit(1, "No session logs found. Enable Record session transcript in Akane's Session settings.\n")
    if path.suffix.lower() == ".txt":
        path = path.with_suffix(".jsonl")
    try:
        print(review_session(path))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not review {path}: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
