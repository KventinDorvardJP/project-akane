
# Project Akane — Codex Instructions

## Project
Build an anime-style AI gaming and streaming co-host
called Akane.

Akane should support natural conversation, playful
banter, speech, an animated avatar, Twitch interaction,
and persistent memory.

## Environment
- Windows
- Python
- Intel Core i7, 13th generation
- 32 GB RAM
- NVIDIA RTX 4050, 6 GB VRAM
- Ollama installed with llama3.2:3b

## Architecture
- Build independent, interchangeable components.
- Use Ollama for initial development.
- Allow future cloud LLM providers.
- Keep GPU usage low during gaming.
- Favor simple, maintainable Python.

## Development Rules
- Work through milestones sequentially.
- Explain important decisions and unfamiliar code.
- Make small, testable changes.
- Do not introduce unnecessary frameworks.
- Do not implement future milestones prematurely.
- Run relevant tests when possible.
- Never claim tests passed unless executed.
- Ask before major architectural changes.
- Preserve existing project decisions.

## Current Milestone
Milestone 1: Basic AI Brain

Requirements:
- Terminal-based conversation
- Ollama connection
- Conversation history
- Configurable personality
- Interchangeable LLM provider interface

## Reference
https://github.com/rayenfeng/riko_project

Use Project Riko for architectural inspiration,
not as a codebase to blindly copy.
