"""Identify the executable actually mapped by a game PID before selecting M1 addresses."""
import ctypes as ct
from ctypes import wintypes as wt
from hashlib import sha256
import json
from pathlib import Path


def numeric(values):
    return {key: (int(value, 16) if isinstance(value, str) and value.startswith("0x") else value)
            for key, value in values.items()}


def profiles():
    for path in sorted(Path(__file__).with_name("builds").glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"), object_hook=numeric)
        profile["scene"]["slots"] = [int(value, 16) for value in profile["scene"]["slots"]]
        yield profile


def identify(pid):
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = wt.DWORD, wt.BOOL, wt.DWORD
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)
    kernel.CloseHandle.argtypes = (wt.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ct.WinError(ct.get_last_error())
    try:
        size = wt.DWORD(32768)
        path = ct.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, ct.byref(size)):
            raise ct.WinError(ct.get_last_error())
    finally:
        kernel.CloseHandle(handle)
    exe = Path(path.value)
    digest = sha256(exe.read_bytes()).hexdigest()
    for profile in profiles():
        if profile["sha256"] == digest and profile["process"].casefold() == exe.name.casefold():
            return profile
    raise RuntimeError(f"Unsupported game executable {exe.name} (SHA-256 {digest})")
