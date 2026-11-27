# Single-PC controls and cloud conversation

These additions were explicitly authorized before the avatar milestone. They
extend the working voice prototype without requiring a second machine, Stream
Deck, extra subtitle server, or new Python dependency.

## Launch when ready

Normal terminal mode is unchanged:

```powershell
.\run.ps1
```

Optional background hotkeys, with local conversation:

```powershell
.\run.ps1 -Hotkeys
```

Optional hotkeys with paid OpenAI conversation:

```powershell
.\run.ps1 -Hotkeys -LlmBackend openai
```

Equivalent Python arguments are `--hotkeys --llm-backend openai`. Use
`--llm-backend ollama` to select local conversation explicitly. `LLM_BACKEND` in
`config.py` supplies the default; STT selection remains independent. Existing
`OPENAI_API_KEY` in `.env`/environment serves cloud STT and conversation. No secret
is added to tracked configuration. No automatic provider fallback occurs.

Start VOICEVOX yourself when speech is wanted. Local conversation also needs
Ollama. `-NoStt` and `-NoTts` remain available; `-FakeBackend -NoStt -NoTts` uses
no providers, microphone, or playback. Adding `-Hotkeys` still registers keys when
you launch it, even with those fake switches.

## Controls

| Control | Behavior |
| --- | --- |
| Ctrl+Shift+F8 | Start recording while idle; press again to end capture and transcribe. |
| Ctrl+Shift+F9 | Pause/resume. Pausing cancels the current turn and clears its caption. |
| Ctrl+Shift+F10 | Stop the current turn/speech and clear captions, without toggling pause. |
| `mic` or `record` | Same recording toggle, typed in the focused Akane console. |
| `audio filename.wav` | Transcribe a sample from `audio_samples`; do not delete it. |
| Typed text | Start a text turn while idle and unpaused. |
| `reset` | Cancel first; clear history after the worker finishes. |
| `status`, `help`, `exit` | Inspect state, show commands, or stop the session. |

Hotkeys register only for the running hotkey session and are released on exit.
They can work while another app has focus, but availability depends on Windows
and other apps' shortcuts. Change `HOTKEY_RECORD`, `HOTKEY_PAUSE`, and
`HOTKEY_STOP` if registration reports a conflict. Supported forms use Ctrl/Alt/Shift
with one letter, digit, or F-key; F12 is rejected because Windows reserves it.
No general key hook or keystroke log is used. The application does not send input
to other apps. Terminal mode remains usable if global keys are unavailable.

Hotkey capture stops automatically at `MIC_MAX_SECONDS`. Minimum duration and RMS
gates still apply. No continuous listening or automatic voice activation is added.
Microphone WAVs are temporary and removed after STT; cancelled capture is not saved.
`MIC_INPUT_DEVICE` selects capture; `AUDIO_OUTPUT_DEVICE` selects sounddevice
playback in hotkey mode (`None` follows the Windows default).

## Cancellation and sequencing

Exactly one worker owns recording -> STT -> LLM -> TTS -> playback. Input arriving
while thinking/speaking is rejected, not queued. Recording never overlaps Akane's
playback. Stop cancels recording or aborts chunked playback; actual device buffering
affects the audible stop delay, so no exact millisecond guarantee is made.

An in-flight STT, LLM, or VOICEVOX HTTP call can continue until completion/timeout.
Stop/pause discards its cancelled output and prevents late speech/caption revival;
it does not guarantee avoiding an API charge. New turns remain blocked until the
worker exits. Quit also waits before closing clients or deleting an uploaded file.
Configured request timeouts apply; this is cooperative cancellation, not a forced
network disconnect. Normal terminal mode remains blocking and cannot process stop
commands during playback; use hotkey mode for interruption.

Before a valid reply is committed, cancellation/failure removes its pending user
message. A valid bilingual reply already committed stays in history if speech is
stopped or fails. Reset deliberately clears that history once the worker finishes.

## Output and costs

- `runtime/subtitle_en.txt` remains the OBS caption source.
- Hotkey mode writes `runtime/status.txt` and `status.json`: idle, starting,
  recording, transcribing, thinking, synthesizing, speaking, paused, or stopped.
  JSON also reports paused/busy/phase. These files contain no transcript.
- `runtime/timings.csv` adds LLM provider/model, input/cached/output/reasoning
  token counts, and estimated conversation API cost. It stores no prompt/reply text.
- On the first new write, a recognized old CSV header is migrated after creating
  `timings.legacy.<UTC timestamp>.csv`. Unknown headers are preserved and reported.
- Local conversation has no API charge. OpenAI costs use reported input/cache/output
  tokens and `OPENAI_PRICING_USD_PER_MILLION`; reasoning is part of output usage,
  not charged twice. No usage means no invented estimate.

`OPENAI_MODEL` currently selects `gpt-6-luna`, with low reasoning and strict
`{"ja": "...", "en": "..."}` Responses output. The configured 1,024 output-token
limit includes reasoning. Refusal, missing text, malformed bilingual output, and
incomplete generation are recoverable errors. Automatic SDK retries are disabled.

`OPENAI_SESSION_BUDGET_USD = 0.50` is a **soft conversation session check**, now including hosted search fees (see WEB_SEARCH.md).
Each session starts at zero; it is not an account-level spending limit. A request
can exceed the remaining allowance. STT is separately billed. Unknown configured
model prices or missing prior usage block further budgeted calls; `None` explicitly
disables this check. Estimates use standard text rates and exclude cache-write,
tier, regional, and other additional charges. Check official prices when changing
models/rates and compare actual charges in your API dashboard.

Implementation references (checked 2026-11-09):

- [OpenAI strict structured output](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
- [OpenAI selected model and pricing](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [Microsoft RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)
- [Microsoft GetMessage](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getmessagew)
- [sounddevice raw streams](https://python-sounddevice.readthedocs.io/en/latest/api/raw-streams.html)

## Verification and remaining acceptance

Executed verification: **66 tests passed**, `pip check` reported no broken
requirements, CLI help loaded, and the PowerShell launcher parsed successfully.

Automated verification uses mocked hotkey/native APIs, audio streams, and provider
responses, including the installed OpenAI SDK through an in-memory HTTP transport.
It does not register real keys, open a device, control apps, play sound, or spend
API credit. Cases cover registration conflict cleanup, cancellation at turn stages,
late-caption prevention, deferred reset, temp-file cleanup, recording limits,
playback abort, schema/usage/error handling, budgets, and legacy CSV preservation.

Run from the project venv:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

Live checks for the user when free (not performed by the agent for this addition):

1. Start required services yourself and launch `run.ps1 -Hotkeys`. Confirm the
   displayed keys register and the correct microphone/output devices are selected.
2. Record an utterance with F8 twice. During a spoken reply, use F10; confirm speech
   stops and captions stay clear. Use F9 during recording/thinking, then resume.
3. Attempt F8 while thinking: confirm it does not queue a second recording. Try
   reset/exit during processing; the terminal should wait safely for the worker.
4. If wanted, choose OpenAI explicitly and make one paid turn. Check JA/EN quality,
   token/cost columns and latency. No live cloud-conversation speed claim is made.
5. Check OBS's existing Text (GDI+) file source and playback audio routing yourself.
   Hotkey playback uses sounddevice rather than winsound; confirm it reaches the
   intended OBS source once. If no subtitle source exists yet, follow README's OBS
   steps. No OBS changes are required merely to run Akane.

Local development measurements are not a latency guarantee for this cloud backend
or evidence of another machine's hotkey/device acceptance. Use benchmark.py for
an explicit measurement of your own local services.
