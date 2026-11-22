"""Disassemble a stretch of the dumped game image (dump\\dmc4se_mem.exe, build 24329835).

    .venv\\Scripts\\python.exe tools\\disasm.py RVA [bytes before] [bytes after]
    .venv\\Scripts\\python.exe tools\\disasm.py --func RVA      # from the function start that contains RVA

RVAs are relative to the module base (what the crash log prints as exe+0x...).
"""
import struct
import sys
from pathlib import Path

import capstone

ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "dump" / "dmc4se_mem.exe").read_bytes()
PE = struct.unpack_from("<I", DATA, 0x3C)[0]
COUNT = struct.unpack_from("<H", DATA, PE + 6)[0]
FIRST = PE + 24 + struct.unpack_from("<H", DATA, PE + 20)[0]
SECTIONS = [struct.unpack_from("<4I", DATA, FIRST + 40 * i + 8) for i in range(COUNT)]   # vsize, rva, rsize, raw
MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)


def offset(rva):
    for vsize, start, rsize, raw in SECTIONS:
        if start <= rva < start + max(vsize, rsize):
            return raw + rva - start
    raise ValueError(f"RVA {rva:#x} is in no section")


def code(rva, size):
    at = offset(rva)
    return DATA[at:at + size]


def function_start(rva):
    """Walk back to the nearest padding (int3 / nop run) followed by an aligned instruction."""
    at = rva
    while at > rva - 0x4000:
        at -= 1
        if code(at - 1, 1) in (b"\xCC", b"\x90") and at % 16 == 0 and code(at, 1) not in (b"\xCC", b"\x90"):
            return at
    return rva


def show(start, size, mark=None):
    for ins in MD.disasm(code(start, size), start):
        print(f"{'>>' if ins.address == mark else '  '} {ins.address:08x}  {ins.mnemonic} {ins.op_str}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[0] == "--func":
        rva = int(args[1], 16)
        start = function_start(rva)
        show(start, rva - start + int(args[2], 16) if len(args) > 2 else rva - start + 0x40, rva)
    else:
        rva = int(args[0], 16)
        before = int(args[1], 16) if len(args) > 1 else 0x30
        after = int(args[2], 16) if len(args) > 2 else 0x30
        # Disassembling from an arbitrary byte can start mid-instruction: begin at the function start.
        start = function_start(rva)
        lines = list(MD.disasm(code(start, rva - start + after), start))
        for ins in lines:
            if ins.address >= rva - before:
                print(f"{'>>' if ins.address == rva else '  '} {ins.address:08x}  {ins.mnemonic} {ins.op_str}")
