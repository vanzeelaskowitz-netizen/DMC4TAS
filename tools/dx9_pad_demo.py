"""Build a gamepad demo for the original (DX9) game: runs/dx9-pad-demo.tas.

    dx9_pad_demo.py OUT.tas

Needs a paused timeline in Bloody Palace on the original game with Nero on the ground, a scarecrow
a few steps away and nothing held. Everything is pad input (work/pad.py), no pad needed: lock on
(RB), Streak (stick towards + Y) with an Exceed (LT) in its window, a jump (A), Calibur in the air
(stick away, then towards + Y) with another Exceed. The stick direction is worked out from Nero,
the scarecrow and the camera just before each move; each Exceed frame is searched around the first frame of
the game's own window timer and the one that fills the gauge most is kept.
Nero's default pad buttons are assumed: Y sword, A jump, RB lock on, LT Exceed.
"""
import ctypes as ct
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]
from client import Client  # noqa: E402
from pad import short  # noqa: E402

CAMERA_YAW = 0x16F4             # player object: the yaw the stick is measured from (docs/MAP_DX9.md)
CALIBUR = (861, 862)
SCRATCH = ROOT / "runs" / "dx9-pad-search.tas"
LOCK = "PadRB"

client = Client()
kernel32 = ct.WinDLL("kernel32")
kernel32.OpenProcess.restype = ct.c_void_p
game = kernel32.OpenProcess(0x0410, False, client.call("status")["gamePid"])     # read only


def read_float(address):
    value, got = ct.c_float(), ct.c_size_t()
    kernel32.ReadProcessMemory(ct.c_void_p(game), ct.c_void_p(address), ct.byref(value), 4, ct.byref(got))
    return value.value


def trace(rows, first, last):
    """Play rows on the loaded timeline and sample frames first..last."""
    SCRATCH.write_text("\n".join(rows) + "\n", encoding="utf-8")
    client.call("load", path=str(SCRATCH))
    client.call("goto", count=first - 1)
    frames = []
    for number in range(first, last + 1):
        row = client.call("step", count=1)[-1]
        player = row["combat"]["player"]
        frames.append({"frame": number, "action": player["action"], "motion": player["motion"],
                       "timer": player["exceed"]["timer"], "gauge": player["exceed"]["gauge"],
                       "landed": player.get("landed"), "y": player["position"][1], "pad": row.get("pad")})
    return frames


def sticks(rows, frame):
    """PadLS tokens that push towards and away from the nearest enemy on `frame`, seen from the
    camera. The lock-on camera keeps turning, so this is asked again before every move."""
    trace(padded(list(rows), frame + 1), frame, frame)
    row = client.call("status")["last"]
    player = row["combat"]["player"]
    enemy = min(row["combat"]["enemies"], key=lambda e: e["distance"])
    px, _, pz = player["position"]
    ex, _, ez = enemy["position"]
    # A yaw of t faces (sin t, cos t); the game adds the stick angle atan2(-x, y) to the camera yaw.
    angle = math.atan2(ex - px, ez - pz) - read_float(int(player["pointer"], 16) + CAMERA_YAW)
    x, y = -math.sin(angle), math.cos(angle)
    token = lambda sx, sy: f"PadLS={short(round(sx, 3))}/{short(round(sy, 3))}"
    return token(x, y), token(-x, -y), enemy["distance"]


def padded(rows, frame):
    return rows + [LOCK] * max(0, frame - 1 - len(rows))


def exceed(rows, start, end, notes, label):
    """Put LT on the frame in start..end that leaves the highest gauge; rows unchanged if none helps."""
    base = trace(padded(list(rows), end + 20), start, end + 20)
    opened = [f["frame"] for before, f in zip(base, base[1:]) if f["timer"] > 0 >= before["timer"]]
    centre = opened[0] if opened else None
    candidates = range(max(start, centre - 3), min(end, centre + 6) + 1) if centre else range(start, end + 1)
    best = (base[-1]["gauge"], None)
    for frame in candidates:
        trial = padded(list(rows), frame) + [f"{LOCK} PadLT"]
        gauge = trace(padded(trial, frame + 12), frame, frame + 11)[-1]["gauge"]
        if gauge > best[0]:
            best = (gauge, frame)
    if best[1] is None:
        print(f"{label}: no Exceed frame found (window opened at {centre})")
        return rows, base[-1]["gauge"]
    notes[best[1]] = f"Exceed (LT) in the {label} window: gauge {best[0]}"
    print(f"{label}: Exceed at frame {best[1]}, gauge {best[0]} (window opened at {centre})")
    return padded(list(rows), best[1]) + [f"{LOCK} PadLT"], best[0]


def main():
    out = Path(sys.argv[1]).resolve()
    state = client.call("status")
    scale = round((state.get("frameRate") or 60) / 60)
    idle = trace([LOCK] * 4, 1, 2)[-1]["action"]
    notes = {1: "lock on (RB)"}
    rows = [LOCK] * (10 * scale)

    # Streak: lock + stick towards + sword.
    start = len(rows) + 1
    toward, _, distance = sticks(rows, start - 1)
    print(f"Streak: stick {toward}, enemy {distance:.0f} away, {60 * scale} fps")
    rows += [f"{LOCK} {toward} PadY"] * (2 * scale)
    notes[start] = "Streak: stick towards the enemy + Y"
    frames = trace(padded(list(rows), start + 150 * scale), start, start + 150 * scale)
    busy = [f["frame"] for f in frames if f["action"] != idle]
    if not busy:
        raise SystemExit("Streak did not start: check the starting scene and the pad buttons")
    print("Streak motions:", sorted({f["motion"] for f in frames if f["action"] != idle}))
    end = busy[-1]
    rows, _ = exceed(rows, busy[0], end, notes, "Streak")

    # Jump, then Calibur: stick away, then towards + sword.
    jump = end + 2 * scale
    rows = padded(rows, jump) + [f"{LOCK} PadA"] * (2 * scale)
    notes[jump] = "jump (A)"
    found = None
    for delay in range(6 * scale, 24 * scale, scale):
        begin = jump + delay
        toward, away, _ = sticks(rows, begin - 1)
        trial = padded(list(rows), begin) + [f"{LOCK} {away}"] * scale + [f"{LOCK} {toward} PadY"] * (2 * scale)
        frames = trace(padded(list(trial), begin + 60 * scale), begin, begin + 60 * scale)
        flying = [f["frame"] for f in frames if f["motion"] in CALIBUR]
        if flying:
            found = (trial, begin, flying)
            break
    if not found:
        raise SystemExit("Calibur did not start after the jump")
    rows, begin, flying = found
    notes[begin] = "Calibur: stick away from the enemy ..."
    notes[begin + scale] = "... then towards + Y"
    rows, gauge = exceed(rows, flying[0], flying[-1] + 10 * scale, notes, "Calibur")

    rows = padded(rows, len(rows) + 60 * scale)
    text = [f"# {60 * scale} fps", "# Gamepad demo, no pad needed: RB lock on, Y sword, A jump, LT Exceed, left stick."]
    text += [row + (f"   # {notes[i + 1]}" if i + 1 in notes else "") for i, row in enumerate(rows)]
    out.write_text("\n".join(text) + "\n", encoding="utf-8")
    client.call("load", path=str(out))
    print(f"{len(rows)} frames, final gauge {gauge}: {out}")


if __name__ == "__main__":
    main()
