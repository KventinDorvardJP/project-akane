"""Main-thread window coordinator; reuse the existing single turn worker."""
import queue
import threading

import config
from memory_commands import is_memory_command
from stream_controls import StreamController
from window_model import WindowModel, backend_label
from windows_hotkeys import WindowsHotkeys, HotkeyError
from text_context import TextContextListener, TextContextError
from wow_context import WoWContextClient, cache_path, question_domains
from witchcraft_bridge import WitchcraftBridge


class WindowSession:
    def __init__(self, processor, record_function, view, *, initialize_stt=None,
                 hotkeys_enabled=False, hotkeys_factory=WindowsHotkeys, context_factory=TextContextListener,
                 wow_factory=WoWContextClient, witchcraft_enabled=False, bridge_factory=WitchcraftBridge):
        self.processor, self.view = processor, view
        self.events = queue.Queue()
        self.previous_callback = processor.event_callback
        processor.event_callback = self.emit
        self.context_listener = context_factory(config.TEXT_CONTEXT_WS_URL, max_chars=config.TEXT_CONTEXT_MAX_CHARS)
        self.wow_factory = wow_factory
        self.controller = StreamController(processor, None, record_function, context_provider=self.selected_context,
                                           wow_provider=self.selected_wow_context)
        self.model = WindowModel(backend_label(processor.reply_function),
                                 tts_enabled=processor.tts_enabled, budget=config.OPENAI_SESSION_BUDGET_USD)
        self.model.wow_available = getattr(processor, "wow_enabled", True)
        if getattr(processor, "wow_module", None) is not None:
            self.model.include_wow_context = True
            self.model.wow_status = "WoW module: freezes local cache immediately"
        self.refresh_session_log()
        self.model.overlay_url = getattr(processor, "overlay_url", None)
        self.model.tts_backend = getattr(processor, "tts_backend", config.TTS_BACKEND)
        self.model.overlay_status = getattr(processor, "overlay_status", "Off")
        if getattr(processor, "overlay_warning", None):
            self.model.apply("notice", {"message": processor.overlay_warning, "level": "warning"})
        if getattr(processor, "memory_warning", None):
            self.model.apply("notice", {"message": processor.memory_warning, "level": "warning"})
        if processor.session_log and processor.session_log.error:
            self.model.apply("notice", {"message": f"Session transcript unavailable: {processor.session_log.error}", "level": "warning"})
        if getattr(processor.reply_function, "startup_error", None):
            self.model.apply("notice", {"message": processor.reply_function.startup_error, "level": "error"})
        self.model.apply("metrics", {"row": {}, "session_cost": getattr(processor.reply_function, "estimated_session_usd", None),
                                     "cost_unknown": getattr(processor.reply_function, "unaccounted_usage", False)})
        self.initialize_stt = initialize_stt
        self.hotkeys_enabled, self.hotkeys_factory = hotkeys_enabled, hotkeys_factory
        self.listener = self.init_worker = self.stt_client = None
        self.init_finished = initialize_stt is None
        self.started = self.finished = False
        self.restart_requested = False
        self.witchcraft_enabled, self.bridge_factory = witchcraft_enabled, bridge_factory
        self.witchcraft = None

    def refresh_session_log(self):
        log = self.processor.session_log
        self.model.session_log_path = str(log.text_path) if log and log.files_created else None
        self.model.session_log_status = "On" if log and log.active else "Unavailable" if log else "Off"

    def selected_context(self):
        if not self.model.include_context:
            return None
        return self.context_listener.fresh_text(config.TEXT_CONTEXT_MAX_AGE_SECONDS)

    def refresh_context(self):
        snapshot = self.context_listener.snapshot()
        self.model.context_text, self.model.context_status = snapshot.text, snapshot.status
        self.model.context_running = self.context_listener.running
        self.model.context_stopping = self.context_listener.stop_event.is_set() and self.context_listener.running

    def selected_wow_context(self, question=None):
        if not self.model.wow_available or not self.model.include_wow_context:
            return None
        if getattr(self.processor, "wow_module", None) is not None:
            return self.processor.wow_module.freeze()
        previous = None
        for message in reversed(self.model.messages):
            if message["role"] == "user":
                previous = (message.get("wow_context") or {}).get("requiredDomains")
                break
        return self.wow_factory(cache_path(config.WOW_CONTEXT_CACHE_PATH, self.processor.root),
                                max_age=config.WOW_CONTEXT_MAX_AGE_SECONDS, wait_seconds=config.WOW_CONTEXT_WAIT_SECONDS,
                                domains=question_domains(question, previous))

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
        if self.witchcraft_enabled:
            self.witchcraft = self.bridge_factory(self.processor.root)
            try:
                self.witchcraft.start()
                self.processor.notice("Witchcraft input enabled. In-game questions share this conversation; WoW context is separately selected.")
                self.witchcraft.publish("context", enabled=self.model.include_wow_context)
                self.witchcraft.publish("capabilities", sttReady=False)
            except OSError as error:
                self.witchcraft.close()
                self.witchcraft = None
                self.processor.notice(f"Witchcraft input unavailable: {error}. Control-panel chat still works.", "warning")
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
        if action == "settings_notice":
            self.model.apply("notice", {"message": value, "level": "info"})
            self.view.render(self.model)
            return True
        if action == "restart":
            if not self.init_finished or self.controller.state.snapshot()["busy"]:
                self.model.apply("notice", {"message": "Finish the active turn and microphone startup before restarting.", "level": "warning"})
                self.view.render(self.model)
                return False
            self.restart_requested = True
            return self.request("exit")
        if action == "text":
            value = value.strip() if isinstance(value, str) else ""
            if not value:
                return False
        if action == "context_include":
            self.model.include_context = value is True
            self.view.render(self.model)
            return True
        if action == "wow_include":
            if not self.model.wow_available:
                self.model.apply("notice", {"message": "Start Akane with --wow to enable game-data tools.", "level": "info"})
                self.view.render(self.model)
                return False
            self.model.include_wow_context = value is True
            self.model.wow_status = (("WoW module: cache frozen on your next question" if getattr(self.processor, "wow_module", None)
                                      else "Selected — context requested on your next question") if value is True else "Off")
            if not self.model.include_wow_context:
                self.model.wow_snapshot = None
            if self.witchcraft:
                self.witchcraft.publish("context", enabled=self.model.include_wow_context)
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
            if self.witchcraft:
                self.witchcraft.stop_accepting()
        if action == "reset":
            self.context_listener.clear()
            self.model.include_context = False
            self.model.include_wow_context = False
            self.model.wow_snapshot, self.model.wow_status = None, "Off"
            if self.witchcraft:
                self.witchcraft.publish("context", enabled=False)
        accepted = self.controller.handle(action, value)
        self.refresh_context()
        wow_module = getattr(self.processor, "wow_module", None)
        if wow_module is not None and self.model.include_wow_context:
            self.model.wow_status = wow_module.status_text()
        self.model.apply("status", self.controller.state.snapshot())
        self.view.render(self.model)
        return accepted is not False

    def bridge_request(self, action, value):
        if action == "text" and is_memory_command(value):
            return False
        return self.request(action, value)

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
                if self.witchcraft:
                    self.witchcraft.publish("capabilities", sttReady=self.model.stt_ready)
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
            if self.witchcraft:
                self.witchcraft.publish(kind, **payload)
        if self.witchcraft and not self.model.closing:
            self.witchcraft.drain(self.bridge_request)
        self.controller.check_ptt_timeout()
        self.model.apply("status", self.controller.state.snapshot())
        self.refresh_context()
        self.refresh_session_log()
        wow_module = getattr(self.processor, "wow_module", None)
        if wow_module is not None and self.model.include_wow_context:
            self.model.wow_status = wow_module.status_text()
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
        if self.witchcraft:
            self.witchcraft.close()
        self.processor.event_callback = self.previous_callback
        self.finished = True
        # Session callbacks held by workers must release their last route to Tk.
        view, self.view = self.view, None
        view.destroy()

    def cleanup(self):
        """Fallback if Tk exits unexpectedly; don't close clients under active calls."""
        if self.finished:
            return
        if self.witchcraft:
            self.witchcraft.stop_accepting()
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
