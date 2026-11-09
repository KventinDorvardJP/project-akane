# Windows hotkey listener. Native registration occurs only in start(), not import.
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import sys
import threading


class HotkeyError(Exception):
    pass


@dataclass(frozen=True)
class Hotkey:
    modifiers: int
    key: int


def parse_hotkey(text):
    parts = [part.strip().lower() for part in text.split("+")]
    modifiers = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004}
    mask, keys = 0, []
    for part in parts:
        if part in modifiers:
            mask |= modifiers[part]
        else:
            keys.append(part)
    if len(keys) != 1 or not mask:
        raise HotkeyError("Use modifiers plus one letter, digit, or F-key, e.g. ctrl+shift+f8.")
    key = keys[0]
    if len(key) == 1 and key.isascii() and key.isalnum():
        code = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        code = 0x70 + int(key[1:]) - 1
    else:
        raise HotkeyError(f"Unsupported hotkey: {text}")
    if code == 0x7B:
        raise HotkeyError("F12 is reserved by Windows debugging; choose another hotkey.")
    return Hotkey(mask | 0x4000, code)  # MOD_NOREPEAT prevents held-key retriggers.


class NativeHotkeys:
    """Small injectable wrapper so tests never register real keys or send input."""
    def __init__(self):
        if sys.platform != "win32":
            raise HotkeyError("Global hotkeys require Windows.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def thread_id(self):
        return self.kernel32.GetCurrentThreadId()

    def register(self, identifier, hotkey):
        if not self.user32.RegisterHotKey(None, identifier, hotkey.modifiers, hotkey.key):
            raise HotkeyError(f"Hotkey unavailable (Windows error {ctypes.get_last_error()}); change the configured key combination.")

    def unregister(self, identifier):
        self.user32.UnregisterHotKey(None, identifier)

    def next_action_id(self):
        message = wintypes.MSG()
        result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
        if result == -1:
            raise HotkeyError("Windows hotkey message loop failed.")
        if result == 0:
            return None
        return int(message.wParam) if message.message == 0x0312 else -1  # WM_HOTKEY

    def wake_stop(self, thread_id):
        self.user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)  # WM_QUIT


class WindowsHotkeys:
    def __init__(self, bindings, callback, native_factory=NativeHotkeys):
        self.bindings, self.callback, self.native_factory = bindings, callback, native_factory
        self.ready = threading.Event()
        self.error = None
        self.native = None
        self.thread_id = None
        self.thread = None

    def start(self):
        parsed = {action: parse_hotkey(text) for action, text in self.bindings.items()}
        if len(set(parsed.values())) != len(parsed):
            raise HotkeyError("Hotkey combinations must be distinct.")

        def listen():
            registered = []
            try:
                self.native = self.native_factory()
                self.thread_id = self.native.thread_id()
                actions = {}
                for identifier, (action, hotkey) in enumerate(parsed.items(), 1):
                    self.native.register(identifier, hotkey)
                    registered.append(identifier)
                    actions[identifier] = action
                self.ready.set()
                while True:
                    identifier = self.native.next_action_id()
                    if identifier is None:
                        break
                    if identifier in actions:
                        self.callback(actions[identifier])  # Queue only; never block this thread.
            except Exception as error:
                self.error = error
                self.ready.set()
                self.callback("listener_failed")
            finally:
                # Unregister on the same thread that owns the registrations.
                for identifier in registered:
                    self.native.unregister(identifier)

        self.thread = threading.Thread(target=listen, name="Akane hotkeys", daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            self.stop()
            raise HotkeyError("Timed out initializing hotkeys.")
        if self.error:
            self.thread.join(2)
            raise HotkeyError(str(self.error))

    def stop(self):
        if self.native is not None and self.thread_id is not None and self.thread and self.thread.is_alive():
            self.native.wake_stop(self.thread_id)
            self.thread.join(2)
