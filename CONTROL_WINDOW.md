# Akane control window

A native window in the existing Python process. Read replies directly without OBS
or a subtitle preview. Terminal mode remains available. No web server or extra process
is required; the window uses Python's bundled Tkinter. Its optional LunaTranslator
text feed adds the pinned `websockets` dependency.

Add `-Overlay` for optional OBS Browser Source subtitles with a fitted background.
That loopback server runs in this process too. **Copy overlay URL** is enabled only
when it starts successfully; the footer shows its status. See
[SUBTITLE_OVERLAY.md](SUBTITLE_OVERLAY.md) for the manual OBS setup.

## Launch

Cloud conversation with the current Luna settings:

```powershell
.\run.ps1 -Gui -LlmBackend openai
```

Add `-Hotkeys` for the configured global recording/pause/stop keys:

```powershell
.\run.ps1 -Gui -Hotkeys -LlmBackend openai
```

Without `-LlmBackend`, the configured provider is used (currently Ollama).
Direct venv equivalent:

```powershell
.\.venv\Scripts\python.exe main.py --gui --hotkeys --llm-backend openai
```

Start VOICEVOX yourself for speech, and Ollama for local conversation. The existing
API key supplies cloud conversation/STT. The window does not configure OBS, audio
routing, or other apps. The launch terminal may stay open for logs, but you do not
need to watch it to read replies or operate Akane.

First try the window with **no provider calls, recording, or playback**:

```powershell
.\run.ps1 -Gui -FakeBackend -NoStt -NoTts
```

This opens a window only when you launch it. The fake backend echoes text in the
bilingual format; it does not test intelligence or Japanese quality.

## Controls and display

**Japanese reference · LunaTranslator** adds opt-in connection and an **Include
Japanese line** checkbox. Verify the preview, then ask by text or microphone. The
attached line is frozen at turn/recording start and shown in conversation history.
Incoming lines never trigger replies. See [TEXT_CONTEXT.md](TEXT_CONTEXT.md).

- **Conversation:** English and Japanese replies, user input, and clickable source
  references. Replies remain after speech/subtitles finish. The last 100 visible
  messages stay in memory, separately from the smaller model-history limit. The
  display does not restore context already removed from the model's history.
- **Record / Finish recording:** explicitly start/stop bounded microphone capture.
  Available once speech recognition is ready. **Stop** cancels recording without STT.
- **Pause / Resume:** cancel the turn and block new input until resumed.
- **Stop:** cancel current work/speech and clear live captions/latest sources.
  Previously displayed valid conversation remains readable.
- **Reset chat:** cancel first, then clear model/display history after the worker
  finishes. Personality and estimated session spend are preserved.
- **Audio file:** choose an existing sample. It is preserved; microphone WAVs stay temporary.
- **Send / Enter:** send typed text, including `/search ...`. Buttons perform control
  actions; typed `exit` or `reset` is conversation text here. Blank input is ignored.
- **Status/sidebar:** current phase, microphone readiness, backend, notices, latest
  timings, search count/sources, and estimated conversation/search spending. Cloud
  STT charges are separate. Hosted search is not streamed, so precise search
  start/end is not shown; its count appears after the response.

One turn runs at a time. Controls disable or reject overlapping input. STT starts
in a background initializer, leaving the window and typed input responsive. Failure
disables microphone/audio-file input and shows a notice; typed chat still works.
Speech failure leaves text readable. Fix services/settings and restart to retry
failed startup initialization.

Closing cancels output and waits for initialization/active work before closing
clients. The window stays responsive. Pending HTTP calls may still finish and bill;
an initial local model download can also delay exit until initialization returns.
No forced process kill is used.

Global hotkeys register only with `-Hotkeys`/`--hotkeys`; `-Gui` alone uses window
buttons. A hotkey failure warns while keeping buttons usable. Sources open in your
browser only after an explicit click. Incoming replies never open pages or dialogs.

## Implementation and verification

- `control_window.py`: widgets, conversation, controls, and links.
- `window_model.py`: bounded plain display state and button availability.
- `window_session.py`: main-thread polling, background STT startup, optional hotkeys,
  and shutdown through the existing StreamController.
- `turns.py`/`stream_controls.py`: optional structured events for replies, sources,
  status, notices, transcripts, and usage. Workers enqueue data; only the Tk thread
  changes widgets. The existing cancellation guard blocks late reply publication.

Executed checks: **113 tests passed** (89 existing and 24 window-related cases).
Headless tests use fake widgets/event loops, microphones/keys, and providers. They
cover persistent JA/EN display, sources, errors, async startup, recording/stop,
pause/reset, costs, shutdown, and client cleanup. Tkinter import and launcher/CLI
flags were checked without opening a window.

No actual window, browser, microphone, playback, global keys, or live API calls were
used during agent verification. When free:

1. Launch fake/no-speech mode. Check EN/JA, resizing, scrolling/copying and Reset chat.
2. Launch your desired services and cloud mode. Try Record/Finish, Stop/Pause and
   persistent replies; add hotkeys only when you want to register them.
3. Try `/search`, inspect source labels, and click a link only when you want to open it.
4. Close during a turn; confirm responsive waiting and key release after final exit.

Native appearance/font scaling and live device/provider acceptance remain manual.
Implementation references: [Tkinter](https://docs.python.org/3.12/library/tkinter.html)
and [themed widgets](https://docs.python.org/3.12/library/tkinter.ttk.html).
