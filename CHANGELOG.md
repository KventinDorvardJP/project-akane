# Changelog

Notable changes to Project Akane are recorded here.
Unreleased changes stay under Unreleased until a version is released.

## [Unreleased]

#### Added
- Terminal chatbot connected to local Ollama.
- Conversation history containing user messages and assistant replies.
- Configurable character personality loaded from personality.txt.
- `exit` command to end the session.
- `reset` command to clear conversation history while preserving personality.
- Error messages for connection failures, request timeouts, and HTTP errors.
- Centralized settings for the Ollama URL, model, request timeout, and reply token limit.

#### Changed
- Extracted Ollama communication into ollama_backend.py.
- Moved application settings into config.py.
- Organized command handling with match / case.
- Wrapped startup and the chat loop in main().
- Added a startup guard so importing main.py does not launch the chatbot.

#### Fixed
- Included assistant replies in conversation history so subsequent requests contain both sides of the conversation.
- Prevented the reset command from being sent to the model.
- Removed unanswered user messages from history when requests fail.
- Ignored empty or whitespace-only input.
- Removed surrounding whitespace before processing commands.
- Resolved the personality file relative to the script’s location.