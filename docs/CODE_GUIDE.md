# How Milestone 2 fits together

Input and model generation are sequential; speech can prepare one part ahead:

```text
typed input -----------------------------+
audio filename -> STT -------------------+-> user text -> LLM -> bilingual JSON
mic -> temporary WAV -> STT -> delete WAV +                      |
                                                   validate paired segments
                                                               |
                                           JA part -> selected TTS -> WAV bytes
                                           EN part -> subtitle file -> OBS
                                                               |
                                             play -> next pair -> clear
```

## Read the code in this order

The optional WoW module is described in [WOW_MODULE.md](WOW_MODULE.md):
`wow_addon/AkaneData/` collects on events and serializes optical packets;
`wow_module.py` validates/reassembles, tracks revisions and freezes local copies;
`wow_capture.py` is the replaceable foreground-only Win32 screen adapter. It is
started only with `--wow`. Neither receiving events nor freezing invokes a model.

1. `config.py`: models, backend selection, microphone limits, and caption duration.
2. `main.py`: command matching, provider selection, and startup/cleanup.
3. `turns.py` and `conversation.py`: shared history, JA/EN validation, stage timing, and recovery.
4. `microphone.py`: start/stop capture, callback buffers, RMS validation, WAV output.
5. `speech_to_text.py` and `cloud_speech_to_text.py`: different providers, same result.
6. `llm_backends.py`, `ollama_backend.py`, and `openai_backend.py`: provider selection, requests, schema, usage, and error translation.
   `context_tools.py` projects approved frozen sources for OpenAI's bounded local
   tool round; `tests/test_context_tools.py` exercises the SDK loop without services.
7. `text_to_speech.py` and its adapters: select VOICEVOX/GPT-SoVITS and return WAV bytes.
8. `subtitles.py` and `metrics.py`: generated output and timing/usage records.
9. `controls.py`, `stream_controls.py`, `windows_hotkeys.py`, and `playback.py`: optional hotkeys, one worker, cancellation, and interruptible output.
10. `tests/test_voice_pipeline.py` and `tests/test_controls_and_cloud.py`: examples of expected normal and failure behavior.
11. `subtitle_overlay.py` and `overlay/`: optional loopback server and Browser Source page. The server reads the existing caption file; the page owns both text and background, hides on blank/failure, and renders plain text. `subtitles.py` shares a short file read/write lock to avoid replacement conflicts on Windows. No OBS calls occur inside turn/cancellation locks. See `SUBTITLE_OVERLAY.md` for setup and tests.

## Contracts worth understanding

`witchcraft_bridge.py` is an opt-in loopback mailbox for the existing GUI, not a
second conversation backend. HTTP handlers authenticate and queue bounded commands;
`WindowSession.tick()` accepts them on the main thread through the same controller.
Only plain questions, English replies and status/control notices are mirrored.
`witchcraft_terminal.py` is a small polling ConPTY frontend launched by
`start_witchcraft.ps1` through Witchcraft's existing custom-child support. It owns
no model/audio state and never automatically retries a question. See WOW_CONTEXT.md.

The companion daemon's opt-in `src/akane.js` relay adds a bounded, validated
English/recording-state extension to the existing ring. Its Lua `Akane.lua`
component prints new English replies locally and sends explicit press/hold/release
gestures. `StreamController.hold_to_talk()` owns a gesture-specific recording
lease and routes release to the existing stop event; timeout/early release discard
instead of transcribing. See WOW_PUSH_TO_TALK.md for the manual acceptance limits.

For accurate context freshness, `RingWriter.captureAnchor()` binds a slot's first
write-start time to one host/UI session. `contextClockProtocol: 1` negotiates a v2
context envelope with the slot captured before the native read. The cache validates
this reference and uses its lower bound, preserving it across multipart assembly,
replays, same-UI cursor resets and quest resumption. V1 packets retain legacy
transport timing. Neither the threshold nor old capture timestamps are advanced.

`get_llm_reply(messages)` returns **JSON text**, not a Python dictionary. The loop
uses `json.loads()` to validate an ordered `segments` array of nonempty `ja`/`en`
pairs. The original JSON is retained once in history. `parse_bilingual_reply()`
joins pairs for the full GUI reply/compatibility, while `reply_delivery.py` keeps
the boundaries for TTS/captions. Older single-pair backends are still accepted.
`test_fake_backend.py` echoes input as one pair without an LLM request.

`reply_delivery.py` owns a single synthesis executor with at most one next part
prepared during current playback. It releases old WAVs, joins in-flight work during
cleanup and checks cancellation before synthesis/publication/playback. Speech errors
page remaining English; they do not trigger another model request. See `REPLY_CHUNKS.md`.

`OpenAIBackend` is a callable object: `backend(messages)` has the same contract as
the Ollama function. Its instance retains a client, last usage, and estimated
session spend. The provider factory chooses once at startup; it never silently
falls back. The cloud adapter requests strict JSON via Responses and closes its
client after the turn worker has stopped. Both providers expose optional
`last_usage` metadata without changing the reply return type.

`transcribe_audio(model_or_client, audio_path)` returns a dictionary containing
`text`, `languages`, `language_probability`, and `elapsed_seconds`. In `initialize_stt`,
`partial()` binds the model/client once, giving the loop a common `recognize_audio(path)`
call. Cloud confidence can be `None`, and language metadata is diagnostic rather than
proof of every language in mixed speech.

`record_microphone(path, input_device=...)` returns a path after its input stream is
closed. Its callback receives small buffers while `input()` waits for Enter. Each
buffer is copied before reuse. The main loop owns a temporary directory around
recording and STT, which removes the microphone WAV even when transcription fails.
Standalone recording experiments can still save a WAV intentionally.

`synthesize_speech(ja_text)` returns WAV **bytes**. `winsound.SND_MEMORY` plays those
bytes synchronously; it does not create a temporary playback file.

`text_to_speech.py` selects `voicevox_backend.py` by default or the optional
`gpt_sovits_backend.py` WebUI client. Both share `SpeechSynthesisError` and PCM WAV
validation from `speech_audio.py`. GPT-SoVITS is imported on demand, so the default
path needs no Gradio client. The main/UI/hotkey turn flow does not handle model
loading or reference uploads itself. See `GPT_SOVITS.md` for setup and scope.

In hotkey mode, `playback.play_wav()` instead writes short chunks to a sounddevice
output stream and aborts queued audio on cancellation. The input stream is closed
before STT, and the single worker prevents capture/playback overlap.

## Optional hotkey mode

`WindowsHotkeys` owns a Windows message-loop thread. RegisterHotKey reports only
the configured combinations; this is not a general keyboard hook. It posts actions
to a queue rather than running audio/network work inside the key listener.

`StreamController` consumes that queue while one worker performs the sequential
turn. `ControlState` uses a lock to guard pause, active turn, and caption writes.
`TurnControl` holds cancellation and recording-stop events. Each stage checks
cancellation; stop/pause during HTTP discards its eventual output. Events cannot
forcibly cancel the provider's bill. New turns are rejected until the worker exits.

Record presses during capture end that utterance. During later stages they do not
start a second recording. Stop cancels without pausing future turns; pause also
blocks new input until resumed. Reset waits for the worker before changing history.
Quit waits for active requests to return/time out before closing SDK clients and
deleting microphone files. Console input is polled on the main thread in this mode;
there is no permanently blocked background stdin reader.

The guarded caption write matters: checking an event and then writing without a
shared lock could let a late reply restore subtitles immediately after stop cleared
them. Cancel and caption publication use the same lock.

## Error and state handling

- Failed recording/STT never adds a user message to history.
- Failed LLM or malformed bilingual output removes the pending user message.
- Failed speech keeps the valid conversation turn and leaves English readable.
- The history limit removes complete user/assistant pairs and retains the system prompt.
- Startup/reset/exit clear subtitles. EOF and Ctrl+C also close the cloud client.
- A subtitle or metrics file error is reported without ending conversation.

`subtitles.write_subtitle()` writes a closed temporary file and atomically replaces
the destination. OBS should see a complete old or new UTF-8 caption, avoiding a
partially written multibyte character. No Tkinter window is required for OBS;
`subtitle_window.py` is an optional local preview.

## Timing boundaries

STT, LLM, and TTS generation are measured separately. Response-ready time starts
immediately before STT for audio input, or after typed input, and ends when the
generated audio is ready. It excludes microphone recording, WAV saving before STT,
spoken playback, and any minimum caption hold. `runtime/timings.csv` stores durations,
backend labels, status, and character counts, without storing transcript/reply text.

It also stores reported token usage and estimated conversation cost. Legacy CSV
headers migrate atomically after saving original bytes as a backup. The optional
cloud budget uses reported usage and configured standard text rates, so it is a
soft session check, excludes STT, and can overshoot by one request. Unknown pricing
or missing usage blocks subsequent budgeted requests rather than inventing costs.

## Small exercises for later

`settings_store.py` derives all editable fields from `config._defaults`, captured
before personal overrides are loaded. It parses/validates form values and atomically
saves differences while detecting external file edits. `settings_window.py` builds
the grouped form only on explicit click. Saving does not mutate active config.
WindowSession permits restart only after initialization/active turns finish. The
CLI closes the old backend, reloads config, and rebuilds the GUI in the same process
without replaying old launch overrides. See `SETTINGS.md` and `tests/test_settings.py`.

The optional Japanese reference is described in [TEXT_CONTEXT.md](TEXT_CONTEXT.md).
`text_context.py` receives bounded raw text on a separate thread, storing only one
latest value. The Tk thread polls snapshots. StreamController snapshots selected
context before starting a turn; TurnProcessor quotes it alongside the user question
without promoting it to system instructions. `question_part()` keeps quoted dialogue
out of web-search intent detection. Reception never starts a model request.

The native interface is described in [CONTROL_WINDOW.md](CONTROL_WINDOW.md).
`window_model.py` holds plain state; `window_session.py` drains worker events on
the main thread and reuses StreamController; `control_window.py` handles widgets.
TurnProcessor emits data through an optional callback rather than calling Tk from
workers. GUI replies persist independently of short-lived OBS captions. There is
no second conversation worker/client, and STT initializes in the background.

The cloud search extension is explained in [WEB_SEARCH.md](WEB_SEARCH.md).
`web_search.py` holds the lookup policy and source/fee extraction; the adapter
adds the hosted tool without changing its JA/EN return contract. Source links live
in `last_sources` metadata, while `source_output.py` writes the citation view.
The turn controller protects publication and clearing with the existing cancel
lock. No browser/server or manual custom-tool loop is required for hosted search.
`tests/test_web_search.py` tests request/response handling through the installed SDK
with a mock HTTP transport; it does not prove live model accuracy.

- Trace why a failed TTS request keeps history while a failed LLM request rolls it back.
- Change a microphone threshold in `config.py` and predict which regression case it affects.
- Follow one temporary WAV through creation, transcription, and exception cleanup.

## Optional transcript path

`main.py` owns a `session_log.SessionLog` only when SESSION_LOG_ENABLED is true,
then closes it after workers/clients finish (including GUI restarts). Files are
under ignored runtime/session_logs. The shared TurnProcessor logs selected
display events and captions before forwarding GUI events; logging works in both
terminal and window modes. One turn ID links accepted recording/input, generated
JA/EN segments, sources, metrics and the final outcome. StreamController also
records recording phases and controls. Reply delivery supplies per-part synthesis
and playback events, including generated but cancelled prefetch.

SessionLog uses a lock because UI/turn/synthesis workers can report concurrently.
It writes each event to UTF-8 JSONL and a readable text companion, flushing each,
with UTC and monotonic elapsed time. Write failures disable logging and warn once;
they do not stop chat or add provider requests. No audio callback writes logs.
Playback dispatch and caption-file publication timestamps do not measure device
or OBS onset. See SESSION_LOGS.md and tests/test_session_log.py.
