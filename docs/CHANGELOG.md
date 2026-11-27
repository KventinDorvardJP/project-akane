# Changelog

Notable changes to Project Akane are recorded here.
Unreleased changes stay under Unreleased until a version is released.

## [Unreleased]

### Fixed
- Release Tkinter objects on the UI thread when the control window closes or restarts, preventing a worker from tearing down the old Tcl interpreter.
- Clarified Japanese speech instructions for multilingual input and route obvious non-Japanese speech fields to text before they reach the voice engine.
- Included Forever's Reagent Bag (container 5) in AkaneData inventory scans and raised the bounded receiver capacity to 259 carried/equipped item rows. Regression tests cover Linen Cloth stored only there, bag-event count updates and full-capacity packet decoding. Partial/truncated inventory must not be presented as a complete zero count.
- AkaneData's optical retransmission reuses RGB/encoding/manifest buffers instead of allocating roughly 400 temporary Lua tables per packet. An offline 1,000-packet probe dropped from 401,001 to 385 table allocations including warm-up; a steady-state allocation regression preserves packet decoding and bounded snapshot state. Native WoW memory/GC acceptance remains manual.
- Clarified Luna's search selection: personal banter about Akane's hobbies/activities should not become research about ChatGPT or OpenAI. External facts/recommendations, required identity checks and `/search` remain supported; live tool-choice acceptance is still needed.
- Quest freshness now supports negotiated host-issued capture references: the addon freezes the host slot read before capture, and the daemon validates its immutable first write time. Anchored captures avoid the legacy blanket timing margin while retaining the age limit; old, replayed, unknown, future and cross-session references cannot become fresh. Legacy timing remains supported.
- Fixed Witchcraft hold-to-talk starving game context: physical hold state uses the existing heartbeat row, fresh cursor movement gates leases, and short controls retain partial packet progress. Quest rows pack into bounded packets; urgent small-domain requests can suspend/resume one quest revision without changing capture timestamps. Existing freshness limits and combat/build gates remain.
- Optional session logs now include per-domain status/age when a WoW lookup exhausts its wait, so stale/missing domains can be diagnosed from the logs.
- Typed WoW questions wait only for required domains, with mixed-query scope, recognized follow-up reuse and conservative fallback for unknown/spoken questions; stale unrelated fields no longer delay an otherwise answerable question.
- Raw WoW snapshots are hidden from the control-window conversation; selected context still reaches the LLM and remains in optional local transcripts for diagnosis.
- Oversized reply pairs no longer disable all speech: Japanese is synthesized as bounded local pieces while its complete parent English caption remains visible. Long English captions no longer block TTS; speech-piece metrics/logging and safe CSV migration distinguish audio pieces from model pairs.
- Cold Witchcraft refreshes prioritize quest objectives before unrelated domains, reducing stale-quest partial replies under the short wait budget.
- Removed hard generation character caps that could force a valid paired reply to end inside a quest name; short-part instructions, bounded speech and complete text fallback remain.
- Retrieved-only web links remain in the sources sidebar instead of crowding the conversation; cited references retain inline links.

### Added
- Optional SQLite memory for user-saved facts and events, with local save, browse, correct and forget commands. Luna can search relevant notes through one bounded read-only lookup.
- Local session review command for saved transcripts, with per-turn text, playback and subtitle-fallback outcomes, timing, and recording markers.
- Choose English or Russian subtitles in Settings. Russian captions use the existing OBS URL and file and follow the corresponding Japanese speech parts.
- Witchcraft hold-to-talk key binding, configurable in WoW under Akane. Keyboard/side-button holds share existing PTT gestures, physical leases and release behavior; key repeat cannot reopen recording and rebinding cancels capture. No key is assigned automatically; native routing remains a manual check.
- Opt-in `-Wow` module and independent event-driven AkaneData addon: revision/session cache, immediate question-time freezing, selective extended context tools, manual full refresh and bounded optical sender/receiver without Witchcraft's combat gate. Offline tests cover protocol and fake combat; native/combat acceptance remains manual. No autonomous replies or per-field TTLs.
- Optional transcript events for bounded provider-returned web-search queries, tied to each turn/request, with explicit missing-query handling and no archived page bodies.
- Luna-selected read-only local context tools for specific WoW fields/matching tracked quests and the selected LunaTranslator line. Unrequested data stays local, raw attachments no longer accumulate in OpenAI history, and one bounded tool round preserves consent, frozen observations, cancellation, web-search limits and combined usage/cost accounting. Qwen's existing attachment path remains unchanged.
- Local `[Akane]` English replies in WoW's normal General chat frame and a movable in-game hold-to-talk button on the Witchcraft companion branch. Explicit press/release ownership, physical hold leases, early-release cancellation and lost-release/duration-limit discard reuse the existing single worker and STT/speech/subtitle pipeline. Installed addon files are backed up; live game/audio acceptance remains manual.
- Optional Witchcraft in-game questions and English replies using a bounded authenticated loopback adapter to the running control panel; shared history/turn worker, Japanese speech/OBS captions, busy rejection, cancellation and opt-in context are preserved. Includes `-Witchcraft`, a saved Settings choice, `start_witchcraft.ps1`, and headless transport/GUI tests.
- Accepted typed input is recorded before context lookup in optional session logs, including attempts that fail before reaching the model.
- Opt-in Witchcraft event-driven background snapshots on the companion branch, bounded idle renewal/coalescing, an eight-second cold-cache fallback, and separate WoW context timing with safe CSV migration.
- Opt-in Witchcraft/WoW Forever context in the control window, per-question local refresh requests, strict freshness/session checks, bounded cancellation, visible quoted snapshots, settings and service-free tests; paired speech/subtitles and both conversation providers remain supported.
- Optional timestamped session transcripts (readable text plus JSONL), turn-linked input/replies/context/sources/metrics, per-part speech/caption events and failure isolation. Disabled by default, stored under ignored runtime output.
- GUI transcript status, explicit copy-path/recording-marker controls, terminal markers, and service-free logging/cancellation/file-failure tests.
- Ordered paired JA/EN response segments, per-part TTS/subtitles, one-part prefetch, text-only/failure paging and preserved full GUI/history replies; shared by cloud/local conversation and both speech engines.
- Chunk length/count/prefetch/reading-speed settings, first-speech/whole-turn metrics, older timing-CSV migration with backups and service-free chunk/cancellation/schema tests.
- Control-window Settings tabs for all current public configuration, audio/reference pickers, device listing, typed validation, atomic local overrides and idle-only in-process Save & restart.
- Saved startup choices for test replies, recognition and global hotkeys; headless settings/persistence/restart checks and a settings guide.
- Optional GPT-SoVITS inference-WebUI TTS with local reference audio/text, auxiliary clips, timeout/error handling and temporary-download cleanup; VOICEVOX remains the default.
- TTS configuration/CLI/PowerShell selection, visible engine name, WAV sample generator, optional client requirements, shared WAV validation and mocked backend regression checks.
- Configurable VOICEVOX speech volume (`VOICEVOX_VOLUME_SCALE`, default 1.5), applied before synthesis in all playback modes, with input validation and mocked request checks.
- Optional same-process OBS Browser Source (`--overlay` / `-Overlay`): wrapped English text with a fitted translucent background, blank/disconnected hiding, loopback-only read-only routes, configurable appearance, and a control-window URL copy button.
- Isolated overlay HTTP/lifecycle tests and browser-client tests with fake DOM/fetch/clocks; manual OBS setup guide. Existing text-file subtitles remain supported.
- Windows CI for service-free tests and an ignored `config.local.json` override for personal settings.
- Opt-in LunaTranslator original-text WebSocket reception, Japanese preview/attachment controls, bounded reconnecting reception, and quoted per-turn context for typed/spoken questions.
- Recording-start context snapshots, stale/disconnected-line rejection, and service-free socket/context regression tests; pinned `websockets==17.4`.
- Optional same-process native control window (`--gui` / `-Gui`) with persistent JA/EN conversation, typed/audio input, Record/Pause/Stop/Reset buttons, clickable sources, status, timings, and estimated spending.
- Background STT startup and headless window lifecycle/control tests using the existing single turn worker.
- Cloud-only hosted web search with forced identity verification, explicit `/search`, source metadata/clickable citation output, and bounded tool operations.
- Search-fee accounting in the soft session budget and timing CSV, with migration from both earlier CSV formats.
- Optional Windows hotkeys for recording, pause/resume, and stop, with a single active turn worker and interruptible speech playback.
- Ollama/OpenAI conversation selection, strict cloud JA/EN output, reported token counts, estimated API costs, and a soft conversation session budget.
- Runtime status files, safe timing-CSV migration with an original backup, and mocked hotkey/cancellation/cloud regression checks.
- Microphone input with Enter start/stop, configurable device/duration/RMS limits, and bounded capture.
- Local Faster-Whisper and cloud transcription adapters sharing a common result format.
- Audio-file input and sequential microphone -> STT -> reply -> speech flow.
- Paired Japanese/English JSON replies, Japanese VOICEVOX speech, and English subtitle-file output for OBS.
- Atomic caption updates, short-caption minimum duration, startup/reset/exit clearing, and an optional Tkinter preview.
- Service-free fake-backend/STT/TTS CLI switches, a venv launcher, timing CSV, and an explicit live benchmark.
- Automated voice-pipeline regression tests, voice credit references, setup documentation, and a code walkthrough.

### Changed
- Generate one translation per reply, English or Russian, alongside Japanese speech. The control window, OBS captions, citations and transcripts use the selected language. Russian prompts favor natural spoken phrasing and established game terminology.
- Prepared the source snapshot for publication: documented the current prototype, excluded local notes/runtime/audio/experiments, and separated core requirements from optional local STT.
- Public defaults use the system microphone; personal device settings can stay local.
- Configured the optional cloud model as Luna and added its rates; local Ollama remains the default provider.
- Shared turn processing between terminal and hotkey modes; retain the Enter-based terminal workflow by default.
- Current setup instructions target one PC, with no Stream Deck or second-machine dependency.
- Switched the default local conversation model to Qwen3 4B Instruct for bilingual experiments.
- Expanded configuration to cover STT, microphone validation, VOICEVOX timeouts, and subtitle duration.
- Updated the fake backend to satisfy the bilingual JSON contract.
- Load the local recognition model only when local STT is selected.

### Fixed
- Preserve segment boundaries through citation cleanup and avoid sending oversized unsplit replies whole to TTS; release earlier WAV data during delivery.
- Read current Ollama settings for each request so session reloads use saved models/limits.
- Keep the control window and Settings usable when the selected conversation backend cannot initialize; report the error instead of silently changing providers.
- Withhold answers when required search did not run or a searched answer has no usable sources; prevent stale sources from surviving failed/cancelled turns.
- Prevent stopped/paused late replies from restoring captions or starting speech; defer reset until the active worker is finished.
- Reject malformed, empty, and non-string bilingual replies without crashing the conversation loop.
- Handle malformed provider envelopes, LLM truncation, decoder failures, and invalid speech audio with recoverable errors.
- Remove temporary microphone audio after STT and on failed/interrupted turns while preserving user sample files.
- Keep valid conversation history and readable text when speech generation/playback fails.
- Close the cloud client and clear subtitles on EOF/Ctrl+C as well as normal exit.

Milestone 2 implementation and automated checks are complete; stream-scene acceptance
remains a manual check. No new release number or Git tag has been assigned.

## [0.1.0] - 2026-10-29

#### Added
- Terminal chatbot connected to local Ollama.
- Conversation history containing user messages and assistant replies.
- Configurable character personality loaded from personality.txt.
- `exit` command to end the session.
- `reset` command to clear conversation history while preserving personality.
- Error messages for connection failures, request timeouts, and HTTP errors.
- Centralized settings for the Ollama URL, model, request timeout, and reply token limit.
- Configurable conversation history limit that preserves the system prompt and removes the oldest complete exchanges.
- Replacement test backend for checking the chat loop without model requests.
- Pinned dependency in `requirements.txt` and Windows setup, launch, command, and configuration instructions in `README.md`.

#### Changed
- Extracted Ollama communication into ollama_backend.py.
- Moved application settings into config.py.
- Organized command handling with match / case.
- Wrapped startup and the chat loop in main().
- Added a startup guard so importing main.py does not launch the chatbot.
- Made the reply function an argument to `main()` so backends can be replaced without changing conversation logic.
- Introduced a shared `BackendError` exception; the Ollama backend translates request failures and the chat loop handles them consistently.

#### Fixed
- Included assistant replies in conversation history so subsequent requests contain both sides of the conversation.
- Prevented the reset command from being sent to the model.
- Removed unanswered user messages from history when requests fail.
- Ignored empty or whitespace-only input.
- Removed surrounding whitespace before processing commands.
- Resolved the personality file relative to the script’s location.
