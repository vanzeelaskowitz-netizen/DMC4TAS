"""Look for the camera's view and projection matrices (read-only).

Walks sCamera and the objects it points to, and prints every 4x4 float block that looks like a
perspective projection or a rigid view matrix, with where the player would land on screen.
"""
import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from tasctl import target_pid  # noqa: E402

ARENA = (0x6050000, 0x27550000)


def main():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x0410, False, target_pid())

    def read(address, size):
        buffer = (ctypes.c_char * size)()
        got = ctypes.c_size_t()
        kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(got))
        return bytes(buffer[:got.value])

    def u32(address):
        data = read(address, 4)
        return struct.unpack("<I", data)[0] if len(data) == 4 else 0

    def name(pointer):
        getter = u32(u32(pointer) + 16)
        code = read(getter, 6)
        if len(code) < 6 or code[0] != 0xB8 or code[5] != 0xC3:
            return None, 0
        dti = struct.unpack("<I", code[1:5])[0]
        return read(u32(dti + 4), 40).split(b"\0")[0].decode("ascii", "replace"), u32(dti + 0x18) & 0x7FFFFF

    mediator = u32(0x1154B34)
    player = u32(mediator + 0x24)
    position = struct.unpack("<3f", read(player + 0x40, 12)) if player else None
    print("player", hex(player), position)
    camera = u32(0x118D474)
    todo, seen = [(camera, "sCamera", 0)], set()
    while todo:
        pointer, label, depth = todo.pop(0)
        if pointer in seen or not (ARENA[0] <= pointer < ARENA[1]):
            continue
        seen.add(pointer)
        cls, size = name(pointer)
        if cls is None:
            continue
        size = min(max(size, 0x40), 0x2000)
        data = read(pointer, size)
        print(f"{label}: {cls} at {pointer:#x} size {size:#x}")
        for offset in range(0, len(data) - 63, 4):
            m = struct.unpack_from("<16f", data, offset)
            if not all(abs(v) < 1e6 and v == v for v in m):
                continue
            kind = None
            if abs(m[11]) == 1 and m[15] == 0 and m[0] > 0.2 and m[5] > 0.2 and m[1] == 0 and m[4] == 0:
                kind = "PROJECTION"
            elif m[3] == 0 and m[7] == 0 and m[11] == 0 and m[15] == 1:
                rows = [m[0:3], m[4:7], m[8:11]]
                if all(abs(sum(x * x for x in row) - 1) < 1e-3 for row in rows) and any(abs(v) > 1 for v in m[12:15]):
                    kind = "view/world"
            if kind:
                extra = ""
                if kind == "view/world" and position:
                    x, y, z = position
                    v = [x * m[k] + y * m[4 + k] + z * m[8 + k] + m[12 + k] for k in range(3)]
                    extra = f"  player in this space: ({v[0]:.0f}, {v[1]:.0f}, {v[2]:.0f})"
                print(f"   +{offset:#06x} {kind}: {[round(v, 3) for v in m]}{extra}")
        if depth < 2:
            for offset in range(0, len(data) - 3, 4):
                child = struct.unpack_from("<I", data, offset)[0]
                if ARENA[0] <= child < ARENA[1] and child not in seen:
                    todo.append((child, f"{label}+{offset:#x}", depth + 1))


if __name__ == "__main__":
    main()
