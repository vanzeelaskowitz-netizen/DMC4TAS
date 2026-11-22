"""Where do the engine's singletons live: inside the snapshotted arenas, or outside them?

Read-only. Lists every pointer in the singleton table (0x118D400..0x118D520) with its class, size
and location, plus sSound's native object. Usage: singleton_survey.py
"""
import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from tasctl import target_pid  # noqa: E402

SLOTS = (0x1184D64, 0x1184D74, 0x1184D88, 0x1184D8C, 0x1184D90, 0x1184D94, 0x1184D6C, 0x1184D70)


def main():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x0410, False, target_pid())

    def read(address, size=4):
        buffer = (ctypes.c_char * size)()
        got = ctypes.c_size_t()
        kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(got))
        return bytes(buffer[:got.value])

    def u32(address):
        data = read(address)
        return struct.unpack("<I", data)[0] if len(data) == 4 else 0

    arenas = []
    for slot in SLOTS:
        descriptor = u32(slot)
        arenas.append((u32(descriptor + 0x5C), u32(descriptor + 0x60)))
    print("arenas", [(hex(a), hex(b)) for a, b in arenas])

    def where(pointer):
        if 0x400000 <= pointer < 0x1400000:
            return "image"
        for index, (low, high) in enumerate(arenas):
            if low <= pointer < high:
                return f"arena{index}"
        return "OUTSIDE"

    def name(pointer):
        getter = u32(u32(pointer) + 16)
        code = read(getter, 6)
        if len(code) < 6 or code[0] != 0xB8 or code[5] != 0xC3:
            return None
        dti = struct.unpack("<I", code[1:5])[0]
        text = read(u32(dti + 4), 40).split(b"\0")[0].decode("ascii", "replace")
        return text, u32(dti + 0x18) & 0x7FFFFF

    for slot in range(0x118D400, 0x118D520, 4):
        pointer = u32(slot)
        if pointer and where(pointer) != "image":
            print(hex(slot), hex(pointer), where(pointer), name(pointer))
    sound = u32(0x118D494)
    native = u32(sound + 0x24)
    print("sSound", hex(sound), where(sound), "native", hex(native), where(native), "vtable", hex(u32(native)))
    words = struct.unpack("<64I", read(native, 256))
    print("native object words:", " ".join(f"{word:08x}:{where(word)[0]}" for word in words))


if __name__ == "__main__":
    main()
