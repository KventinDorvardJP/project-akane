"""Optional native control window. Tk is created only by run_control_window()."""
import gc
import tkinter as tk
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText
import webbrowser
from pathlib import Path

from web_search import safe_url
from window_session import WindowSession


class ControlWindow:
    def __init__(self, root):
        self.root, self.request = root, lambda action, value=None: False
        self.latest_model = None
        self.project_dir = Path(__file__).resolve().parent
        self.settings_dialog = None
        self.poll_job = None
        self.rendered_revision = -1
        self.rendered_sources = None
        self.rendered_context = None
        self.rendered_retry_revision = 0
        self.link_tags = []
        root.title("Akane — control room")
        root.geometry("1080x800")
        root.minsize(850, 650)
        root.configure(background="#101722")
        root.protocol("WM_DELETE_WINDOW", lambda: self.request("exit"))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#101722")
        style.configure("TLabel", background="#101722", foreground="#edf2fa", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", foreground="#a5b5c9")
        style.configure("Title.TLabel", font=("Segoe UI", 23, "bold"))
        style.configure("Status.TLabel", font=("Segoe UI", 13, "bold"), foreground="#78ded2")
        style.configure("TButton", padding=(14, 9), font=("Segoe UI", 10), background="#263549", foreground="#edf2fa")
        style.map("TButton", background=[("active", "#354a65"), ("disabled", "#182331")],
                  foreground=[("disabled", "#738095")])
        style.configure("Record.TButton", background="#176c66")
        style.map("Record.TButton", background=[("active", "#218b82"), ("disabled", "#182331")])
        style.configure("Stop.TButton", background="#6b3542")
        style.map("Stop.TButton", background=[("active", "#8b4556"), ("disabled", "#182331")])
        style.configure("TEntry", fieldbackground="#1a2636", foreground="#edf2fa", insertcolor="#edf2fa", padding=10)
        style.configure("TNotebook", background="#101722", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(8, 7), font=("Segoe UI", 9))
        style.configure("TCombobox", fieldbackground="#1a2636", foreground="#edf2fa", padding=8)
        style.map("TCombobox", fieldbackground=[("readonly", "#1a2636")], foreground=[("readonly", "#edf2fa")])
        style.configure("TCheckbutton", background="#101722", foreground="#edf2fa", font=("Segoe UI", 10))
        style.map("TCheckbutton", background=[("active", "#101722")], foreground=[("disabled", "#738095")])
        style.map("TEntry", fieldbackground=[("disabled", "#141e2c")], foreground=[("disabled", "#738095")])

        outer = ttk.Frame(root, padding=20)
        outer.grid(sticky="nsew")
        root.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="AKANE", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Conversation · Japanese voice · Stream subtitles", style="Muted.TLabel", wraplength=320).grid(row=1, column=0, sticky="w")
        self.status_var = tk.StringVar(root, "Ready")
        self.status_label = ttk.Label(header, textvariable=self.status_var, style="Status.TLabel", wraplength=400)
        self.status_label.grid(row=0, column=1, rowspan=2, sticky="e", padx=(16, 0))
        self.settings_button = ttk.Button(header, text="Settings…", command=self.open_settings)
        self.settings_button.grid(row=0, column=2, rowspan=2, padx=(16, 0), sticky="e")
        self.backend_var = tk.StringVar(root)
        self.backend_label = ttk.Label(outer, textvariable=self.backend_var, style="Muted.TLabel", wraplength=980)
        self.backend_label.grid(row=1, column=0, sticky="w", pady=(10, 4))
        self.notice_var = tk.StringVar(root)
        self.notice_label = ttk.Label(outer, textvariable=self.notice_var, wraplength=980)
        self.notice_label.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.grid(row=3, column=0, sticky="nsew")
        conversation_frame, sidebar = ttk.Frame(body), ttk.Frame(body, padding=(16, 0, 0, 0))
        body.add(conversation_frame, weight=4)
        body.add(sidebar, weight=1)
        conversation_frame.columnconfigure(0, weight=1)
        conversation_frame.rowconfigure(1, weight=1)
        ttk.Label(conversation_frame, text="CONVERSATION", style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.conversation = self.text_widget(conversation_frame)
        self.conversation.grid(row=1, column=0, sticky="nsew")
        self.conversation.tag_configure("speaker", foreground="#78ded2", font=("Segoe UI", 10, "bold"), spacing1=14)
        self.conversation.tag_configure("user", foreground="#c5d3e8", font=("Segoe UI", 12))
        self.conversation.tag_configure("en", foreground="#f3f5fb", font=("Segoe UI", 12), spacing3=5)
        self.conversation.tag_configure("ja", foreground="#aebed3", font=("Yu Gothic UI", 12), spacing3=8)

        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(5, weight=1)
        ttk.Label(sidebar, text="SESSION ESTIMATE", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.cost_var = tk.StringVar(root)
        ttk.Label(sidebar, textvariable=self.cost_var, wraplength=230).grid(row=1, column=0, sticky="nw", pady=(6, 16))
        ttk.Label(sidebar, text="LAST TURN", style="Muted.TLabel").grid(row=2, column=0, sticky="w")
        self.metrics_var = tk.StringVar(root, "Timings appear after a turn.")
        ttk.Label(sidebar, textvariable=self.metrics_var, wraplength=230).grid(row=3, column=0, sticky="nw", pady=(6, 16))
        ttk.Label(sidebar, text="LATEST SOURCES · click to open", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(0, 6))
        self.sources = self.text_widget(sidebar, width=27, height=5)
        self.sources.grid(row=5, column=0, sticky="nsew")
        self.activity_var = tk.StringVar(root)
        ttk.Label(sidebar, textvariable=self.activity_var, style="Muted.TLabel", wraplength=230).grid(row=6, column=0, sticky="nw", pady=(10, 0))

        context_frame = ttk.Frame(outer)
        context_frame.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        context_frame.columnconfigure(0, weight=1)
        ttk.Label(context_frame, text="JAPANESE REFERENCE · LunaTranslator", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.include_context_var = tk.BooleanVar(root, False)
        self.include_context = ttk.Checkbutton(context_frame, text="Include Japanese line", variable=self.include_context_var,
                                               command=lambda: self.request("context_include", self.include_context_var.get()))
        self.include_context.grid(row=0, column=1, padx=10)
        self.context_connect = ttk.Button(context_frame, text="Connect text feed", command=lambda: self.request("context_connect"))
        self.context_connect.grid(row=0, column=2)
        self.context_status_var = tk.StringVar(root, "Disconnected")
        self.context_status_label = ttk.Label(context_frame, textvariable=self.context_status_var, style="Muted.TLabel", wraplength=980)
        self.context_status_label.grid(row=1, column=0, columnspan=3, sticky="w")
        self.context_preview = self.text_widget(context_frame, height=2, width=1)
        self.context_preview.configure(font=("Yu Gothic UI", 12))
        self.context_preview.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(3, 0))

        wow_bar = ttk.Frame(context_frame)
        wow_bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        wow_bar.columnconfigure(1, weight=1)
        self.include_wow_var = tk.BooleanVar(root, False)
        self.include_wow = ttk.Checkbutton(wow_bar, text="Include WoW context", variable=self.include_wow_var,
                                           command=lambda: self.request("wow_include", self.include_wow_var.get()))
        self.include_wow.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.wow_status_var = tk.StringVar(root, "Off")
        self.wow_status_label = ttk.Label(wow_bar, textvariable=self.wow_status_var, style="Muted.TLabel", wraplength=680)
        self.wow_status_label.grid(row=0, column=1, sticky="w")

        toolbar = ttk.Frame(outer)
        toolbar.grid(row=5, column=0, sticky="ew", pady=(14, 10))
        self.buttons = {}
        for index, (action, label, button_style) in enumerate((
            ("record", "Record", "Record.TButton"), ("pause", "Pause", "TButton"),
            ("stop", "Stop", "Stop.TButton"), ("reset", "Reset chat", "TButton"),
            ("audio", "Audio file…", "TButton"),
        )):
            command = self.choose_audio if action == "audio" else lambda action=action: self.request(action)
            button = ttk.Button(toolbar, text=label, command=command, style=button_style)
            button.grid(row=0, column=index, padx=(0, 8))
            self.buttons[action] = button
        self.overlay_copy = ttk.Button(toolbar, text="Copy overlay URL", command=self.copy_overlay_url, state="disabled")
        self.overlay_copy.grid(row=0, column=5, padx=(0, 8))
        composer = ttk.Frame(outer)
        composer.grid(row=6, column=0, sticky="ew")
        composer.columnconfigure(0, weight=1)
        self.input_var = tk.StringVar(root)
        self.entry = ttk.Entry(composer, textvariable=self.input_var, font=("Segoe UI", 12))
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry.bind("<Return>", self.send)
        self.buttons["send"] = ttk.Button(composer, text="Send", command=self.send, style="Record.TButton")
        self.buttons["send"].grid(row=0, column=1)
        self.footer_var = tk.StringVar(root)
        self.footer_label = ttk.Label(outer, textvariable=self.footer_var, style="Muted.TLabel", wraplength=980)
        self.footer_label.grid(row=7, column=0, sticky="w", pady=(8, 0))
        log_bar = ttk.Frame(outer)
        log_bar.grid(row=8, column=0, sticky="ew", pady=(8, 0))
        log_bar.columnconfigure(0, weight=1)
        self.log_var = tk.StringVar(root, "Transcript: Off")
        ttk.Label(log_bar, textvariable=self.log_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.log_copy = ttk.Button(log_bar, text="Copy log path", command=self.copy_log_path, state="disabled")
        self.log_copy.grid(row=0, column=1, padx=8)
        self.log_mark = ttk.Button(log_bar, text="Mark recording", command=lambda: self.request("mark"), state="disabled")
        self.log_mark.grid(row=0, column=2)
        outer.bind("<Configure>", lambda event: self.resize_labels(event.width))
        self.entry.focus_set()

    def resize_labels(self, width):
        for label in (self.backend_label, self.notice_label, self.footer_label, self.context_status_label):
            label.configure(wraplength=max(300, width - 10))

    def open_settings(self):
        if self.latest_model is not None and self.latest_model.closing:
            return
        if self.settings_dialog is not None:
            try:
                if self.settings_dialog.window.winfo_exists():
                    self.settings_dialog.window.lift()
                    return
            except tk.TclError:
                pass
        try:
            from settings_window import SettingsDialog
            self.settings_dialog = SettingsDialog(
                self.root, self.project_dir, on_restart=lambda: self.request("restart"),
                on_saved=lambda: self.request("settings_notice", "Settings saved locally; restart to apply them."))
        except (OSError, ValueError) as error:
            self.request("settings_notice", f"Cannot open Settings: {error}")

    @staticmethod
    def text_widget(parent, **options):
        return ScrolledText(parent, state="disabled", wrap="word", background="#172231", foreground="#edf2fa",
                            selectbackground="#355b77", relief="flat", borderwidth=0, padx=14, pady=10,
                            font=("Segoe UI", 11), **options)

    def after(self, milliseconds, callback):
        def run_poll():
            self.poll_job = None
            callback()
        self.poll_job = self.root.after(milliseconds, run_poll)
        return self.poll_job

    def destroy(self):
        self.request = lambda action, value=None: False
        if self.poll_job is not None:
            try:
                self.root.after_cancel(self.poll_job)
            except tk.TclError:
                pass
            self.poll_job = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        finally:
            self.settings_dialog = None

    def send(self, event=None):
        if self.request("text", self.input_var.get()):
            self.input_var.set("")
        return "break"

    def choose_audio(self):
        path = filedialog.askopenfilename(parent=self.root, title="Choose an audio sample",
                                          filetypes=[("Audio", "*.wav *.flac *.ogg *.mp3 *.m4a"), ("All files", "*.*")])
        if path:
            self.request("audio", path)

    def copy_overlay_url(self):
        if self.latest_model is not None and self.latest_model.overlay_url:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(self.latest_model.overlay_url)
                self.notice_var.set("Overlay URL copied. Paste it into an OBS Browser Source.")
            except tk.TclError as error:
                self.notice_var.set(f"Could not copy overlay URL: {error}")

    def copy_log_path(self):
        if self.latest_model is not None and self.latest_model.session_log_path:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(self.latest_model.session_log_path)
                self.notice_var.set("Transcript path copied.")
            except tk.TclError as error:
                self.notice_var.set(f"Could not copy transcript path: {error}")

    def open_link(self, url):
        # Called only by an explicit click. Never open a page on incoming replies.
        if safe_url(url):
            try:
                webbrowser.open(url, new=2)
            except Exception as error:
                self.notice_var.set(f"Could not open source: {error}")

    def insert_link(self, widget, label, url, tag):
        widget.insert("end", label, tag)
        widget.tag_configure(tag, foreground="#81cfff", underline=True)
        widget.tag_bind(tag, "<Button-1>", lambda event, url=url: self.open_link(url))
        widget.tag_bind(tag, "<Enter>", lambda event: widget.configure(cursor="hand2"))
        widget.tag_bind(tag, "<Leave>", lambda event: widget.configure(cursor="arrow"))

    def render_conversation(self, model):
        widget = self.conversation
        follow = widget.yview()[1] >= 0.98
        position = widget.index("@0,0")
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for tag in self.link_tags:
            widget.tag_delete(tag)
        self.link_tags = []
        if not model.messages:
            widget.insert("end", "Start a conversation below, or use Record.\nReplies stay here after speech finishes.", "ja")
        for index, message in enumerate(model.messages):
            if message["role"] == "user":
                suffix = " · transcribed" if message["mode"] != "typed" else ""
                widget.insert("end", f"YOU{suffix}\n", "speaker")
                widget.insert("end", message["text"] + "\n", "user")
                if message.get("japanese_context"):
                    widget.insert("end", "Selected Japanese reference\n", "speaker")
                    widget.insert("end", message["japanese_context"] + "\n", "ja")
            elif message["role"] == "memory":
                widget.insert("end", "MEMORY\n", "speaker")
                widget.insert("end", message["text"] + "\n", "user")
            else:
                language = "ru" if "ru" in message else "en"
                widget.insert("end", f"AKANE · {language.upper()}\n", "speaker")
                widget.insert("end", message[language], "en")
                for number, source in enumerate(message.get("sources", []), 1):
                    if source.get("kind") != "cited" or not safe_url(source.get("url")):
                        continue
                    tag = f"source_{index}_{number}"
                    self.link_tags.append(tag)
                    label = f" [{number}]"
                    self.insert_link(widget, label, source["url"], tag)
                widget.insert("end", "\n", "en")
                widget.insert("end", "JA  " + message["ja"] + "\n", "ja")
        widget.configure(state="disabled")
        widget.see("end") if follow else widget.yview(position)
        self.rendered_revision = model.revision

    def render_sources(self, sources):
        widget = self.sources
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for tag in widget.tag_names():
            if str(tag).startswith("latest_"):
                widget.tag_delete(tag)
        if not sources:
            widget.insert("end", "No lookup sources for this turn.")
        for number, source in enumerate(sources, 1):
            if safe_url(source.get("url")):
                self.insert_link(widget, f"{number}. {source['title']}\n", source["url"], f"latest_{number}")
                widget.insert("end", f"{source['kind']}\n\n")
        widget.configure(state="disabled")
        self.rendered_sources = list(sources)

    def render(self, model):
        self.latest_model = model
        if self.rendered_retry_revision != model.retry_revision:
            if not self.input_var.get():
                self.input_var.set(model.retry_text or "")
            self.rendered_retry_revision = model.retry_revision
        self.settings_button.configure(state="disabled" if model.closing else "normal")
        self.overlay_copy.configure(state="normal" if model.overlay_url and not model.closing else "disabled")
        self.context_status_var.set(model.context_status)
        self.include_wow_var.set(model.include_wow_context)
        self.include_wow.configure(state="disabled" if model.closing or model.status["busy"]
                                   or not getattr(model, "wow_available", True) else "normal")
        self.wow_status_var.set(model.wow_status)
        self.include_context_var.set(model.include_context)
        self.include_context.configure(state="disabled" if model.closing else "normal")
        self.context_connect.configure(text="Disconnect" if model.context_running else "Connect text feed",
                                       state="disabled" if model.closing or model.context_stopping else "normal")
        if self.rendered_context != model.context_text:
            self.context_preview.configure(state="normal")
            self.context_preview.delete("1.0", "end")
            self.context_preview.insert("end", model.context_text or "No captured line. Connect, then advance/capture a line in LunaTranslator.")
            self.context_preview.configure(state="disabled")
            self.rendered_context = model.context_text
        self.status_var.set(model.status_text)
        state = model.status["state"]
        color = "#f08d9f" if state == "recording" else "#ebc67e" if model.closing or state == "paused" else "#78ded2"
        self.status_label.configure(foreground=color)
        tts_name = {"voicevox": "VOICEVOX", "gpt_sovits": "GPT-SoVITS"}.get(model.tts_backend, str(model.tts_backend))
        voice = f"Japanese voice: {tts_name}" if model.tts_enabled else "Speech disabled"
        self.backend_var.set(f"{model.backend_label}  ·  STT: {model.stt_state}  ·  {voice}")
        self.notice_var.set(model.notice)
        self.notice_label.configure(foreground="#f2a6ad" if model.notice_level == "error"
                                    else "#ebc67e" if model.notice_level == "warning" else "#a5b5c9")
        controls = model.buttons()
        for action, button in self.buttons.items():
            button.configure(state="normal" if controls[action] else "disabled")
        self.buttons["record"].configure(text=controls["record_text"])
        self.buttons["pause"].configure(text=controls["pause_text"])
        self.entry.configure(state="normal" if controls["send"] else "disabled")
        self.cost_var.set(model.cost_text)
        row = model.metrics
        if row:
            count = row.get("web_search_calls")
            count = count if isinstance(count, int) and not isinstance(count, bool) else "—"
            result = {"ok": "Spoken", "tts_disabled": "Text only", "reply_unavailable": "Reply failed",
                      "speech_unavailable": "Speech unavailable", "cancelled": "Stopped"}.get(row.get("status"), "—")
            context_time = row.get("context_seconds")
            context_label = f"{context_time:.2f} s" if isinstance(context_time, (int, float)) else "—"
            lines = [f"WoW context   {context_label}",
                     f"STT   {row.get('stt_seconds', 0):.2f} s",
                     f"Reply   {row.get('llm_seconds', 0):.2f} s",
                     f"Voice generation   {row.get('tts_seconds', 0):.2f} s",
                     f"First output ready   {row.get('ready_seconds', 0):.2f} s",
                     f"Speech pieces   {row.get('spoken_chunks', 0)} / {row.get('speech_chunks') or row.get('reply_chunks', 1)}",
                     f"Turn total   {row.get('turn_seconds', 0):.2f} s",
                     f"Search requests   {count}",
                     f"Result   {result}"]
            if row.get("provider_requests") is not None:
                lines[-1:-1] = [f"Model requests   {row['provider_requests']}",
                                f"Local context calls   {row.get('local_tool_calls', 0)}"]
            self.metrics_var.set("\n".join(lines))
        self.activity_var.set("\n".join(message for level, message in list(model.activity)[-3:]))
        self.log_var.set(f"Transcript: {model.session_log_status}")
        self.log_copy.configure(state="normal" if model.session_log_path and not model.closing else "disabled")
        self.log_mark.configure(state="normal" if model.session_log_status == "On" and not model.closing else "disabled")
        self.footer_var.set(f"Enter sends text · /search forces lookup · Hotkeys: {model.hotkeys_status} · Overlay: {model.overlay_status}")
        if self.rendered_revision != model.revision:
            self.render_conversation(model)
        if self.rendered_sources != model.sources:
            self.render_sources(model.sources)


def run_control_window(processor, record_function, *, initialize_stt=None, hotkeys_enabled=False, witchcraft_enabled=False):
    root = view = session = None
    try:
        root = tk.Tk(className="Akane")
        view = ControlWindow(root)
        view.project_dir = processor.root
        session = WindowSession(processor, record_function, view, initialize_stt=initialize_stt,
                                 hotkeys_enabled=hotkeys_enabled, witchcraft_enabled=witchcraft_enabled)
        view.request = session.request
        session.start()
        while not session.finished:
            try:
                root.mainloop()
                break
            except KeyboardInterrupt:
                session.request("exit")
        from settings_store import RESTART_SESSION
        return RESTART_SESSION if session.restart_requested else 0
    except tk.TclError as error:
        print(f"Cannot start the control window: {error}. Terminal mode remains available without --gui.")
        return 1
    finally:
        try:
            if session is not None:
                session.cleanup()
            elif root is not None:
                root.destroy()
        finally:
            # Tk.destroy() clears Tcl widgets, but Python widget/callback cycles
            # can still own the interpreter. Collect them on its creating thread
            # before another session or a background worker triggers collection.
            automatic_gc = gc.isenabled()
            gc.disable()
            try:
                session = view = root = None
                gc.collect()
            finally:
                if automatic_gc:
                    gc.enable()
