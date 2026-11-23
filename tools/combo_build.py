"""Build "Split, JC, then Calibur back and forth over the enemy with a rev at each end" from the
frame the timeline is on now, by trying inputs and reading what the game did.

    combo_build.py OUT.tas PASSES BASE.tas START      passes: how many Calibur crossings (default 3: tail, head, tail)

The frames before the current one are kept as they are (the take that set the scene up). Which
keys mean "towards" and "away" depends on the camera and changes with every crossing, so each
crossing tries all eight pairs and keeps the one whose jump cancel lands nearest the far step point.
Timing rules used (measured 2026-11-22): next action 8 frames after an Enemy Step; Exceed must be
pressed 5 frames after it; a Calibur input 5 frames into the rev cancels it.
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work"), str(ROOT / "tools")]

import combo_search as search  # noqa: E402
from timeline import expand_text  # noqa: E402

PAIRS = (("W", "S"), ("S", "W"), ("A", "D"), ("D", "A"), ("A S", "W D"), ("W D", "A S"), ("A W", "D S"), ("D S", "A W"))
SPLIT, CALIBUR, REV = 786, (852, 853), 847


def main():
    out = Path(sys.argv[1])
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    base, start = Path(sys.argv[3]), int(sys.argv[4])
    rows = [line for line in expand_text(base.read_text(encoding="utf-8")).split("\n") if not line.startswith("@key")]
    rows = [line.split("#")[0].rstrip() for line in rows]
    rows = (rows + [""] * start)[:start]
    notes = {}

    def put(frame, text):
        while len(rows) < frame:
            rows.append("Space")
        rows[frame - 1] = text

    def hp_of(trace):
        return trace[0][10]

    # 1. the Split has to hit before it is cancelled. On the ground Nero cannot get nearer than about
    # 100 (the body pushes him back) and from there the Split only connects after landing, when no step
    # is possible. So: jump towards the enemy without lock-on (that jump travels), lock on again over
    # it and Split; the cancel goes on the first frame after the hit on which the step really happens.
    HIT = (61, 58, 53)                       # enemy motions of the hit reaction
    best = None
    for key in ("A", "D", "W", "S"):
        for air in (21, 18, 15, 12, 9):
            trial = rows + ["Space"] * 3 + [f"{key} K"] * 2 + [key] * air + [f"Space {key} J"] * 2 + ["Space"] * 34
            trace = search.play(trial, start + 1, len(trial))
            split = [r for r in trace if r[2] == SPLIT]
            hits = [r for r in trace if r[7] in HIT]
            if not split or not hits:
                continue
            hit = hits[0]
            cans = [r[0] for r in split if r[8] and r[0] > hit[0]]
            score = (not hit[4], hit[5])     # a hit in the air first, then the higher one
            if cans and (best is None or score > best[0]):
                best = (score, key, air, hit[0], cans)
        if best:
            break
    if best is None:
        raise SystemExit("no jump gave a Split that hits and can then be jump cancelled")
    _, key, air, hit, cans = best
    jump = start + 4
    put(jump, f"{key} K"); put(jump + 1, f"{key} K")
    for frame in range(jump + 2, jump + 2 + air):
        put(frame, key)
    put(jump + 2 + air, f"Space {key} J"); put(jump + 3 + air, f"Space {key} J")
    notes[jump] = "jump towards the enemy, lock-on released so the jump travels"
    notes[jump + 2 + air] = "lock on again over the enemy: Split (towards + sword)"
    step = None
    for frame in cans:
        trial = rows + ["Space"] * (frame - 1 - len(rows)) + ["Space K"] * 2 + ["Space"] * 12
        trace = search.play(trial, hit - 2, frame + 6)
        stepped = [r[0] for r in trace if r[9]]
        if stepped and any(r[7] in HIT and r[0] < stepped[0] for r in trace):
            step = frame
            break
    if step is None:
        raise SystemExit("the Split could not be jump cancelled after its hit")
    put(step, "Space K"); put(step + 1, "Space K")
    notes[hit] = "the Split hits"
    notes[step] = "JC out of the Split"
    print(f"jump '{key}' on {jump}, Split on {jump + 2 + air}, hit on {hit}, JC on {step}", flush=True)

    # 2. the crossings
    for index in range(passes):
        target = 1 if index % 2 == 0 else 0          # far step point: tail first, then head
        name = "tail" if target == 1 else "head"
        rev = index > 0
        best = None
        for lead in ((10, 13, 16, 19, 22, 26, 30) if rev else (6, 8, 10, 12, 14, 16)):   # how long to fall before the Calibur
            for away, towards in PAIRS:
                trial = (rows + ["Space"] * (step + 4 - len(rows)))[:step + 4]
                hold = "Space Q " if rev else "Space "
                trial += [hold.strip()] * (lead - 5) + [hold + away] * 2 + [f"Space {towards} J"] * 2 + ["Space"] * 30
                trace = search.play(trial, step, step + lead + 30)
                full = hp_of(trace)
                struck = False               # the health value dips on the frame of a hit
                for r in trace:
                    if r[2] in CALIBUR and r[10] is not None and r[10] < 900:
                        struck = True            # the Calibur connected: not a fly-over any more
                    if r[2] in CALIBUR and r[8] and len(r[14]) > target and not struck:
                        # A Calibur flies through the enemy's middle, so the head-side cancel is taken as
                        # far from the tail as it can be: the next Calibur then really crosses to the tail.
                        centre = r[14][1][0]
                        distance = math.hypot(r[11][0] - centre[0], r[11][2] - centre[2])
                        score = distance if target == 1 else -distance
                        if best is None or score < best[7]:
                            best = (distance, away, towards, r[0], r[11], centre, lead, score)
        if best is None:
            print(f"crossing {index + 1} to the {name}: no Calibur with a jump cancel was found; stopping here")
            break
        distance, away, towards, frame, where, centre, lead, _ = best
        if rev:
            for held in range(step + 5, step + lead):
                put(held, "Space Q")
        hold = "Space Q " if rev else "Space "
        put(step + lead, hold + away); put(step + lead + 1, hold + away)
        put(step + lead + 2, f"Space {towards} J"); put(step + lead + 3, f"Space {towards} J")
        for clear in range(step + lead + 4, frame):
            put(clear, "Space")
        put(frame, "Space K"); put(frame + 1, "Space K")
        if rev:
            notes[step + 5] = "rev (hold Exceed)"
        notes[step + lead] = f"Calibur over the enemy to the {name}: away '{away}', then towards '{towards}' + sword"
        notes[frame] = f"JC at the {name} point"
        print(f"crossing {index + 1} to the {name}: away '{away}' towards '{towards}', JC on {frame} at {where}, "
              f"{distance:.0f} from the tail point {centre}", flush=True)
        step = frame
    for frame in range(step + 5, step + 17):
        put(frame, "Space Q")
    notes[step + 5] = "rev (hold Exceed)"
    final = rows + ["Space"] * 45
    trace = search.play(final, start + 1, len(final))
    steps = [r[0] for r in trace if r[9]]
    revs = sorted({r[0] for r in trace if r[2] == REV})
    print("steps happened on", steps, "| rev frames", revs[:1], "...", revs[-1:], "| end:", search.summary(final, len(final) - 1, len(final)))
    out.write_text("\n".join(row + (f"    # {notes[number]}" if number in notes else "")
                             for number, row in enumerate(final, 1)) + "\n", encoding="utf-8", newline="\n")
    search.client.call("load", path=str(out.resolve()))
    search.client.call("goto", count=start)
    print(f"written {out.name}: {len(final)} frames, the combo starts after frame {start}")
    for number, row in enumerate(final, 1):
        if number in notes:
            print(f"  {number}: {row}    # {notes[number]}")


if __name__ == "__main__":
    main()
