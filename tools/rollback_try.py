"""One rollback experiment: reload the service, make sure the game simulates, run the acceptance, summarise.

Usage: rollback_try.py [profile] [mode]      profile: heaps (default) | fields | ...   mode: core | full
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("tas", "work", "tools"):
    sys.path.insert(0, str(ROOT / folder))

from client import Client  # noqa: E402
from rollback import describe_state  # noqa: E402
from tasctl import start_service, target_pid  # noqa: E402

from tasctl import CRASH_LOG as EXCEPTIONS  # noqa: E402
GAME = "DevilMayCry4SpecialEdition.exe"


def alive():
    # tasklist truncates long image names, so ask Frida's process list instead.
    try:
        target_pid()
        return True
    except RuntimeError:
        return False


def sample(client):
    state = client.call("status")
    combat = (state.get("last") or {}).get("combat") or {}
    player = combat.get("player") or {}
    return state["frame"], player.get("motionFrame"), len(combat.get("inputUpdates") or []), player.get("hp"), \
        [(e.get("type"), e.get("hp"), round(e.get("distance") or 0)) for e in combat.get("enemies", [])]


def main():
    profile = sys.argv[1] if len(sys.argv) > 1 else "heaps"
    mode = sys.argv[2] if len(sys.argv) > 2 else "core"
    pid = target_pid()
    try:
        Client().call("shutdown")
        time.sleep(.5)
    except (OSError, RuntimeError):
        pass
    start_service(pid)
    client = Client(pid)
    # The game opens its own pause screen whenever its window loses focus: step, and tap Escape if frozen.
    for attempt in range(3):
        client.call("m1-load", text="1-20: -\n")
        client.call("m1-step", count=20)
        first = sample(client)
        client.call("m1-load", text="1-10: -\n")
        client.call("m1-step", count=10)
        second = sample(client)
        if first[1] != second[1] and second[2]:
            break
        print("units are not simulating, tapping Escape", first, second)
        client.call("m1-load", text="1-3: Escape\n4-40: -\n")
        client.call("m1-step", count=40)
    else:
        print("could not get the units to simulate")
        return 2
    print("simulating:", second)
    print("heal:", client.call("heal")["hp"])
    mark = EXCEPTIONS.stat().st_size if EXCEPTIONS.exists() else 0
    try:
        result = client.call("accept", mode=mode, profile=profile)
        print("accept returned:", result.get("result") if isinstance(result, dict) else result)
    except Exception as error:
        print("accept raised:", str(error)[-300:])
    time.sleep(1.5)
    log_path = ROOT / "logs" / f"rollback-{profile}-{mode}.json"
    log = json.loads(log_path.read_text(encoding="utf-8"))
    snap = log.get("snapshot", {})
    print("snapshot bytes", snap.get("bytes"), "ms", round(snap.get("ms", 0)), "| last_action:", log.get("last_action"))
    for key in ("restore_a", "restore_b", "reset_to_s0"):
        if key in log:
            print(key, {a: (round(b) if isinstance(b, float) else b) for a, b in log[key].items()})
    runs = {name: [describe_state(row) for row in log.get(name, [])] for name in "ABC"}
    print("frames A/B/C:", [len(runs[name]) for name in "ABC"])
    if runs["A"]:
        a = runs["A"]
        print("A moves:", [round(x, 1) for x in a[0]["player.position"]], "->", [round(x, 1) for x in a[-1]["player.position"]],
              "actions", sorted({row["player.action"] for row in a}), "rng states", len({tuple(row["rng"]) for row in a}))
    for other, label in (("B", "A vs B (same input)"), ("C", "A vs C (jump one frame earlier)")):
        if runs["A"] and runs[other]:
            diffs = [(i, sorted({key.split(".")[-1] for key in a if a[key] != b[key]}))
                     for i, (a, b) in enumerate(zip(runs["A"], runs[other]), 1)]
            bad = [item for item in diffs if item[1]]
            print(f"{label}: identical {len(diffs) - len(bad)}/{len(diffs)}, first difference {bad[0] if bad else None}")
    for key in ("baseline", "edit", "effects"):
        if key in log:
            print(key, json.dumps({k: v for k, v in log[key].items() if k in ("result", "expected_frame", "first_difference")}, ensure_ascii=False)[:260])
    print("game alive:", alive())
    if EXCEPTIONS.exists() and EXCEPTIONS.stat().st_size > mark:
        with EXCEPTIONS.open(encoding="utf-8") as handle:
            handle.seek(mark)
            for line in handle:
                item = json.loads(line)
                print("\nEXCEPTION", item["time"], item["at"], item["memory"], "thread", item["thread"], "game thread", item["gameThread"],
                      "mode", item["mode"], "frame", item["frame"], "parked", item["parked"])
                print("  registers", item["registers"])
                print("  trace", item["trace"][:16])
                print("  stack", item["stack"][:40])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
