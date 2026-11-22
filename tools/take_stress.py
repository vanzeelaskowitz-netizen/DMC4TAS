"""Reproduce the 2026-11-19 crash: replay the user's take (lock-on, slash, Q) and jump around in it.

Run in a room with a character and enemies. Loads the take as a timeline from the current frame,
plays it, then does many back / goto / step operations and checks every landing state against the
first pass. Reports how many GPU resources were kept by the guard and whether the game survived.
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

TAKE = ROOT / "runs" / "take-20261119-082814.tas"
OPERATIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 150


def main():
    client = Client()
    state = client.call("pause")
    print("snapshot at frame 0:", bool(state.get("snapshot")), state.get("snapshotUnavailable"))
    loaded = client.call("load", path=str(TAKE))
    frames = loaded["frames"]
    rows = client.call("step", count=frames)
    reference = {row["timelineFrame"]: fingerprint(describe_state(row)) for row in rows}
    status = client.call("status")
    print(f"played the take: {frames} frames, kept GPU resources so far {status['keptResources']}")
    generator = random.Random(1119)
    wrong, position, started = 0, frames, time.perf_counter()
    for index in range(OPERATIONS):
        roll = generator.random()
        if roll < .5 and position > 1:
            result = client.call("back", count=min(generator.choice((1, 1, 2, 5, 20, 80, 250)), position - 1))
            position, last = result["frame"], result["last"]
        elif roll < .75:
            result = client.call("goto", count=generator.randint(1, frames))
            position, last = result["frame"], result["last"]
        else:
            count = min(generator.choice((1, 3, 10, 40)), frames - position)
            if count < 1:
                continue
            last = client.call("step", count=count)[-1]
            position = last["timelineFrame"]
        if fingerprint(describe_state(last)) != reference[position]:
            wrong += 1
    status = client.call("status")
    print(f"{OPERATIONS} operations in {time.perf_counter() - started:.1f}s, states differing from the first pass: {wrong}, "
          f"kept GPU resources {status['keptResources']}, failure {status['failure']}")
    client.call("goto", count=0)
    print("heal:", client.call("heal")["hp"])


if __name__ == "__main__":
    main()
