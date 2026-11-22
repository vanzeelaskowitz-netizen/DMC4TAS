"""List every place where the running game's code differs from the clean decrypted dump.

Shows what other tools (trainers) and the TAS agent changed, so a clash between them can be seen.
    code_diff.py [pid]                       list (read-only)
    code_diff.py pid --restore VA [VA ...]   put the clean bytes back at those diffs (hex VAs)
Never restore a TAS agent hook site this way: the agent's trampolines would be cut off.
"""
import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / "dump" / "dmc4se_mem.exe"
BASE = 0x400000
# Places the TAS agent itself patches (VA), for labelling.
AGENT = {0x9D1A70: "TAS 帧闸 (frame gate)", 0xC420B0: "TAS GPU 资源保护 (Resource::Release)",
         0xAB2160: "TAS 焦点函数"}


def sections(image):
    pe = struct.unpack_from("<I", image, 0x3C)[0]
    count = struct.unpack_from("<H", image, pe + 6)[0]
    optional = struct.unpack_from("<H", image, pe + 20)[0]
    table = pe + 24 + optional
    for index in range(count):
        name, vsize, rva, rsize, raw, _, _, _, _, flags = struct.unpack_from("<8sIIIIIIHHI", image, table + 40 * index)
        yield name.rstrip(b"\0").decode(), rva, vsize, raw, rsize, flags


def restore(pid, found, wanted):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x0008 | 0x0010 | 0x0020 | 0x0400, False, pid)
    for va, before, _ in found:
        if va not in wanted:
            continue
        if any(site <= va < site + 16 for site in AGENT):
            print(f"0x{va:08X} 是 TAS 自己的钩子，不还原")
            continue
        old = wintypes.DWORD()
        kernel.VirtualProtectEx(handle, ctypes.c_void_p(va), len(before), 0x40, ctypes.byref(old))
        ok = kernel.WriteProcessMemory(handle, ctypes.c_void_p(va), before, len(before), None)
        kernel.VirtualProtectEx(handle, ctypes.c_void_p(va), len(before), old.value, ctypes.byref(old))
        kernel.FlushInstructionCache(handle, ctypes.c_void_p(va), len(before))
        print(f"0x{va:08X} 还原 {'成功' if ok else '失败'}")
    kernel.CloseHandle(handle)


def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    wanted = {int(text, 16) for text in sys.argv[3:]} if sys.argv[2:3] == ["--restore"] else None
    if pid is None:
        import subprocess
        out = subprocess.run(["tasklist", "/fi", "imagename eq DevilMayCry4SpecialEdition.exe", "/fo", "csv", "/nh"],
                             capture_output=True, text=True).stdout
        pid = int(out.split(",")[1].strip('"'))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x0010 | 0x0400, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error())
    image = DUMP.read_bytes()
    found = []
    for name, rva, vsize, raw, rsize, flags in sections(image):
        if not flags & 0x20000000:          # executable sections only
            continue
        size = min(vsize, rsize)
        clean = image[raw:raw + size]
        live = (ctypes.c_char * size)()
        read = ctypes.c_size_t()
        if not kernel.ReadProcessMemory(handle, ctypes.c_void_p(BASE + rva), live, size, ctypes.byref(read)):
            print(f"{name}: read failed {ctypes.get_last_error()}")
            continue
        live = bytes(live)
        offset = 0
        while offset < size:
            if live[offset] != clean[offset]:
                start = offset
                while offset < size and (live[offset] != clean[offset] or live[offset:offset + 8] != clean[offset:offset + 8]):
                    offset += 1
                found.append((BASE + rva + start, clean[start:offset], live[start:offset]))
            offset += 1
    kernel.CloseHandle(handle)
    for va, before, after in found:
        label = next((text for site, text in AGENT.items() if site <= va < site + 16), "")
        print(f"0x{va:08X} (exe+0x{va - BASE:06X}) {len(before):3d} 字节  原 {before[:16].hex(' ')}  现 {after[:16].hex(' ')}  {label}")
    print(f"共 {len(found)} 处")
    if wanted:
        restore(pid, found, wanted)


if __name__ == "__main__":
    main()
