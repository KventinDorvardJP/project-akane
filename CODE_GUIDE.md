# How Milestone 2 fits together

The main path is deliberately sequential:

```text
typed input -----------------------------+
audio filename -> STT -------------------+-> user text -> LLM -> bilingual JSON
mic -> temporary WAV -> STT -> delete WAV +                      |
                                                    validate ja/en fields
                                                               |
                                            JA -> VOICEVOX -> WAV bytes
                                            EN -> UTF-8 subtitle file -> OBS
                                                               |
                                              play WAV -> clear subtitle
```

## Read the code in this order

1. `config.py`: models, backend selection, microphone limits, and caption duration.
2. `main.py`: command matching, provider selection, and startup/cleanup.
3. `turns.py` and `conversation.py`: shared history, JA/EN validation, stage timing, and recovery.
4. `microphone.py`: start/stop capture, callback buffers, RMS validation, WAV output.
5. `speech_to_text.py` and `cloud_speech_to_text.py`: different providers, same result.
6. `llm_backends.py`, `ollama_backend.py`, and `openai_backend.py`: provider selection, requests, schema, usage, and error translation.
7. `text_to_speech.py`: `/audio_query`, then `/synthesis`, returning WAV bytes.
8. `subtitles.py` and `metrics.py`: generated output and timing/usage records.
9. `controls.py`, `stream_controls.py`, `windows_hotkeys.py`, and `playback.py`: optional hotkeys, one worker, cancellation, and interruptible output.
10. `tests/test_voice_pipeline.py` and `tests/test_controls_and_cloud.py`: examples of expected normal and failure behavior.
11. `subtitle_overlay.py` and `overlay/`: optional loopback server and Browser Source page. The server reads the existing caption file; the page owns both text and background, hides on blank/failure, and renders plain text. `subtitles.py` shares a short file read/write lock to avoid replacement conflicts on Windows. No OBS calls occur inside turn/cancellation locks. See `SUBTITLE_OVERLAY.md` for setup and tests.

## Contracts worth understanding

`get_llm_reply(messages)` returns **JSON text**, not a Python dictionary. The loop
uses `json.loads()` to validate exactly two non-empty string fields, `ja` and `en`.
It retains the original JSON string in history because message content is text.
`test_fake_backend.py` implements the same interface without an LLM request.

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
