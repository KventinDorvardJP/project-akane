# Optional local preview; OBS reads the same text file directly for streaming.
# Running this window is not required by main.py or the OBS subtitle source.

from pathlib import Path
import tkinter as tk


def main():
    # This path must match main.py; an empty file means the caption should disappear.
    subtitle_path = (
        Path(__file__).resolve().parent
        / "runtime"
        / "subtitle_en.txt"
    )

    window = tk.Tk()
    window.title("Akane — English subtitles")
    window.geometry("900x220")
    window.configure(bg="black")
    # Keep the caption visible while another application, such as the terminal, has focus.
    window.attributes("-topmost", True)

    label = tk.Label(
        window,
        text="",
        font=("Segoe UI", 22),
        fg="white",
        bg="black",
        wraplength=840,
        justify="center",
        padx=20,
        pady=20,
    )
    label.pack(fill="both", expand=True)

    # Poll a small shared text file; starting the window before the producer is supported.
    def refresh_subtitle():
        try:
            text = subtitle_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        except (OSError, UnicodeError):
            text = "Subtitles unavailable."

        # Avoid reconfiguring the label when the producer has not changed its contents.
        if text != label.cget("text"):
            label.configure(text=text)

        # Schedule the next check in milliseconds, allowing Tk to process window events meanwhile.
        window.after(200, refresh_subtitle)

    refresh_subtitle()
    # Tk owns this process's event loop; blocking chat/audio work runs in main.py's process.
    window.mainloop()


if __name__ == "__main__":
    main()
