# Development Log

Notes on implementation, experiments, decisions, and lessons learned.
Early entries were reconstructed from development conversations.

## Current state
The terminal prototype supports conversation history, personality configuration, reset and exit commands, and common request-error handling.

## 2026-10-26 — Verifying configuration and extracting a function

#### Personality experiment
Added a Japanese-only instruction. The model still answered in English until reminded.

Printed the loaded prompt to verify the file contents directly. The instruction was present: loading worked, while model compliance was inconsistent.

Lesson: verify file loading through program output rather than relying only on the model’s behavior.

#### Refactoring
Created get_llm_reply(messages) to build the payload, send the request, check its status, and return reply text.

The chat loop remains responsible for history, commands, output, and handling request failures.

#### Organization
- Moved model communication into ollama_backend.py.
- Moved the URL, model name, timeout, and reply limit into config.py.
- Kept personality instructions in personality.txt.

Learned that a Python file is a module. Importing a module executes its top-level code, while defining a function does not execute its body.

#### Input handling
Added .strip() to remove surrounding whitespace and skipped blank input before making a request.

Chose match / case for named commands because more commands may be added later. Command matching uses lowercase text while normal messages retain their original capitalization.

#### Startup
Moved application startup and the conversation loop into main().

Added if __name__ == "__main__": so importing the module does not start an interactive session.

#### Missing personality file
Added a FileNotFoundError handler.

The first attempt surrounded path construction, which does not read the file. Corrected it to surround read_text(), where the missing-file error occurs.

The program now has a clear error message and an early return for that case. A deliberate missing-file test remains to be recorded.



## 2026-10-24 — Personality file

Started moving personality instructions from Python code into personality.txt.

Used pathlib to locate the file beside main.py, independently of the terminal’s working directory. Read the file with UTF-8 encoding.

This makes personality experiments possible without editing application logic.

## 2026-10-23 — First working conversation

#### Implemented
- Sent a terminal message to Ollama using requests.
- Extracted the assistant’s reply from the JSON response.
- Added a conversation loop and an exit command.
- Added conversation history and a system prompt.

#### Learned
The server returns a dictionary containing reply text and additional metadata. The reply is accessed through ["message"]["content"].

Conversation history must include both user messages and assistant replies. Initially, only user messages were saved. Adding assistant replies made follow-up exchanges more coherent.

#### Observations
Llama 3.2 3B frequently invents game details, repeats greetings, and ignores personality instructions. It remains useful for testing the application’s message flow.

#### Error handling and reset
Added a reset command that preserves the system prompt. Initially forgot continue, so the history cleared but the word reset was also sent to Ollama.

Added handlers for connection errors, timeouts, and HTTP errors. Failed requests remove the pending user message from history.

Manually exercised the handlers by stopping Ollama, using a very short timeout, and requesting a nonexistent model. Each case displayed an error and returned to the input prompt.

Stopping Ollama also disabled Continue’s local coding suggestions because both applications use the same server.

## Project setup — date not recorded
Established Project Akane as a personal Python learning project: an AI gaming and streaming co-host.

Decided to start with a terminal chatbot using local Ollama. OpenAI support will come later, so model communication should remain replaceable.

Inspected Project Riko as a reference. Its public code demonstrates a voice conversation pipeline, but Akane will start with a smaller, independently written core.