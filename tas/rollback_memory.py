"""Windows process I/O for snapshots taken at the DMC4SE frame gate."""
from contextlib import contextmanager
import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import dataclass

from concurrent.futures import ThreadPoolExecutor

from live_read import api, close, open_process, read, read_process


PAGE = 4096
MEM_COMMIT, MEM_PRIVATE, MEM_IMAGE = 0x1000, 0x20000, 0x1000000
PAGE_READWRITE, PAGE_WRITECOPY = 4, 8
LIMIT = 1 << 32


class MemoryInfo(ct.Structure):
    _fields_ = [
        ("base", ct.c_void_p), ("allocation", ct.c_void_p), ("allocation_protect", wt.DWORD),
        ("size", ct.c_size_t), ("state", wt.DWORD), ("protect", wt.DWORD), ("kind", wt.DWORD),
    ]


class ThreadInfo(ct.Structure):
    _fields_ = [
        ("size", wt.DWORD), ("usage", wt.DWORD), ("tid", wt.DWORD), ("owner", wt.DWORD),
        ("base_priority", wt.LONG), ("delta_priority", wt.LONG), ("flags", wt.DWORD),
    ]


query_region = api("VirtualQueryEx", ct.c_size_t, wt.HANDLE, ct.c_void_p,
                   ct.POINTER(MemoryInfo), ct.c_size_t)
write_process = api("WriteProcessMemory", wt.BOOL, wt.HANDLE, ct.c_void_p, ct.c_char_p,
                    ct.c_size_t, ct.POINTER(ct.c_size_t))
thread_snapshot = api("CreateToolhelp32Snapshot", wt.HANDLE, wt.DWORD, wt.DWORD)
thread_first = api("Thread32First", wt.BOOL, wt.HANDLE, ct.POINTER(ThreadInfo))
thread_next = api("Thread32Next", wt.BOOL, wt.HANDLE, ct.POINTER(ThreadInfo))
open_thread = api("OpenThread", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
suspend_thread = api("SuspendThread", wt.DWORD, wt.HANDLE)
resume_thread = api("ResumeThread", wt.DWORD, wt.HANDLE)
thread_process = api("GetProcessIdOfThread", wt.DWORD, wt.HANDLE)
thread_exit_code = api("GetExitCodeThread", wt.BOOL, wt.HANDLE, ct.POINTER(wt.DWORD))
INVALID_HANDLE = ct.c_void_p(-1).value


@contextmanager
def session_owner():
    create = api("CreateMutexW", wt.HANDLE, ct.c_void_p, wt.BOOL, wt.LPCWSTR)
    mutex = create(None, False, "Local\\DMC4SE-TAS-24329835")
    error = ct.get_last_error()
    if not mutex:
        raise ct.WinError(error)
    try:
        if error == 183:
            raise RuntimeError("A DMC4SE TAS controller already owns this desktop")
        yield
    finally:
        close(mutex)


@dataclass(frozen=True)
class Region:
    base: int
    end: int
    allocation: int
    state: int
    protect: int
    kind: int

    @property
    def writable(self):
        return self.state == MEM_COMMIT and self.protect in (PAGE_READWRITE, PAGE_WRITECOPY)


def thread_ids(pid):
    snapshot = thread_snapshot(4, 0)
    if snapshot == INVALID_HANDLE:
        raise ct.WinError(ct.get_last_error())
    try:
        entry = ThreadInfo()
        entry.size = ct.sizeof(entry)
        found = thread_first(snapshot, ct.byref(entry))
        result = set()
        while found:
            if entry.owner == pid:
                result.add(entry.tid)
            entry.size = ct.sizeof(entry)
            found = thread_next(snapshot, ct.byref(entry))
        if ct.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ct.WinError(ct.get_last_error())
        return result
    finally:
        close(snapshot)


class ProcessMemory:
    def __init__(self, pid, *, write=False):
        if ct.sizeof(ct.c_void_p) != 8:
            raise RuntimeError("Run snapshots with the project's 64-bit Python")
        self.pid = pid
        # QUERY_INFORMATION | VM_READ | SUSPEND_RESUME, plus VM_WRITE | VM_OPERATION for restores.
        self.handle = open_process(0xC10 | (0x28 if write else 0), False, pid)
        if not self.handle:
            raise ct.WinError(ct.get_last_error())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        close(self.handle)

    def region(self, address):
        info = MemoryInfo()
        if not query_region(self.handle, address, ct.byref(info), ct.sizeof(info)):
            raise OSError(ct.get_last_error(), f"VirtualQueryEx at {address:#x}")
        base = info.base or 0
        return Region(base, base + info.size, info.allocation or 0, info.state,
                      info.protect, info.kind)

    def regions(self):
        address = 0
        while address < LIMIT:
            try:
                region = self.region(address)
            except OSError as error:
                if error.errno == 87:  # end of the target's address space
                    break
                raise
            yield region
            address = region.end

    def read(self, address, size):
        return read(self.handle, address, size)

    def u32(self, address):
        return int.from_bytes(self.read(address, 4), "little")

    def default_blocks(self, descriptor):
        """Match MtDefaultAllocator's 16-byte VirtualAlloc header and both live byte totals."""
        blocks = []
        for region in self.regions():
            if region.kind != MEM_PRIVATE or not region.writable or region.base != region.allocation:
                continue
            header = self.read(region.base, 16)
            size = int.from_bytes(header[12:], "little")
            if header[:12] == bytes(12) and size and (size + 15 + PAGE) // PAGE * PAGE == region.end - region.base:
                blocks.append((region.base + 16, size))
        total = sum(size for _, size in blocks)
        if total != self.u32(descriptor + 4) or total != self.u32(descriptor + 12):
            raise RuntimeError("DX9 default allocator ownership could not be reconciled")
        return blocks

    def read_spans(self, ranges, workers=6, chunk=8 << 20):
        """Read many [start, end) ranges at once, in parallel, straight into bytearrays.

        ReadProcessMemory releases the GIL, so several threads really do copy at the same time;
        reading the whole 561 MB image this way is roughly three times faster than one by one.
        """
        buffers = [bytearray(end - start) for start, end in ranges]
        jobs = [(index, offset, min(chunk, len(buffer) - offset))
                for index, buffer in enumerate(buffers) for offset in range(0, len(buffer), chunk)]

        def copy(job):
            index, offset, size = job
            target = (ct.c_char * size).from_buffer(buffers[index], offset)
            count = ct.c_size_t()
            address = ranges[index][0] + offset
            if not read_process(self.handle, address, target, size, ct.byref(count)) or count.value != size:
                raise OSError(ct.get_last_error(), f"read at {address:#x}, wanted {size}, got {count.value}")

        if len(jobs) == 1:
            copy(jobs[0])
        else:
            list(POOL.map(copy, jobs))
        return buffers

    def write(self, address, data):
        if not isinstance(data, bytes):
            data = bytes(data)
        # Every write made by this controller is remembered, so the page-tracking snapshot engine
        # knows about changes that did not come from the game (healing, restores, experiments).
        WRITTEN.append((address, len(data)))
        count = ct.c_size_t()
        # One system call. WriteProcessMemory wraps it in a query, a protection change and a cache
        # flush and cost about 170 microseconds per call; a restore makes over a hundred writes.
        if nt_write(self.handle, address, data, len(data), ct.byref(count)) == 0 and count.value == len(data):
            return
        if not write_process(self.handle, address, data, len(data), ct.byref(count)) or count.value != len(data):
            raise OSError(ct.get_last_error(), f"write at {address:#x}, wanted {len(data)}, got {count.value}")

    @contextmanager
    def quiescent(self, gate_tid):
        """Suspend the target only while native memory I/O is in progress."""
        # One call for the whole process. Walking the system thread list and suspending the game's
        # ~100 threads one by one cost about 55 ms per snapshot or restore (measured 2026-11-18).
        if nt_suspend(self.handle) == 0:
            try:
                yield 0
            finally:
                if nt_resume(self.handle) != 0:
                    raise RuntimeError("Failed to resume the game process")
            return
        handles = {}
        try:
            for _ in range(8):
                live = thread_ids(self.pid)
                if gate_tid not in live:
                    raise RuntimeError("The frame gate thread exited")
                new = live - handles.keys()
                if not new:
                    break
                for tid in sorted(new, key=lambda tid: tid != gate_tid):
                    handle = open_thread(0x802, False, tid)  # SUSPEND_RESUME, QUERY_LIMITED_INFORMATION
                    if not handle:
                        error = ct.get_last_error()
                        if error == 87:
                            continue
                        raise ct.WinError(error)
                    if thread_process(handle) != self.pid:
                        close(handle)
                        continue
                    if suspend_thread(handle) == 0xffffffff:
                        error = ct.get_last_error()
                        code = wt.DWORD()
                        exited = thread_exit_code(handle, ct.byref(code)) and code.value != 259
                        close(handle)
                        if exited:
                            continue
                        raise ct.WinError(error)
                    handles[tid] = handle
            else:
                raise RuntimeError("Thread list did not stabilize")
            yield len(handles)
        finally:
            errors = []
            for tid, handle in reversed(handles.items()):
                if resume_thread(handle) == 0xffffffff:
                    error = ct.get_last_error()
                    code = wt.DWORD()
                    if not thread_exit_code(handle, ct.byref(code)) or code.value == 259:
                        errors.append((tid, error))
                close(handle)
            if errors:
                raise RuntimeError(f"Failed to resume target threads: {errors}")


nt_suspend = ct.WinDLL("ntdll").NtSuspendProcess
nt_resume = ct.WinDLL("ntdll").NtResumeProcess
for _call in (nt_suspend, nt_resume):
    _call.argtypes = [wt.HANDLE]
    _call.restype = ct.c_long
nt_write = ct.WinDLL("ntdll").NtWriteVirtualMemory
nt_write.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_char_p, ct.c_size_t, ct.POINTER(ct.c_size_t)]
nt_write.restype = ct.c_long
POOL = ThreadPoolExecutor(6)   # kept for the life of the process: creating one per read cost 2 ms
WRITTEN = []   # (address, size) of every ProcessMemory.write in this process, drained by SnapshotEngine
COARSE = 64 * PAGE
memcmp = ct.cdll.msvcrt.memcmp
memcmp.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_size_t]
memcmp.restype = ct.c_int


def changed_runs(before, after):
    """Contiguous changed pages within one span, as half-open byte offsets.

    Two levels: 256 KiB blocks are compared first (a single memcmp each), and only blocks that
    differ are walked page by page. Most of a 561 MB image is identical, so this is what keeps the
    comparison at memory speed instead of 137,000 Python-level page compares.
    """
    size = len(before)
    if size != len(after):
        raise ValueError("Memory span changed size")
    if not size:
        return
    # C memcmp on the buffers' own storage. Comparing memoryview slices looked equivalent but ran
    # at about 0.5 GB/s (990 ms for one image, measured 2026-11-18); memcmp does it in tens of ms.
    pins = []

    def address(buffer):
        if isinstance(buffer, bytearray):
            pins.append((ct.c_char * len(buffer)).from_buffer(buffer))
            return ct.addressof(pins[-1])
        pins.append(buffer)
        return ct.cast(ct.c_char_p(buffer), ct.c_void_p).value

    old, new = address(before), address(after)
    start = None
    for block in range(0, size, COARSE):
        stop = min(block + COARSE, size)
        if memcmp(old + block, new + block, stop - block) == 0:
            if start is not None:
                yield start, block
                start = None
            continue
        for offset in range(block, stop, PAGE):
            if memcmp(old + offset, new + offset, min(PAGE, size - offset)) != 0:
                if start is None:
                    start = offset
            elif start is not None:
                yield start, offset
                start = None
    if start is not None:
        yield start, size
