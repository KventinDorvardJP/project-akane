"""Main-thread window coordinator; reuse the existing single turn worker."""
import queue
import threading

import config
from stream_controls import StreamController
from window_model import WindowModel, backend_label
from windows_hotkeys import WindowsHotkeys, HotkeyError
from text_context import TextContextListener, TextContextError


class WindowSession:
    def __init__(self, processor, record_function, view, *, initialize_stt=None,
                 hotkeys_enabled=False, hotkeys_factory=WindowsHotkeys, context_factory=TextContextListener):
        self.processor, self.view = processor, view
        self.events = queue.Queue()
        self.previous_callback = processor.event_callback
        processor.event_callback = self.emit
        self.context_listener = context_factory(config.TEXT_CONTEXT_WS_URL, max_chars=config.TEXT_CONTEXT_MAX_CHARS)
        self.controller = StreamController(processor, None, record_function, context_provider=self.selected_context)
        self.model = WindowModel(backend_label(processor.reply_function),
                                 tts_enabled=processor.tts_enabled, budget=config.OPENAI_SESSION_BUDGET_USD)
        self.model.overlay_url = getattr(processor, "overlay_url", None)
        self.model.overlay_status = getattr(processor, "overlay_status", "Off")
        if getattr(processor, "overlay_warning", None):
            self.model.apply("notice", {"message": processor.overlay_warning, "level": "warning"})
        self.model.apply("metrics", {"row": {}, "session_cost": getattr(processor.reply_function, "estimated_session_usd", None),
                                     "cost_unknown": getattr(processor.reply_function, "unaccounted_usage", False)})
        self.initialize_stt = initialize_stt
        self.hotkeys_enabled, self.hotkeys_factory = hotkeys_enabled, hotkeys_factory
        self.listener = self.init_worker = self.stt_client = None
        self.init_finished = initialize_stt is None
        self.started = self.finished = False

    def selected_context(self):
        if not self.model.include_context:
            return None
        return self.context_listener.fresh_text(config.TEXT_CONTEXT_MAX_AGE_SECONDS)

    def refresh_context(self):
        snapshot = self.context_listener.snapshot()
        self.model.context_text, self.model.context_status = snapshot.text, snapshot.status
        self.model.context_running = self.context_listener.running
        self.model.context_stopping = self.context_listener.stop_event.is_set() and self.context_listener.running

    def emit(self, kind, **payload):
        self.events.put((kind, payload))  # Called from workers; never call Tk here.

    def on_hotkey(self, action, value=None):
        if action == "listener_failed":
            self.emit("hotkeys_failed", message="Global hotkeys stopped; window buttons still work.")
        else:
            self.controller.submit(action, value)

    def start(self):
        if self.started:
            return
        self.started = True
        if self.hotkeys_enabled:
            self.listener = self.hotkeys_factory(
                {"record": config.HOTKEY_RECORD, "pause": config.HOTKEY_PAUSE, "stop": config.HOTKEY_STOP}, self.on_hotkey)
            try:
                self.listener.start()
                self.model.hotkeys_status = f"On · {config.HOTKEY_RECORD} / {config.HOTKEY_PAUSE} / {config.HOTKEY_STOP}"
            except HotkeyError as error:
                self.listener.stop()
                self.model.hotkeys_status = "Unavailable — use window buttons"
                self.emit("notice", level="warning", message=f"Hotkeys unavailable: {error}. Window buttons still work.")
        if self.initialize_stt is None:
            self.model.stt_state = "Disabled · typed input available"
            if self.model.notice.startswith("Type a message, or wait"):
                self.model.notice = "Type a message below. Microphone input is disabled in this run."
        else:
            def initialize():
                try:
                    recognizer, client = self.initialize_stt()
                    self.emit("stt_ready", recognizer=recognizer, client=client)
                except Exception as error:
                    self.emit("stt_ready", recognizer=None, client=None,
                              error=f"Speech recognition unavailable: {error}. Typed input still works.")
            self.init_worker = threading.Thread(target=initialize, name="Akane speech initialization", daemon=False)
            self.init_worker.start()
        self.controller.publish_status()
        self.tick()

    def request(self, action, value=None):
        """UI callbacks run on the main thread; network work stays in the worker."""
        if self.finished or (self.model.closing and action != "exit"):
            return False
        if action == "text":
            value = value.strip() if isinstance(value, str) else ""
            if not value:
                return False
        if action == "context_include":
            self.model.include_context = value is True
            self.view.render(self.model)
            return True
        if action == "context_connect":
            try:
                if self.context_listener.running:
                    self.context_listener.request_stop()
                    self.model.include_context = False
                else:
                    self.context_listener.start()
            except TextContextError as error:
                self.model.apply("notice", {"message": str(error), "level": "error"})
            self.refresh_context()
            self.view.render(self.model)
            return True
        state = self.controller.state.snapshot()
        if action in {"text", "audio"} and (state["busy"] or state["paused"]):
            self.model.apply("notice", {"message": "Wait for the turn to finish, or resume Akane first.", "level": "warning"})
            self.view.render(self.model)
            return False
        if action == "exit":
            self.model.closing = True
            self.context_listener.request_stop()
        if action == "reset":
            self.context_listener.clear()
            self.model.include_context = False
        accepted = self.controller.handle(action, value)
        self.refresh_context()
        self.model.apply("status", self.controller.state.snapshot())
        self.view.render(self.model)
        return accepted is not False

    def tick(self):
        if self.finished:
            return
        # Bounded batches keep the window responsive during bursts of status events.
        for _ in range(100):
            try:
                action, value = self.controller.events.get_nowait()
            except queue.Empty:
                break
            if action in {"exit", "listener_failed"}:
                self.model.closing = True
                self.context_listener.request_stop()
            self.controller.handle(action, value)
        for _ in range(200):
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "stt_ready":
                self.init_finished = True
                self.stt_client = payload.get("client")
                self.controller.recognize_audio = payload.get("recognizer")
                self.model.stt_ready = callable(self.controller.recognize_audio)
                self.model.stt_state = f"Ready · {config.STT_BACKEND}" if self.model.stt_ready else "Unavailable · typed input available"
                if payload.get("error"):
                    self.model.apply("notice", {"message": payload["error"], "level": "warning"})
                elif self.model.notice.startswith("Type a message, or wait"):
                    self.model.notice = "Microphone ready. Press Record to begin, or type below."
            elif kind == "hotkeys_failed":
                self.model.hotkeys_status = "Unavailable — use window buttons"
                self.model.apply("notice", {"message": payload["message"], "level": "warning"})
            else:
                self.model.apply(kind, payload)
        self.model.apply("status", self.controller.state.snapshot())
        self.refresh_context()
        self.view.render(self.model)
        initialization_running = self.init_worker is not None and self.init_worker.is_alive()
        if (self.model.closing and self.init_finished and not initialization_running and not self.model.status["busy"]
                and not self.context_listener.running):
            self._finish()
            return
        self.view.after(50, self.tick)

    def _finish(self):
        if self.finished:
            return
        if not self.controller.finish_shutdown():
            return
        if self.listener is not None:
            self.listener.stop()
        if self.stt_client is not None:
            try:
                self.stt_client.close()
            except Exception as error:
                print(f"Speech client cleanup failed: {error}")
            self.stt_client = None
        self.processor.event_callback = self.previous_callback
        self.finished = True
        self.view.destroy()

    def cleanup(self):
        """Fallback if Tk exits unexpectedly; don't close clients under active calls."""
        if self.finished:
            return
        self.controller.handle("exit")
        self.context_listener.request_stop()
        for worker in (self.init_worker, self.controller.worker, self.context_listener.worker):
            while worker is not None and worker.is_alive():
                try:
                    worker.join(0.1)
                except KeyboardInterrupt:
                    print("Waiting for active work to finish safely.")
        # An initializer may have returned a client just before Tk exited.
        while not self.events.empty():
            kind, payload = self.events.get_nowait()
            if kind == "stt_ready":
                self.stt_client = payload.get("client")
        self._finish()
