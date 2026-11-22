"""Find a countdown in the scene objects: step the game and look for values that fall steadily.

Takes the game over through the service without making a timeline (m1 commands), steps twice,
compares three memory samples of sMediator and the aGame / aMission / aRoom objects, then hands
the game back. Usage: find_timer.py [frames per sample]
"""
import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from client import Client  # noqa: E402

SIZE = 0x1000


def main():
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    client = Client()
    pid = client.call("status")["gamePid"]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x0010 | 0x0400, False, pid)

    def read(address, size):
        buffer = (ctypes.c_char * size)()
        got = ctypes.c_size_t()
        kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(got))
        return bytes(buffer[:got.value])

    def pointer(address):
        data = read(address, 4)
        return struct.unpack("<I", data)[0] if len(data) == 4 else 0

    area = pointer(0x118D448)
    objects = {"mediator": pointer(0x1154B34), "aGame": pointer(area + 0x3830),
               "aMission": pointer(area + 0x3834), "aRoom": pointer(area + 0x3838)}
    print({name: hex(value) for name, value in objects.items()})

    def sample():
        return {name: read(base, SIZE) for name, base in objects.items() if base}

    print("unpause", client.call("unpause"))
    client.call("m1-pause")
    shots = [sample()]
    for _ in range(2):
        client.call("m1-step", count=frames)
        shots.append(sample())
    print("handed back:", client.call("live").get("mode"))
    for name in shots[0]:
        a, b, c = (shot[name] for shot in shots)
        for offset in range(0, min(len(a), len(b), len(c)) - 3, 4):
            for kind, code in (("f32", "<f"), ("i32", "<i")):
                x, y, z = (struct.unpack_from(code, data, offset)[0] for data in (a, b, c))
                if kind == "f32" and not all(abs(v) < 1e7 and v == v for v in (x, y, z)):
                    continue
                first, second = y - x, z - y
                if first == 0 or abs(first - second) > max(1e-3, abs(first) * 0.02):
                    continue
                if kind == "i32" and abs(first) > 100000:
                    continue
                print(f"{name}+0x{offset:03X} {kind}: {x:.4f} -> {y:.4f} -> {z:.4f}  每 {frames} 帧 {first:+.4f}")


if __name__ == "__main__":
    main()
