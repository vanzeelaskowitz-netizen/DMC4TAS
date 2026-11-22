"""Locate the visible game window and require the configured windowed mode."""
import ctypes as ct
from ctypes import wintypes as wt
import os
from pathlib import Path
import re


user32 = ct.WinDLL("user32", use_last_error=True)
WINDOW_ENUM = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = (WINDOW_ENUM, wt.LPARAM)
user32.GetWindowThreadProcessId.argtypes = (wt.HWND, ct.POINTER(wt.DWORD))
user32.GetWindowLongW.argtypes = (wt.HWND, ct.c_int)
user32.IsWindowVisible.argtypes = (wt.HWND,)
user32.GetWindowTextLengthW.argtypes = (wt.HWND,)


def require_windowed(pid):
    path = Path(os.environ["LOCALAPPDATA"]) / "CAPCOM/DEVILMAYCRY4SPECIALEDITION/config.ini"
    if not re.search(rb"(?im)^\s*FullScreen\s*=\s*OFF\s*$", path.read_bytes()):
        raise RuntimeError("DMC4SE config must have FullScreen=OFF")
    found = []

    @WINDOW_ENUM
    def check(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ct.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            style = user32.GetWindowLongW(hwnd, -16) & 0xffffffff
            if style & 0x00c00000 == 0x00c00000:
                found.append((hwnd, style))
        return True

    user32.EnumWindows(check, 0)
    if len(found) != 1:
        raise RuntimeError("DMC4SE must have one visible window with a title bar")
    return found[0]
