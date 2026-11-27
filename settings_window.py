"""Settings dialog; created only by an explicit click in the control window."""
import tkinter as tk
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText

from settings_store import (CHOICES, STRICT_CHOICES, NULLABLE, SettingsStore,
                            format_value, group_for, label_for, parse_value)


class SettingsDialog:
    def __init__(self, parent, project_dir, *, on_restart, on_saved=None):
        self.store = SettingsStore(project_dir)
        self.values = self.store.load()
        self.on_restart, self.on_saved = on_restart, on_saved
        self.fields, self.canvases = {}, {}
        self.window = tk.Toplevel(parent)
        self.window.title("Akane — settings")
        self.window.geometry("940x760")
        self.window.minsize(760, 560)
        self.window.configure(background="#101722")
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        intro = ("Saved defaults for your next session; current launch flags may differ. Save leaves this conversation running.\n"
                 "Save & restart uses these choices, clears chat and resets the session cost estimate. "
                 "Account charges remain unchanged.")
        ttk.Label(self.window, text=intro, wraplength=850).grid(row=0, column=0, sticky="ew", padx=18, pady=12)
        self.notebook = ttk.Notebook(self.window)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=18)
        self.pages = {}
        order = ("Session", "Conversation", "Recognition", "VOICEVOX", "GPT-SoVITS", "Audio & keys", "Subtitles", "Japanese text", "WoW context")
        for group in order:
            page = ttk.Frame(self.notebook)
            page.rowconfigure(0, weight=1)
            page.columnconfigure(0, weight=1)
            self.notebook.add(page, text=group)
            canvas = tk.Canvas(page, background="#101722", highlightthickness=0)
            bar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=bar.set)
            canvas.grid(row=0, column=0, sticky="nsew")
            bar.grid(row=0, column=1, sticky="ns")
            body = ttk.Frame(canvas, padding=12)
            body.columnconfigure(1, weight=1)
            item = canvas.create_window((0, 0), window=body, anchor="nw")
            body.bind("<Configure>", lambda event, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>", lambda event, c=canvas, i=item: c.itemconfigure(i, width=event.width))
            self.pages[group] = (body, 0)
            self.canvases[str(page)] = canvas
        for name, value in self.values.items():
            self.add_field(name, value)
        audio_body, audio_row = self.pages["Audio & keys"]
        ttk.Button(audio_body, text="List audio devices", command=self.list_devices).grid(row=audio_row, column=0, sticky="w", pady=10)
        self.devices = ScrolledText(audio_body, height=7, width=30, wrap="word", state="disabled",
                                    background="#172231", foreground="#edf2fa")
        self.devices.grid(row=audio_row + 1, column=0, columnspan=3, sticky="ew")
        self.status = tk.StringVar(self.window, "Changes take effect after a session restart. API keys stay in .env / environment variables.")
        ttk.Label(self.window, textvariable=self.status, wraplength=850).grid(row=2, column=0, sticky="ew", padx=18, pady=10)
        actions = ttk.Frame(self.window, padding=(18, 0, 18, 16))
        actions.grid(row=3, column=0, sticky="ew")
        ttk.Button(actions, text="Restore form defaults", command=self.restore_defaults).pack(side="left")
        ttk.Button(actions, text="Close", command=self.close).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Save & restart", command=lambda: self.save(restart=True)).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Save", command=self.save).pack(side="right")
        self.window.bind("<MouseWheel>", self.scroll)

    def add_field(self, name, value):
        group = group_for(name)
        body, row = self.pages[group]
        label = label_for(name)
        if name in NULLABLE and name not in {"MIC_INPUT_DEVICE", "AUDIO_OUTPUT_DEVICE"}:
            label += " (blank = unset)"
        ttk.Label(body, text=label, wraplength=290).grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=8)
        default = self.store.defaults[name]
        multiline = isinstance(default, dict) or name in {"GPT_SOVITS_REFERENCE_TEXT", "GPT_SOVITS_AUX_REFERENCE_AUDIO"}
        if multiline:
            widget = ScrolledText(body, height=7 if isinstance(default, dict) else 3, width=30,
                                  wrap="word", background="#172231", foreground="#edf2fa", insertbackground="#edf2fa")
            widget.insert("1.0", format_value(name, value))
            self.fields[name] = (widget, "text")
        elif isinstance(default, bool):
            variable = tk.BooleanVar(body, value)
            widget = ttk.Checkbutton(body, variable=variable)
            self.fields[name] = (variable, "variable")
        else:
            variable = tk.StringVar(body, format_value(name, value))
            if name in CHOICES:
                widget = ttk.Combobox(body, textvariable=variable, values=CHOICES[name],
                                      state="readonly" if name in STRICT_CHOICES else "normal", width=25)
            else:
                widget = ttk.Entry(body, textvariable=variable, width=25)
            self.fields[name] = (variable, "variable")
        widget.grid(row=row, column=1, sticky="ew", pady=8)
        if name in {"GPT_SOVITS_REFERENCE_AUDIO", "GPT_SOVITS_AUX_REFERENCE_AUDIO", "WOW_CONTEXT_CACHE_PATH"}:
            ttk.Button(body, text="Browse…", command=lambda n=name: self.browse(n)).grid(row=row, column=2, padx=(8, 0), sticky="n", pady=8)
        self.pages[group] = (body, row + 1)

    def scroll(self, event):
        if not isinstance(event.widget, tk.Text):
            canvas = self.canvases.get(self.notebook.select())
            if canvas is not None:
                canvas.yview_scroll(-int(event.delta / 120), "units")

    def browse(self, name):
        if name == "WOW_CONTEXT_CACHE_PATH":
            path = filedialog.askopenfilename(parent=self.window, title="Choose Witchcraft wow-context.json",
                                               filetypes=[("Witchcraft cache", "wow-context.json")])
            if path:
                self.fields[name][0].set(path)
            return
        options = {"parent": self.window, "title": "Choose reference audio", "initialdir": str(self.store.root / "audio_samples"),
                   "filetypes": [("Audio", "*.wav *.flac *.ogg *.mp3"), ("All files", "*.*")]}
        if name == "GPT_SOVITS_AUX_REFERENCE_AUDIO":
            paths = filedialog.askopenfilenames(**options)
            if paths:
                widget, _ = self.fields[name]
                widget.delete("1.0", "end")
                widget.insert("1.0", "\n".join(paths))
        else:
            path = filedialog.askopenfilename(**options)
            if path:
                self.fields[name][0].set(path)

    def collect(self):
        values = {}
        for name, (field, kind) in self.fields.items():
            raw = field.get("1.0", "end-1c") if kind == "text" else field.get()
            values[name] = parse_value(name, raw, self.store.defaults[name])
        return values

    def save(self, restart=False):
        try:
            values = self.collect()
            self.store.save(values)
        except (OSError, ValueError) as error:
            self.status.set(str(error))
            return False
        self.values = values
        self.status.set("Saved locally. Restart the session to apply these settings.")
        if self.on_saved:
            self.on_saved()
        if restart:
            if self.on_restart():
                self.close()
            else:
                self.status.set("Saved. Wait for recording, processing and microphone startup to finish, then use Save & restart.")
        return True

    def restore_defaults(self):
        for name, value in self.store.defaults.items():
            field, kind = self.fields[name]
            if kind == "text":
                field.delete("1.0", "end")
                field.insert("1.0", format_value(name, value))
            else:
                field.set(value if isinstance(value, bool) else format_value(name, value))
        self.status.set("Form defaults restored. Nothing is saved until you press Save.")

    def list_devices(self):
        # Explicit read-only enumeration; never opens a recording/playback stream.
        try:
            import sounddevice as sd
            lines = [f"{index}: {device['name']} (inputs: {device['max_input_channels']}, outputs: {device['max_output_channels']})"
                     for index, device in enumerate(sd.query_devices())]
            text = "\n".join(lines) or "No audio devices reported."
        except Exception as error:
            text = f"Could not list audio devices: {error}"
        self.devices.configure(state="normal")
        self.devices.delete("1.0", "end")
        self.devices.insert("1.0", text)
        self.devices.configure(state="disabled")

    def close(self):
        try:
            self.window.destroy()
        except tk.TclError:
            pass
