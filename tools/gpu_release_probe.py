"""Find inputs that make the game release GPU resources, then stress rollback across them.

Plays a long timeline that presses many non-movement keys (devil trigger, items, taunts, weapon
switches...) while snapshots exist, watches the guard's keptResources counter, and if releases did
happen, jumps around in that timeline checking every landing state against the first pass.
"""
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("tas", "work", "tools"):
    sys.path.insert(0, str(ROOT / folder))

from client import Client  # noqa: E402
from rollback import describe_state, fingerprint  # noqa: E402

PROBE = ROOT / "runs" / "gpu_probe.tas"
KEYS = ["Q", "E", "R", "F", "I", "U", "O", "C", "V", "Tab", "LeftShift", "Z", "X", "G", "H", "Y", "T"]


def main():
    lines, frame = [], 1
    for key in KEYS:
        for _ in range(3):
            lines.append(f"{frame}-{frame + 9}: Space {key}")
            lines.append(f"{frame + 10}-{frame + 49}: Space")
            frame += 50
        lines.append(f"{frame}-{frame + 9}: Space J")
        lines.append(f"{frame + 10}-{frame + 39}: -")
        frame += 40
    PROBE.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    total = frame - 1
    client = Client()
    client.call("pause")
    client.call("goto", count=0)
    client.call("load", path=str(PROBE))
    before = client.call("status")["keptResources"]
    reference, per_key, done = {}, {}, 0
    for key_index, key in enumerate(KEYS):
        rows = client.call("step", count=190)
        for row in rows:
            reference[row["timelineFrame"]] = fingerprint(describe_state(row))
        kept = client.call("status")["keptResources"]
        per_key[key] = kept - before - sum(per_key.values())
        done += 190
    print("frames", done, "of", total, "| kept GPU resources per key:", {k: v for k, v in per_key.items() if v})
    kept = client.call("status")["keptResources"] - before
    print("total kept:", kept)
    generator = random.Random(1119)
    wrong, position = 0, done
    started = time.perf_counter()
    for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 200):
        roll = generator.random()
        if roll < .5 and position > 1:
            result = client.call("back", count=min(generator.choice((1, 1, 3, 10, 60, 300, 1200)), position - 1))
            position, last = result["frame"], result["last"]
        elif roll < .75:
            result = client.call("goto", count=generator.randint(1, done))
            position, last = result["frame"], result["last"]
        else:
            count = min(generator.choice((1, 5, 30, 120)), done - position)
            if count < 1:
                continue
            last = client.call("step", count=count)[-1]
            position = last["timelineFrame"]
        if fingerprint(describe_state(last)) != reference[position]:
            wrong += 1
    status = client.call("status")
    print(f"random operations done in {time.perf_counter() - started:.1f}s, differing states: {wrong}, "
          f"kept total {status['keptResources']}, failure {status['failure']}")
    client.call("goto", count=0)
    print("heal:", client.call("heal")["hp"])


if __name__ == "__main__":
    main()
