# WoW chat replies and hold-to-talk

Akane's English replies now appear locally in WoW's normal **General** chat frame,
prefixed **[Akane]**, as well as in Witchcraft and the control panel. Nothing is
sent to /say, party, guild or another player. The addon uses `AddMessage` on the
local chat frame; it never calls `SendChatMessage`. Links/color codes in model
text are escaped. Long answers are split into bounded UTF-8 chat lines, preserving
the full text. A fresh UI/backend session establishes a baseline instead of
reprinting old answers. Very large answers have an explicit control-panel fallback.

A separate **Hold to talk: Akane** button sits above the lower-left chat area.
It remains available when Witchcraft's large window is closed. Right-drag moves
it for this UI session. The existing single Akane worker handles recording, the
selected STT, the selected conversation provider, Japanese TTS and English OBS
captions. No global recording hotkey or additional audio/model process is needed.

## Hold a key or mouse side button

In WoW's **Key Bindings**, find **Akane → Hold to talk to Akane** and assign a
comfortable key. A mouse thumb button such as **Mouse Button 4** is a useful first
choice; a free keyboard key also works. No existing binding is overwritten
automatically. This is a game binding, independent of Akane's global recording
toggle hotkey.

Hold the assigned key, wait for **REC - release to send**, speak, then release.
It uses the same leased recording, STT and reply flow as the screen button.
The button still works. Keyboard combinations and two alternative bindings are
supported; releasing a combination's modifier also ends recording. Wheel-scroll
bindings cannot represent a held gesture and will not start capture.

Only the physically held input that started a gesture can keep it alive. Repeated
key-down events cannot restart a refused/completed recording until physical
release. Changing bindings during a keyboard recording cancels it. A missing
physical-key API cancels/refuses capture; no toggle fallback is used. Existing
foreground, combat/link, busy and STT-ready limits still apply.

Run `/reload` after updating the addon so WoW loads the added action in its existing
`Bindings.xml`. If it is missing from Key Bindings, fully restart WoW. Akane/Python
and the daemon do not need a restart for this addon-only binding change. Live key
routing and layout compatibility still need a game test.

## Start and test manually

The updated `witchcraft-akane` checkout and the installed addon contain the new
`Akane.lua`, updated TOC, controller and ring validator. Installed files were
backed up under Akane's ignored `runtime/witchcraft_backups/`. Restart both Python
and the Witchcraft daemon to load changed source. Fully restart WoW once to load
the new addon file list.

1. Close the old Akane application and stop the old Witchcraft daemon. Keep
   VOICEVOX running if you want Japanese speech. Start Akane from its folder:

   ```powershell
   .\run.ps1 -Gui -Wow -Witchcraft -TtsBackend voicevox
   ```

   **Do not add `-NoStt`.** Speech recognition is currently enabled in this user's
   saved configuration and selects cloud STT. If the window says recognition is
   unavailable, fix that setup before testing the button. Settings → Session →
   Enable speech recognition selects recognition for future sessions.
2. In another PowerShell, from the same Akane folder:

   ```powershell
   .\start_witchcraft.ps1
   ```

   The launcher now supplies `--akane-endpoint` to the updated Witchcraft daemon.
   This opt-in relay adds structured English/recording-state data to existing
   validated ring snapshots and routes short button gestures to the same local
   Akane mailbox. The CODEX pane still carries ordinary typed questions.
3. Restart WoW, wait for Witchcraft to link, and initially test **outside combat**.
   The small Akane button appears when the updated relay is available. It says
   **mic disabled** until Akane reports STT ready.
4. For game-aware questions, enable AkaneData and launch with `-Wow` as above;
   **Include WoW context** is selected initially. See [WOW_MODULE.md](WOW_MODULE.md).
   The new module freezes the cache immediately without a question-time wait.
   `/akanedata refresh` manually recollects everything if needed.
5. Hold the button with the left mouse button. Wait until its label says
   **REC - release to send**, then speak. Release the mouse button to end capture
   and begin normal STT → conversation → Japanese speech/English captions.
   The English answer should appear in General chat even with Witchcraft closed.

The press travels over Witchcraft's existing game-to-desktop transport, so this
is not instant hardware push-to-talk. Bridge delivery and microphone startup
can add a preparation delay; release also takes effect when its message arrives.
The button shows **Preparing mic...** until actual recording is confirmed.
Releasing before recording starts cancels instead of opening the microphone later.
Partial/stale game-data handling is unchanged. If combat updates are paused in
Witchcraft, the button refuses/cancels capture until that transport can update.
This addition does not bypass existing combat or optional-carrier build gates.

## Recording and failure behavior

- Press and release use an explicit gesture ID, rather than a recording toggle.
  A duplicate/refused press cannot begin later when Akane becomes free; releases
  from another gesture cannot finish someone else's recording.
- Physically held mouse state travels as a bit in the existing heartbeat row,
  leaving the input/data row free for quest export. Only an advancing UI cursor
  can renew the backend lease; repeated/stale frames cannot renew it. Clearing
  the hold bit ends capture without waiting behind a long data packet. No lease
  messages are fabricated while the game is unavailable.
- If the release is lost, the backend cancels after an eight-second lease expiry
  and discards audio without STT/model calls. Stop, Pause, Reset, exit, hiding the
  button or leaving the world also cancel. Existing microphone duration limits
  remain a hard capture bound. Reaching the duration limit while still held
  discards the recording rather than automatically transcribing it.
- Releasing outside the button is detected from physical mouse state. Missing
  mouse-state APIs fail closed. UI reload/host ownership changes close the old
  gesture; SavedVariables recovery never starts a microphone.
- Busy/paused input is refused, not queued for a later turn. Failed deliveries
  are not automatically retried. Recording files remain temporary and are removed
  after STT or cancellation. Normal selected cloud STT/model charges apply only
  when those requests are actually made.

The ordinary GUI recording controls, terminal/hotkey modes, local/cloud provider
choices, VOICEVOX default, optional GPT-SoVITS, logging and OBS subtitle outputs
remain available. Raw game snapshots never enter General chat.

## Verification

The keyboard-binding addition has offline Lua tests for physical holds, missed
release recovery, repeat suppression, busy refusal, two bindings, modifier/side
buttons, mouse ownership, missing APIs, rebind cancellation and actual binding
XML code forwarding. Automated tests do not press live keys or activate a mic.

**288 service-free Akane tests and 50 offline Witchcraft tests passed** on
2026-11-12. Eight added Akane tests exercise leased press/release, early release,
STT disabled, expiry, heartbeat renewal, wrong/duplicate gestures, busy refusal,
duration-limit discard, cancellation and main-thread GUI ownership. Twelve added
Witchcraft tests cover relay bounds/ownership/no-retry, Lua button wiring and
physical release, reply deduplication/escaping, and a serialized ring snapshot
reaching the controller while the terminal window is closed. All addon Lua files
pass the existing Lua 5.1 parser check. Seven additional context-flow tests cover
packet preservation, urgent small-domain refresh/resumption, quest batching,
cache validation, and native heartbeat ownership/sequence checks.

On 2026-11-12, live logs exposed repeated eight-second failures before capture/STT:
hold input messages restarted context packets, and nine one-quest packets arrived
outside the configured 60-second age limit. The fixes above retain that limit and
the existing 30-second conservative transport allowance. A read-only replay of
the user's nine-quest cache packed nine parts into two with identical exported
data; its idealized 5 Hz/1.2-second ACK simulation changed calculated arrival age
from 64.2 to 48 seconds. These are simulated values, not a live timing benchmark.
Quest-only scope is retained; mixed/spoken requests fetch small domains before
the large quest batch. Failed lookups now log per-domain status/age.

The subsequent live retry confirmed microphone requests reached Luna but quest
freshness still had short usable windows under legacy timing. The host/addon now
negotiate a capture-time reference to the already-read host slot. Its immutable
first write-start timestamp gives a conservative lower bound without a blanket
30-second penalty on those anchored captures. Legacy packets retain that penalty;
the 60-second age limit is unchanged. Twelve further tests cover bound correctness,
slot rewrites/resets, stale/future/unknown references, multipart consistency, legacy
fallback and the serialized host-slot → Lua → cache path.

An additional temporary Node-cache → Python-reader → headless question-worker test
reproduced the same data as legacy age 80 seconds / anchored age 51 seconds and
confirmed a typed quest question reached the fake LLM. No live model/mic/game was
used. Restart the daemon, `/reload` the addon, and re-enable automatic sharing to
load this negotiation. Akane can remain running and keep its conversation; its
existing reader already consumes the cache's sample timestamps.

A separate temporary integration fixture passed through Node's actual
authenticated loopback HTTP client → headless WindowSession → fake recording →
fake STT → English reply. No real microphone, sound, model request, gameplay,
desktop capture, reload or live button click was used. Files were backed up and
installed with matching hashes; real WoW/audio/transport latency acceptance still
requires the manual steps above.

The local display API follows the client UI's own
[chat-frame implementation](https://github.com/Gethe/wow-ui-source/blob/live/Interface/AddOns/Blizzard_ChatFrameBase/Shared/ChatFrameUtil.lua).
That source is a reference for the API pattern, not runtime proof for Forever.
