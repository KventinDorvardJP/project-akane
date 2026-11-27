"""Explicit typed memory actions, handled before any conversation request."""
import re
import sqlite3


COMMANDS = {"/remember", "/memories", "/correct", "/forget"}
HELP = (
    "/remember [fact|event [YYYY-MM-DD]] [game in brackets] text\n"
    "/memories [--page N] [search words]\n"
    "/correct ID new text\n"
    "/forget ID"
)


def is_memory_command(text):
    return isinstance(text, str) and split_argument(text)[0].lower() in COMMANDS


def split_argument(text):
    parts = text.split(maxsplit=1)
    return parts[0] if parts else "", parts[1] if len(parts) > 1 else ""


def list_memories(store, argument):
    page_size, page, query = 10, 1, argument
    option, rest = split_argument(argument)
    if option == "--page":
        number, query = split_argument(rest)
        if not re.fullmatch(r"[1-9][0-9]{0,5}", number):
            raise ValueError("Page must be a positive number.")
        page = int(number)
    rows = store.search(query, limit=page_size + 1, offset=(page - 1) * page_size)
    if not rows:
        return "No matching memories on this page."
    lines = []
    for row in rows[:page_size]:
        game = f" · {row['game']}" if row["game"] else ""
        day = row["event_date"] or row["created_at"][:10]
        lines.append(f"#{row['id']} · {row['kind']} · {day}{game}\n{row['text']}")
        if row["source_session"]:
            lines.append(f"Saved in session {row['source_session']}")
    if len(rows) > page_size:
        lines.append(f"More results: /memories --page {page + 1} {query}".rstrip())
    return "\n\n".join(lines)


def memory_command(store, text, *, source_session=None):
    """Return the local result to display. Nothing here goes to the model."""
    if store is None:
        return "Memory is off or unavailable. Enable Persistent memory in Settings → Session and restart."
    command, argument = split_argument(text)
    try:
        if command.lower() == "/remember":
            keyword, remainder = split_argument(argument)
            kind, note = (keyword.lower(), remainder) if keyword.lower() in ("fact", "event") else ("fact", argument)
            event_date = None
            if kind == "event":
                first, rest = split_argument(note)
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", first):
                    event_date, note = first, rest
            game = ""
            if note.startswith("["):
                game, sep, note = note[1:].partition("]")
                if not sep or not game.strip():
                    raise ValueError("Put the game name inside brackets, for example [Cold Steel III].")
            entry_id = store.add(kind, note, game=game, event_date=event_date, source_session=source_session)
            return f"Saved {kind} #{entry_id}."
        if command.lower() == "/memories":
            return list_memories(store, argument)
        identifier, note = split_argument(argument)
        if not re.fullmatch(r"[1-9][0-9]{0,17}", identifier):
            raise ValueError("Use a memory ID shown by /memories.")
        entry_id = int(identifier)
        if command.lower() == "/correct":
            changed = store.correct(entry_id, note)
            return f"Corrected #{entry_id}." if changed else f"No memory #{entry_id}."
        if note:
            raise ValueError("Use /forget ID.")
        changed = store.forget(entry_id)
        return f"Forgot #{entry_id}." if changed else f"No memory #{entry_id}."
    except ValueError as error:
        return f"Memory: {error}\n{HELP}"
    except (sqlite3.Error, OSError):
        return "Memory database is unavailable. Try again after checking the runtime folder."
