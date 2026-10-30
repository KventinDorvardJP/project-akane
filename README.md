# Project Akane

A Python terminal prototype for an AI gaming and streaming co-host.

Currently uses local Ollama with configurable personality and conversation history.

## Requirements

- Windows with Python 3.12, 64-bit.
- Ollama installed and running.
- The `llama3.2:3b` model installed in Ollama.

## Setup

From the project directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If the model is not already installed:

```powershell
ollama pull llama3.2:3b
```

## Run

Start Ollama, then run:

```powershell
.\.venv\Scripts\python.exe main.py
```

Type a message and press Enter.

## Commands

- `exit` — end the session.
- `reset` — clear conversation history while preserving personality.
- Blank input is ignored.

Commands are case-insensitive.

## Configuration

Edit `config.py` to change the model, server URL, request timeout, reply token limit, or retained conversation length.

Edit `personality.txt` to change Akane’s character instructions. Restart the program after changing configuration or personality.

## Current limitations

- Conversation history is kept only during the current session.
- Older exchanges are discarded when the configured history limit is exceeded.
- Model replies may contain invented facts or ignore personality instructions.
- Speech, avatars, Twitch, and OpenAI integration are not implemented yet.