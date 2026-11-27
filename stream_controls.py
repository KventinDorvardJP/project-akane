# Optional hotkey mode: a responsive control loop plus exactly one turn worker.
import json
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
from collections import deque
from time import perf_counter, monotonic

import config
from controls import ControlState, TurnCancelled
from microphone import RecordingError
from speech_to_text import TranscriptionError
from subtitles import write_subtitle
from windows_hotkeys import WindowsHotkeys, HotkeyError
from wow_context import WoWContextError


class ConsoleCommands:
    """Poll only this application's console; no blocked stdin thread on shutdown."""
    def __init__(self, submit, console=None):
        if console is None:
            import msvcrt
            console = msvcrt
        self.console, self.submit = console, submit
        self.buffer = ""
        self.skip_extended = False

    def command(self, line):
        line, command = line.strip(), line.strip().lower()
        if command in {"mic", "record"}:
            self.submit("record")
        elif command in {"pause", "stop", "reset", "status", "help", "exit", "mark"}:
            self.submit(command)
        elif command.startswith("audio "):
            self.submit("audio", line[6:].strip())
        elif command == "audio":
            print("In hotkey mode, use: audio filename.wav")
        elif line:
            self.submit("text", line)

    def poll(self):
        # Bound the amount of console work per iteration so hotkeys remain responsive.
        for _ in range(64):
            if not self.console.kbhit():
                break
            character = self.console.getwch()
            if self.skip_extended:
                self.skip_extended = False
                continue
            if character in {"\x00", "\xe0"}:
                self.skip_extended = True
            elif character == "\x03":
                self.submit("exit")
            elif character in {"\r", "\n"}:
                print()
                self.command(self.buffer)
                self.buffer = ""
            elif character == "\b":
                if self.buffer:
                    self.buffer = self.buffer[:-1]
                    print("\b \b", end="", flush=True)
            elif character.isprintable():
                self.buffer += character
                print(character, end="", flush=True)


class StreamController:
    def __init__(self, processor, recognize_audio, record_function, *, context_provider=None, wow_provider=None):
        self.processor, self.recognize_audio, self.record = processor, recognize_audio, record_function
        self.events = queue.Queue()
        self.state = ControlState()
        self.worker = None
        self.pending_reset = False
        self.closing = False
        self.last_published = None
        self.context_provider = context_provider
        self.wow_provider = wow_provider
        self.ptt = None
        self.ptt_seen = deque(maxlen=64)
        self.ptt_clock = monotonic
        self.ptt_last = None

    def submit(self, action, value=None):
        self.events.put((action, value))

    def notice(self, message, level="info"):
        self.processor.notice(message, level)

    def publish_status(self):
        with self.state.lock:
            snapshot = self.state.snapshot()
            try:
                write_subtitle(self.processor.root / "runtime" / "status.json", json.dumps(snapshot))
                write_subtitle(self.processor.root / "runtime" / "status.txt", f"Akane: {snapshot['state']}")
            except OSError as error:
                self.notice(f"Status output unavailable: {error}", "warning")
            if snapshot != self.last_published:
                print(f"Status: {snapshot['state']}")
                self.last_published = snapshot
                self.processor.emit("status", **snapshot)
            gesture = self.ptt
            ptt_state = {"token": gesture["token"] if gesture else None,
                         "state": snapshot["state"]}
            if ptt_state != self.ptt_last:
                self.ptt_last = ptt_state
                self.processor.emit("ptt", **ptt_state)

    def start_turn(self, mode, value=None, *, ptt_token=None):
        if self.closing:
            return False
        if mode in {"mic", "audio"} and self.recognize_audio is None:
            self.notice("Speech recognition unavailable in this session.", "warning")
            return False
        state = self.state.snapshot()
        if state["busy"] or state["paused"]:
            self.notice("Akane is paused or still processing a turn; input was not queued.", "warning")
            return False
        # Snapshot at acceptance, before recording/STT. Later game lines cannot
        # replace the sentence the user was referring to when starting this turn.
        try:
            japanese_context = self.context_provider() if self.context_provider else None
            # The optional module freezes memory here, before recording/STT.
            # Legacy readers defer file I/O/refresh waits to the worker.
            wow_client = self.wow_provider(value if mode == "typed" else None) if self.wow_provider else None
        except ValueError as error:
            self.notice(str(error), "warning")
            return False
        control = None

        def phase(name):
            self.state.set_phase(control, name)
            self.processor.log("phase", turn_id=control.turn_id, phase=name)
            if name == "recording":
                self.processor.log("recording_start", turn_id=control.turn_id)
            self.publish_status()

        control = self.state.begin(phase)
        if control is None:
            self.notice("Akane is paused or still processing a turn; input was not queued.", "warning")
            return False
        control.turn_id = self.processor.begin_log_turn(mode, japanese_context)
        if ptt_token is not None:
            self.ptt = {"token": ptt_token, "control": control,
                        "deadline": self.ptt_clock() + 8, "released": False}
        if mode == "typed":
            self.processor.log("input_accepted", turn_id=control.turn_id, text=value, mode=mode)
        self.publish_status()

        def work():
            response_entered = False
            input_status = "input_error"
            try:
                response_started, stt_seconds = perf_counter(), 0.0
                wow_context = None
                if wow_client is not None:
                    immediate = getattr(wow_client, "immediate", False) is True
                    if not immediate:
                        control.phase("context")
                        self.notice("Getting WoW context — keep WoW in the foreground until recording/reply starts.")
                    context_started = perf_counter()
                    domains = getattr(wow_client, "domains", None)
                    self.processor.log("wow_context_start", turn_id=control.turn_id,
                                       required_domains=list(domains) if isinstance(domains, (tuple, list)) else None)
                    try:
                        wow_context = wow_client.collect(control)
                    finally:
                        control.context_seconds = perf_counter() - context_started
                        self.processor.log("wow_context_end", turn_id=control.turn_id,
                                           elapsed_seconds=control.context_seconds, cancelled=control.cancel.is_set())
                    control.check()
                    self.processor.emit("wow_context", snapshot=wow_context)
                    if not immediate and wow_context["status"] == "partial":
                        missing = ", ".join(name for name, row in wow_context["domains"].items() if row["status"] != "available")
                        self.notice(f"Partial WoW context: {missing} unavailable/stale; those values were withheld.", "warning")
                text = value
                control.check()
                if mode == "mic":
                    with tempfile.TemporaryDirectory(prefix="akane-mic-") as directory:
                        path = self.record(
                            Path(directory) / "input.wav", input_device=config.MIC_INPUT_DEVICE,
                            stop_event=control.stop_recording, cancel_event=control.cancel,
                            on_started=lambda: control.phase("recording"),
                        )
                        self.processor.log("recording_end", turn_id=control.turn_id)
                        control.check()
                        if ptt_token is not None and not self.ptt["released"]:
                            raise RecordingError("Hold-to-talk recording limit reached; audio discarded. Release the button before trying again.")
                        control.phase("transcribing")
                        response_started = perf_counter()
                        result = self.recognize_audio(path)
                elif mode == "audio":
                    path = self.processor.root / "audio_samples" / value
                    if not path.is_file():
                        raise TranscriptionError(f"Audio file not found: {path}")
                    control.phase("transcribing")
                    response_started = perf_counter()
                    result = self.recognize_audio(path)
                control.check()
                if mode in {"mic", "audio"}:
                    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                        raise TranscriptionError("STT returned an invalid result.")
                    text = result["text"].strip()
                    if not text:
                        input_status = "no_speech"
                        self.notice("No speech recognized.", "warning")
                        return
                    stt_seconds = result["elapsed_seconds"]
                    print(f"You (transcribed): {text}")
                    print(f"Languages: {', '.join(result['languages'])}")
                    print(f"Transcription time: {stt_seconds:.2f} seconds")
                    self.processor.emit("transcription", text=text, languages=result["languages"],
                                        elapsed_seconds=stt_seconds, language_probability=result.get("language_probability"))
                response_entered = True
                self.processor.respond(text, input_mode=mode, stt_seconds=stt_seconds,
                                       response_started=response_started, control=control,
                                       japanese_context=japanese_context, wow_context=wow_context)
            except TurnCancelled:
                input_status = "cancelled"
                self.notice("Turn stopped.")
            except WoWContextError as error:
                if not control.cancel.is_set():
                    self.processor.log("wow_context_failure", turn_id=control.turn_id, message=str(error), details=error.details)
                    self.notice(str(error), "warning")
                    self.processor.emit("wow_context_failed", message=str(error), text=value if mode == "typed" else None)
            except (RecordingError, TranscriptionError, OSError) as error:
                if not control.cancel.is_set():
                    self.notice(str(error), "error")
            except Exception as error:
                # Always signal completion even if an unexpected adapter bug occurs.
                self.notice(f"Turn failed: {type(error).__name__}: {error}", "error")
            finally:
                if not response_entered:
                    self.processor.end_log_turn(control.turn_id, "cancelled" if control.cancel.is_set() else input_status)
                self.submit("done", control)

        self.worker = threading.Thread(target=work, name="Akane turn", daemon=False)
        self.worker.start()
        return True

    def hold_to_talk(self, action, token):
        """An explicit, leased gesture; never a recording toggle or queued turn."""
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{8}:[0-9a-f]{1,8}", token):
            return False
        self.processor.log("hold_to_talk", action=action, token=token)
        if action == "ptt_down":
            if token in self.ptt_seen:
                return False
            # Remember refused gestures too; a later retry must not start capture.
            self.ptt_seen.append(token)
            return self.start_turn("mic", ptt_token=token)
        gesture = self.ptt
        if not gesture or gesture["token"] != token or self.state.current is not gesture["control"]:
            if token not in self.ptt_seen:
                self.ptt_seen.append(token)  # A release before its press closes that gesture.
            return False
        with self.state.lock:
            control = gesture["control"]
            if action == "ptt_hold":
                if not gesture["released"] and not control.cancel.is_set():
                    gesture["deadline"] = self.ptt_clock() + 8
                    return True
                return False
            if action == "ptt_cancel":
                self.state.stop(self.processor.update_subtitle)
            elif action == "ptt_up":
                if gesture["released"]:
                    return True
                gesture["released"] = True
                if self.state.phase == "recording":
                    control.stop_recording.set()
                else:
                    # Released during context/startup: never open a mic afterward.
                    self.state.stop(self.processor.update_subtitle)
            self.publish_status()
        return True

    def check_ptt_timeout(self):
        gesture = self.ptt
        if (gesture and not gesture["released"] and not gesture["control"].cancel.is_set()
                and self.ptt_clock() >= gesture["deadline"]):
            self.hold_to_talk("ptt_cancel", gesture["token"])
            self.notice("Hold-to-talk connection timed out; recording cancelled and audio discarded.", "warning")

    def handle(self, action, value=None):
        accepted = True
        if action in {"record", "pause", "stop", "reset", "exit", "listener_failed"}:
            self.processor.log("control", action=action)
        if action == "mark":
            return self.processor.mark_recording()
        if action == "done":
            if self.worker:
                self.worker.join()
            self.state.finish(value)
            if self.ptt and self.ptt["control"] is value:
                self.ptt = None
            if self.pending_reset:
                self.processor.reset()
                self.pending_reset = False
        elif action == "record":
            with self.state.lock:
                if self.state.current and self.state.phase in {"starting", "recording"}:
                    self.state.current.stop_recording.set()
                else:
                    accepted = self.start_turn("mic")
        elif action == "text":
            if not self.processor.handle_memory_command(value, busy=self.state.snapshot()["busy"]):
                accepted = self.start_turn("typed", value)
        elif action in {"ptt_down", "ptt_up", "ptt_hold", "ptt_cancel"}:
            accepted = self.hold_to_talk(action, value)
        elif action == "audio":
            accepted = self.start_turn("audio", value)
        elif action == "pause":
            self.state.toggle_pause(self.processor.update_subtitle)
        elif action == "stop":
            self.state.stop(self.processor.update_subtitle)
        elif action == "reset":
            self.state.stop(self.processor.update_subtitle)
            if self.state.snapshot()["busy"]:
                self.pending_reset = True  # Only reset history after its worker has finished.
            else:
                self.processor.reset()
        elif action in {"exit", "listener_failed"}:
            self.closing = True
            self.state.stop(self.processor.update_subtitle)
            if self.state.snapshot()["busy"]:
                self.notice("Stopping; waiting for the in-flight request to finish or time out.")
        elif action == "help":
            print("mic/record: toggle recording; pause: toggle pause; stop: cancel; reset; status; mark: log a recording marker; exit.")
            print("You may also type a message or: audio filename.wav")
            print("Memory commands: /remember, /memories, /correct, /forget.")
        elif action == "status":
            print(json.dumps(self.state.snapshot()))
        self.publish_status()
        return accepted

    def finish_shutdown(self):
        """Non-blocking finalization for the GUI; never close clients under a worker."""
        if self.worker and self.worker.is_alive():
            return False
        self.state.stop(self.processor.update_subtitle)
        with self.state.lock:
            self.state.current, self.state.phase, self.state.paused = None, "stopped", False
        self.publish_status()
        return True

    def run(self, *, hotkeys_factory=WindowsHotkeys, console_input=None):
        listener = hotkeys_factory(
            {"record": config.HOTKEY_RECORD, "pause": config.HOTKEY_PAUSE, "stop": config.HOTKEY_STOP},
            self.submit,
        )
        try:
            listener.start()
        except HotkeyError as error:
            listener.stop()
            print(f"Cannot start hotkey mode: {error}")
            return 1
        except (KeyboardInterrupt, EOFError):
            listener.stop()
            raise
        try:
            print(f"Record: {config.HOTKEY_RECORD}; Pause: {config.HOTKEY_PAUSE}; Stop: {config.HOTKEY_STOP}")
            print("Hotkey mode. Type help for console commands; Ctrl+C exits.")
            self.publish_status()
            if console_input is None:
                console_input = sys.stdin.isatty()
            console = ConsoleCommands(self.submit) if console_input else None
            while not self.closing or self.state.snapshot()["busy"]:
                try:
                    if console:
                        console.poll()
                    action, value = self.events.get(timeout=0.05)
                    self.handle(action, value)
                except queue.Empty:
                    continue
                except KeyboardInterrupt:
                    self.handle("exit")
        finally:
            self.state.stop(self.processor.update_subtitle)
            # Do not close SDK clients or delete an upload under a still-running request.
            # Cancellation is cooperative; the configured HTTP timeout bounds this wait.
            while self.worker and self.worker.is_alive():
                try:
                    self.worker.join(0.1)
                except KeyboardInterrupt:
                    print("Still waiting for the in-flight request to finish safely.")
            listener.stop()
            with self.state.lock:
                self.state.current, self.state.phase, self.state.paused = None, "stopped", False
            self.publish_status()
        return 0
