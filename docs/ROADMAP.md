# Project Akane — Roadmap

The current Windows prototype runs on one PC. Voice interaction, the control
window, optional hotkeys, local/cloud conversation, hosted web lookup, and selected
LunaTranslator reference text are implemented. Live stream/device acceptance is
separate from automated tests; see README.md and the feature guides.

Approved sidetrack (2026-11-11): opt-in WoW Forever context and in-game questions/
English replies via Witchcraft, sharing Akane's existing GUI worker/history; see
WOW_CONTEXT.md and TODO.md. This does not start the avatar milestone or automatic
game commentary.

## Milestone 1: Basic AI Brain
- Terminal chatbot
- Conversation history
- Configurable personality
- Interchangeable LLM backends

## Milestone 2: Voice Interaction
- Software implemented with service-free regression coverage; live setup checks are listed in README.md.
- Local and cloud STT experiments
- Microphone recording
- Local Japanese TTS using VOICEVOX
- Paired JA/EN replies: Japanese speech through VOICEVOX, with the English translation displayed as subtitles
- Spoken conversations
- OBS subtitle text-file output and optional same-process Browser Source with fitted, automatically hidden background; standalone preview is optional
- Paired short JA speech / EN captions with one-part prefetch and text-only paging are implemented; live quality/timing acceptance remains manual
- Optional local timestamped user/JA/EN session transcripts and recording markers for reviewing real stream tests
- Optional global hotkeys, interruption controls, and selectable local/cloud conversation

## Milestone 3: Avatar
- Live2D or VRM integration
- Lip synchronization
- Basic reactions

## Milestone 4: Streaming Integration
- Twitch chat
- OBS integration
- Game dialogue recognition

## Milestone 5: Advanced Intelligence
- Persistent memory foundation implemented early: explicit saved notes, local SQLite search, and Luna's read-only search tool. Automatic memory suggestions remain future work.
- Japanese learning progress
- Autonomous conversation behavior
