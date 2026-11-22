"""End-to-end check of the user workflow: load, step, back, goto, edit the file, goto again."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("tas", "work", "tools"):
    sys.path.insert(0, str(ROOT / folder))

from client import Client  # noqa: E402
from rollback import describe_state, fingerprint  # noqa: E402

DEMO = ROOT / "runs" / "demo.tas"
ORIGINAL = """1-200: Space W
201: Space J
202-215: Space
216: Space J
217-240: Space
241: Space K
242-270: Space
271: Space K
272-300: Space
"""
EDITED = ORIGINAL.replace("1-200: Space W\n201: Space J\n202-215: Space\n", "1-199: Space W\n200: Space J\n201-215: Space\n")


def brief(row):
    combat = row["combat"]
    player = combat["player"]
    enemies = [(e["type"], e["hp"], round(e["distance"])) for e in combat["enemies"]]
    return (f"帧 {row.get('timelineFrame', '?')} 键 {row['keys']['held']} 动作 {player['action']} "
            f"游标 {player['motionFrame']:.1f} 生命 {player['hp']:.0f} 敌人 {enemies} 事件 {combat.get('events')}")


def events(rows):
    steps = [r["timelineFrame"] for r in rows if r["combat"].get("enemyStep") or r["combat"].get("events")]
    hits = []
    last = None
    for r in rows:
        hp = tuple(e["hp"] for e in r["combat"]["enemies"])
        if last is not None and hp != last:
            hits.append((r["timelineFrame"], last, hp))
        last = hp
    frozen = [r["timelineFrame"] for r in rows if any(e.get("frozen") for e in r["combat"]["enemies"])]
    return {"enemy_step_or_event_frames": steps[:8], "enemy_hp_changes": hits[:6], "enemy_frozen_frames": frozen[:12]}


def main():
    client = Client()
    DEMO.write_text(ORIGINAL, encoding="utf-8", newline="\n")
    state = client.call("pause")
    print("pause:", {k: state.get(k) for k in ("mode", "parked", "frame")}, "snapshot:", state.get("snapshot") or state.get("snapshotUnavailable"))
    print("load:", client.call("load", path=str(DEMO)))
    started = time.perf_counter()
    rows = client.call("step", count=300)
    print(f"step 300: {time.perf_counter() - started:.1f}s  ->", brief(rows[-1]))
    print("  events:", events(rows))
    first = {row["timelineFrame"]: fingerprint(describe_state(row)) for row in rows}
    status = client.call("status")
    print("  snapshots at timeline frames:", status.get("snapshots"), "timelineFrame", status.get("timelineFrame"))

    started = time.perf_counter()
    back = client.call("back", count=3)
    print(f"back 3: {time.perf_counter() - started:.2f}s  now frame {back['frame']} restored from {back['restored'] and back['restored']['frame']} replayed {back['replayed']}")
    print("  state equals first pass at this frame:", fingerprint(describe_state(back["last"])) == first[back["frame"]], "|", brief(back["last"]))

    started = time.perf_counter()
    again = client.call("goto", count=300)
    print(f"goto 300: {time.perf_counter() - started:.2f}s  state equals first pass:", fingerprint(describe_state(again["last"])) == first[300])

    started = time.perf_counter()
    far = client.call("back", count=250)
    print(f"back 250: {time.perf_counter() - started:.2f}s  now frame {far['frame']} replayed {far['replayed']} equal:",
          fingerprint(describe_state(far["last"])) == first[far["frame"]])

    DEMO.write_text(EDITED, encoding="utf-8", newline="\n")
    started = time.perf_counter()
    edited = client.call("goto", count=300)
    print(f"edit file (slash one frame earlier) + goto 300: {time.perf_counter() - started:.2f}s  restored from {edited['restored'] and edited['restored']['frame']}")
    print("  state differs from first pass:", fingerprint(describe_state(edited["last"])) != first[300], "|", brief(edited["last"]))

    DEMO.write_text(ORIGINAL, encoding="utf-8", newline="\n")
    reverted = client.call("goto", count=300)
    print("revert file + goto 300: state equals first pass:", fingerprint(describe_state(reverted["last"])) == first[300])
    final = client.call("status")
    print("final:", {k: final.get(k) for k in ("mode", "parked", "frame", "timelineFrame", "snapshots")})


if __name__ == "__main__":
    main()
