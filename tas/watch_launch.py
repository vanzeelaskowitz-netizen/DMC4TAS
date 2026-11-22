"""Start the game with write-watch on its big allocations, so snapshots can ask the system which pages changed.

The engine creates its eight allocator arenas with VirtualAlloc about 266 ms after the process appears.
MEM_WRITE_WATCH can only be requested at that moment, so the process is suspended as soon as KernelBase
is mapped, VirtualAlloc gets a five-byte detour into a small stub, and the process is resumed. The
engine asks for each arena as VirtualAlloc(NULL, size, MEM_COMMIT, PAGE_READWRITE) (measured
2026-11-18: 48, 160, 128, 96, 64, 1, 32 and 4 MiB); exactly that pattern, for 1 MiB and more, gets
MEM_RESERVE | MEM_WRITE_WATCH added, which is the same allocation plus the watch. No file is changed and nothing runs in the game
except that stub. The detour is removed again once the arenas exist.
"""
import ctypes as ct
import os
import subprocess
import sys
import time
from ctypes import wintypes as wt
from pathlib import Path

import pefile
from keystone import KS_ARCH_X86, KS_MODE_32, Ks

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from pe_image import EXE as EXE_PATH, GAME  # noqa: E402  (found through Steam, or DMC4SE_EXE)
KERNELBASE = Path(os.environ["WINDIR"]) / "SysWOW64" / "KernelBase.dll"
PROLOGUE = bytes.fromhex("8bff558bec")        # mov edi,edi / push ebp / mov ebp,esp
FIRST_ALLOCATOR_SLOT = 0x1184D64
MINIMUM = 0x100000                            # only reservations of 1 MiB and more
LOGGED = 100                                  # large calls recorded for diagnosis
MEM_RESERVE, MEM_COMMIT, MEM_WRITE_WATCH = 0x2000, 0x1000, 0x200000

k32 = ct.WinDLL("kernel32", use_last_error=True)
psapi = ct.WinDLL("psapi", use_last_error=True)
ntdll = ct.WinDLL("ntdll")
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.VirtualAllocEx.restype = ct.c_void_p
k32.VirtualAllocEx.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_size_t, wt.DWORD, wt.DWORD]
k32.VirtualProtectEx.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_size_t, wt.DWORD, ct.POINTER(wt.DWORD)]
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_char_p, ct.c_size_t, ct.POINTER(ct.c_size_t)]
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t)]
k32.FlushInstructionCache.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_size_t]
psapi.EnumProcessModulesEx.argtypes = [wt.HANDLE, ct.POINTER(ct.c_void_p), wt.DWORD, ct.POINTER(wt.DWORD), wt.DWORD]
psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, ct.c_void_p, wt.LPWSTR, wt.DWORD]
for call in (ntdll.NtSuspendProcess, ntdll.NtResumeProcess):
    call.argtypes = [wt.HANDLE]
    call.restype = ct.c_long


class ProcessEntry(ct.Structure):
    _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD), ("heap", ct.c_size_t),
                ("module", wt.DWORD), ("threads", wt.DWORD), ("parent", wt.DWORD), ("priority", wt.LONG),
                ("flags", wt.DWORD), ("name", wt.WCHAR * 260)]


k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wt.HANDLE, ct.POINTER(ProcessEntry)]


def find_pid():
    snapshot = k32.CreateToolhelp32Snapshot(2, 0)
    try:
        entry = ProcessEntry()
        entry.size = ct.sizeof(entry)
        found = k32.Process32FirstW(snapshot, ct.byref(entry))
        while found:
            if entry.name.casefold() == GAME.casefold():
                return entry.pid
            found = k32.Process32NextW(snapshot, ct.byref(entry))
    finally:
        k32.CloseHandle(snapshot)
    return None


def read(handle, address, size):
    buffer = ct.create_string_buffer(size)
    count = ct.c_size_t()
    if not k32.ReadProcessMemory(handle, address, buffer, size, ct.byref(count)) or count.value != size:
        raise OSError(ct.get_last_error(), f"read at {address:#x}")
    return buffer.raw


def write(handle, address, data):
    count = ct.c_size_t()
    if not k32.WriteProcessMemory(handle, address, data, len(data), ct.byref(count)) or count.value != len(data):
        raise OSError(ct.get_last_error(), f"write at {address:#x}")


def module_base(handle, name):
    """Base of a 32-bit module in the target, or None while its loader list is not there yet."""
    modules = (ct.c_void_p * 512)()
    needed = wt.DWORD()
    if not psapi.EnumProcessModulesEx(handle, modules, ct.sizeof(modules), ct.byref(needed), 1):
        return None
    label = ct.create_unicode_buffer(260)
    for index in range(min(needed.value // ct.sizeof(ct.c_void_p), 512)):
        if psapi.GetModuleBaseNameW(handle, modules[index], label, 260) and label.value.casefold() == name.casefold():
            return modules[index]
    return None


def export_rva(path, name):
    image = pefile.PE(str(path), fast_load=True)
    image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
    for symbol in image.DIRECTORY_ENTRY_EXPORT.symbols:
        if symbol.name == name.encode():
            return symbol.address
    raise RuntimeError(f"{name} is not exported by {path}")


def install(handle):
    """Detour KernelBase!VirtualAlloc. Returns what is needed to verify and remove it."""
    base = module_base(handle, "KernelBase.dll")
    if base is None:
        raise RuntimeError("KernelBase is not mapped yet")
    target = base + export_rva(KERNELBASE, "VirtualAlloc")
    if read(handle, target, 5) != PROLOGUE:
        raise RuntimeError("VirtualAlloc does not start with the expected prologue")
    cave = k32.VirtualAllocEx(handle, None, 0x1000, MEM_RESERVE | MEM_COMMIT, 0x40)
    if not cave:
        raise ct.WinError(ct.get_last_error())
    counter = cave + 0x800
    seen, table = cave + 0x804, cave + 0x900
    source = f"""
        cmp dword ptr [esp + 8], {MINIMUM}
        jb skip
        push eax
        push ecx
        mov eax, dword ptr [{seen}]
        cmp eax, {LOGGED}
        jae logged
        imul ecx, eax, 16
        add ecx, {table}
        mov eax, dword ptr [esp + 0xc]
        mov dword ptr [ecx], eax
        mov eax, dword ptr [esp + 0x10]
        mov dword ptr [ecx + 4], eax
        mov eax, dword ptr [esp + 0x14]
        mov dword ptr [ecx + 8], eax
        mov eax, dword ptr [esp + 0x18]
        mov dword ptr [ecx + 12], eax
        inc dword ptr [{seen}]
    logged:
        pop ecx
        pop eax
        cmp dword ptr [esp + 4], 0
        jne skip
        cmp dword ptr [esp + 0xc], {MEM_COMMIT}
        jne skip
        cmp dword ptr [esp + 0x10], 4
        jne skip
        or dword ptr [esp + 0xc], {MEM_RESERVE | MEM_WRITE_WATCH}
        inc dword ptr [{counter}]
    skip:
        mov edi, edi
        push ebp
        mov ebp, esp
        jmp {target + 5}
    """
    code, _ = Ks(KS_ARCH_X86, KS_MODE_32).asm(source, cave)
    write(handle, cave, bytes(code))
    jump = b"\xe9" + ((cave - (target + 5)) & 0xFFFFFFFF).to_bytes(4, "little")
    old = wt.DWORD()
    if not k32.VirtualProtectEx(handle, target, 5, 0x40, ct.byref(old)):
        raise ct.WinError(ct.get_last_error())
    try:
        write(handle, target, jump)
    finally:
        k32.VirtualProtectEx(handle, target, 5, old.value, ct.byref(old))
    k32.FlushInstructionCache(handle, target, 5)
    return {"target": target, "cave": cave, "counter": counter, "seen": seen, "table": table}


def remove(handle, patch):
    old = wt.DWORD()
    if not k32.VirtualProtectEx(handle, patch["target"], 5, 0x40, ct.byref(old)):
        raise ct.WinError(ct.get_last_error())
    try:
        write(handle, patch["target"], PROLOGUE)
    finally:
        k32.VirtualProtectEx(handle, patch["target"], 5, old.value, ct.byref(old))
    k32.FlushInstructionCache(handle, patch["target"], 5)


def launch(log=print):
    """Kill any running game, start it through Steam and add write-watch to its arenas.

    Returns {"pid", "watched": number of allocations that got the flag, "note"}. A failure to patch
    is not fatal: the game still runs and snapshots fall back to reading everything.
    """
    if find_pid() is not None:
        subprocess.run(["taskkill", "/F", "/IM", GAME], capture_output=True)
        deadline = time.monotonic() + 15
        while find_pid() is not None and time.monotonic() < deadline:
            time.sleep(.1)
        time.sleep(2)   # let Steam register that the old process is gone
    pid = None
    # Start the executable ourselves. With SteamAppId / SteamGameId in its environment the Steam API
    # attaches to the running Steam client without asking Steam to launch the game (verified
    # 2026-11-18: Steam registers the process, overlay included). This avoids Steam's own launch
    # queue, which can hang on "CheckShaderDepotManifest" when the network is poor, and the process
    # id is known at once, so the write-watch patch is never late.
    log("正在启动游戏…")
    try:
        if not EXE_PATH.is_file():
            raise OSError("找不到游戏：Steam 里没有找到鬼泣4特别版。请把环境变量 DMC4SE_EXE 设成 "
                          "DevilMayCry4SpecialEdition.exe 的完整路径后再试")
        environment = dict(os.environ, SteamAppId="329050", SteamGameId="329050")
        # Fully detached: no inherited console or pipe handles, so closing the window that ran this
        # script (or a caller waiting for its output) never touches the game.
        process = subprocess.Popen([str(EXE_PATH)], cwd=str(EXE_PATH.parent), env=environment,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, close_fds=True,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                                   | subprocess.DETACHED_PROCESS | 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB
        pid = process.pid
    except OSError as error:
        log(f"直接启动失败（{error}），改由 Steam 启动…")
    if pid is None:
        for attempt in range(4):
            os.startfile("steam://rungameid/329050")
            started = time.perf_counter()
            while pid is None and time.perf_counter() - started < 25:
                pid = find_pid()
                if pid is None:
                    time.sleep(.001)
            if pid is not None:
                break
    if pid is None:
        raise TimeoutError("游戏没有启动起来。请看看 Steam 是否弹了提示框。")
    born = time.perf_counter()
    handle = None
    while not handle and time.perf_counter() - born < 5:
        handle = k32.OpenProcess(0x1F0FFF, False, pid)
        if not handle:
            time.sleep(.001)
    if not handle:
        return {"pid": pid, "watched": 0, "note": "could not open the process"}
    result = {"pid": pid, "watched": 0, "note": ""}
    patch = None
    try:
        while module_base(handle, "KernelBase.dll") is None:
            if time.perf_counter() - born > 5:
                raise RuntimeError("KernelBase never appeared in the module list")
            time.sleep(.0005)
        if ntdll.NtSuspendProcess(handle) != 0:
            raise RuntimeError("Could not suspend the new process")
        try:
            at = (time.perf_counter() - born) * 1000
            try:
                already = int.from_bytes(read(handle, FIRST_ALLOCATOR_SLOT, 4), "little")
            except OSError:
                already = 0
            patch = install(handle)
            result["note"] = f"patched {at:.0f} ms after the process appeared"
            if already:
                result["note"] += "; allocator slot was already non-zero before the patch"
        finally:
            ntdll.NtResumeProcess(handle)
        # Wait for the arenas, then take the detour out again.
        while time.perf_counter() - born < 30:
            try:
                if int.from_bytes(read(handle, FIRST_ALLOCATOR_SLOT, 4), "little"):
                    break
            except OSError:
                pass
            time.sleep(.002)
        time.sleep(1.0)
        result["watched"] = int.from_bytes(read(handle, patch["counter"], 4), "little")
        count = min(int.from_bytes(read(handle, patch["seen"], 4), "little"), LOGGED)
        rows = read(handle, patch["table"], count * 16)
        # (address asked for, size, allocation type, protection) of every large VirtualAlloc so far
        result["calls"] = [tuple(int.from_bytes(rows[i * 16 + k:i * 16 + k + 4], "little") for k in (0, 4, 8, 12))
                           for i in range(count)]
        ntdll.NtSuspendProcess(handle)
        try:
            remove(handle, patch)
        finally:
            ntdll.NtResumeProcess(handle)
    except Exception as error:
        result["note"] = f"write-watch not installed: {error}"
    finally:
        k32.CloseHandle(handle)
    log(f"游戏已启动（PID {pid}），带写入监视的内存申请 {result['watched']} 次。{result['note']}")
    return result


if __name__ == "__main__":
    print(launch())
    sys.exit(0)
