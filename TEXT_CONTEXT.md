# Japanese reference from LunaTranslator

Akane can receive original text from LunaTranslator and attach the selected line
to your typed or spoken question. This is an opt-in GUI feature and works with
either conversation backend. It does not translate every incoming line or trigger
autonomous replies.

The supplied endpoint is `ws://127.0.0.1:2333/api/ws/text/origin`.
LunaTranslator documents it as a stream of extracted original text, distinct from
its translated-text endpoint. See the [official network-service documentation](https://docs.lunatranslator.org/en/apiservice.html).
The [upstream sender](https://github.com/HIllya51/LunaTranslator/blob/main/src/LunaTranslator/textio/textoutput/websocket.py)
sends the original string as a text frame, without a JSON envelope.

## Use it

1. When free, start your game/text source in LunaTranslator and ensure extraction
   works there. Enable its network service on port **2333** yourself if it is off.
   Akane does not launch LunaTranslator, choose hooks, configure it, or send input.
2. Launch Akane's control window, for example:

   ```powershell
   .\run.ps1 -Gui -Hotkeys -LlmBackend openai
   ```

3. In **Japanese reference · LunaTranslator**, click **Connect text feed**.
4. Advance/capture a line in LunaTranslator and confirm the correct sentence appears
   in Akane's preview. Do not assume the service sends the current line immediately
   on connection; a new extraction may be needed.
5. Check **Include Japanese line**, then type a question or start recording.
   For example, ask “What does 私 mean here?” while the preview contains
   `私の名前は朱音です。`.

Akane sends your question and a quoted reference together. The actual attached
sentence appears beneath your input in conversation history, so you can verify
what was used. While the checkbox stays checked, each accepted question attaches
the current fresh line. Uncheck it for ordinary conversation.

For microphone input, the line is captured when recording starts, not when STT
finishes. Advancing the game while recording cannot replace that turn's sentence.
The same snapshot rule applies when a typed/audio-file turn is accepted.

No question is sent automatically when text arrives. If attachment is checked but
no current/fresh line exists, the turn is rejected with a notice before recording,
STT or LLM work begins; typed input remains in its entry. Capture a line or uncheck
the option. Freshness is checked at turn acceptance.

## Connection and clearing

- Connection is **off by default**, independent of microphone hotkeys. Click Connect
  explicitly. The GUI remains useful without the feed or its Python package.
- Transient disconnects retry every two seconds while enabled. The old preview is
  cleared immediately, and a new line must arrive before it can be attached again.
- **Disconnect** stops the listener, clears the line, and unchecks attachment.
- **Reset chat** clears the current line and attachment selection, while leaving
  the connection enabled for subsequent new text.
- Pause/Stop cancel conversation work; the receive-only feed can continue updating
  its preview. An already accepted turn's snapshot stays fixed.
- Window exit stops the feed and waits for its thread to close, including a pending
  handshake. No socket close/join blocks Tk's main event loop during normal exit.

Only the latest line is held in memory; received updates do not queue chat requests
or create an extraction log. Repeated frames do not accumulate. Source text is
quoted user data, not a system message or command. Web-search intent detection uses
your actual question, so `誰` inside a quoted sentence does not itself force search.
This separation helps handling; it is not a guarantee of model correctness.

With OpenAI conversation, the selected line is sent to the API as part of your
question and retained in bounded chat history. Listening itself makes no model/API
calls. Attached text adds normal input tokens; existing usage estimates count them.

## Configuration and dependency

```python
TEXT_CONTEXT_WS_URL = "ws://127.0.0.1:2333/api/ws/text/origin"
TEXT_CONTEXT_MAX_CHARS = 2000
TEXT_CONTEXT_MAX_AGE_SECONDS = 300
```

The URL accepts only loopback `ws`/`wss` addresses with the original-text path and
an explicit port. It bypasses proxy settings. Incoming WebSocket messages are
bounded to 16 KiB, with one queued frame; non-text, oversized, and control-character
frames are rejected. Text is preserved rather than guessing a JSON envelope or
rewriting Japanese. Whitespace-only frames are ignored.

The new dependency is pinned as `websockets==17.4` in `requirements.txt` and was
installed in the project's existing venv. For another checkout:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The listener uses the library's threading client, short receive timeouts, a
two-second connection timeout, and a half-second closing timeout. See its
[official client reference](https://websockets.readthedocs.io/en/stable/reference/sync/client.html).
It never sends application messages to LunaTranslator; normal WebSocket handshake
and protocol handling are performed by the library.

## Verification

**129 tests passed**: 113 existing and 16 new context tests. Fake WebSocket objects
cover explicit connection, bounded receive, reconnect/clearing, duplicates, stale
and malformed frames, shutdown, optional attachment, exact Japanese preservation,
question-only search intent, and the recording-start snapshot. No actual socket,
window, game, microphone, or paid API was used for verification.

The live LunaTranslator stream and native preview still require your manual check.
No external-app settings were changed. If your installed LunaTranslator emits a
different frame format, capture an example before adding a format-specific parser.
