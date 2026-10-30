# Milestone 1 — Basic AI Brain

- [x] Create a terminal chat loop using Ollama.
- [x] Maintain user and assistant conversation history.
- [x] Add exit and reset commands.
- [x] Ignore blank input.
- [x] Handle connection errors, timeouts, and HTTP errors.
- [x] Load personality from a separate text file.
- [x] Separate Ollama communication into a backend module.
- [x] Move model settings into config.py.
- [x] Add a main function and startup guard.
- [x] Handle a missing personality file.
- [x] Limit conversation history while preserving the system prompt.
- [x] Define a common backend interface for Ollama and future OpenAI support.
- [x] Verify the chat loop works with a replacement test backend.
- [x] Record dependencies and setup instructions.
- [x] Run final checks: chat, context recall, reset, exit, and error handling.

## Completion criteria

A working terminal-based Akane with configurable personality, bounded
conversation history, and a backend that can be replaced without rewriting
the chat loop. Paid API integration is deferred.