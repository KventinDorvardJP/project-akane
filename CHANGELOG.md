# Changelog

Notable changes to Project Akane are recorded here.
Unreleased changes stay under Unreleased until a version is released.

## [Unreleased]

### Added
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
