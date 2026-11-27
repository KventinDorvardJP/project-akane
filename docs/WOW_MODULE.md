# Event-driven WoW module

Start Akane with `-Wow` (`--wow` in Python) to receive **AkaneData**, the independent
addon in `wow_addon/AkaneData/`. Witchcraft remains the optional in-game chat and
hold-to-talk interface. The new data path does not use its helper-addon ring,
acknowledgments, cache files, refresh requests or combat gate.

This is an implemented prototype with offline Lua/Python tests. Native screen
capture, strip placement and API availability on the actual Forever client,
including during combat, still require the manual checks below.

## Install and start

From the Akane repository, with your usual services running:

```powershell
.\install_wow_addon.ps1
.\run.ps1 -Gui -Wow -Witchcraft -LlmBackend openai -TtsBackend voicevox
```

The installer defaults to
`D:\Games\World of Warcraft\_forever_\Interface\AddOns`. Pass `-AddOnsPath` for
another existing AddOns folder. It only copies AkaneData, marks ownership and
backs up an existing marked copy under `runtime/wow_addon_backups/`. An unmarked
AkaneData folder is not overwritten. Fully restart WoW after adding this new addon
folder; enable **Akane Data** in the addon list. Its TOC targets interface 16001.

Keep the existing Witchcraft daemon/terminal adapter running for in-game questions,
English chat replies and the microphone button. `-Wow` alone suffices for data
and control-window questions. `-Witchcraft` alone enables the interface, without
WoW context tools. Launches without `-Wow` do not start the receiver or advertise
WoW tools, even if the old saved include-context setting is enabled. The flag
survives **Save & restart**; it is not a persistent Settings preference.

The GUI selects **Include WoW context** initially with `-Wow`; uncheck it to omit
game data/tools from questions. Reset also clears that selection. The connection
label shows linked/waiting state and received categories. No old `/witch context`
command is required for AkaneData; old Witchcraft context export may be switched
off while leaving its input/response interface enabled.

## Collection and transport

The addon captures a baseline at login, then coalesces relevant WoW events over
250 ms and recaptures affected categories. There is **no age-based refresh or
field-specific TTL**. Cooking remains valid throughout a connected session until
a skill event changes it. The local cache stores source observation timestamps
and increasing revisions, not the time a repeated packet was seen.

| Category | Exported scope |
| --- | --- |
| Player | Name, class, level, zone/subzone, map ID |
| Progress | Money, XP, rested XP, group/role, dead/ghost/combat and instance flag |
| Quests | Up to 25 watched quests, up to 4 objectives each; truncation explicit |
| Professions | Known supported Classic profession/secondary-skill names and ranks; no recipes |
| Inventory | Bags 0–5 including the Reagent Bag, up to 40 slots each, plus equipment 1–19; no bank/mail |
| Hearthstone | Item 6948 count, enabled flag, observed remaining cooldown |
| Events | Last 32 observed death, level-up and zone-change notifications |

Unavailable/restricted values become nulls or an unsupported/unavailable category.
Empty lists mean a successful empty observation, not a failed read. Partial data
is labelled. Instance name/type/difficulty, precise coordinates, the full combat
log and death-recap attackers are not collected. A death event alone cannot explain
what killed you. Events are cached and optionally logged; they do not trigger
autonomous replies, speech or model requests.

An ordinary, unprotected frame displays a **192 × 18 physical-pixel strip**, at
client coordinates **0,24**. Every 150 ms it transmits one packet of previously
serialized data. This retransmission is for delivery/recovery; it never rereads
game APIs or changes observation time. Categories take turns so a large inventory
does not block player/quest packets. Large categories can take several seconds
to arrive and need a quiet interval to finish if continually changed.

The optional Python receiver samples only that bounded screen rectangle while
Wow.exe/WowB.exe is foreground. There is no game RAM reading, process injection,
input automation or addon network/file writing. Foreground identity uses Windows
process metadata only. This first transport remains replaceable. Keep the strip
uncovered, use windowed/borderless WoW initially, and avoid HDR/color filters for
the first test. Switching away stops reception; this is separate from the addon
continuing to collect local events.

Packets have checksum, UI-session nonce, advancing counter, a manifest of category
revisions and bounded multipart payloads. Mixed/corrupt/replayed packets never
publish partial data. A new UI session clears the old cache. Two advancing packets
confirm connection; five seconds without an advancing packet marks it disconnected.
That is a **connection timeout**, not a field age limit. Disconnected values are
withheld. On reconnection the current manifest identifies any changed category;
unchanged completed categories stay valid, and changed ones show `updating` until
their new revision arrives. No claim is made that a never-observed or missed game
event can be inferred; manual refresh is the recovery path.

## Questions and tools

At typed-question acceptance or the start of microphone/audio input, Akane freezes
the current in-memory cache immediately. It never asks the game to refresh and
never waits for a newer snapshot. Later collection cannot alter that turn.
Missing/disconnected/updating data does not prevent “Hello, Akane!” or recording.
The model may describe an unavailable observation if it specifically asks for it.

Luna receives tool descriptions, the question and dialogue first. It chooses
`get_wow_context` for exact player/progress fields or watched quests, and
`get_wow_details(domain, query)` for professions, matching carried items,
Hearthstone or events. Only requested projections enter the model continuation.
Hearthstone remaining time is calculated at the frozen snapshot time; it does not
keep counting down within a turn. Existing Japanese-reference tools and the
one-batch retrieval/cancellation/accounting rules remain in place. Qwen tool
integration and automatic local fallback remain deferred.

## Full manual refresh

```text
/akanedata refresh
```

This recaptures all seven categories, increments revisions, restarts delivery and
prints available/partial/unsupported/unavailable counts in WoW chat. It never
substitutes data in a turn already accepted. Wait for delivery before starting
the question you want to use the refreshed data.

`/akanedata off` hides/stops export; local event collection continues.
`/akanedata on` resumes export. For complete collection shutdown, disable the addon.

## Verification and first live check

### Memory regression

The first optical sender allocated a new 384-entry RGB table and 384 RGB triples
for every packet, plus 15 integer-encoding tables and a revision manifest. An
offline Fengari probe measured **401,001 Lua table allocations over 1,000 idle
packets**. The retained snapshot graph remained at 11 tables before/after; that
does not measure the entire addon or prove the absence of a native client leak.

The sender now reuses its RGB output, integer scratch and manifest buffers. The
same probe measured **385 allocations including initial colour-buffer warm-up**;
the steady-state regression runs 500 real sender ticks and checks that allocation
does not scale per cell/frame or grow the snapshot graph. Packet bytes/checksums,
event collection, revisions, rate and observation timing remain unchanged.

No garbage-collector settings or forced collection were added. Lua automatically
collects unreachable objects; allocation counters can rise between collections
([Lua 5.1 memory-management reference](https://www.lua.org/manual/5.1/manual.html#2.10)).
Reload the UI after updating and observe actual addon memory through a play session.
These offline counts are not WoW memory usage in MB or a live GC benchmark.

`python -m unittest discover -s tests -v` uses fake services, binary fixtures and
fake WoW APIs. Lua integration uses existing Node/Fengari from the sibling
Witchcraft daemon, or `AKANE_TEST_FENGARI`; it skips clearly if unavailable and
downloads nothing. No screen, microphone, sound, gameplay or paid request is
used by these tests.

1. Restart WoW, confirm the strip and addon startup message, launch with the command
   above, then leave WoW foreground until the receiver has collected data.
2. Ask a greeting, location, watched-quest objective and a cooking-rank question.
   Confirm no “Getting WoW context” wait. With transcripts enabled, inspect
   `context_tool` selection and aggregate provider usage.
3. Gain XP/change a watched objective or item count; confirm subsequent questions
   see the change. Leave cooking untouched; it should not expire independently.
4. Test a question while in combat. Check that player/progress packets keep arriving;
   secret/restricted fields should be unavailable, never fabricated. Actual combat
   and native capture acceptance remain unverified until this succeeds.
5. Switch windows for more than five seconds: personal values should be withheld,
   but general questions must still work. Return to WoW and confirm recovery.
6. Run `/akanedata refresh`, allow delivery, then ask again. Restart without `-Wow`
   and verify no game tools/receiver. Existing Witchcraft chat/PTT retains its
   existing transport/foreground limitations; replacing its input path is separate.
