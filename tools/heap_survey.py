"""Read-only survey: MT Framework allocator arenas and engine objects that live outside them."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tas"))

from tasctl import target_pid  # noqa: E402
from rollback_memory import ProcessMemory, MEM_PRIVATE  # noqa: E402

BASE = 0x400000
SLOTS = {"Global": 0x1184D64, "Temp": 0x1184D74, "Resource": 0x1184D88, "System": 0x1184D8C,
         "Unit": 0x1184D90, "Area": 0x1184D94, "Array": 0x1184D6C, "Collision": 0x1184D70}
DATA = (BASE + 0xB8F000, BASE + 0xB8F000 + 0x25F3C8)
IMAGE_END = BASE + 0xE86000


def dti_of(memory, address):
    try:
        vtable = memory.u32(address)
        if not BASE <= vtable < IMAGE_END:
            return None
        getter = memory.u32(vtable + 16)
        if not BASE <= getter < IMAGE_END:
            return None
        code = memory.read(getter, 6)
        if code[0] != 0xB8 or code[5] != 0xC3:
            return None
        dti = int.from_bytes(code[1:5], "little")
        name_pointer = memory.u32(dti + 4)
        name = memory.read(name_pointer, 40).split(b"\0")[0].decode("ascii", "replace")
        size = (memory.u32(dti + 0x18) & 0x7FFFFF) * 4
        return name, size
    except OSError:
        return None


def main():
    pid = target_pid()
    with ProcessMemory(pid) as memory:
        regions = [r for r in memory.regions() if r.writable]
        arenas = {}
        for name, slot in SLOTS.items():
            descriptor = memory.u32(slot)
            low, high = memory.u32(descriptor + 0x5C), memory.u32(descriptor + 0x60)
            committed = sum(min(r.end, high) - max(r.base, low) for r in regions if r.base < high and r.end > low)
            home = memory.region(descriptor)
            arenas[name] = (low, high)
            print(f"{name:9} descriptor {descriptor:#010x} (region {home.base:#x}-{home.end:#x} alloc {home.allocation:#x} kind {home.kind:#x}) "
                  f"arena {low:#010x}-{high:#010x} {(high - low) >> 20} MiB, writable committed {committed >> 20} MiB, type {dti_of(memory, descriptor)}")
            words = [memory.u32(descriptor + off) for off in range(0, 0x80, 4)]
            print("          words:", " ".join(f"{w:08x}" for w in words))

        def inside(address):
            return any(low <= address < high for low, high in arenas.values())

        data = memory.read(DATA[0], DATA[1] - DATA[0])
        seen = {}
        for offset in range(0, len(data) - 3, 4):
            pointer = int.from_bytes(data[offset:offset + 4], "little")
            if pointer < 0x10000 or BASE <= pointer < IMAGE_END or inside(pointer) or pointer in seen:
                continue
            info = dti_of(memory, pointer)
            if info:
                seen[pointer] = (DATA[0] + offset, info)
        print(f"\nengine objects referenced from .data that live outside the image and the arenas: {len(seen)}")
        total = 0
        for pointer, (slot, (name, size)) in sorted(seen.items()):
            region = memory.region(pointer)
            total += size
            print(f"  {pointer:#010x} {name:28} size {size:#8x}  slot {slot:#x}  region {region.base:#x}-{region.end:#x} kind {region.kind:#x}")
        print("total bytes of those objects:", total)
        private = sum(r.end - r.base for r in regions if r.kind == MEM_PRIVATE)
        print("all private writable bytes:", private >> 20, "MiB in", sum(1 for r in regions if r.kind == MEM_PRIVATE), "regions")


if __name__ == "__main__":
    main()
