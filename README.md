# Project Akane

A Windows Python prototype for a bilingual AI companion and streaming co-host.
Akane accepts typed or spoken questions, replies in Japanese and English, speaks
Japanese through VOICEVOX, and can write English subtitles for OBS.

**Current stage:** voice interaction plus a native control window, interchangeable
local/cloud backends, cloud web lookup, an optional OBS subtitle overlay, and
opt-in Japanese reference text from LunaTranslator. Avatar animation, Twitch chat and persistent memory are planned.
This is a source-code prototype, not a packaged desktop release.

## What works

- Native control window with persistent EN/JA conversation, typed input,
  Record/Finish recording, Pause/Resume, Stop, Reset, and audio-file input.
- Terminal mode and optional global hotkeys, using the same single turn worker.
- Ollama or OpenAI conversation with the same bilingual JSON contract.
- Cloud transcription or optional local Faster-Whisper; temporary microphone WAV cleanup.
- Japanese VOICEVOX playback and an English subtitle file for OBS Text (GDI+).
- Optional OBS Browser Source with wrapping, a fitted translucent background, and
  automatic hiding when captions clear; runs in the same Akane process.
- Optional hosted web search for cloud conversation, source links, usage estimates,
  and a soft session budget. `/search ...` forces lookup; identity questions also
  request verification. Search does not guarantee factual correctness.
- Optional LunaTranslator original-text preview/attachment, frozen at question or
  recording start. Receiving text never triggers a model request by itself.
- Service-free regression tests and per-turn timing/usage records.

## Requirements and setup

Tested development target: **Windows, Python 3.12, 64-bit**. Tkinter is supplied by
the standard Python installer. Platform-specific audio/hotkey code makes Windows
the supported target for now.

From the repository directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The core requirements list direct app dependencies with pinned versions. They do
not install local model weights, VOICEVOX, Ollama, CUDA runtimes, or unrelated
text-extractor experiments. Pip resolves their transitive dependencies.

Try the control window without API credit, local services, recording or playback:

```powershell
.\run.ps1 -Gui -FakeBackend -NoStt -NoTts
```

The fake backend echoes input to check the pipeline; it does not test model quality.
`run.ps1` uses the project's venv directly, so activation is optional. If scripts
are blocked, run the equivalent Python command without changing execution policy:

```powershell
.\.venv\Scripts\python.exe main.py --gui --fake-backend --no-stt --no-tts
```

## Choose the services

Conversation and speech recognition are selected independently in `config.py`.

| Layer | Current default | Alternatives / setup |
| --- | --- | --- |
| Conversation | Ollama, `qwen3:4b-instruct-2507-q4_K_M` | OpenAI, currently `gpt-6-luna` |
| Speech recognition | OpenAI cloud transcription | Optional local Faster-Whisper, `base` / CPU / int8 |
| Speech output | VOICEVOX, 冥鳴ひまり・ノーマル, style 14 | Configure engine address/style or disable TTS |
| Display | Control window when launched with `-Gui` | Terminal; optional OBS subtitle file |
| Japanese reference | Disconnected, attachment off | Explicitly connect LunaTranslator and select attachment |

For local conversation, install/start Ollama and pull the configured model:

```powershell
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

For cloud conversation and/or cloud STT, copy the placeholder file and edit it:

```powershell
Copy-Item .env.example .env
```

```dotenv
OPENAI_API_KEY=your_api_key_here
```

An existing environment variable also works. Keep real keys out of tracked source;
`.env` and its variants are ignored. Only `.env.example` is committed.

For local STT, install the optional dependency and select `STT_BACKEND = "local"`:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-local-stt.txt
```

Local model weights download on first use if not cached. CPU/int8 is the documented
starting configuration; GPU STT needs compatible NVIDIA runtimes set up separately.

For speech, install/start [VOICEVOX](https://voicevox.hiroshiba.jp/) yourself. The
default engine URL is `http://127.0.0.1:50021`. Published audio using the current
voice needs the credit **`VOICEVOX:冥鳴ひまり`**; see [CREDITS.md](CREDITS.md) and its
official terms. No voice binaries, models, character art, or game audio are bundled.

## Run Akane

Control window, cloud conversation, and optional global hotkeys:

```powershell
.\run.ps1 -Gui -Hotkeys -LlmBackend openai
```

Omit `-Hotkeys` to use window buttons only. Use `-LlmBackend ollama` for local
conversation, or omit the override to use `config.py`. Without `-Gui`, the terminal
interface remains available. `-NoStt` disables voice input; `-NoTts` keeps replies textual.

| Global key | Action |
| --- | --- |
| Ctrl+Shift+F8 | Start recording; press again to finish and transcribe |
| Ctrl+Shift+F9 | Pause/resume; pausing cancels the current turn |
| Ctrl+Shift+F10 | Stop the current turn/speech and clear live captions |

Keys register only with `-Hotkeys`. One turn runs at a time; input while busy is
rejected rather than queued. Stop/pause suppress late results, but an already sent
HTTP request may still complete and bill. Exit waits for active work to finish.

The GUI keeps valid replies visible after speech finishes. Source links open only
when clicked. In terminal mode, commands include `mic`, `audio`, `reset`, `pause`,
`stop`, `help`, and `exit`. Ordinary terminal recording uses Enter to start/stop;
GUI/hotkey recording uses the toggle and automatically ends at its duration cap.

## Personal configuration

`config.py` contains public defaults. For machine-specific changes, create the
ignored `config.local.json` beside it, using existing setting names:

```json
{
  "MIC_INPUT_DEVICE": 1,
  "AUDIO_OUTPUT_DEVICE": null
}
```

The public microphone default is `None` (Windows default device). List device IDs
with `.\.venv\Scripts\python.exe -m sounddevice` and use your own ID/name; numbers
vary by machine. Local JSON overrides only the specified settings. Keep API keys in
`.env`, not this JSON file. Restart after changing settings or `personality.txt`.

Akane's speech volume is controlled by `VOICEVOX_VOLUME_SCALE` in `config.py`
(or your local JSON overrides). The default `1.5` boosts the generated speech;
`1.0` restores normal volume, and `0.0` mutes it. Akane accepts values up to `2.0`.
Restart after changes. This applies to GUI, hotkey and terminal playback without
changing Windows/OBS mixer levels. Lower the value if the voice sounds distorted.

## Optional Japanese reference and OBS output

In the window, click **Connect text feed**, capture a new line in LunaTranslator,
verify the preview, and check **Include Japanese line** before asking your question.
The configured endpoint is `ws://127.0.0.1:2333/api/ws/text/origin`. Attachment is
opt-in; missing/stale selected text blocks that turn instead of silently substituting
another sentence. See [TEXT_CONTEXT.md](TEXT_CONTEXT.md) for manual service setup.

OBS is optional for reading replies. For stream subtitles with a background that
fits the text and disappears when empty, add `-Overlay` to your launch command.
In OBS, add a **Browser** source using `http://127.0.0.1:8765/overlay`, initially
1200 × 420 pixels. The GUI's **Copy overlay URL** button supplies the address.
Hide the previous subtitle text/background sources to avoid duplicates. See
[SUBTITLE_OVERLAY.md](SUBTITLE_OVERLAY.md) for appearance and manual setup.

The existing **Text (GDI+)** / **Read from file** output remains available at
`runtime/subtitle_en.txt` (created on startup). Disable chatlog mode and verify
wrapping/audio routing in your own scene. Akane does not configure OBS or
launch/control other apps. Whole-reply captions are used; sentence paging is planned.

Generated outputs under `runtime/` include captions, source pages, status, timing
CSV and benchmark results. They are ignored. Audio samples/recordings are local too;
bring your own audio when testing file input.

## Costs and data flow

OpenAI conversation sends the personality, retained history and selected reference
text to the API. Cloud STT sends chosen/recorded audio. Local Ollama/STT/VOICEVOX
perform their respective work locally; downloads and an explicitly enabled hosted
search are separate network operations.

The default **$0.50 soft session budget** estimates conversation tokens plus search
fees. It excludes STT and other unmodelled charges, can overshoot by a request,
and resets when the program restarts. It is not an account spending limit. Cost
rates are maintained in configuration; consult current provider pricing.

## Verification and prototype limits

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
```

Tests mock providers, text-feed sockets, audio, keys, widgets and the event loop.
Overlay tests use their own temporary loopback HTTP servers; browser-client tests
use Node when available, with fake DOM/fetch/clocks. No Node install is needed to run Akane. Tests do not
spend API credit, connect to LunaTranslator, open windows, record, or play sound.
A Windows GitHub Actions workflow runs the service-free checks on pushes/PRs.

Before relying on a stream setup, manually verify your microphone/output selection,
hotkey conflicts, native GUI scaling, cloud/search response quality and latency,
LunaTranslator frame delivery, and OBS subtitle/audio routing. Mocked tests do not
establish these live conditions. Models/STT/translations can still make mistakes.
There is no continuous listening, automatic voice activation, avatar, Twitch chat,
persistent memory, or autonomous speaking yet.

`benchmark.py --runs 3` explicitly exercises local Ollama/VOICEVOX and writes local
diagnostics without recording/playback. It needs those services. `--cloud-stt` adds
one paid transcription check; no benchmark runs during ordinary startup or tests.

## Guides and roadmap

- [Control window](CONTROL_WINDOW.md)
- [OBS subtitle overlay](SUBTITLE_OVERLAY.md)
- [Recording hotkeys, cancellation and cloud conversation](STREAM_CONTROLS.md)
- [Cloud web search, sources and fees](WEB_SEARCH.md)
- [Japanese reference text](TEXT_CONTEXT.md)
- [Code walkthrough](CODE_GUIDE.md)
- [Roadmap](ROADMAP.md), [changelog](CHANGELOG.md), and [voice credits](CREDITS.md)

Tests and the replacement backend are part of the source distribution. Local
learning notes, machine-specific reports, one-off sample scripts, credentials,
runtime files and audio are intentionally excluded. Development history remains
under **Unreleased** until a version is deliberately released/tagged.

## Licensing

No license has been selected for Akane's original source code yet. Dependencies,
model weights, services and voices retain their own terms; see [CREDITS.md](CREDITS.md).
