"""Experiments for Nero's Exceed (red blade) timing. Needs a frozen timeline in a room.

    exceed_probe.py dump NAME SCRIPT.tas [frames]   play a script from frame 0, save the player object of
                                                    every frame to logs\\exceed-NAME.bin (+ .json with motion)
    exceed_probe.py scan KEY FIRST LAST BASE.tas    BASE with KEY added on one frame f, for f = FIRST..LAST:
                                                    print what the Exceed fields are afterwards

The caller's timeline (path and frame) is put back at the end of every command.
"""
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work"), str(ROOT / "tools")]

from client import Client  # noqa: E402
from pooled_guard_test import Memory  # noqa: E402

SIZE = 0x18000
LOGS = ROOT / "logs"
SCRATCH = ROOT / "runs" / "exceed-probe.tas"


def player(memory):
    return memory.u32(memory.u32(0x1154B34) + 0x24)


class Borrow:
    """Use the timeline that is loaded now and give it back afterwards."""
    def __enter__(self):
        self.client = Client()
        state = self.client.call("status")
        if state.get("timelineFrame") is None:
            raise SystemExit("no timeline: take the game over in a room first")
        self.path, self.frame = state.get("timelinePath"), state["timelineFrame"]
        self.memory = Memory(state["gamePid"])
        return self

    def __exit__(self, *_):
        if self.path:
            self.client.call("load", path=self.path)
        self.client.call("goto", count=self.frame)
        state = self.client.call("status")
        print(f"put back: {Path(state.get('timelinePath') or '-').name} at frame {state['timelineFrame']}")


def dump(name, script, frames):
    with Borrow() as b:
        total = b.client.call("load", path=str(Path(script).resolve()))["frames"]
        frames = frames or total
        b.client.call("goto", count=0)
        blobs, rows = [], []
        for frame in range(1, frames + 1):
            row = b.client.call("step", count=1)[-1]
            who = (row.get("combat") or {}).get("player") or {}
            blobs.append(b.memory.read(player(b.memory), SIZE))
            rows.append({"frame": frame, "keys": (row.get("keys") or {}).get("held"), "action": who.get("action"),
                         "motion": who.get("motion"), "motionFrame": who.get("motionFrame"),
                         "buttons": who.get("buttons"), "grounded": who.get("grounded")})
        (LOGS / f"exceed-{name}.bin").write_bytes(b"".join(blobs))
        (LOGS / f"exceed-{name}.json").write_text(json.dumps(rows), encoding="utf-8")
        print(f"{name}: {len(blobs)} frames of {SIZE:#x} bytes saved")


def scan(key, first, last, base, fields):
    lines = Path(base).read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    with Borrow() as b:
        for frame in range(first, last + 1):
            rows = list(lines) + [""] * max(0, frame - len(lines))
            rows[frame - 1] = (rows[frame - 1] + " " + key).strip()
            SCRATCH.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
            b.client.call("load", path=str(SCRATCH))
            b.client.call("goto", count=len(rows) + 30)
            base_address = player(b.memory)
            values = []
            for offset, kind in fields:
                data = b.memory.read(base_address + offset, 4)
                values.append(struct.unpack("<f" if kind == "f" else "<i", data)[0] if kind != "b" else data[0])
            print(f"{key} on frame {frame:3}: " + "  ".join(f"+{offset:#x}={value:.3f}" if kind == "f" else f"+{offset:#x}={value}"
                                                            for (offset, kind), value in zip(fields, values)), flush=True)


def matrix(name, key, first, last, base, size):
    """BASE with KEY on one frame f (f = FIRST..LAST), played to the end + 30: the player object
    after each variant goes to logs\\exceed-NAME.bin, to look for what depends on the frame."""
    lines = Path(base).read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    with Borrow() as b:
        blobs = []
        for frame in [0] + list(range(first, last + 1)):          # 0: the base script without KEY
            rows = list(lines) + [""] * max(0, frame - len(lines))
            if frame:
                rows[frame - 1] = (rows[frame - 1] + " " + key).strip()
            SCRATCH.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
            b.client.call("load", path=str(SCRATCH))
            b.client.call("goto", count=len(lines) + 30)
            blobs.append(b.memory.read(player(b.memory), size))
        (LOGS / f"exceed-{name}.bin").write_bytes(b"".join(blobs))
        (LOGS / f"exceed-{name}.json").write_text(json.dumps({"size": size, "frames": [0] + list(range(first, last + 1))}),
                                                   encoding="utf-8")
        print(f"{name}: {len(blobs)} variants of {size:#x} bytes saved")


TIMER, GAUGE = 0xF2D0, 0xF1C8      # Exceed window timer (float) and gauge (0..3), found 2026-11-21


def verify(key, base):
    """Predict the red blade frames of BASE from the window timer, then press KEY on each frame
    around them in turn and compare with what the gauge really does."""
    lines = Path(base).read_text(encoding="utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    with Borrow() as b:
        def f32(offset):
            return struct.unpack("<f", b.memory.read(player(b.memory) + offset, 4))[0]

        def u32(offset):
            return b.memory.u32(player(b.memory) + offset)

        b.client.call("load", path=str(Path(base).resolve()))
        b.client.call("goto", count=0)
        start, timers, motions = None, [0.0], [None]
        for frame in range(1, len(lines) + 1):
            row = b.client.call("step", count=1)[-1]
            if start is None:
                start = u32(GAUGE)              # after a frame has run: right after a jump the
                                                # memory still shows the state jumped away from
            who = (row.get("combat") or {}).get("player") or {}
            timers.append(f32(TIMER))
            motions.append((who.get("motion"), round(who.get("motionFrame") or 0, 1)))
        predicted = {}
        for frame in range(1, len(lines) + 1):
            # Same rule as Player.exceed_at: open while the timer moves, MAX on the frame it starts.
            if timers[frame] > 0 and timers[frame] != timers[frame - 1]:
                predicted[frame] = "MAX" if timers[frame - 1] == 0 or timers[frame] < timers[frame - 1] else "EX"
        print(f"{Path(base).name}: gauge at start {start}; window frames by the timer:",
              {frame: (kind, motions[frame]) for frame, kind in predicted.items()})
        if not predicted:
            print("  no window in this script")
            return
        wrong = 0
        for frame in range(max(1, min(predicted) - 2), min(len(lines), max(predicted) + 2) + 1):
            rows = list(lines)
            rows[frame - 1] = (rows[frame - 1] + " " + key).strip()
            SCRATCH.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
            b.client.call("load", path=str(SCRATCH))
            b.client.call("goto", count=frame + 2)
            got = u32(GAUGE)
            want = {"MAX": 3, "EX": min(3, start + 1)}.get(predicted.get(frame), start)
            wrong += got != want
            print(f"  {key} on frame {frame:3}: gauge {got}  predicted {predicted.get(frame, '-'):3} -> {want}"
                  + ("" if got == want else "   <-- differs"))
        print(f"  {'all as predicted' if not wrong else str(wrong) + ' frames differ'}")


if __name__ == "__main__":
    command = sys.argv[1]
    if command == "verify":
        for script in sys.argv[3:]:
            verify(sys.argv[2], script)
        raise SystemExit(0)
    if command == "matrix":
        matrix(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6],
               int(sys.argv[7], 16) if len(sys.argv) > 7 else 0x18000)
        raise SystemExit(0)
    if command == "dump":
        dump(sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 0)
    elif command == "scan":
        fields = [(int(item.split(":")[0], 16), item.split(":")[1]) for item in sys.argv[6:]]
        scan(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], fields)
