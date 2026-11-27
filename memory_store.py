"""SQLite memory that the user writes and Akane can search."""
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
import re
import sqlite3


MAX_TEXT = 500
MAX_GAME = 80
MAX_QUERY = 160
MAX_RESULTS = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('fact', 'event')),
    game TEXT NOT NULL,
    game_key TEXT NOT NULL,
    text TEXT NOT NULL,
    event_date TEXT,
    created_at TEXT NOT NULL,
    edited_at TEXT NOT NULL,
    source_session TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    text, game, content='memory_entries', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS memory_insert AFTER INSERT ON memory_entries BEGIN
    INSERT INTO memory_fts(rowid, text, game) VALUES (new.id, new.text, new.game);
END;
CREATE TRIGGER IF NOT EXISTS memory_delete AFTER DELETE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, text, game)
        VALUES ('delete', old.id, old.text, old.game);
END;
CREATE TRIGGER IF NOT EXISTS memory_update AFTER UPDATE OF text ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, text, game)
        VALUES ('delete', old.id, old.text, old.game);
    INSERT INTO memory_fts(rowid, text, game) VALUES (new.id, new.text, new.game);
END;
"""


def checked_text(value, limit, label, *, allow_empty=False):
    if not isinstance(value, str) or len(value) > limit or "\0" in value:
        raise ValueError(f"{label} must contain at most {limit} characters.")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f"{label} cannot be empty.")
    return value


def checked_date(value):
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except (ValueError, TypeError):
        pass
    raise ValueError("Use a date in YYYY-MM-DD format.")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.executescript("BEGIN;\n" + SCHEMA + "\nCOMMIT;")

    def connect(self, *, readonly=False):
        # Each operation owns its connection, including lookups on the turn worker.
        target = self.path.as_uri() + "?mode=ro" if readonly else self.path
        db = sqlite3.connect(target, uri=readonly, timeout=1)
        db.row_factory = sqlite3.Row
        return db

    def add(self, kind, text, *, game="", event_date=None, source_session=None):
        if kind not in ("fact", "event"):
            raise ValueError("Choose fact or event.")
        text = checked_text(text, MAX_TEXT, "Memory text")
        game = checked_text(game, MAX_GAME, "Game", allow_empty=True)
        if kind == "event":
            event_date = checked_date(event_date) if event_date is not None else date.today().isoformat()
        else:
            event_date = None
        now = utc_now()
        with closing(self.connect()) as db, db:
            row = db.execute(
                "INSERT INTO memory_entries(kind, game, game_key, text, event_date, created_at, edited_at, source_session) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, game, game.casefold(), text, event_date, now, now, source_session),
            )
            return row.lastrowid

    def correct(self, entry_id, text):
        text = checked_text(text, MAX_TEXT, "Memory text")
        with closing(self.connect()) as db, db:
            row = db.execute("UPDATE memory_entries SET text=?, edited_at=? WHERE id=?", (text, utc_now(), entry_id))
            return row.rowcount == 1

    def forget(self, entry_id):
        with closing(self.connect()) as db, db:
            return db.execute("DELETE FROM memory_entries WHERE id=?", (entry_id,)).rowcount == 1

    def search(self, query="", *, game="", since="", limit=MAX_RESULTS, offset=0):
        query = checked_text(query, MAX_QUERY, "Search query", allow_empty=True)
        game = checked_text(game, MAX_GAME, "Game", allow_empty=True)
        since = checked_text(since, 10, "Since date", allow_empty=True)
        filters, values = ["1=1"], []
        if game:
            filters.append("entry.game_key = ?")
            values.append(game.casefold())
        if since:
            filters.append("COALESCE(entry.event_date, substr(entry.created_at, 1, 10)) >= ?")
            values.append(checked_date(since))
        fields = "entry.id, entry.kind, entry.game, entry.text, entry.event_date, entry.created_at, entry.edited_at, entry.source_session"
        where = " AND ".join(filters)
        newest = "COALESCE(entry.event_date, entry.created_at) DESC, entry.id DESC"
        with closing(self.connect(readonly=True)) as db:
            if not query:
                rows = db.execute(
                    f"SELECT {fields} FROM memory_entries AS entry WHERE {where} ORDER BY {newest} LIMIT ? OFFSET ?",
                    [*values, limit, offset],
                ).fetchall()
            elif re.search(r"[ぁ-ゖァ-ヺ㐀-鿿]", query):
                # Japanese phrases may not have word boundaries in stored notes.
                rows = db.execute(
                    f"SELECT {fields} FROM memory_entries AS entry WHERE {where} "
                    f"AND (instr(entry.text, ?) > 0 OR instr(entry.game, ?) > 0) ORDER BY {newest} LIMIT ? OFFSET ?",
                    [*values, query, query, limit, offset],
                ).fetchall()
            else:
                # Quote each word and supply the MATCH expression as a bound value.
                words = re.findall(r"[^\W_]+", query)[:8]
                match = " OR ".join(f'"{word}"' for word in words)
                rows = db.execute(
                    f"SELECT {fields} FROM memory_fts JOIN memory_entries AS entry ON entry.id=memory_fts.rowid "
                    f"WHERE {where} AND memory_fts MATCH ? ORDER BY bm25(memory_fts), {newest} LIMIT ? OFFSET ?",
                    [*values, match, limit, offset],
                ).fetchall() if match else []
        return [dict(row, source="user-entered") for row in rows]
