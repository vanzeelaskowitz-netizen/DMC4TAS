"""Export an analysis PE using read-only access to the uninstrumented game."""
import argparse
import ctypes as ct
from ctypes import wintypes as wt
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import struct

import pefile

from keyboard_probe import EXPECTED_SHA
from live_read import close, open_process, query_name, read
from pe_image import EXE

ROOT = Path(__file__).resolve().parents[1]


def export(pid):
    disk = EXE.read_bytes()
    if sha256(disk).hexdigest() != EXPECTED_SHA:
        raise RuntimeError("Unexpected game build")
    pe = pefile.PE(data=disk)
    header = pe.OPTIONAL_HEADER
    base = header.ImageBase
    handle = open_process(0x410, False, pid)
    if not handle:
        raise ct.WinError(ct.get_last_error())
    sections = []
    image = bytearray(header.SizeOfImage)
    try:
        path = ct.create_unicode_buffer(32768)
        size = wt.DWORD(len(path))
        if not query_name(handle, 0, path, ct.byref(size)) or Path(path.value) != EXE:
            raise RuntimeError("PID does not identify the installed game")
        if read(handle, base, 2) != b"MZ":
            raise RuntimeError("Game is not mapped at its preferred base")
        image[:header.SizeOfHeaders] = disk[:header.SizeOfHeaders]
        for sec in pe.sections:
            rva = sec.VirtualAddress
            size = max(sec.Misc_VirtualSize, sec.SizeOfRawData)
            raw_size = (size + header.FileAlignment - 1) // header.FileAlignment * header.FileAlignment
            image[rva:rva + size] = read(handle, base + rva, size)
            struct.pack_into("<II", image, sec.get_file_offset() + 16, raw_size, rva)
            sections.append(dict(name=sec.Name.rstrip(b"\0").decode("ascii"), rva=rva,
                                 address=base+rva, virtual_size=sec.Misc_VirtualSize,
                                 size=raw_size, memory_size=size,
                                 memory_sha256=sha256(image[rva:rva+size]).hexdigest()))
    finally:
        close(handle)

    def restore(rva, size):
        offset = pe.get_offset_from_rva(rva)
        image[rva:rva+size] = disk[offset:offset+size]

    imports = header.DATA_DIRECTORY[1]
    restore(imports.VirtualAddress, imports.Size)
    for lib in pe.DIRECTORY_ENTRY_IMPORT:
        restore(lib.struct.Name, len(lib.dll)+1)
        size = (len(lib.imports)+1)*4
        if lib.struct.OriginalFirstThunk:
            restore(lib.struct.OriginalFirstThunk, size)
        restore(lib.struct.FirstThunk, size)
        for item in lib.imports:
            if item.hint_name_table_rva is not None:
                restore(item.hint_name_table_rva, len(item.name)+3)

    # The certificate is a file-offset directory and does not map into memory.
    struct.pack_into("<II", image, header.DATA_DIRECTORY[4].get_file_offset(), 0, 0)
    struct.pack_into("<I", image, header.get_field_absolute_offset("CheckSum"), 0)
    for entry in getattr(pe, "DIRECTORY_ENTRY_DEBUG", []):
        addr = entry.struct.AddressOfRawData
        offset = pe.get_rva_from_offset(entry.struct.get_file_offset())
        struct.pack_into("<I", image, offset+24, addr)

    parsed = pefile.PE(data=bytes(image))
    def symbols(source):
        return [(lib.dll, [(i.name, i.ordinal, i.address) for i in lib.imports])
                for lib in source.DIRECTORY_ENTRY_IMPORT]
    if symbols(parsed) != symbols(pe):
        raise RuntimeError("Import validation failed")
    for sec in sections:
        sec["sha256"] = sha256(image[sec["rva"]:sec["rva"]+sec["size"]]).hexdigest()
    output = ROOT / "dump"
    output.mkdir(exist_ok=True)
    (output / "dmc4se_mem.exe").write_bytes(image)
    metadata = dict(pid=pid, exported_at=datetime.now().astimezone().isoformat(),
                    path=str(EXE), access="0x0410", base=base,
                    entry_rva=header.AddressOfEntryPoint, sha256=sha256(image).hexdigest(),
                    input_sha256=EXPECTED_SHA, import_libraries=len(parsed.DIRECTORY_ENTRY_IMPORT),
                    sections=sections)
    (output / "dump.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(f"PID={pid} base={base:#x} entry={header.AddressOfEntryPoint:#x} "
          f"sections={len(sections)} imports={metadata['import_libraries']} sha256={metadata['sha256']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pid", type=int)
    export(parser.parse_args().pid)
