# Changelog

Notable changes to Project Akane are recorded here.
Unreleased changes stay under Unreleased until a version is released.

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