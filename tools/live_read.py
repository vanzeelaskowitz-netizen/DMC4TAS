"""Read-only evidence for the installed x86 DMC4SE. No raw code dump."""
from collections import Counter
import ctypes as ct
from ctypes import wintypes as wt
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import struct
import time
import uuid

from pe_image import EXE, Image, u32
from keyboard_probe import EXPECTED_SHA, inspect_layout, key_sample, check_dx9

LOG = Path(__file__).resolve().parents[1] / "logs"
DLL = ct.WinDLL("kernel32", use_last_error=True)


def api(name, result, *args):
    func = getattr(DLL, name)
    func.argtypes, func.restype = args, result
    return func


open_process = api("OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
close = api("CloseHandle", wt.BOOL, wt.HANDLE)
read_process = api("ReadProcessMemory", wt.BOOL, wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t))
query_name = api("QueryFullProcessImageNameW", wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD))


def read(handle, address, size):
    data = ct.create_string_buffer(size)
    count = ct.c_size_t()
    if not read_process(handle, address, data, size, ct.byref(count)) or count.value != size:
        raise OSError(ct.get_last_error(), f"read at {address:#x}, wanted {size}, got {count.value}")
    return data.raw


def rvas(block, pattern, start):
    return [start+m.start() for m in re.finditer(re.escape(pattern), block)]


def compare(handle, base, pe, s):
    start = s["rva"]
    size = s["virtual_size"] or s["raw_size"]
    raw = s["raw_offset"]
    expected = bytearray(pe.data[raw:raw+min(size,s["raw_size"])]).ljust(size, b"\0")
    delta = base-pe.base
    for kind, rv in pe.relocations():
        if delta and start <= rv < start+size:
            if kind != 3:
                raise ValueError("Unknown non-HIGHLOW relocation in executable section")
            pos = rv-start
            struct.pack_into("<I",expected,pos,(u32(expected,pos)+delta)&0xffffffff)
    difference = total = pages = changed_pages = 0
    first = last = None
    digest = sha256()
    frequencies = Counter()
    missing = []
    for pos in range(0, size, 4096):
        disk = expected[pos:pos+4096]
        try:
            live = read(handle, base+start+pos, len(disk))
        except OSError as err:
            missing.append([hex(start+pos),err.errno])
            continue
        digest.update(live)
        frequencies.update(live)
        pages += 1
        total += len(live)
        if disk != live:
            changed_pages += 1
            offsets = [i for i,(x,y) in enumerate(zip(disk,live)) if x!=y]
            if first is None:
                first = hex(start+pos+offsets[0])
            last = hex(start+pos+offsets[-1])
            difference += len(offsets)
    entropy = sum(-n/total*math.log2(n/total) for n in frequencies.values())
    padding = s["raw_size"]-size
    pad_report = None
    if padding > 0:
        try:
            tail = read(handle,base+start+size,padding)
            disk_tail = pe.data[raw+size:raw+size+padding]
            pad_report = {"bytes":padding,"different":sum(a!=b for a,b in zip(tail,disk_tail))}
        except OSError as err:
            pad_report = {"bytes":padding,"error":str(err)}
    return dict(name=s["name"], virtual_bytes=size, read_bytes=total, different=difference, raw_padding=pad_report,
                equal=total-difference, pages=pages, different_pages=changed_pages, first_diff_rva=first,
                last_diff_rva=last, unread_pages=missing, normalized_sha256=sha256(expected).hexdigest(),
                live_sha256=digest.hexdigest() if size==total else None, live_entropy=round(entropy,6))


def inspect_keys(handle, base, pe):
    patterns = {"GUID_SysKeyboard":uuid.UUID("6F1D2B61-D5A0-11CF-BFC7-444553540000").bytes_le,
                "sKeyboard":b"sKeyboard\0", "sPad":b"sPad\0", "sInput":b"sInput\0",
                "c_dfDIKeyboard_prefix":struct.pack("<5I",24,16,2,256,256)}
    hits = {key:[] for key in patterns}
    sec = next(s for s in pe.sections if s["name"]==".text")
    code = read(handle,base+sec["rva"],sec["virtual_size"])
    for s in pe.sections:
        if s["name"] not in (".text",".rdata",".data","_RDATA"):
            continue
        block = code if s==sec else read(handle,base+s["rva"],s["virtual_size"])
        for key,pattern in patterns.items():
            hits[key].extend(rvas(block, pattern, s["rva"]))
    result = {}
    for key,locations in hits.items():
        result[key] = [{"rva":hex(rv), "text_absolute_refs":[hex(x) for x in
                       rvas(code,struct.pack("<I",base+rv),sec["rva"])]} for rv in locations]
    for mod in pe.imports():
        for item in mod["entries"]:
            key = item["symbol"]
            if key in ("GetAsyncKeyState","DirectInput8Create"):
                rv=item["iat_rva"]
                result[key] = {"iat_rva":hex(rv),"resolved":hex(u32(read(handle,base+rv,4),0)),
                               "text_absolute_refs":[hex(x) for x in rvas(code,struct.pack("<I",base+rv),sec["rva"])]}
    # Candidate COM idiom, not disassembly or proof of keyboard consumption.
    calls = []
    for rv in rvas(code,bytes.fromhex("68 00 01 00 00"),sec["rva"]):
        pos = rv-sec["rva"]+5
        m = re.search(rb"\xff[\x50-\x53\x55-\x57]\x24",code[pos:pos+24])
        if m:
            calls.append(dict(size_arg_rva=hex(rv),indirect_call_rva=hex(sec["rva"]+pos+m.start())))
    result["size_256_and_vtable_slot_9"] = calls
    return result


def watch(handle, base, size, keyboard_verified):
    # Field offsets from the September 2026 state block, interpreted as data only.
    if size != 15228928:
        return {"status":"unknown module size","samples":[]}
    fields = {
        "missionNumber":(0xD54B34,0x150), "missionTime":(0xD54B34,0x264), "cutscene":(0xD54B34,0x1A4),
        "playerPos":(0xD54B34,0x24), "LoadingScreen":(0xD54B34,0x14C), "m1LRT":(0xD54B34,0x140),
        "bloodyPalace":(0xD8D448,0x3830,0x90), "boss":(0xD8D448,0x3834,0x50,0),
        "doorsplitter":(0xD8D448,0x3830,0x88), "menu":(0xD8D450,0x2B4), "menu4":(0xD8D450,0x40),
        "ngPlusReset":(0xD84C78,4), "ngStart":(0xD8D450,0x1C4)}
    samples = []
    started = time.perf_counter()
    for i in range(21):
        time.sleep(max(0,started+i*0.25-time.perf_counter()))
        values = {}
        for name,(root,*offsets) in fields.items():
            address = base+root
            try:
                for offset in offsets:
                    pointer = u32(read(handle,address,4),0)
                    if pointer < 0x10000 or pointer+offset > 0xfffffffc:
                        raise ValueError(f"invalid pointer {pointer:#x} at {address:#x}")
                    address = pointer+offset
                values[name] = {"va":hex(address),"i32":struct.unpack("<i",read(handle,address,4))[0]}
            except (ValueError,OSError) as err:
                values[name] = {"error":str(err)}
        row = {"seconds":round(time.perf_counter()-started,5),"values":values}
        if keyboard_verified:
            try:
                row["keys"] = key_sample(lambda addr,n: read(handle,addr,n),base)
            except OSError as err:
                row["keys"] = {"error":str(err)}
        samples.append(row)
    return dict(fields={k:[hex(x) for x in v] for k,v in fields.items()}, samples=samples)


def main():
    proc = json.loads((LOG/"process.json").read_text(encoding="utf-8-sig"))
    module = next(m for m in proc["modules"] if Path(m["path"])==EXE)
    base = module["base"]
    handle = open_process(0x410, False, proc["pid"])
    if not handle:
        raise ct.WinError(ct.get_last_error())
    try:
        buffer = ct.create_unicode_buffer(32768)
        length = wt.DWORD(len(buffer))
        if not query_name(handle,0,buffer,ct.byref(length)) or Path(buffer.value)!=EXE:
            raise RuntimeError("Target path mismatch")
        pe = Image()
        known = sha256(pe.data).hexdigest()==EXPECTED_SHA
        def fetch(addr, n):
            return read(handle,addr,n)
        try:
            layout = inspect_layout(fetch,base,proc["modules"]) if known else None
        except OSError as err:
            layout = {"error":str(err)}
        report = dict(pid=proc["pid"], start=datetime.now().astimezone().isoformat(), path=buffer.value,
                      base=hex(base), relocation_delta=base-pe.base, access="0x0410",
                      executable_sections=[compare(handle,base,pe,s) for s in pe.sections if "X" in s["permissions"]],
                      keyboard=inspect_keys(handle,base,pe), dx9=check_dx9(fetch,base,pe), layout=layout,
                      telemetry=watch(handle,base,module["size"],known))
        (LOG/"live.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print("Read-only scan and sampling complete:", LOG/"live.json")
    finally:
        close(handle)


if __name__ == "__main__":
    main()


