"""Frame-gate checkpoints and input-aware navigation for DMC4SE."""
from bisect import bisect_right
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from time import perf_counter

from rollback_memory import MEM_IMAGE, MEM_PRIVATE, PAGE, WRITTEN, ProcessMemory, changed_runs
from timeline import Timeline, read_script


DEFAULT_INTERVAL = 120
DEFAULT_LIMIT = 120  # one full image plus deltas of about 3.5 MB each: see Snapshot
DATA_RVA, DATA_SIZE = 0xB8F000, 0x25F3C8
NEAR = 16            # frames between a step-back target and the extra checkpoint left before it
GAP = 8 * PAGE       # tracked restore: stretches closer than this are written as one
DENSE_INTERVAL = 4   # with page tracking: checkpoint spacing while stepping near the target
DENSE_SPAN = 120     # ... within this many frames of the target (one whole coarse interval)
DENSE_LIMIT = 160    # most recently made checkpoints kept regardless of the coarse grid
CLOCK_KEYS = ("frame", "ticks", "clockStart", "millisStart", "last")
DEFAULT_PROFILE = "fields"
# What save / back / goto use. Only the whole-heap snapshot replays deterministically (2026-11-18).
ROUTE_PROFILE = "heaps"
SNAPSHOT_PROFILES = (DEFAULT_PROFILE, "actors", "actor-pool", "heaps")
# The eight MtScalableAllocator pointers (Global, Temp, Resource, System, Unit, Area, Array/String,
# Collision) and the two engine singletons that live outside every arena (sApp, sDevil4Main).
ALLOCATOR_SLOTS = (0x1184D64, 0x1184D74, 0x1184D88, 0x1184D8C, 0x1184D90, 0x1184D94, 0x1184D6C, 0x1184D70)
OUTSIDE_SINGLETONS = (0x118D45C, 0x118D460)
ALLOCATOR_SIZE = 0x4D0
HP_FIELDS = (0x1a50, 0x1a58, 0x1a60, 0x1a70, 0x1a80, 0x2c30, 0x2d80, 0x3e38, 0x85e8)


def describe_state(row):
    """Use game-side values and keep field names for first-divergence reports."""
    combat = row["combat"]
    if not combat or not combat["player"] or not combat["enemiesComplete"]:
        raise RuntimeError(f"Battle sample missing at frame {row['number']}")
    player_fields = ("position", "action", "motion", "motionFrame", "hp", "stepCooldown", "hitStopTimer")
    enemy_fields = ("position", "action", "phase", "motion", "motionFrame", "hp", "hitStopTimer")
    result = {"player." + key: combat["player"][key] for key in player_fields}
    for enemy in sorted(combat["enemies"], key=lambda item: int(item["pointer"], 16)):
        for key in enemy_fields:
            result[f"enemy[{enemy['pointer']}].{key}"] = enemy[key]
    result["rng"] = combat["rng"]
    return result


def fingerprint(state):
    payload = json.dumps(state, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Span:
    name: str
    address: int
    end: int
    allocation: int
    kind: int
    protect: int


@dataclass(eq=False)
class Snapshot:
    """A full memory image, or the pages that differ from a full one (`base`).

    A whole-heap image is 561 MB but only a few MB change between nearby frames, so every snapshot
    after the first is stored as a delta against the first. That is what lets a timeline keep
    dozens of checkpoints instead of four.
    """
    clocks: dict
    identity: tuple
    spans: tuple
    base: object            # None for a full image, else the full Snapshot this one is stored against
    data: tuple             # full: bytes per span; delta: ((offset, bytes), ...) per span
    milliseconds: float
    threads: int
    pages: tuple = None     # page-tracked delta: one {page offset: bytes} per span instead of `data`

    @property
    def frame(self):
        return self.clocks["frame"]

    def runs(self, index):
        """The stored differences of one span as sorted (offset, bytes) pairs."""
        if self.pages is not None:
            return sorted(self.pages[index].items())
        return self.data[index]

    def page_map(self, index):
        """The stored differences of one span as {page offset: bytes}."""
        if self.pages is not None:
            return self.pages[index]
        pages = {}
        for offset, chunk in self.data[index]:
            for start in range(0, len(chunk), PAGE):
                pages[offset + start] = bytes(chunk[start:start + PAGE])
        return pages

    @property
    def bytes(self):
        if self.base is None:
            return sum(len(item) for item in self.data)
        return sum(len(chunk) for index in range(len(self.spans)) for _, chunk in self.runs(index))

    def patches(self, current):
        """(address, wanted, present) for every stretch where the game's memory differs from this snapshot.

        `current` is the game's memory now, one buffer per span. For a delta snapshot nothing is
        rebuilt in full: the candidates are the pages where the game differs from the base image
        plus the pages this snapshot stores, and only those are assembled and compared.
        """
        full = self.base is None
        images = self.data if full else self.base.data
        for index, (span, basis, now) in enumerate(zip(self.spans, images, current)):
            intervals = list(changed_runs(basis, now))
            if full:
                for a, b in intervals:
                    yield span.address + a, bytes(basis[a:b]), bytes(now[a:b])
                continue
            runs = self.runs(index)
            intervals += [(offset, offset + len(chunk)) for offset, chunk in runs]
            if not intervals:
                continue
            intervals.sort()
            merged = [list(intervals[0])]
            for a, b in intervals[1:]:
                if a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            position = 0
            for a, b in merged:
                wanted = bytearray(basis[a:b])
                while position < len(runs) and runs[position][0] + len(runs[position][1]) <= a:
                    position += 1
                cursor = position
                while cursor < len(runs) and runs[cursor][0] < b:
                    offset, chunk = runs[cursor]
                    wanted[offset - a:offset - a + len(chunk)] = chunk
                    cursor += 1
                present = now[a:b]
                if wanted != present:
                    yield span.address + a, bytes(wanted), bytes(present)

    @property
    def blocks(self):
        if self.base is None:
            return tuple(zip(self.spans, self.data))
        result = []
        for index, (span, basis) in enumerate(zip(self.spans, self.base.data)):
            runs = self.runs(index)
            if runs:
                image = bytearray(basis)
                for offset, chunk in runs:
                    image[offset:offset + len(chunk)] = chunk
                basis = bytes(image)
            result.append((span, basis))
        return tuple(result)


def pointer_fields(gate):
    combat = gate["combat"]
    return tuple(sorted(int(entry["pointer"], 16) for entry in (combat["player"], *combat["enemies"])))


def identity(gate, profile=DEFAULT_PROFILE):
    fixed = (*[gate[k] for k in ("base", "clock", "keyboard", "renderer", "swapChain", "area", "tid")],
             gate.get("sceneEpoch"), *gate["scene"])
    # A whole-heap snapshot brings its own actors back, so the actor set may differ by then.
    return fixed if profile == "heaps" else (*fixed, *pointer_fields(gate))


def object_size(memory, address):
    getter = memory.u32(memory.u32(address) + 16)
    code = memory.read(getter, 6)
    if code[0] != 0xb8 or code[5] != 0xc3:
        raise RuntimeError(f"Unrecognized object type getter at {getter:#x}")
    dti = int.from_bytes(code[1:5], "little")
    size = (memory.u32(dti + 0x18) & 0x7fffff) * 4
    if not 4 <= size <= 0x1000000:
        raise RuntimeError(f"Invalid engine object size {size:#x} at {address:#x}")
    return size


def logic_fields(memory, gate, at):
    """Known scalar, input and clock fields. Avoid replaying allocator links."""
    clock = int(gate["clock"], 16)
    keyboard = int(gate["keyboard"], 16)
    mediator, rates = memory.u32(at(0x1154B34)), memory.u32(at(0x1164B50))
    player = int(gate["combat"]["player"]["pointer"], 16)
    yield (".data RNG", at(0x1150A40), at(0x1150A40) + 16, MEM_IMAGE)
    objects = [
        ("clock", clock, [(0x4c, 0x28), (0x280c0, 0x70)]),
        ("sKeyboard", keyboard, [(0x120, 0x80)]),
        ("sMediator", mediator, [(0x5ec, 2)]),
        ("sWorkRate", rates, [(0x20, 4), (0x28, 4), (0x38, 4)]),
        ("player", player, [
            (0x1c, 4), (0x40, 0x20), (0x4e4, 2), (0x530, 4), (0xf10, 12),
            (0x1008, 4), (0x1010, 4), (0x11f0, 12), (0x1531, 1), (0x1888, 12),
            (0x1920, 12), (0x1938, 1), (0x193c, 1), (0x1940, 1), (0x19a0, 8),
            (0x1a00, 4), (0x1a64, 4), (0x1b00, 4), (0x2448, 4), (0x2504, 4),
            (0x25e4, 4), (0x25f5, 1), (0x3048, 1),
        ]),
        ("grounded", memory.u32(player + 0x2470), [(0x34, 1)]),
    ]
    for entry in gate["combat"]["enemies"]:
        actor = int(entry["pointer"], 16)
        size = object_size(memory, actor)
        offsets = [offset for offset in HP_FIELDS
                   if offset + 0x34 <= size and memory.u32(actor + offset) == at(0xf2d184)]
        if len(offsets) != 1:
            raise RuntimeError(f"Enemy {actor:#x} has no unique HP field")
        fields = [(4, 2), (0x1c, 4), (0x40, 12), (0x4e4, 2), (0x530, 4),
                  (0x11d0, 1), (0x1944, 5), (offsets[0] + 0x30, 4)]
        objects.append(("enemy", actor, fields))
    for name, addr, fields in objects:
        for off, size in fields:
            yield name, addr + off, addr + off + size, MEM_PRIVATE


def unit_heap(memory, at, actors):
    descriptor = memory.u32(at(0x1184D90))
    low = memory.u32(descriptor + 0x5c)
    high = memory.u32(descriptor + 0x60)
    if (memory.u32(descriptor) != at(0xEF1208) or high - low != 64 << 20
            or not all(low <= actor < high for actor in actors)):
        raise RuntimeError("Actor allocation does not match the MT Framework Unit heap")
    return low, high


def heap_ranges(memory, base, at):
    """Whole engine state: .data, every allocator arena with its descriptor, and the outside singletons.

    Restoring objects without the allocator's own bookkeeping crashed in the heap unlink routine
    (2026-11-18), so the arenas and their descriptors always travel together.
    """
    yield ".data", base + DATA_RVA, base + DATA_RVA + DATA_SIZE, MEM_IMAGE
    writable = [region for region in memory.regions() if region.writable and region.kind == MEM_PRIVATE]
    for slot in ALLOCATOR_SLOTS:
        descriptor = memory.u32(at(slot))
        low, high = memory.u32(descriptor + 0x5c), memory.u32(descriptor + 0x60)
        if memory.u32(descriptor) != at(0xEF1208) or not 0 < low < high or high - low > 256 << 20:
            raise RuntimeError(f"Unexpected allocator descriptor at {descriptor:#x}")
        yield "allocator", descriptor, descriptor + ALLOCATOR_SIZE, MEM_PRIVATE
        for region in writable:
            if region.base < high and region.end > low:
                yield "arena", max(region.base, low), min(region.end, high), MEM_PRIVATE
    for slot in OUTSIDE_SINGLETONS:
        singleton = memory.u32(at(slot))
        yield "singleton", singleton, singleton + object_size(memory, singleton), MEM_PRIVATE


def snapshot_spans(memory, gate, profile):
    base = int(gate["base"], 16)
    at = lambda address: base + address - 0x400000
    if profile == DEFAULT_PROFILE:
        return checked_spans(memory, logic_fields(memory, gate, at), base)
    if profile == "heaps":
        return checked_spans(memory, heap_ranges(memory, base, at), base)
    if profile not in SNAPSHOT_PROFILES:
        raise ValueError(f"Unknown snapshot profile {profile!r}")
    actors = pointer_fields(gate)
    objects = [
        ("clock", int(gate["clock"], 16)),
        ("keyboard", int(gate["keyboard"], 16)),
        ("sArea", int(gate["area"], 16)),
        ("aGame", int(gate["scene"][1], 16)),
        ("aMission", int(gate["scene"][2], 16)),
        ("aRoom", int(gate["scene"][3], 16)),
        ("sMediator", memory.u32(at(0x1154B34))),
        ("sUnit", memory.u32(at(0x118D450))),
        ("sWorkRate", memory.u32(at(0x1164B50))),
        ("sCamera", memory.u32(at(0x118D474))),
    ]
    ranges = [(".data", base + DATA_RVA, base + DATA_RVA + DATA_SIZE, MEM_IMAGE)]
    for name, address in objects:
        size = object_size(memory, address)
        if name == "sUnit" and profile == "actors":
            # Stage one stores the dispatch lists, without the embedded worker scratch.
            size = 0x300
        ranges.append((name, address, address + size, MEM_PRIVATE))
    if profile == "actors":
        for actor in actors:
            ranges.append(("actor", actor, actor + object_size(memory, actor), MEM_PRIVATE))
    else:
        low, high = unit_heap(memory, at, actors)
        for region in memory.regions():
            if region.writable and low <= region.base < high:
                ranges.append(("Unit heap", region.base, min(region.end, high), MEM_PRIVATE))
    return checked_spans(memory, ranges, base)


def checked_spans(memory, ranges, base):
    spans = []
    for name, start, end, kind in sorted(ranges, key=lambda item: item[1]):
        while start < end:
            region = memory.region(start)
            if not region.writable or region.kind != kind or (kind == MEM_IMAGE and region.allocation != base):
                raise RuntimeError(f"Unexpected memory mapping for {name} at {start:#x}: {region}")
            last = min(region.end, end)
            if spans and spans[-1].end > start:
                raise RuntimeError(f"Overlapping snapshot object {name} at {start:#x}")
            spans.append(Span(name, start, last, region.allocation, region.kind, region.protect))
            start = last
    return spans


class SnapshotEngine:
    def __init__(self, controller, profile=DEFAULT_PROFILE):
        self.controller = controller
        self.profile = profile

    # ----- page tracking ------------------------------------------------------------------------
    # With write watch (see watch_launch.py) the engine keeps `live`: for every span, the pages of
    # the game's memory that differ from the base image, as of the last capture or restore. The
    # system then reports which arena pages were written since, so a capture reads only those and
    # a restore writes only the pages that are dirty or differ between `live` and the target.
    # The small spans outside the arenas (.data, allocator descriptors, singletons, about 2.6 MB)
    # are not watched and are simply read in full every time.
    # Every `verify_every`-th fast operation re-reads the whole image and compares; any mismatch is
    # repaired on the spot and switches this engine back to the full path for good.

    live = None            # list of {page offset: bytes}, one per span, or None when not in sync
    verify_every = int(os.environ.get("DMC4TAS_VERIFY_EVERY", "50"))   # 0 = never, 1 = every time
    fast_operations = 0
    verify_failures = 0
    tracking_disabled = os.environ.get("DMC4TAS_NO_TRACKING") == "1"   # force the read-everything path

    def _watched(self, span):
        return span.name == "arena"

    def _pages_of(self, index, image):
        """{offset: page bytes} for the pages of `image` that differ from the base image of that span."""
        basis = self.base.data[index]
        pages = {}
        for a, b in changed_runs(basis, image):
            for offset in range(a, b, PAGE):
                pages[offset] = bytes(image[offset:offset + PAGE])
        return pages

    def _dirty_offsets(self, dirty):
        """System dirty runs plus this controller's own writes, as {span index: sorted page offsets}."""
        table = getattr(self, "_arena_table", None)
        if table is None or table[0] is not self.base:
            arenas = sorted((span.address, span.end, index) for index, span in enumerate(self.base.spans)
                            if self._watched(span))
            table = self._arena_table = (self.base, [item[0] for item in arenas], arenas)
        _, starts, arenas = table
        wanted = {}
        runs = [(address, count * PAGE) for address, count in dirty]
        runs += [(address, size) for address, size in WRITTEN]
        del WRITTEN[:]
        for address, size in runs:
            end = address + size
            position = max(bisect_right(starts, address) - 1, 0)
            while position < len(arenas) and arenas[position][0] < end:
                low, high, index = arenas[position]
                if address < high:
                    first = (max(address, low) - low) // PAGE * PAGE
                    wanted.setdefault(index, set()).update(range(first, min(end, high) - low, PAGE))
                position += 1
        return wanted

    @staticmethod
    def _runs(offsets):
        """Group sorted page offsets into [start, end) byte ranges."""
        ranges = []
        for offset in offsets:
            if ranges and ranges[-1][1] == offset:
                ranges[-1][1] = offset + PAGE
            else:
                ranges.append([offset, offset + PAGE])
        return ranges

    def _read_small(self, memory):
        small = [index for index, span in enumerate(self.base.spans) if not self._watched(span)]
        images = memory.read_spans([(self.base.spans[i].address, self.base.spans[i].end) for i in small])
        return dict(zip(small, images))

    def _verify(self, memory, snapshot, where):
        """Full re-read: is the game's memory exactly this snapshot? Repairs and disables tracking if not."""
        current = memory.read_spans([(span.address, span.end) for span in snapshot.spans])
        wrong = list(snapshot.patches(current))
        if wrong:
            for address, wanted, _ in wrong:
                memory.write(address, wanted)
            self.verify_failures += 1
            self.tracking_disabled = True
            self.live = None
        return {"where": where, "mismatched_bytes": sum(len(wanted) for _, wanted, _ in wrong),
                "stretches": len(wrong)}

    def capture(self):
        start = perf_counter()
        heaps = self.profile == "heaps"
        base = getattr(self, "base", None)
        verification = None
        try:
            with self.controller.frame_gate(collect=heaps and not self.tracking_disabled) as (gate, _), \
                    ProcessMemory(self.controller.pid) as memory:
                dirty = gate.get("dirty")
                tracked = (heaps and base is not None and dirty is not None and self.live is not None
                           and identity(gate, self.profile) == base.identity)
                with memory.quiescent(gate["tid"]) as threads:
                    if tracked:
                        offsets = self._dirty_offsets(dirty)
                        ranges = [(index, a, b) for index, pages in offsets.items()
                                  for a, b in self._runs(sorted(pages))]
                        chunks = memory.read_spans([(base.spans[i].address + a, base.spans[i].address + b)
                                                    for i, a, b in ranges])
                        small = self._read_small(memory)
                        spans = base.spans
                    else:
                        del WRITTEN[:]
                        spans = tuple(snapshot_spans(memory, gate, self.profile))
                        raw = tuple(memory.read_spans([(span.address, span.end) for span in spans]))
                    clocks = {key: gate[key] for key in CLOCK_KEYS}
                    if tracked:
                        for (index, a, b), chunk in zip(ranges, chunks):
                            basis, pages = base.data[index], self.live[index]
                            for offset in range(a, b, PAGE):
                                page = bytes(chunk[offset - a:offset - a + PAGE])
                                if page == basis[offset:offset + PAGE]:
                                    pages.pop(offset, None)
                                else:
                                    pages[offset] = page
                        for index, image in small.items():
                            self.live[index] = self._pages_of(index, image)
                        snapshot = Snapshot(clocks, identity(gate, self.profile), spans, base, None,
                                            0, threads, tuple(dict(pages) for pages in self.live))
                        self.fast_operations += 1
                        if self.verify_every and self.fast_operations % self.verify_every == 0:
                            verification = self._verify(memory, snapshot, "capture")
            if not tracked:
                if heaps and base is not None and spans == base.spans:
                    # The game is running again; the delta is computed on our own copy.
                    self.live = [self._pages_of(index, image) for index, image in enumerate(raw)] \
                        if dirty is not None else None
                    pages = self.live if self.live is not None else \
                        [self._pages_of(index, image) for index, image in enumerate(raw)]
                    snapshot = Snapshot(clocks, identity(gate, self.profile), spans, base, None, 0, threads,
                                        tuple(dict(item) for item in pages))
                else:
                    snapshot = Snapshot(clocks, identity(gate, self.profile), spans, None, raw, 0, threads)
                    if heaps:
                        self.base = snapshot
                        # In sync with the base by definition, provided the watch was reset in this gate.
                        self.live = [{} for _ in spans] if dirty is not None else None
        except BaseException:
            self.live = None
            raise
        snapshot.milliseconds = (perf_counter() - start) * 1000
        snapshot.tracked = tracked
        snapshot.verification = verification
        return snapshot

    def restore(self, snapshot, trace=None):
        start = perf_counter()
        heaps = self.profile == "heaps"
        fast = None
        try:
            fast = self._restore_tracked(snapshot, start) if heaps and not self.tracking_disabled else None
        except BaseException:
            self.live = None
            raise
        if fast is not None:
            return fast
        self.live = None
        return self._restore_full(snapshot, trace, start)

    def _restore_tracked(self, snapshot, start):
        """Write only dirty pages and pages that differ from the target. None when tracking cannot be used."""
        base = getattr(self, "base", None)
        if base is None or self.live is None:
            return None
        if snapshot is base:
            target_pages = tuple({} for _ in base.spans)     # the base image differs from itself nowhere
        elif snapshot.pages is not None and snapshot.base is base:
            target_pages = snapshot.pages
        else:
            return None
        with self.controller.frame_gate(collect=True) as (gate, saved), \
                ProcessMemory(self.controller.pid, write=True) as memory:
            if identity(gate, self.profile) != snapshot.identity:
                raise RuntimeError("Room, actor set or rendering device changed since this snapshot")
            dirty = gate.get("dirty")
            if dirty is None:
                self.live = None
                return None
            gate_ms = (perf_counter() - start) * 1000
            with memory.quiescent(gate["tid"]) as threads:
                offsets = self._dirty_offsets(dirty)
                written = 0
                for index, span in enumerate(base.spans):
                    if not self._watched(span):
                        continue
                    now, want, basis = self.live[index], target_pages[index], base.data[index]
                    candidates = set(offsets.get(index, ()))
                    # Pages the game did not touch since the last sync still hold `now`; they only
                    # need writing where the target stores something else. Shared page objects
                    # (the usual case between neighbouring checkpoints) are skipped by identity.
                    for offset, page in want.items():
                        if offset not in candidates:
                            other = now.get(offset)
                            if other is not page and other != page:
                                candidates.add(offset)
                    for offset in now.keys() - want.keys():
                        candidates.add(offset)
                    # Bridge small gaps between stretches: the pages in a gap are not candidates, so
                    # by the model they already hold the target's content and rewriting them with
                    # it changes nothing, while each separate write costs a system call.
                    merged = []
                    for a, b in self._runs(sorted(candidates)):
                        if merged and a - merged[-1][1] <= GAP:
                            merged[-1][1] = b
                        else:
                            merged.append([a, b])
                    for a, b in merged:
                        data = b"".join(want.get(offset) or bytes(basis[offset:offset + PAGE])
                                        for offset in range(a, b, PAGE))
                        memory.write(span.address + a, data)
                        written += len(data)
                arena_ms = (perf_counter() - start) * 1000 - gate_ms
                for index, image in self._read_small(memory).items():
                    span, basis = base.spans[index], base.data[index]
                    wanted = bytearray(basis)
                    for offset, page in target_pages[index].items():
                        wanted[offset:offset + len(page)] = page
                    for a, b in changed_runs(wanted, image):
                        memory.write(span.address + a, bytes(wanted[a:b]))
                        written += b - a
                del WRITTEN[:]
                self.live = [dict(pages) for pages in target_pages]
                saved["__reset_watch__"] = True
                self.fast_operations += 1
                verification = None
                if self.verify_every and self.fast_operations % self.verify_every == 0:
                    verification = self._verify(memory, snapshot, "restore")
                    del WRITTEN[:]
                saved.update(snapshot.clocks)
        return {"frame": snapshot.frame, "milliseconds": (perf_counter() - start) * 1000, "tracked": True,
                "dirty_pages": sum(len(pages) for pages in offsets.values()), "threads": threads,
                "gate_ms": gate_ms, "arena_ms": arena_ms,
                "written": written, "verification": verification}

    def _restore_full(self, snapshot, trace, start):
        patches = []
        with self.controller.frame_gate(collect=self.profile == "heaps" and not self.tracking_disabled) \
                as (gate, saved), ProcessMemory(self.controller.pid, write=True) as memory:
            if identity(gate, self.profile) != snapshot.identity:
                raise RuntimeError("Room, actor set or rendering device changed since this snapshot")
            with memory.quiescent(gate["tid"]) as threads:
                if self.profile == DEFAULT_PROFILE:
                    if list(snapshot_spans(memory, gate, self.profile)) != list(snapshot.spans):
                        raise RuntimeError("Known battle field mappings changed")
                for span in snapshot.spans:
                    # Image pages flip from copy-on-write to read-write the first time they are
                    # written, which also splits the region: walk the pieces and only insist on
                    # the same allocation, kind and writability.
                    cursor = span.address
                    while cursor < span.end:
                        region = memory.region(cursor)
                        if (region.allocation, region.kind) != (span.allocation, span.kind) or not region.writable:
                            raise RuntimeError(f"Snapshot mapping changed at {span.name} {cursor:#x}")
                        cursor = region.end
                mark = perf_counter()
                current = memory.read_spans([(span.address, span.end) for span in snapshot.spans])
                read_ms = (perf_counter() - mark) * 1000
                mark = perf_counter()
                patches = list(snapshot.patches(current))
                compare_ms = (perf_counter() - mark) * 1000
                if trace:
                    trace([(address, len(old)) for address, old, _ in patches])
                applied = []
                try:
                    for address, old, current in patches:
                        applied.append((address, current))
                        memory.write(address, old)
                except OSError:
                    for address, current in reversed(applied):
                        memory.write(address, current)
                    raise
                del WRITTEN[:]
                # Memory now equals the snapshot. If the watch is running and this snapshot belongs
                # to the current base, page tracking can pick up again from here.
                base = getattr(self, "base", None)
                if (gate.get("dirty") is not None and base is not None
                        and (snapshot is base or snapshot.base is base)):
                    self.live = ([{} for _ in base.spans] if snapshot is base
                                 else [dict(snapshot.page_map(index)) for index in range(len(base.spans))])
                    saved["__reset_watch__"] = True
                saved.update(snapshot.clocks)
        return {"frame": snapshot.frame, "milliseconds": (perf_counter() - start) * 1000, "tracked": False,
                "read_ms": read_ms, "compare_ms": compare_ms,
                "threads": threads, "written": sum(len(old) for _, old, _ in patches)}


def input_prefix(timeline, end):
    """Physical DIK/VK ranges, coalesced across spelling and interval edits."""
    start = 1
    result = []
    for _, stop, keys in timeline.rows:
        if start > end:
            break
        stop = min(stop, end)
        state = tuple(sorted(timeline.codes(key) for key in keys))
        if result and result[-1][1] == state:
            result[-1] = (stop, state)
        else:
            result.append((stop, state))
        start = stop + 1
    if start <= end:
        if result and not result[-1][1]:
            result[-1] = (end, ())
        else:
            result.append((end, ()))
    return tuple(result)


class Rollback:
    def __init__(self, controller, interval=DEFAULT_INTERVAL, limit=DEFAULT_LIMIT, profile=ROUTE_PROFILE):
        if interval < 1 or limit < 1:
            raise ValueError("Automatic interval and retention must be positive")
        self.controller = controller
        self.engine = SnapshotEngine(controller, profile)
        self.interval, self.limit = interval, limit
        self.checkpoints = []
        self.timeline = Timeline()
        self.path = None
        self.origin = None
        self.running = False
        self.resume_target = None
        # The snapshot taken at the moment the game's sound threads went to sleep for good. Only in
        # that state does the game's memory agree with what XAudio2 still holds, so it is the one
        # state in which the threads may wake again (see hand_back in service_operations).
        self.clean = None

    def return_clean(self):
        """Restore the sound-clean snapshot, whatever the script says."""
        if self.clean is None:
            raise RuntimeError("这条时间线没有声音线程睡下那一刻的存档")
        self.pause()
        self.running = False
        return self.engine.restore(self.clean)

    def save(self, detail=True, clean=False):
        snapshot = self.engine.capture()
        if clean:
            self.clean = snapshot
        if self.origin is None:
            self.origin = snapshot.frame
        frame = snapshot.frame - self.origin
        prefix = input_prefix(self.timeline, frame)
        self.serial = getattr(self, "serial", 0) + 1
        snapshot.serial = self.serial
        self.checkpoints = [(item, known) for item, known in self.checkpoints
                            if (item.frame, known) != (snapshot.frame, prefix)]
        self.checkpoints.append((snapshot, prefix))
        self.checkpoints.sort(key=lambda item: item[0].frame)
        # What is kept: the first checkpoint for good (with it any frame stays reachable by replay),
        # the last `limit` on the coarse grid (every `interval` frames), and the `DENSE_LIMIT` most
        # recently made ones wherever they are, so the area being worked on stays cheap to step in.
        first, rest = self.checkpoints[0], self.checkpoints[1:]
        coarse = [item for item in rest if (item[0].frame - self.origin) % self.interval == 0][-self.limit:]
        recent = sorted(rest, key=lambda item: item[0].serial)[-DENSE_LIMIT:]
        keep = {id(item[0]) for item in coarse} | {id(item[0]) for item in recent} | {id(self.clean)}
        self.checkpoints = [first] + [item for item in rest if id(item[0]) in keep]
        if not detail:
            return None
        # Page-tracked checkpoints share unchanged pages, so count every stored page object once.
        unique = {}
        for item, _ in self.checkpoints:
            if item.pages is None:
                unique[id(item)] = item.bytes
            else:
                for pages in item.pages:
                    for chunk in pages.values():
                        unique[id(chunk)] = len(chunk)
        base = getattr(self.engine, "base", None)
        if base is not None:
            unique[id(base)] = base.bytes
        return {"frame": frame, "gameFrame": snapshot.frame, "snapshot_bytes": snapshot.bytes,
                "retained_bytes": sum(unique.values()), "checkpoints": len(self.checkpoints),
                "milliseconds": snapshot.milliseconds, "tracked": getattr(snapshot, "tracked", False),
                "verification": getattr(snapshot, "verification", None),
                "verify_failures": self.engine.verify_failures}

    def _advance(self, current, target):
        """Step to `target`, leaving checkpoints: on the coarse grid far away, every few frames near it."""
        rows = []
        while current < target:
            limit = target
            if not self.checkpoints:
                boundary = target + 1
            elif self.engine.live is None:
                boundary = self._next_checkpoint(current)
            elif target - current <= DENSE_SPAN:
                # Dense checkpoints only pay off when a capture is cheap, i.e. with page tracking.
                boundary = self._next_checkpoint(current, DENSE_INTERVAL)
            else:
                # Far from the target: coarse grid, but do not run past the start of the dense zone.
                boundary = self._next_checkpoint(current)
                limit = target - DENSE_SPAN
            stop = min(limit, current + 10000, boundary)
            rows.extend(self.controller.step(stop - current))
            current = stop
            if current == boundary:
                self.save(detail=False)
        return rows

    def _next_checkpoint(self, current, interval=None):
        interval = interval or self.interval
        return self.origin + ((current - self.origin) // interval + 1) * interval

    def step(self, n=1):
        if n < 1:
            raise ValueError("Step count must be positive")
        current = self.controller.status()["frame"]
        rows = self._advance(current, current + n)
        if self.origin is not None:
            for row in rows:
                row["timelineFrame"] = row["number"] - self.origin
        return rows

    def load(self, path):
        path = path.strip().strip('"')
        timeline = read_script(path)
        current = self.controller.status()["frame"]
        if self.origin is None:
            self.origin = current
        position = current - self.origin
        if input_prefix(timeline, position) != input_prefix(self.timeline, position):
            self._goto(current, timeline)
        else:
            self.controller.load(timeline, position)
            self.timeline = timeline
        self.path = path
        return {"frames": timeline.rows[-1][1] if timeline.rows else 0}

    def _goto(self, target, timeline):
        if self.origin is None:
            raise RuntimeError("Save a snapshot or load a timeline before navigating")
        if target < self.origin:
            raise ValueError("Target frame must be nonnegative")
        current = self.controller.status()["frame"]
        restored = None
        position = current - self.origin
        if target < current or input_prefix(self.timeline, position) != input_prefix(timeline, position):
            candidates = [item for item, prefix in self.checkpoints
                          if item.frame <= target and prefix == input_prefix(timeline, item.frame - self.origin)]
            if not candidates:
                raise RuntimeError("No retained snapshot with a matching input prefix at or before target")
            snapshot = max(candidates, key=lambda item: item.frame)
            restored = self.engine.restore(snapshot)
            current = snapshot.frame
        self.controller.load(timeline, current - self.origin)
        self.timeline = timeline
        self.checkpoints = [(item, prefix) for item, prefix in self.checkpoints
                            if prefix == input_prefix(timeline, item.frame - self.origin)]
        rows = []
        # Stepping back is usually followed by stepping back again. Without page tracking a
        # checkpoint costs over 100 ms, so leave just one a few frames short of the target; with
        # page tracking _advance() leaves one every DENSE_INTERVAL frames on the way in.
        if restored is not None and target - current > 2 * NEAR and self.engine.live is None:
            rows += self._advance(current, target - NEAR)
            self.save(detail=False)
            current = target - NEAR
        rows += self._advance(current, target)
        return {"frame": target - self.origin, "gameFrame": target, "restored": restored, "replayed": len(rows),
                "last": rows[-1] if rows else self.controller.status()["last"]}

    def goto(self, target):
        self.pause()
        timeline = read_script(self.path) if self.path else self.timeline
        if self.origin is None:
            raise RuntimeError("Save a snapshot or load a timeline before navigating")
        return self._goto(self.origin + target, timeline)

    def back(self, n):
        if n < 1:
            raise ValueError("Back count must be positive")
        state = self.pause()
        timeline = read_script(self.path) if self.path else self.timeline
        return self._goto(state["frame"] - n, timeline)

    def pause(self):
        self.running = False
        return self.controller.pause()

    def resume(self, count=60):
        if count < 1:
            raise ValueError("Resume count must be positive")
        current = self.controller.status()["frame"]
        self.running = True
        self.resume_target = current + count
        return self._schedule(current)

    def _schedule(self, current):
        stop = self.resume_target
        if self.checkpoints:
            stop = min(stop, self._next_checkpoint(current))
        return self.controller.resume(stop)

    def service(self):
        state = self.controller.status()
        if self.running and state["parked"] and state["mode"] == "paused":
            frame = state["frame"]
            if self.checkpoints and (frame - self.origin) % self.interval == 0:
                self.save(detail=False)
            if frame >= self.resume_target:
                self.running = False
            else:
                self._schedule(frame)
