"""GPU resource guard test for textures shared by name (see agent.js, 2026-11-21).

Needs the game frozen in a room with a character (tas\\boot_to_room.py leaves it like that).

    pooled_guard_test.py watch TAKE.tas [extra frames]   play a script once, print when shared textures
                                                         appear, disappear or are kept by the guard
    pooled_guard_test.py stress [operations]             jump around in the loaded timeline
    pooled_guard_test.py break INSIDE AFTER on|off       checkpoint at frame INSIDE (textures alive), play
                                                         to AFTER (released), step back, play on; with
                                                         the guard off this is expected to crash the game
"""
import ctypes
import random
import struct
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from client import Client  # noqa: E402

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
COUNTERS = ("keptResources", "pooledKept", "sweepsSkipped", "releaseFaults", "finalReleases")


class Memory:
    def __init__(self, pid):
        self.handle = kernel32.OpenProcess(0x0410, False, pid)

    def read(self, address, size):
        buffer = (ctypes.c_char * size)()
        got = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(got)):
            return b""
        return bytes(buffer[:got.value])

    def u32(self, address):
        data = self.read(address, 4)
        return struct.unpack("<I", data)[0] if len(data) == 4 else 0

    def text(self, address):
        return self.read(address, 64).split(b"\0")[0].decode("ascii", "replace")

    def shared(self):
        """{resource pointer: (names, reference count)} of sRender's by-name table, and the length
        of the deferred delete list."""
        render = self.u32(0x118D42C)
        count = min(self.u32(render + 0x46D8A4), 512)
        data = self.read(render + 0x46C0A0, 12 * count)
        table = {}
        for index in range(len(data) // 12):
            first, second, resource = struct.unpack_from("<3I", data, 12 * index)
            table[resource] = (f"{self.text(first)} / {self.text(second)}", self.u32(resource + 8))
        return table, self.u32(render + 0x46D8A0)


def alive(client):
    try:
        state = client.call("status")
    except Exception as error:
        return None, f"service: {error}"
    if state.get("mode") == "detached":
        return None, "the game is gone (detached)"
    return state, None


def frozen(client):
    """Take the game over; close its own pause screen first when it is open."""
    state = client.call("status")
    if state.get("mode") == "observe" or state.get("timelineFrame") is None:
        if client.call("record-scene")["scene"].get("paused"):
            print("unpause:", client.call("unpause"))
        client.call("pause")
    return client.call("status")


def watch(path, extra):
    client = Client()
    state = frozen(client)
    memory = Memory(state["gamePid"])
    frames = client.call("load", path=str(Path(path).resolve()))["frames"]
    client.call("goto", count=0)
    table, queue = memory.shared()
    print(f"script {Path(path).name}: {frames} frames (+{extra} idle); shared textures now {len(table)}, "
          f"delete list {queue}, guard {client.call('status').get('guardPooled')}")
    before = {name: client.call("status")[name] for name in COUNTERS}
    events = []
    for frame in range(1, frames + extra + 1):
        client.call("step", count=1)
        state, problem = alive(client)
        if problem:
            print(f"frame {frame}: {problem}")
            return 1
        new, queue = memory.shared()
        for pointer in new.keys() - table.keys():
            events.append((frame, "new", pointer, new[pointer][0]))
        for pointer in table.keys() - new.keys():
            events.append((frame, "gone", pointer, table[pointer][0]))
        for name in COUNTERS:
            if name != "sweepsSkipped" and state[name] != before[name]:
                events.append((frame, name, state[name] - before[name], ""))
                before[name] = state[name]
        table = new
    for frame, kind, what, name in events:
        print(f"  frame {frame:4}: {kind:14} {what if isinstance(what, int) and kind in COUNTERS else hex(what)} {name}")
    state = client.call("status")
    print("counters:", {name: state[name] for name in COUNTERS}, "| kept names:", state.get("keptNames"),
          "| delete list", queue, "| failure", state.get("failure"))
    return 0


def stress(operations):
    client = Client()
    state = client.call("status")
    total = state["timelineFrame"]
    generator = random.Random(1121)
    started = time.perf_counter()
    position = total
    for index in range(operations):
        roll = generator.random()
        if roll < .45 and position > 1:
            position = client.call("back", count=min(generator.choice((1, 3, 10, 60, 200)), position - 1))["frame"]
        elif roll < .75:
            position = client.call("goto", count=generator.randint(1, total))["frame"]
        else:
            count = min(generator.choice((1, 5, 30, 90)), total - position)
            if count < 1:
                continue
            position = client.call("step", count=count)[-1]["timelineFrame"]
        state, problem = alive(client)
        if problem:
            print(f"operation {index}: {problem}")
            return 1
    print(f"{operations} operations in {time.perf_counter() - started:.1f}s, game alive at frame {position}; counters:",
          {name: state[name] for name in COUNTERS}, "| failure", state.get("failure"))
    return 0


def break_it(inside, after, guard):
    """The failure of 2026-11-21: a checkpoint while the shared textures of a blur are alive, play
    past their release, step back to the checkpoint, play on. With the guard off the textures are
    really destroyed in between, so the game is expected to fault or crash here."""
    client = Client()
    memory = Memory(client.call("status")["gamePid"])
    client.call("goto", count=inside)
    client.call("save")
    print(f"guard {client.call('guard', on=guard).get('guardPooled')}; checkpoint at frame {inside}, "
          f"shared textures {len(memory.shared()[0])}")
    steps = [("played past the release", lambda: client.call("step", count=after - inside)),
             ("stepped back to the checkpoint", lambda: client.call("goto", count=inside))]
    steps += [(f"played on to frame {inside + 1 + index}", lambda: client.call("step", count=1))
              for index in range(after - inside + 240)]
    for label, action in steps:
        try:
            action()
        except Exception as error:
            print(f"{label}: {str(error)[:160]}")
        state, problem = alive(client)
        if problem:
            print(f"{label}: {problem}")
            return 1
        if state["releaseFaults"] or not label.startswith("played on"):
            print(f"{label}: frame {state['timelineFrame']}, shared textures {len(memory.shared()[0])}, "
                  f"delete list {memory.shared()[1]}, counters", {name: state[name] for name in COUNTERS},
                  "| last fault", state.get("lastReleaseFault"))
        if state["releaseFaults"]:
            return 1
    time.sleep(2)
    state, problem = alive(client)
    print("end:", problem or f"game alive at frame {state['timelineFrame']}, release faults {state['releaseFaults']}")
    return 0


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "watch":
        raise SystemExit(watch(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 150))
    if command == "stress":
        raise SystemExit(stress(int(sys.argv[2]) if len(sys.argv) > 2 else 60))
    if command == "break":
        raise SystemExit(break_it(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4] != "off"))
