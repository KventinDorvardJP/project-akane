"""Bounded foreground-only Win32 capture for AkaneData; no hooks or input.

The addon draws 64 x 6 cells of three physical pixels at client coordinate 0,24.
This module is never imported by normal startup without --wow.
"""
import ctypes
from ctypes import wintypes as w
from pathlib import PureWindowsPath


class WindowsWoWCapture:
    def __enter__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        declarations = [
            (self.user, "GetForegroundWindow", [], w.HWND),
            (self.user, "GetWindowThreadProcessId", [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            (self.user, "ClientToScreen", [w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            (self.user, "GetClientRect", [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            (self.user, "GetDC", [w.HWND], w.HDC),
            (self.user, "ReleaseDC", [w.HWND, w.HDC], ctypes.c_int),
            (self.kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            (self.kernel, "QueryFullProcessImageNameW", [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
            (self.kernel, "CloseHandle", [w.HANDLE], w.BOOL),
            (self.gdi, "CreateCompatibleDC", [w.HDC], w.HDC),
            (self.gdi, "DeleteDC", [w.HDC], w.BOOL),
            (self.gdi, "DeleteObject", [w.HANDLE], w.BOOL),
            (self.gdi, "SelectObject", [w.HDC, w.HANDLE], w.HANDLE),
            (self.gdi, "CreateDIBSection", [w.HDC, ctypes.c_void_p, w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD], w.HANDLE),
            (self.gdi, "BitBlt", [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD], w.BOOL),
        ]
        for library, name, args, result in declarations:
            function = getattr(library, name)
            function.argtypes, function.restype = args, result
        # Affect the receiver thread only, leaving Tk's DPI policy unchanged.
        self.user.SetThreadDpiAwarenessContext.argtypes = [w.HANDLE]
        self.user.SetThreadDpiAwarenessContext.restype = w.HANDLE
        self.previous_dpi = self.user.SetThreadDpiAwarenessContext(w.HANDLE(-4))
        return self

    def read(self):
        window = self.user.GetForegroundWindow()
        if not window:
            return None
        pid = w.DWORD()
        self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        # Query executable identity only; PROCESS_VM_READ is never requested.
        process = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not process:
            return None
        try:
            name, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
            if not self.kernel.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(length)):
                return None
            if PureWindowsPath(name.value).name.casefold() not in {"wow.exe", "wowb.exe"}:
                return None
        finally:
            self.kernel.CloseHandle(process)
        point, rectangle = w.POINT(0, 24), w.RECT()
        if not self.user.ClientToScreen(window, ctypes.byref(point)) or not self.user.GetClientRect(window, ctypes.byref(rectangle)):
            return None
        if rectangle.right < 192 or rectangle.bottom < 42:
            return None
        # BITMAPINFOHEADER plus one unused colour entry; negative height is top-down.
        header = ctypes.create_string_buffer(44)
        import struct
        struct.pack_into("<IiiHHIIiiII", header, 0, 40, 192, -18, 1, 32, 0, 192 * 18 * 4, 0, 0, 0, 0)
        screen = self.user.GetDC(None)
        memory, bitmap, previous = None, None, None
        try:
            if not screen:
                raise OSError("Cannot acquire desktop capture")
            memory = self.gdi.CreateCompatibleDC(screen)
            bits = ctypes.c_void_p()
            bitmap = self.gdi.CreateDIBSection(screen, header, 0, ctypes.byref(bits), None, 0)
            if not memory or not bitmap or not bits.value:
                raise OSError("Cannot allocate bounded WoW capture")
            previous = self.gdi.SelectObject(memory, bitmap)
            if not previous or previous == w.HANDLE(-1).value:
                previous = None
                raise OSError("Cannot select WoW capture bitmap")
            if not self.gdi.BitBlt(memory, 0, 0, 192, 18, screen, point.x, point.y, 0x00CC0020):
                return None
            if self.user.GetForegroundWindow() != window:
                return None
            bgra = ctypes.string_at(bits, 192 * 18 * 4)
            cells = bytearray()
            for row in range(6):
                for column in range(64):
                    offset = ((row * 3 + 1) * 192 + column * 3 + 1) * 4
                    cells.extend((bgra[offset + 2], bgra[offset + 1], bgra[offset]))
            return bytes(cells)
        finally:
            if previous and memory:
                self.gdi.SelectObject(memory, previous)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if memory:
                self.gdi.DeleteDC(memory)
            if screen:
                self.user.ReleaseDC(None, screen)

    def __exit__(self, *args):
        if self.previous_dpi:
            self.user.SetThreadDpiAwarenessContext(self.previous_dpi)
        return False
