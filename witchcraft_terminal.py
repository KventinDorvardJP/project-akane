"""English-only Witchcraft frontend. No model, audio or gameplay code lives here."""
import argparse
import json
from pathlib import Path
import re
import signal
from time import monotonic, sleep
import uuid
from urllib.error import URLError
from urllib.request import Request, ProxyHandler, build_opener

from witchcraft_bridge import ENDPOINT_NAME, MAX_TEXT


def endpoint_document(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(4097)
    if len(raw) > 4096:
        raise ValueError("Invalid Akane endpoint file")
    document = json.loads(raw)
    if (not isinstance(document, dict) or document.get("protocol") != 1
            or type(document.get("port")) is not int or not 1 <= document["port"] <= 65535
            or any(not isinstance(document.get(key), str) or
                   not re.fullmatch(r"[0-9a-f]{64}", document[key]) for key in ("session", "token"))):
        raise ValueError("Invalid Akane endpoint file")
    return document


def display_text(value):
    # Model/game text must not operate the terminal through ANSI/control bytes.
    return re.sub(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", "", str(value)).replace("\r", "")


class TerminalClient:
    def __init__(self, endpoint_path, *, output=print):
        self.endpoint_path, self.output = Path(endpoint_path), output
        self.endpoint = None
        self.connected = False
        self.cursor = 0
        self.last_error = None
        self.closed = False
        self.pending = {}  # Only acknowledgements, never questions for automatic retry.
        self.opener = build_opener(ProxyHandler({}))  # Always literal loopback; ignore proxy environment.

    def exchange(self, route, document=None):
        endpoint = self.endpoint
        data = json.dumps(document, ensure_ascii=False).encode("utf-8") if document is not None else None
        request = Request(f"http://127.0.0.1:{endpoint['port']}{route}", data=data,
                          headers={"Authorization": f"Bearer {endpoint['token']}", "Content-Type": "application/json"})
        with self.opener.open(request, timeout=0.8) as response:
            raw = response.read(3 * 1024 * 1024 + 1)
        if len(raw) > 3 * 1024 * 1024:
            raise ValueError("Akane response exceeds the local display budget")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid Akane response")
        return result

    def poll(self):
        try:
            endpoint = endpoint_document(self.endpoint_path)
            if endpoint != self.endpoint:
                self.endpoint, self.cursor = endpoint, 0
                if self.pending:
                    self.output("Akane session changed. Pending input will not be resent.")
                self.pending.clear()
                self.connected = False
            result = self.exchange(f"/events?session={endpoint['session']}&after={self.cursor}")
            if result.get("session") != endpoint["session"]:
                raise ValueError("Akane session changed")
            if result.get("closing"):
                raise ValueError("Akane is closing")
            if not self.connected:
                self.output("Connected to Akane's control panel. Type a question and press Send.")
                self.output("/context on: attach WoW data; /stop, /pause, /reset, /help. Japanese voice plays through Akane.")
            self.connected, self.last_error = True, None
            if result.get("gap"):
                self.output("Some older display events expired; the full conversation is in Akane's control panel.")
            for event in result["events"]:
                self.show_event(event)
                self.cursor = event["seq"]
            for identifier, started in list(self.pending.items()):
                if monotonic() - started > 6:
                    del self.pending[identifier]
                    self.output("Input acknowledgement unavailable. Check Akane before sending again; no automatic retry was made.")
        except (OSError, ValueError, KeyError, TypeError, URLError) as error:
            self.connected = False
            message = "Waiting for Akane. Start run.ps1 -Gui -Witchcraft; keep its control panel open."
            if self.last_error != message:
                self.output(message)
            self.last_error = message

    def show_event(self, event):
        kind = event.get("kind")
        if kind == "reply":
            self.output("\nAKANE: " + display_text(event.get("en", "")) + "\n")
        elif kind == "user":
            self.output("YOU: " + display_text(event.get("text", "")))
        elif kind == "notice":
            self.output(display_text(event.get("message", "")))
        elif kind == "status":
            self.output("Akane: " + display_text(event.get("state", "")))
        elif kind == "context":
            self.output("WoW context: " + ("on" if event.get("enabled") else "off (use /context on to attach game data)"))
        elif kind == "reset":
            self.output("Conversation reset. WoW context is off; /context on enables it again.")
        elif kind == "ack":
            self.pending.pop(event.get("id"), None)
            if not event.get("accepted"):
                self.output(display_text(event.get("message", "Input refused.")))

    def command(self, line):
        line = line.strip()
        if not line:
            return
        if line.lower() == "/help":
            self.output("Type a question, or /stop, /pause (toggle), /reset, /context on, /context off, /exit (disconnect this adapter).")
            return
        if line.lower() == "/exit":
            self.closed = True
            return
        action, value = "text", line
        if line.lower() in {"/stop", "/pause", "/reset"}:
            action, value = line[1:].lower(), None
        elif line.lower() in {"/context on", "/context off"}:
            action, value = "wow_include", line.lower().endswith(" on")
        if len(line) > MAX_TEXT:
            self.output("Question too long; it was not sent. Please split it into separate questions.")
            return
        if not self.connected:
            self.output("Akane is disconnected. Input was not queued; resend after the connection returns.")
            return
        if len(self.pending) >= 8:
            self.output("Wait for the pending input acknowledgement before sending more.")
            return
        identifier = uuid.uuid4().hex
        # Never retry a POST: a broken connection does not prove it was refused.
        try:
            self.exchange("/command", {"session": self.endpoint["session"], "id": identifier,
                                      "action": action, "value": value})
            self.pending[identifier] = monotonic()
        except (OSError, ValueError, URLError):
            self.connected = False
            self.output("Delivery could not be confirmed. Check Akane before sending again; no automatic retry was made.")


class TerminalInput:
    """Poll ConPTY without a blocked stdin thread; collect bracketed paste as one question."""
    START, END = "\x1b[200~", "\x1b[201~"

    def __init__(self, command, *, output=print):
        self.command, self.output = command, output
        self.buffer, self.escape = "", ""
        self.pasting = self.oversized = self.extended = False

    def feed(self, character):
        if self.extended:
            self.extended = False
            return
        if character in {"\x00", "\xe0"}:
            self.extended = True
            return
        if character == "\x03":
            self.buffer, self.escape = "", ""
            self.pasting = self.oversized = False
            self.command("/stop")
            return
        if character == "\x1b" or self.escape:
            self.escape += character
            if self.escape.startswith("\x1b[") and len(self.escape) >= 3 and "@" <= character <= "~":
                if self.escape == self.START:
                    self.pasting = True
                elif self.escape == self.END:
                    self.pasting = False
                # Consume complete CSI responses/keys, including cursor reports.
                self.escape = ""
            elif len(self.escape) > 32 or (len(self.escape) == 2 and character != "["):
                self.escape = ""
            return
        if character in {"\r", "\n"}:
            if self.pasting:
                self.append(" ")
            else:
                if self.oversized:
                    self.output("Question too long; it was not sent.")
                else:
                    self.command(self.buffer)
                self.buffer, self.oversized = "", False
        elif character in {"\b", "\x7f"}:
            self.buffer = self.buffer[:-1]
        elif character == "\t":
            self.append(" ")
        elif character.isprintable():
            self.append(character)

    def append(self, character):
        if len(self.buffer) < MAX_TEXT and not self.oversized:
            self.buffer += character
        else:
            self.oversized = True  # Reject whole input; never send a clipped question.

    def poll(self, console):
        for _ in range(256):
            if not console.kbhit():
                break
            self.feed(console.getwch())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint-file", type=Path,
                        default=Path(__file__).resolve().parent / "runtime" / ENDPOINT_NAME)
    args = parser.parse_args()
    import msvcrt
    client = TerminalClient(args.endpoint_file)
    keyboard = TerminalInput(client.command)
    # Witchcraft's interrupt button sends Ctrl+C. Cancel Akane's turn, not the adapter.
    interrupted = False
    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True
    signal.signal(signal.SIGINT, interrupt)
    print("AKANE - Witchcraft interface\nQuestions stay in this private Witchcraft window. /help for controls.", flush=True)
    next_poll = 0
    while not client.closed:
        if interrupted:
            interrupted = False
            keyboard.feed("\x03")
        keyboard.poll(msvcrt)
        if monotonic() >= next_poll:
            client.poll()
            next_poll = monotonic() + 0.3
        sleep(0.02)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
