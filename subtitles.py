# A shared UTF-8 file lets OBS render captions without another display application.

import os
from pathlib import Path
import tempfile
import threading


# Windows readers can briefly prevent os.replace while holding a file handle.
# Share this short lock between our reader/writer; external OBS readers are separate.
_file_lock = threading.Lock()


def write_subtitle(path, text):
    """Replace a caption atomically; an empty string clears it."""
    with _file_lock:
        _replace_text(path, text)


def read_subtitle(path, *, max_bytes=65536):
    """Bounded UTF-8 read; missing, unreadable or oversized output is blank."""
    try:
        with _file_lock:
            with Path(path).open("rb") as source:
                data = source.read(max_bytes + 1)
        return data.decode("utf-8") if len(data) <= max_bytes else ""
    except (OSError, UnicodeError):
        return ""


def _replace_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        # Windows requires closing the temporary file before replacing the target.
        # Readers see either a complete old caption or a complete new one.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(text)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
