# WoW Forever context through Witchcraft

**Legacy transport reference:** new `-Wow` launches use the independent event-driven
[AkaneData module](WOW_MODULE.md), with immediate cache freezing and no per-question
refresh wait. Witchcraft remains the in-game question/response interface. The older
file-cache refresh/freshness behavior below is retained for compatibility only.

Akane can attach character, location, progression and tracked-quest snapshots to
your typed or spoken question. Select **Include WoW context** in the control
window. It starts off and resets to off with Reset chat/session restart. The
addon can separately keep its local cache warm with explicitly enabled automatic
updates. Received game data never generates an unsolicited reply.

## Current setup

The user manually verified Witchcraft's character, location, money/XP and tracked
`Kobold Camp Cleanup` objective export on **Forever 1.60.1 build 70888**. The
Witchcraft checkout is a separate sibling repository on branch `witchcraft-akane`.
Its capture helper was changed to accept foreground `Wow.exe` and `WowB.exe`;
the upstream version recognized only `WowB.exe`. Its 11 existing offline tests
and Windows PowerShell/C# compilation checks passed. This is limited runtime
evidence, not a claim that every API or optional transport works on this build.

The current diagnostic bridge runs an ordinary PowerShell terminal in its Codex
tab. It supplies game context without requiring a coding-agent session. From
`C:\HAL Studies\GitHub\witchcraft\tools\witchcraft-daemon`, start it manually:

```powershell
node bin/witchcraft.js run --only codex --no-current --no-mcp --no-overlay -- "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile
```

Keep it running. Existing calibration and installed helper addons remain needed.
If the bridge was restarted, manually `/reload` WoW outside combat and confirm
LINKED. Sharing must be on (`/witch context on`). Optional cooldown/addon-state
transports remain disabled on build 70888; their evidence pin was not bypassed.
Stop the diagnostic bridge by closing its console or using Ctrl+Break; Ctrl+C is
normally forwarded to its terminal.

## Ask from inside WoW (optional)

Witchcraft's existing terminal input/display can now connect to the **running
Akane control panel**. Both interfaces use one conversation and one turn worker;
the adapter never calls a model or speech service itself. English replies appear
in Witchcraft's private window. Japanese speech, English OBS captions and the
full JA/EN control-panel history continue through the existing delivery path.
Replies also appear locally as **[Akane]** in WoW's normal General chat frame.
The optional **Hold to talk: Akane** button records while held, then runs the
existing STT/reply pipeline on release. See [WOW_PUSH_TO_TALK.md](WOW_PUSH_TO_TALK.md)
for the restart/test steps; omit `-NoStt` when using that button. Nothing is sent
to other players.

After updating Akane's Python files, **fully close the old Akane application**
and relaunch it. Settings → Save & restart reloads configuration, not Python code.

1. Stop the old diagnostic Witchcraft bridge manually (Ctrl+Break in its console).
   Start VOICEVOX yourself if you want Japanese speech.
2. From the Akane folder, start the control panel with its optional input bridge:

   ```powershell
   .\run.ps1 -Gui -Witchcraft -NoStt -TtsBackend voicevox
   ```

3. In a **second PowerShell**, from the Akane folder, start Witchcraft with the
   English terminal adapter instead of the diagnostic shell:

   ```powershell
   .\start_witchcraft.ps1
   ```

   The default assumes the sibling `../witchcraft` checkout. For another location,
   use `-WitchcraftPath "C:\path\to\witchcraft"`. Existing Node dependencies,
   calibration and generated helper addons are still required. The launcher uses
   `--only codex --no-current --no-mcp --no-overlay`, the explicit local
   `--akane-endpoint`, and the project venv's adapter.
   It launches no coding agent, shell, second Akane backend or second audio worker.
4. In foreground WoW, manually `/reload` outside combat after the bridge restart.
   Open Witchcraft and select its **CODEX** tab (the existing label is retained).
   It should say **Connected to Akane's control panel**. Enable addon sharing and
   automatic export with `/witch context on` and `/witch context auto on`.
5. In **Witchcraft's input field**, send `/context on`, or select **Include WoW
   context** in Akane's control panel. This explicit choice controls attachment
   from either interface. Let the initial export warm up, then send “Where am I?”
   or “What would you advise me to do next?” using Witchcraft's **Send** button.
   Keep WoW foreground while context is collected.

Adapter commands are entered in Witchcraft's input field, not WoW's slash-command
chat: `/stop` cancels the shared active turn; `/pause` toggles pause; `/reset`
resets the shared history and disables context attachment; `/context on` or
`/context off` changes that attachment; `/help` shows controls; `/exit` disconnects
only the adapter. Other text, including `/search ...`, is a question. Busy/paused
questions are refused instead of held for a later paid request. If delivery is
uncertain, the adapter tells you to check the control panel and never retries the
question automatically. Reloads/session changes likewise never resend questions.

The bridge is **off by default**. Enable it for this launch with `-Witchcraft`/
`--witchcraft` in GUI mode, or save **Settings → WoW context → Enable Witchcraft
in-game chat** and restart. Settings → Save & restart uses saved startup defaults;
save that checkbox if you want to retain the connection after changing settings.
It binds
only `127.0.0.1:18749`, uses a new private token/session each time, rejects browser
origins and other Host headers, and publishes discovery in ignored local
`runtime/witchcraft_endpoint.json`. It accepts only bounded text, Stop, Pause,
Reset, context-selection and leased microphone-gesture messages, with a bounded mailbox, five-second input
expiry and recent-ID deduplication. HTTP handlers never touch Tk or run turns.
Port/startup failure leaves control-panel chat available with a warning.

Only plain questions, full English replies and small control/status messages
and microphone readiness/gesture state are mirrored; no raw snapshots or Japanese
reference attachments are mirrored.
The adapter shows recent bounded display history after reconnecting; the control
panel and optional session log remain the conversation record. Ordinary model
and TTS charges apply when you deliberately submit a question. Context freshness,
sharing and cancellation checks remain unchanged; staying in WoW avoids the
window-switch pause but does not guarantee an instantaneous snapshot.

## Ask from Akane's control panel

1. Run the game and bridge, keep the calibrated screen strip uncovered, and ensure
   sharing is on. For lower latency, enable background updates as below and let
   the first pass finish with WoW foreground before sending.
2. Start/restart Akane's control window. For a first typed test with Japanese
   VOICEVOX speech, start VOICEVOX yourself, then run from the Akane folder:

   ```powershell
   .\run.ps1 -Gui -NoStt -TtsBackend voicevox
   ```

   This uses your selected conversation provider and makes a real model request
   when you submit a question. Normal cloud charges apply if it is OpenAI.
3. In **Settings → WoW context**, check the cache path. The portable default
   `../witchcraft/tools/witchcraft-daemon/.local/wow-context.json` matches the
   current sibling checkout. Use Browse for a different local checkout, then
   Save & restart. The file must be named `wow-context.json`.
4. Check **Include WoW context** and ask “What is my current quest and objective?”
   Switch back to WoW immediately if Akane says it is getting context: the current
   screen transport pauses when WoW is in the background. Stay out of combat for
   the initial test. With opt-in global hotkeys and STT enabled, starting a spoken
   turn while WoW is foreground also works; recording starts after context lookup.
5. Confirm the paired JA/EN reply,
   Japanese speech, and the corresponding English captions. Akane does not send
   its answer back to the diagnostic PowerShell terminal; use the optional
   in-game adapter above for English replies in Witchcraft.

The conversation panel displays your question and Akane's reply without raw WoW
snapshot JSON. OpenAI now requests selected fields through local context tools;
raw snapshots stay out of its model history. Ollama retains direct attachments.
Full selected snapshots remain in optional local logs. See CONTEXT_TOOLS.md.
To let Codex diagnose a session without
screenshots, enable **Settings → Session → Record session transcript**, then
**Save & restart**. Subsequent ignored local logs under `runtime/session_logs/`
include the exact question, snapshot, JA/EN replies and timing/playback events.
The transcript is off by default; earlier unlogged conversations cannot be
reconstructed from the timing CSV, which contains no dialogue. See SESSION_LOGS.md.

No manual Refresh is needed for normal per-question use. A recent cache is used
immediately. Missing/stale domains trigger one request each through Witchcraft's
existing `wow-context.want.player.json`, `.progress.json`, and `.quests.json`
protocol. The running daemon handles delivery. Akane writes only these fixed
small request documents, using atomic replacement; it does not modify the cache,
game settings, addons, bindings, Lua, agent terminals or processes.

## Timing, missing data and cancellation

### Validated capture-time references

The 01:26+ live retry confirmed the packet fix was loaded and microphone turns
reached Luna, but the typed quest-only turn still failed: its calculated quest age
was 81.6 seconds while player/progress ages were 52.2/55.2. The legacy pixel age
includes a blanket 30-second uncertainty margin, leaving short usable windows
between quest updates even after batching.

The host now advertises `contextClockProtocol: 1`. Before capturing a domain, the
addon freezes the sequence of the latest host slot it has already read. Context
envelope v2 carries that reference for every part of the same capture. The daemon
knows the slot's **first write-start time**, which must precede the API read, and
uses it as a conservative capture-time lower bound. Rewrites and same-UI cursor
resets never advance that timestamp; UI/host ownership changes invalidate it.
Suspending a quest revision retains its original reference and capture time.

Unknown, future, cross-session, inconsistent or mixed-part references fail closed.
The 60-second default age limit is unchanged. Legacy/unanchored v1 packets retain
their old transport allowance. No old revision is relabelled as a new capture.
Cache rows expose `timing.method` (`host-anchor` or `transport-bound`) and, for
anchored rows, `timing.captureSeq`, for diagnosis. Akane still computes age from
the stored lower-bound timestamp and withholds data over its configured limit.

Restart the daemon and `/reload` the installed addon together, then re-enable
`/witch context auto on` and wait for warm-up. The already-running Akane reader
can consume the new timestamps; a Python restart is only needed to load the new
optional timing-provenance diagnostics. The native control window can remain open
with Include WoW context selected, preserving its conversation and budget state.

### Background export for lower latency

The `witchcraft-akane` branch now includes bounded event-driven updates. After
installing its updated `Witchcraft/Context.lua`, `Core.lua` and `View.lua`, manually
`/reload` outside combat, then type:

```text
/witch context auto on
```

The panel shows **Sharing: on / auto**. Keep WoW foreground for roughly 20–30
seconds for the initial warm-up (longer batches can take more time). After that,
quest/XP/money/location/situation events mark only the relevant domains dirty.
Quest updates go first on automatic passes. Repeated events coalesce into at
most three flags, with a 0.75-second debounce and a five-second minimum between
background captures of one domain. Idle domains are renewed by actual API
capture after 15 seconds, rather than relabeling old data as fresh. The existing
active-packet/multipart/acknowledgement machinery bounds transfer work, with at
most one temporarily suspended quest revision for an urgent small-domain lookup.

Quest-only requests send only quests. Mixed/spoken requests send the smaller
character and progress records first. An urgent small-domain request can suspend
one unfinished quest revision, fetch those small records, then resume the original
quest capture without changing its timestamp. Quest rows now pack together under
the existing per-packet byte limit instead of spending one ACK/slot round trip per
quest. This preserves exported rows/objectives and reduces transfer time. A
cold/large batch can still exceed the short wait.

The 2026-11-12 live log review found a hold-to-talk starvation bug: every repeated
input heartbeat discarded the current context packet. Mic hold/release state now
uses bit 4 of the existing heartbeat row, with lease renewal only on a fresh,
advancing UI cursor. No additional calibration area or build-gate bypass is used.
Short explicit mic gestures preserve a partial packet and its original expiry.
Restart the daemon and reload the updated addon together.

New background captures pause for combat, a detected link stall and terminal
input; existing retry backoff still applies. Queued event flags merge while the
bridge is unavailable. Unsupported event registrations are skipped safely, with
idle renewal as fallback. This uses the existing addon timer and bridge, with no
new Python listener, model request or process.

Automatic export defaults **off after every UI reload**. Turn it off with
`/witch context auto off`, or Pause sharing. Off discards queued automatic domains;
an already in-flight revision may finish. Disabling/stopping the addon also stops
automatic work. Akane's Stop/Pause/Reset affect its question worker; the separately
selected local addon export can continue until disabled in WoW. Akane's Include
WoW context checkbox remains necessary to send a snapshot to the LLM.

With a warm cache, the question path reads local data immediately. The cold/stale
fallback remains bounded, while unavailable values are still withheld. Updates
are not instantaneous: the existing transport can lag events, especially with
many quests or combat pauses. Performance must be measured live; the prior
roughly 19-second context wait has not been re-benchmarked automatically.

### Refresh budget and measurements

Settings include `WOW_CONTEXT_MAX_AGE_SECONDS` (default 60, range 35–180) and
`WOW_CONTEXT_WAIT_SECONDS` (default 8, range 1–60). Saved overrides can retain an
older wait; use Settings → WoW context to adjust it. Witchcraft conservatively
charges 30 seconds plus assembly time to observations on its screen transport;
that allowance is preserved for legacy packets. Negotiated anchored captures use
the validated host time described above. Lower age limits can reject slow snapshots. The
three-minute upstream MCP freshness default is not used by this adapter.

The GUI and timing CSV now show `context_seconds` separately from model and TTS
time. Existing CSV headers migrate with a byte-for-byte backup; old context times
remain blank, rather than being guessed. Optional session logs have matched
`wow_context_start`/`wow_context_end` events, including cancelled/failed lookups.
For typed input, context time is already included in first-ready/turn elapsed time,
so do not add it twice. For microphone input it happens before recording, which
the existing response-ready timer excludes; the separate context field shows it.

### Wait only for data relevant to a typed question

Clear typed questions select required domains using small local text rules: a
location question needs character/location, a quest/objective question needs
quests, and money/XP/situation questions need progress. Mixed cues combine them;
general next-step advice needs character/location and quests. Recognized cues
cover English and common Japanese/Russian phrasing, not every possible wording.
Clear elaboration/continuation follow-ups reuse the last attached turn's required
domains unless the new question names different data. Unrecognized questions and
spoken/audio-file turns conservatively require all three domains. No extra model
request is used to classify questions.

The worker requests and waits only for stale/missing **required** domains. Other
currently available fields can still be attached, while their stale values remain
null and do not delay the question. If none of the required data arrives, the turn
stops before the LLM rather than answering from unrelated fresh fields. Partial
required data remains explicit. The attachment/log records `requiredDomains` and
`requestedStatus`; the GUI distinguishes readiness for this question from other
partial game data. The quoted-context instructions distinguish quest objective
progress from the money/XP/situation domain.

Freshness and heartbeat connectivity are recomputed from timestamps on each
read, rather than trusting stored status/age labels. A heartbeat older than 30
seconds is disconnected. Return to foreground WoW and retry the question if that
happens. A whole pass can take tens of seconds, especially with many quests or
combat-paused acknowledgements. Increasing wait/age limits cannot make it live
combat telemetry.

The opt-in choice and client configuration are fixed when a turn is accepted.
One immutable game snapshot is collected in the shared turn worker **before
recording/audio STT starts** and then reused for that turn. Later game changes
cannot substitute different quest progress while recognition is running.
The UI labels its preview as the last captured snapshot, not a live observation.
Japanese LunaTranslator text still freezes separately at turn acceptance.

Only currently available domains contain values. Stale/missing/unavailable
domains contain null data with explicit status/age/reason metadata; partial
results show a warning. If none are available, or the cache/session is invalid,
sharing is off or the bridge is disconnected, the turn stops before recording,
STT or LLM work. A typed question is restored for editing/retry. There is no
automatic model retry or silent fallback to old counts. A changed game/bridge
session during refresh also rejects the turn.

Stop/Pause/Reset/exit interrupt refresh waits. Already written want files are
shared protocol state and are not deleted on cancellation; the game may finish
that capture, but the cancelled turn cannot use it or generate speech. The
snapshot-only path needs no extra Akane listener or synthesis queue. The optional
in-game interface above adds a loopback mailbox and terminal display polling.

## Data and limits

Input cache reads are limited to 128 KiB. Only whitelisted character/location,
progress and tracked-quest fields are retained, with bounded text/list sizes.
Nulls, API-unavailability and truncation flags stay explicit. Upstream limits
include ten tracked quests and four objectives each. Lua errors, transcripts and
local goals/passport records are excluded. Network/device paths and URLs are
rejected. Sharing in Witchcraft alone does not upload data to Akane's LLM.

When the checkbox is selected, OpenAI can request specific fields from that
snapshot as quoted tool data; unrequested values stay local. Search-intent
detection uses only your question. OpenAI tool results count as normal input
tokens but are not retained in later model history. Ollama continues receiving
the full quoted attachment in its bounded history. Both paths keep selected
snapshots in ignored local session logs when logging is enabled.
The optional local transcript retains the exact attachment for review. Quoting reduces confusion but
cannot guarantee that a model ignores malicious strings in game/addon data.

`complete: true` is an objective-completion flag, not confirmed turn-in history.
`A Threat Within` returned an empty objective list although the game tracker
displayed “Speak with Marshal McBride”; that turn-in text is not captured by the
current exporter. Akane must not invent it. Normal kill-objective text and counts
were manually observed in the desktop cache. Directions, maps, inventory,
equipment, target/health data, unsolicited spoken reactions and gameplay controls
are outside this addition.

## Verification

**288 service-free Akane tests passed** on 2026-11-12, including 34 WoW context
tests, 23 in-game mailbox/input/GUI/hold-to-talk tests, and reply-schema/full-name/sidebar
regressions. The new tests cover loopback authentication/origin/session checks,
bounded and expired input, deduplication, reconnects, full English-only replies,
shared history, busy rejection, late-output cancellation and mocked Japanese
speech/English captions. A separate temporary fake-backend ConPTY check passed
on Windows for typed input, English replies, Ctrl+C cancellation and clean exit;
it uses the same native ConPTY mode as Witchcraft without running the game bridge.
Both PowerShell launchers parse successfully. **50 offline Witchcraft
tests passed**, including 12 capture-anchor tests, seven context-flow regressions, 12 chat/hold-to-talk
relay/controller tests and eight added scheduler/
controller tests for default-off state, sharing, quest-first bounded capture,
event floods, idle renewal, in-flight changes, disable/stop, input priority,
combat/link/backoff pauses, and avoiding per-event terminal redraws. Lua files
also pass the existing Lua 5.1 parser check. A separate temporary-fixture check
from the initial integration confirmed that the installed
Witchcraft's JavaScript reader accepts Python-generated request files and folds
them into domain mask 7, and that its cache validator accepts the shared fixture.
No real game cache or refresh file was used by that check.

Run `python -m unittest discover -s tests -v` using the project venv. Tests use
temporary caches/want files, fake clocks, mock providers/devices and headless
window views. They cover fresh/partial/stale data, invalid timestamps/schemas,
disconnection/sharing/session changes, bounded refresh, failures/cleanup,
cancellation, off-by-default selection, question-only search intent, history/log
visibility, audio/recording snapshot timing and paired speech/captions.

The new in-game question/reply interface still needs manual acceptance in WoW.
The user manually observed a matching quest/objective bilingual answer before
the latency patch. Live automatic event delivery, before/after response timing,
speech, recording hotkeys and simultaneous VOICEVOX/WoW performance still need
the user's manual check. Automated tests do
not refresh the running game, record, capture the desktop, play sound or call APIs.

Protocol references in the separate upstream repository:
[context and freshness](https://github.com/Geraldlol/witchcraft/blob/main/docs/MCP.md),
[request files](https://github.com/Geraldlol/witchcraft/blob/main/tools/witchcraft-daemon/src/context-want.js).
