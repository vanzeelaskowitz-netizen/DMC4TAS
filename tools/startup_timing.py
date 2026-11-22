"""Measure the game's start-up: when the process appears, when the allocators exist, how they are allocated."""
import ctypes as ct
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("tas", "work", "tools"):
    sys.path.insert(0, str(ROOT / folder))

from rollback_memory import ProcessMemory  # noqa: E402

GAME = "DevilMayCry4SpecialEdition.exe"
SLOTS = (0x1184D64, 0x1184D74, 0x1184D88, 0x1184D8C, 0x1184D90, 0x1184D94, 0x1184D6C, 0x1184D70)
k32 = ct.WinDLL("kernel32", use_last_error=True)


class ProcessEntry(ct.Structure):
    _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD), ("heap", ct.c_size_t),
                ("module", wt.DWORD), ("threads", wt.DWORD), ("parent", wt.DWORD), ("priority", wt.LONG),
                ("flags", wt.DWORD), ("name", wt.WCHAR * 260)]


k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wt.HANDLE, ct.POINTER(ProcessEntry)]


def find_pid():
    snapshot = k32.CreateToolhelp32Snapshot(2, 0)
    try:
        entry = ProcessEntry()
        entry.size = ct.sizeof(entry)
        ok = k32.Process32FirstW(snapshot, ct.byref(entry))
        while ok:
            if entry.name.casefold() == GAME.casefold():
                return entry.pid
            ok = k32.Process32NextW(snapshot, ct.byref(entry))
    finally:
        k32.CloseHandle(snapshot)
    return None


def main():
    subprocess.run(["taskkill", "/F", "/IM", GAME], capture_output=True)
    time.sleep(2)
    os.startfile("steam://rungameid/329050")
    asked = time.perf_counter()
    pid = None
    while pid is None and time.perf_counter() - asked < 60:
        pid = find_pid()
        if pid is None:
            time.sleep(.001)
    born = time.perf_counter()
    print(f"process {pid} seen {born - asked:.2f}s after the launch request")
    events = []
    memory = None
    while memory is None and time.perf_counter() - born < 5:
        try:
            memory = ProcessMemory(pid)
        except OSError:
            time.sleep(.001)
    print(f"handle opened at +{(time.perf_counter() - born) * 1000:.0f} ms")
    text_ready = slot_ready = None
    while time.perf_counter() - born < 30 and slot_ready is None:
        now = (time.perf_counter() - born) * 1000
        try:
            if text_ready is None and memory.read(0x9D1A70, 4) == bytes.fromhex("558bec83"):
                text_ready = now
                events.append(f"+{now:.0f} ms: code section decrypted (frame gate bytes present)")
            first = memory.u32(SLOTS[0])
            if first:
                slot_ready = now
                events.append(f"+{now:.0f} ms: first allocator pointer set ({first:#x})")
        except OSError:
            pass
        time.sleep(.0005)
    print("\n".join(events))
    time.sleep(1.5)
    for slot in SLOTS:
        descriptor = memory.u32(slot)
        low, high = memory.u32(descriptor + 0x5C), memory.u32(descriptor + 0x60)
        region = memory.region(low)
        print(f"arena {low:#010x}-{high:#010x} allocation base {region.allocation:#010x} region {region.base:#x}-{region.end:#x} protect {region.protect:#x} kind {region.kind:#x}")


if __name__ == "__main__":
    main()
