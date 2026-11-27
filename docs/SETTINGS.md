# Control-window settings

Open **Settings…** at the top right of Akane's control window. Every current public
setting in `config.py` has a control, including advanced model-price configuration.
No settings window opens until you click the button.

After saving your choices, launch with:

```powershell
.\run.ps1 -Gui
```

## Tabs

| Tab | Options |
| --- | --- |
| Session | Conversation/TTS providers, recognition, spoken replies, global hotkeys and test echo mode |
| Conversation | Models/URLs, effort, token/history limits, timeouts, search, budget and advanced price JSON |
| Recognition | Local/cloud transcription, models, CPU/CUDA, languages and request timeout |
| VOICEVOX | Engine URL, speaker/style ID, volume and timeouts |
| GPT-SoVITS | Inference URL, transcript/languages, main/extra reference pickers, speed, splitting and timeout |
| Audio & keys | Microphone/output IDs or names, recording limits, silence threshold and key combinations |
| Subtitles | Browser overlay, appearance, paired-part length/count limits and text-only reading speed |
| Japanese text | LunaTranslator endpoint, maximum line length and freshness limit |
| WoW context | Witchcraft cache path, freshness/wait limits and opt-in in-game chat connection |

Tabs scroll vertically. Extra reference files use one path per line; recognition
language hints are comma-separated. The model-price JSON uses the structure from
`config.py`. Editable dropdowns permit additional model-dependent language/effort
values; a provider still determines which values it supports.

**Enable Witchcraft in-game chat** starts the local input connection in GUI mode.
After Save & restart, run `start_witchcraft.ps1` separately to connect Witchcraft's
CODEX tab. This does not select **Include WoW context** automatically. Use that
checkbox or send `/context on` in Witchcraft's input field. `-Witchcraft` is a
one-session launch override; Save & restart uses the saved checkbox choice.
See [WOW_CONTEXT.md](WOW_CONTEXT.md) for setup and controls.

**List audio devices** enumerates IDs/names only when clicked; it never opens a
recording/playback stream. Leave device fields blank for the system default, enter
an integer for an ID or text for a name. Output-device selection applies to GUI/
hotkey playback; basic terminal playback follows Windows.

**Prepare one speech part ahead** in Session overlaps one next synthesis with
current playback. See [REPLY_CHUNKS.md](REPLY_CHUNKS.md) for chunk timing/cancellation.

## Save and restart

The form edits **saved defaults for the next session**. Current command-line
overrides may differ from the saved choices shown.

- **Save** validates and writes local overrides without changing active clients,
  models, audio, hotkeys, overlay or conversation. Continue chatting normally.
- **Save & restart** saves and restarts when idle and microphone initialization
  is finished. If busy, it saves and asks you to wait, then click again. It does
  not forcibly stop an active job.
- **Restore form defaults** changes only the form until you save.
- **Close** leaves unsaved edits unapplied.

Restart clears conversation/reference state and the local session cost estimate;
it does not undo account charges. Old audio, keys, overlay and SDK clients are
cleaned up before rebuilding in the same Python process. No new terminal or other
app is launched. Reconnect the Japanese text feed explicitly if needed.

The restarted GUI uses the saved choices and discards old command-line overrides.
Fresh launches still accept override flags. If you change the overlay port, update
the OBS Browser Source URL yourself. Other apps are never reconfigured.

## Storage and limits

Only differences from public defaults are written to ignored `config.local.json`.
Existing microphone/reference choices load into the form and are preserved unless
edited/reset. `config.py` is not rewritten. If another editor changes the local
file while Settings is open, saving is refused; reopen to load those edits.
Invalid input stays visible for correction.

API credentials stay in `.env`/environment variables and are not displayed or
written by this panel. The character prompt remains in `personality.txt`.
Installing dependencies, loading GPT-SoVITS weights, starting services and setting
up OBS remain manual actions. VOICEVOX stays the public default; another saved
engine is an explicit local choice.

Changing a local STT model can trigger its normal first-use download after restart.
Global keys register only when enabled by saved settings/launch flags. For a
service-free display check, enable test echo mode and disable recognition/speech.

Conversation-backend startup failures keep the GUI/Settings accessible with an
error notice. Fix configuration or the API key and restart. A corrupt local JSON
file that prevents importing `config.py` still needs file-level correction; GUI
saves use validated atomic replacement.

## Verification

All **190 tests** passed after this addition. Headless settings checks cover field
coverage/round trips, reference preservation, concurrent edits, validation, explicit
pickers/device listing, idle-only restart, client cleanup, saved startup toggles
and dialog construction. No real windows, hotkeys, audio, provider calls or other
app changes were used. Native layout and live service/device acceptance remain manual.

## Session transcript option

**Session → Record session transcript** is off by default. Enable it and use
**Save & restart** while idle to start a new local `.txt`/`.jsonl` session in
`runtime/session_logs/`. Saving alone applies on the next launch/restart. Existing
logs remain when disabled. No credentials, configuration dump or raw audio is
added. Conversation text and selected context are saved locally as plain text.
See [SESSION_LOGS.md](SESSION_LOGS.md) for contents, review markers and failure behavior.

