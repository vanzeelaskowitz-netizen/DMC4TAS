"""Build-specific read-only checks. Only the four TAS example keys are logged."""
import struct

EXPECTED_SHA = "ea27c157c5f92e722b2ec0c9f8005a4b71607a8046109ae5a98303007921cd82"
KEYS = {"Space":0x39, "J":0x24, "K":0x25, "L":0x26}


def inspect_layout(read, base, modules):
    # A few operand checks from this build. These explain the data interpretation.
    evidence = {
        "keyboard_instance_store": (0x6b7ee6, 6), "keyboard_device_field": (0x6b8000, 6),
        "scan_to_key_index": (0x6b8557, 5), "current_and_old_first_word": (0x6b862f, 24),
        "supply_output_to_sampler": (0x6b86f8, 6), "edge_words": (0x6b8716, 46)}
    findings = {name:{"rva":hex(rv), "bytes":read(base+rv,n).hex(" ")} for name,(rv,n) in evidence.items()}
    kb = int.from_bytes(read(base+0xd8d468,4), "little")
    device = int.from_bytes(read(kb+0xa08,4), "little")
    table = read(kb+0x20,256)
    result = dict(evidence=findings, root_rva="0xd8d468", instance=hex(kb), device=hex(device),
                  key_map={name:table[scan] for name,scan in KEYS.items()})
    if device:
        vtable = int.from_bytes(read(device,4),"little")
        method = int.from_bytes(read(vtable+9*4,4),"little")
        result["slot9_owner"] = next((m["name"] for m in modules if m["base"]<=method<m["base"]+m["size"]),None)
    return result


def key_sample(read, base):
    kb = int.from_bytes(read(base+0xd8d468,4),"little")
    mapping = read(kb+0x20,256)
    # Single contiguous read for the four neighboring 256-bit bitsets.
    fields = read(kb+0x120,128)
    result = {}
    for n,name in enumerate(("current","previous","pressed","released")):
        bits = int.from_bytes(fields[32*n:32*(n+1)],"little")
        result[name] = [label for label,di in KEYS.items() if bits & (1<<mapping[di])]
    return result


def check_dx9(read, base, pe):
    sec = next(s for s in pe.sections if s["name"]==".text")
    start = sec["rva"]
    block = read(base+start,sec["virtual_size"])
    needle = bytes.fromhex("8b 86 f0 14 00 00")
    old_rva = 0x4035a9
    roots = {}
    for name,va in {"sUnit":0xe552cc,"sMediator":0xe558b8,"sMain":0xe5574c}.items():
        rel = va-base-start
        if 0 <= rel <= len(block)-4:
            roots[name] = {"va":hex(va),"section":".text","value":hex(struct.unpack_from("<I",block,rel)[0])}
    return {"source":"DX9 EnemyStepDisplay original instruction; generic field load",
            "matches_in_text":block.count(needle), "expected_at_old_rva":block[old_rva-start:old_rva-start+6]==needle,
            "old_rva":hex(old_rva),"roots":roots}

