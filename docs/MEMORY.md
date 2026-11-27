# Persistent memory

In **Settings → Session**, enable **Enable persistent memory**, then save and
relaunch Akane. Memory defaults to off. Use the control window’s message box
or the Akane terminal to manage it.

| Command | Example and behavior |
| --- | --- |
| `/remember [fact] [game] text` | `/remember Avoid spoilers for future chapters.` The default kind is a standing fact. To scope it to a game, put its name in square brackets. |
| `/remember event [date] [game] text` | `/remember event [Cold Steel III] We left off at the branch campus.` Uses today's date unless you give a date before the game, for example `/remember event 2026-11-25 [Cold Steel III] We reached the branch campus.` |
| `/memories [--page N] [words]` | `/memories campus` finds notes and their IDs. With no words it lists recent notes. Follow the page prompt for older matches. |
| `/correct ID new text` | `/correct 1 We left off outside the branch campus.` Updates the text and keeps the original game, date, and ID. |
| `/forget ID` | `/forget 1` removes that entry from future memory searches. Use **Reset chat** if the earlier wording is still in the current conversation. |

After relaunching with OpenAI conversation selected, ask: “Where did we leave off
in Cold Steel III?” Luna can choose `search_memory` and use the results in her
reply. The same saved facts and events are available after a restart. The tool
can look for keywords and filter by an exact game title or an inclusive date.
Use likely keywords from the note's language. If a match is missing, try a
distinctive name or phrase rather than a synonym.

## Storage and model access

Akane saves only explicitly entered notes in the Git-ignored
`runtime/memory.sqlite3` database. Entries carry their kind, game when supplied,
date, and user-entered provenance. If transcript logging is active, Akane also
keeps a local link to the session where you saved the note. That link appears in
`/memories`. It stays local during tool calls.

Memory management commands run entirely on your machine and are available with
either conversation backend. Luna's search tool is enabled only with OpenAI.
It can run one read-only lookup per turn and return up to four matching notes.
When Luna uses it, the matching text and dates go to OpenAI in the existing
continuation request, which counts against the normal session budget.

Session transcripts and the short conversation history are separate. Disabling
persistent memory hides the tool and preserves the database for a later session.
Saved notes are user recollections. Check live game context for current state.
