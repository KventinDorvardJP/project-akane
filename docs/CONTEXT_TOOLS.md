# Luna's local context tools

OpenAI conversation uses model-selected, read-only local retrieval. Your question,
recent dialogue and tool descriptions go to Luna first. Full WoW snapshots and
the selected Japanese reference stay local unless requested. Questions needing no
local context can finish in one request; retrieval adds one continuation request.
This is explicitly authorized context acquisition, not an automatic retry or an
extra model call to repair speech/translation.

Restart Akane with OpenAI conversation selected. **Include Japanese line** controls
its selected reference. WoW tools now require the opt-in `-Wow` module and AkaneData
addon; the GUI initially selects **Include WoW context**, which can be unchecked.
Terminal/hotkey questions also freeze the module cache. See [WOW_MODULE.md](WOW_MODULE.md).
Qwen/Ollama keeps its previous attachment path; tool integration and automatic
provider fallback are not implemented.

## Tools

- `get_wow_context(fields, quest_query, quest_detail)` selects character/location,
  money/XP or situation fields. `quests` selects tracked quests, optionally matched
  by case-insensitive title/objective substring. Empty query selects all exported
  tracked quests. `summary` returns names/completion; `objectives` also returns
  objective text/counts. No arbitrary file paths, commands, URLs or game actions.
- `get_japanese_reference()` returns only the original line selected for this
  turn. Receiving lines never calls Luna. Unselected sources are not advertised;
  forged calls to an unselected source return `disabled`, without data.
- `get_wow_details(domain, query)` selects professions, carried/equipped inventory,
  Hearthstone or recent death/level/zone events from AkaneData. Inventory queries
  match a name substring or item ID. No recipes, bank/mail or full death recap.
- `search_memory(query, game, since)` searches user-saved notes only when memory is
  enabled. One search per turn can return up to four matches. See [Persistent memory](MEMORY.md).

Luna is instructed to retrieve current personal observations rather than infer
them from older discussion, and to skip personal state for general game questions.
That is model judgment, not a guaranteed intent detector. Unknown fields/arguments
return an explicit invalid-request result. No matching tracked quest is not proof
that a quest was turned in or that the full quest log is empty.

## Timing, consent and bounds

The module freezes the in-memory WoW cache and selected Japanese line **at turn
acceptance, before recording/STT**. Tools project those copies; they never refresh
or wait for the game, and later updates cannot change them. Missing or disconnected
game data does not block the question. Source observation time, revisions, nulls
and truncation stay explicit. Unchanged fields have no TTL; connection loss and
incomplete revisions withhold affected data. Legacy Witchcraft refresh code remains
for compatibility, but normal `-Wow` launches no longer use that wait path.

One batch of up to eight local calls is allowed, with at most 16,000 characters per
result and 24,000 across the batch. Independent requests should be made together.
The continuation has no local tools; further local requests are rejected without
retry. Each model request retains the configured output-token limit. Stop/Pause
is checked before requests, after accounting in-flight responses, and between
local calls. Cancelled results never become visible replies, audio or history.

Hosted web search remains independently selectable, with identity verification,
`/search`, source validation and citation cleanup. Its operation allowance spans
both requests; it is not reset on continuation. The budget is rechecked before
continuing. Missing usage blocks further budgeted requests. Both stages can bill;
fewer attached facts do not guarantee lower total tokens, cost or speaking latency.

## History and diagnostics

OpenAI history retains questions and full paired JA/EN replies, not raw source
attachments or transient tool-call output. Legacy user attachments are stripped
from the outgoing OpenAI history without changing assistant replies. Facts not
mentioned in replies stay out of durable memory unless you explicitly save them.

The GUI continues to hide raw WoW JSON. The Japanese reference is labelled selected,
which does not mean Luna actually requested it. Existing optional local transcripts
still retain selected full snapshots for diagnosis; those logs are not uploaded.
`context_tool` records the tool, validated selection, result status and character
count. `provider_usage` records each request's usage. The final metrics sum input,
cached input, output, reasoning and estimated costs across requests; they include
`provider_requests` and `local_tool_calls`. The GUI displays those counts. Older
timing CSVs are backed up and migrated automatically.

## Verification

`python -m unittest discover -s tests -v` runs service-free. New tests use the real
installed OpenAI SDK over in-memory HTTP: selective projection, no-upload direct
answers, parallel calls, frozen data, encrypted reasoning/call-ID continuation,
opt-in enforcement, malformed/oversized requests, history, logging, aggregate
usage, budget/search limits and cancellation. No game, microphone, sound or paid
model request is used.

Live acceptance remains manual: compare a general class question, a current quest
count and a Japanese-line explanation. With transcripts enabled, inspect tool
selection and combined usage/timing. Check that answers stay complete and Stop
still discards output. Savings and tool-selection accuracy need live measurement.
