"""Search the original (DX9) Exceed-keeping demo: runs/dx9-exceed-demo.tas.

    dx9_exceed_demo.py OUT.tas [LOOPS]

Needs a paused timeline in Bloody Palace on the original game, frame 0 with Nero standing to the
right of a scarecrow (it on the left of the screen, about 350 away). From there: ground Snatch,
jump, Snatch at the top of the jump, air slash with a MAX-Act, then loops of
    EX Calibur -> jump cancel on the frame after its first hit (keeps the Exceed gauge) -> rev.
Each move is searched for its earliest frame and every jump cancel is checked to keep the gauge.
Facts used (measured 2026-11-24, docs/MAP_DX9.md): the gauge is taken on the frame after the
Calibur's first hit, so the cancel has exactly one frame; Calibur is motion 861/862 and the air
rev 847/848 on this build; the Devil Bringer is Right Alt.
"""
import ctypes as ct
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]
from client import Client  # noqa: E402

GAUGE = 0xCCE8                  # Exceed gauge, i32 0..3 (player object)
CALIBUR, AIR_REV, ENEMY_STEP = (861, 862), 847, 25
HOLD_Q = 6                      # frames Q is held for the rev, long enough to see the throttle pulled
SCRATCH = ROOT / "runs" / "dx9-exceed-search.tas"

client = Client()
kernel32 = ct.WinDLL("kernel32")
kernel32.OpenProcess.restype = ct.c_void_p
game = kernel32.OpenProcess(0x0410, False, client.call("status")["gamePid"])     # read only


def gauge(player):
    value, got = ct.c_int32(), ct.c_size_t()
    kernel32.ReadProcessMemory(ct.c_void_p(game), ct.c_void_p(int(player, 16) + GAUGE),
                               ct.byref(value), 4, ct.byref(got))
    return value.value


def trace(rows, first, last):
    """Play rows on the loaded timeline and sample frames first..last."""
    SCRATCH.write_text("\n".join(rows) + "\n", encoding="utf-8")
    client.call("load", path=str(SCRATCH))
    client.call("goto", count=first - 1)
    frames = []
    for number in range(first, last + 1):
        combat = client.call("step", count=1)[-1]["combat"]
        player = combat["player"]
        frames.append({"frame": number, "action": player["action"], "motion": player["motion"],
                       "gauge": gauge(player["pointer"]),
                       "hit": any(e["hitStopTimer"] > 0 for e in combat["enemies"])})
    return frames


def pad(rows, frame):
    return rows + ["Space"] * max(0, frame - 1 - len(rows))


def opening(notes):
    rows = ["Space"] * 3 + ["Space RightAlt"] * 2 + ["Space"] * 24 + ["K"] * 2 + ["Space"] * 13 + ["Space RightAlt"] * 2
    rows = pad(rows, 68) + ["Space J"] * 2
    rows = pad(rows, 80) + ["Space Q"]
    notes.update({4: "ground Snatch (lock + Devil Bringer) pulls the scarecrow in", 30: "jump",
                  45: "top of the jump: air Snatch pulls the scarecrow up", 68: "air slash",
                  80: "Q on the first frame of the Exceed window: MAX-Act, gauge 3"})
    return rows


def calibur(rows, start, notes):
    """Earliest EX Calibur from `start` whose cancel on the frame after the hit keeps the gauge."""
    for first in range(start, start + 14):
        trial = pad(list(rows), first) + ["Space D"] + ["Space A J"] * 2
        frames = trace(trial + ["Space"] * 30, first, first + 26)
        hit = next((f["frame"] for f in frames if f["motion"] == CALIBUR[1] and f["hit"]), None)
        if hit is None:
            continue
        cancelled = pad(trial, hit + 1) + ["Space K"] * 2
        after = trace(cancelled + ["Space"] * 8, hit + 1, hit + 7)
        if after[0]["action"] == ENEMY_STEP and after[-1]["gauge"] == 3:
            notes[first] = "EX Calibur: away (D), then towards (A) + sword"
            notes[hit + 1] = f"jump cancel on the frame after the hit (frame {hit}): the gauge stays at 3"
            return cancelled, hit + 1
    return None


def rev(rows, step, notes):
    for delay in range(3, 12):
        start = step + delay
        trial = pad(list(rows), start) + ["Space Q"] * HOLD_Q
        if any(f["motion"] == AIR_REV for f in trace(trial + ["Space"] * 4, start, start + 3)):
            notes[start] = f"rev: Q held {HOLD_Q} frames"
            return trial, start
    return None


def main():
    out = Path(sys.argv[1]).resolve()
    loops = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    notes = {}
    rows = opening(notes)
    found = calibur(rows, 86, notes)
    if not found:
        raise SystemExit("The first Calibur did not keep the gauge: check the starting scene")
    rows, step = found
    done = 0
    for loop in range(1, loops + 1):
        revved = rev(rows, step, notes)
        found = revved and calibur(revved[0], revved[1] + 2, notes)
        if not found:
            print(f"loop {loop}: stopped (the scarecrow is usually on the ground by now)")
            break
        rows, step = found
        done = loop
        print(f"loop {loop}: jump cancel at {step}", flush=True)
    text = [row + (f"   # {notes[i + 1]}" if i + 1 in notes else "") for i, row in enumerate(rows + ["Space"] * 40)]
    out.write_text("\n".join(text) + "\n", encoding="utf-8")
    client.call("load", path=str(out))
    print(f"{done + 1} jump cancels that keep the gauge, {len(rows)} frames: {out}")


if __name__ == "__main__":
    main()
