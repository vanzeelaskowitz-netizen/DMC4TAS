"""Make enemies die inside a timeline (HP set to 1 before frame 0) and stress rollback across the deaths."""
import random
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("tas", "work", "tools"):
    sys.path.insert(0, str(ROOT / folder))

from client import Client  # noqa: E402
from rollback import HP_FIELDS, describe_state, fingerprint, object_size  # noqa: E402
from rollback_memory import ProcessMemory  # noqa: E402

TIMELINE = ROOT / "runs" / "demo_hit.tas"
UDAMAGE_VTABLE = 0xF2D184


def counters(client):
    status = client.call("status")
    return {k: status[k] for k in ("releaseCalls", "finalReleases", "keptResources")}


def main():
    client = Client()
    client.call("live")
    client.call("m1-pause")
    client.call("m1-load", text="1: -\n")
    row = client.call("m1-step", count=1)[-1]
    enemies = row["combat"]["enemies"]
    with ProcessMemory(client.call("status")["gamePid"], write=True) as memory:
        for enemy in enemies:
            actor = int(enemy["pointer"], 16)
            size = object_size(memory, actor)
            offsets = [o for o in HP_FIELDS if o + 0x34 <= size and memory.u32(actor + o) == UDAMAGE_VTABLE]
            if len(offsets) == 1:
                memory.write(actor + offsets[0] + 0x30, struct.pack("<f", 1.0))
    print("enemies set to 1 HP:", [(e["type"], round(e["distance"])) for e in enemies])
    client.call("heal")
    state = client.call("pause")
    print("frame 0 snapshot:", bool(state.get("snapshot")), "| counters", counters(client))
    frames = client.call("load", path=str(TIMELINE))["frames"]
    rows = client.call("step", count=frames)
    reference = {r["timelineFrame"]: fingerprint(describe_state(r)) for r in rows}
    alive = [(r["timelineFrame"], len(r["combat"]["enemies"])) for r in rows]
    changes = [alive[i] for i in range(1, len(alive)) if alive[i][1] != alive[i - 1][1]]
    hp = [(r["timelineFrame"], [e["hp"] for e in r["combat"]["enemies"]]) for r in rows[::100]]
    print("enemy count changes (frame, count):", changes, "| hp every 100 frames:", hp)
    print("after first pass:", counters(client))
    generator = random.Random(20261119)
    wrong, position, started = 0, frames, time.perf_counter()
    for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 200):
        roll = generator.random()
        if roll < .5 and position > 1:
            result = client.call("back", count=min(generator.choice((1, 1, 3, 10, 60, 300)), position - 1))
            position, last = result["frame"], result["last"]
        elif roll < .75:
            result = client.call("goto", count=generator.randint(1, frames))
            position, last = result["frame"], result["last"]
        else:
            count = min(generator.choice((1, 5, 30, 120)), frames - position)
            if count < 1:
                continue
            last = client.call("step", count=count)[-1]
            position = last["timelineFrame"]
        if fingerprint(describe_state(last)) != reference[position]:
            wrong += 1
    print(f"random operations in {time.perf_counter() - started:.1f}s, differing states: {wrong}, "
          f"{counters(client)}, failure {client.call('status')['failure']}")
    client.call("goto", count=0)
    print("back at frame 0, heal:", client.call("heal")["hp"])


if __name__ == "__main__":
    main()
