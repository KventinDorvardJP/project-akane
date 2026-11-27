# Session transcripts

Optional transcripts let you compare your external recording with what STT heard,
what Akane generated, and which parts reached playback. They do not affect the
conversation prompt, memory, model calls, or OBS configuration.

In the GUI/shared-worker modes, `input_accepted` saves a typed question before
game-context collection. This retains the question even if context fails before
the normal `user`/model event. In-game Witchcraft questions use the same events
and turn IDs as control-panel questions; full snapshot attachments stay in the
optional transcript and are not mirrored into Witchcraft's conversation.
The in-game microphone button also records `hold_to_talk` press/release/lease
control events. Recorded/transcribed/generated/played events keep their existing
meaning; a cancelled lease does not imply an STT request or saved audio archive.
`wow_context_failure` includes the failure message and, for an exhausted fresh-data
wait, the required domains and each domain's status/age. These diagnostics contain
no raw snapshot; successful selected attachments retain their existing transcript.

With the new `-Wow` module, collection never waits on a question. Optional logs
record frozen snapshots, selective `context_tool` calls, `wow_receiver_error`, and
deduplicated `wow_game_event` death/level/zone notifications. Game events do not
initiate replies or paid requests. See [WOW_MODULE.md](WOW_MODULE.md).

## Enable and find a session

1. In **Settings → Session**, enable **Record session transcript**.
2. Use **Save & restart** while idle. Logging is off by default; saving alone
   applies to the next session. Terminal users can set `SESSION_LOG_ENABLED = True`
   in their local configuration before launching.
3. The control window shows **Transcript: On**. **Copy log path** copies the
   readable transcript path. The terminal also prints it at startup.

Each enabled launch/restart creates a unique pair in `runtime/session_logs/`:

- `<UTC timestamp>_<unique ID>.txt`: readable chronological transcript.
- The matching `.jsonl`: one structured event per line, for later analysis.

Both files flush after each event, so you can read them while Akane runs. Reset
chat does not delete earlier log entries. A normal exit closes the files with a
session-end event; a crash/power loss may leave the last event incomplete. No
automatic rotation/deletion is performed. Disabling logging stops future session
logs; it leaves existing ones on disk for you to review or delete.

## Review a session

From the project folder, print a report for the most recent session.

```powershell
.\.venv\Scripts\python.exe -X utf8 review_session.py
```

To choose an older session, pass the path to its `.jsonl` or the companion `.txt`
from **Copy log path**. Append `> .\runtime\session_review.txt` to save the output
under the Git-ignored runtime folder.

The report shows submitted or recognized text, generated replies, recorded voice
and fallback outcomes, timing, and recording markers. For microphone turns the
wait starts at the logged end of capture. For other inputs it starts at the logged
turn start. The wait ends when Akane calls the audio player. Check the recording
for audible onset and subtitle appearance in OBS.

The command reads logs locally and makes no service calls. Use the exchanges to
judge Akane's character and translation yourself, or share the excerpts you want
someone else to review.

## Match a recording to the transcript

Each event includes UTC time, seconds since this Akane session began, a sequence
number and, when applicable, a turn ID. Elapsed time uses a monotonic clock so
Windows wall-clock adjustments do not distort intervals.

After starting your external recording, click **Mark recording** (or enter `mark`
in either terminal mode). It records and displays a numbered timestamp. For a
clear point in your recorded voice, say “marker one” immediately after clicking
marker 1. This is manual alignment; the button does not start another recorder,
play a tone, or control OBS. For precise frame/audio onset measurements you still
need the recording itself.

## What is saved

- Accepted turn start, input mode, selected Japanese-reference snapshot.
- Microphone stream-start notification and successful capture return; STT text,
  reported language metadata and transcription duration.
- User input and Akane's complete validated Japanese reply and selected translation, ordered paired segments
  and source metadata. Reply events mean **generated**, not necessarily spoken.
- Per-part TTS start/end, duration and cancellation state; playback dispatch/end,
  completed/stopped/error outcome, and subtitle-file publication/clearing.
- Recorded controls, resets, notices/errors, and turn outcome.
- Existing timing/token/cost/search-count metadata, attached to its turn ID.
  Search counts come from backend metadata, not Akane's statements about herself.
- OpenAI local-context tool selections/status/result sizes and per-request usage;
  final usage/cost totals cover both retrieval and answer requests. Selected full
  snapshots remain local diagnostic records even when Luna requests only a subset
  or no data. See [CONTEXT_TOOLS.md](CONTEXT_TOOLS.md).
- Provider-returned web-search queries, associated with the turn and model request.
  Singular/plural query fields are supported, with bounded text and truncation flags.
  If the provider omits queries, the log marks their absence; it never invents them.
  Retrieved page bodies are not saved. Older logs contain sources/counts only.

TTS may finish for a prefetched part that never plays. A stopped late LLM result
is not saved as a visible reply. Missing or invalid audio can end a turn before
any model request. A playback-start event marks the call to the player, not the
moment sound reaches the speakers; subtitle events mark file writes, not OBS
rendering. A missing session/turn-end event can indicate an interrupted run.

## Local files and failures

The existing `/runtime/` ignore rule excludes these files from ordinary Git
commits. The logger does not upload logs, archive raw microphone audio/WAV output,
dump configuration/environment variables/API keys, or save the system prompt.
Conversation text, selected context, source URLs and displayed error messages
are saved as plain text; anything sensitive you say/type can therefore appear.
Inspect files before sharing them.

Disk/serialization failures disable transcript writing and report a warning while
chat continues. The indicator changes to **Unavailable**. Earlier lines remain
usable, but the text and JSONL files may diverge at the failed write. Fix storage
and restart to begin a new session. This feature does not change `timings.csv`,
which continues recording only metrics when transcript logging is off.
