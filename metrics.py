# Record timings and backend labels, never transcript or reply contents.

import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile

LEGACY_FIELDS = (
    "timestamp_utc", "input_mode", "stt_backend", "reply_backend", "status",
    "stt_seconds", "llm_seconds", "tts_seconds", "ready_seconds",
    "playback_seconds", "ja_characters", "en_characters",
)
PRE_SEARCH_FIELDS = LEGACY_FIELDS + (
    "llm_provider", "llm_model", "input_tokens", "cached_input_tokens",
    "output_tokens", "reasoning_tokens", "estimated_api_cost_usd",
)
FIELDS = PRE_SEARCH_FIELDS + (
    "web_search_calls", "search_usage_source", "estimated_search_cost_usd", "estimated_text_cost_usd",
)


def upgrade_legacy_file(path):
    with path.open("r", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        header = tuple(reader.fieldnames or ())
        if header == FIELDS:
            return
        if header not in (LEGACY_FIELDS, PRE_SEARCH_FIELDS):
            raise ValueError("Unknown timing CSV header; preserve/rename this file before recording new timings.")
        rows = list(reader)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, suffix=".tmp", delete=False) as output:
            temporary_path = Path(output.name)
            writer = csv.DictWriter(output, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = path.with_name(f"{path.stem}.legacy.{timestamp}.csv")
        shutil.copy2(path, backup)  # Keep the original bytes before replacing its header.
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def append_timing(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    if not needs_header:
        upgrade_legacy_file(path)
    row = dict(values, timestamp_utc=datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        if needs_header:
            writer.writeheader()
        writer.writerow(row)
