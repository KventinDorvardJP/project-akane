# OBS subtitle overlay

The optional Browser Source shows English or Russian subtitles on a translucent, rounded
background. Text wraps within a configured maximum width; the panel's height fits
the text. Empty captions hide **both** text and background. The rest of the page
is transparent.

It runs inside Akane's existing Python process. No second terminal, OBS WebSocket
connection, password, extra Python package, or separately launched app is needed.
The existing `runtime/subtitle_en.txt` remains the source of truth and still works
with OBS Text (GDI+) if preferred. The control window keeps Japanese and the selected translation.

English is the default. To switch to Russian, open **Settings → Subtitles**, set
**Subtitle language** to `ru`, save, and relaunch with your usual command. Each
reply pairs Japanese with one translation. The OBS URL
and legacy `runtime/subtitle_en.txt` path stay the same and carry the selected
translation. If a Japanese speech part contains Cyrillic or lacks Japanese script,
Akane shows the selected translation for that part and the rest of the reply.
Witchcraft's in-game reply mirror is available in English mode.

## Launch

Add `-Overlay` to your usual command:

```powershell
.\run.ps1 -Gui -Hotkeys -Overlay -LlmBackend openai
```

For a free display check without recording, speech, or model calls:

```powershell
.\run.ps1 -Gui -Overlay -FakeBackend -NoStt -NoTts
```

Type a short message, then a longer one. The fake reply remains until another
turn or a clear command because speech is disabled. **Stop**, **Pause**, **Reset**
and **Exit** clear it. With successful speech, the caption follows the existing
playback/minimum-duration timing. If speech fails, readable text remains.

Direct Python equivalent:

```powershell
.\.venv\Scripts\python.exe main.py --gui --overlay --fake-backend --no-stt --no-tts
```

The feature is off by default unless `-Overlay` / `--overlay` is supplied or
`SUBTITLE_OVERLAY_ENABLED` is enabled in configuration. When enabled, the terminal
prints the URL and the control window offers **Copy overlay URL**, with its status
in the footer. Copying happens only when you click the button.

## Set up OBS yourself

1. Launch Akane with the overlay enabled.
2. Add a **Browser** source to the desired OBS scene. Leave **Local file** unchecked.
3. Paste `http://127.0.0.1:8765/overlay` into **URL** (or use Akane's copy button).
4. Start with **Width: 1200**, **Height: 420**. Position this source at the bottom
   of your scene. The caption is centered along the bottom of its own viewport.
5. Hide the old subtitle Text (GDI+) source and any separate subtitle Color Source
   so they do not draw duplicate text or a permanent rectangle.
6. Send a test message. Confirm wrapping, transparency, readability over your game,
   and disappearance after **Stop**. Test with both short and long replies.

The standard transparent Browser Source custom CSS can stay. Avoid custom CSS
that forces a background onto the full page. If changing CSS/JS while Akane is
running, restart Akane and refresh the Browser Source cache. Normal caption
updates do not require refreshing OBS. Akane does not configure OBS for you.

## Appearance and port

Edit settings in `config.py`, or merge these fields into your existing ignored
`config.local.json` (preserve your microphone and other personal settings):

```json
{
  "SUBTITLE_OVERLAY_ENABLED": true,
  "SUBTITLE_OVERLAY_PORT": 8765,
  "SUBTITLE_OVERLAY_MAX_WIDTH": 1100,
  "SUBTITLE_OVERLAY_FONT_SIZE": 38,
  "SUBTITLE_OVERLAY_BACKGROUND_OPACITY": 0.78
}
```

Restart Akane after changing settings. Width and font size are in CSS pixels.
Allowed width is 240–3840, font size 16–96, and opacity 0–1. The viewport needs room
for the text and padding. A very long reply may need a taller Browser Source or
smaller font. Akane now sends matching short English parts during Japanese speech;
see [REPLY_CHUNKS.md](REPLY_CHUNKS.md) for limits, prefetch and text-only paging.
The overlay itself still renders the current caption file without truncation.

If the port is occupied, overlay startup warns and chat/file captions remain
available. Choose another unused port and update the OBS URL. The server binds
only to `127.0.0.1`; it cannot be reached by OBS on another PC. Port zero is for
isolated tests and produces a changing URL; use a fixed port for your scene.

## Failure behavior and checks

The page polls the current caption about every 150 ms after a completed request.
A failed or invalid request hides the panel; a one-second request deadline and a
1.5-second stale-caption check also hide it when updates stall. Browser scheduling
can delay these checks. Closing Akane clears the file and stops the server;
the disconnected page hides its last caption on its next active check. If OBS
unloads a hidden source, its next page load starts blank and reads current text.

The server serves only the three overlay assets and a read-only caption endpoint,
not the project directory or credentials. Captions are inserted as plain text,
including literal angle brackets; model output is never interpreted as HTML.
It accepts no caption-writing requests and performs no external network requests.

Run the ordinary regression suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Overlay HTTP tests use short-lived servers on their own ephemeral loopback ports.
Client tests use fake DOM/fetch/clocks and Node's built-in test runner when Node
is available; Node is optional for running Akane. These checks do not open OBS,
register keys, record/play audio, or make provider requests. Actual OBS rendering,
source placement and game readability require the manual check above.

OBS's Browser Source controls are documented in its
[official guide](https://obsproject.com/kb/Browser-Source).
