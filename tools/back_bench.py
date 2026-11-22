"""Timing and correctness of stepping back: reload the service, run a timeline, then step back repeatedly."""
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("tas", "work", "tools"):
    sys.path.insert(0, str(ROOT / folder))

from client import Client  # noqa: E402
from rollback import describe_state, fingerprint  # noqa: E402
from tasctl import start_service, target_pid  # noqa: E402

TIMELINE = ROOT / "runs" / "demo_hit.tas"
FRAMES = 860
BACKS = int(sys.argv[1]) if len(sys.argv) > 1 else 24
RANDOM = int(sys.argv[2]) if len(sys.argv) > 2 else 0


def motion(client):
    return ((client.call("status").get("last") or {}).get("combat") or {}).get("player", {}).get("motionFrame")


def main():
    for player in ():
        pass
    try:
        Client().call("shutdown")
        time.sleep(.5)
    except (OSError, RuntimeError):
        pass
    start_service(target_pid())
    client = Client()
    for _ in range(3):
        client.call("m1-load", text="1-20: -\n"); client.call("m1-step", count=20); a = motion(client)
        client.call("m1-load", text="1-10: -\n"); client.call("m1-step", count=10); b = motion(client)
        if a != b:
            break
        client.call("m1-load", text="1-3: Escape\n4-40: -\n"); client.call("m1-step", count=40)
    print("simulating:", a != b, "heal:", client.call("heal")["hp"])
    first_save = client.call("pause").get("snapshot")
    print("first (full) snapshot ms:", round(first_save["milliseconds"]) if first_save else None)
    client.call("load", path=str(TIMELINE))
    started = time.perf_counter()
    rows = client.call("step", count=FRAMES)
    elapsed = time.perf_counter() - started
    reference = {row["timelineFrame"]: fingerprint(describe_state(row)) for row in rows}
    print(f"step {FRAMES}: {elapsed:.1f}s ({FRAMES / elapsed:.0f} fps including checkpoints)")
    save = client.call("save")
    print(f"delta checkpoint: {save['milliseconds']:.0f} ms, {save['snapshot_bytes'] / 1e6:.1f} MB, retained {save['retained_bytes'] / 1e6:.0f} MB")

    print("checkpoint tracked by write watch:", save.get("tracked"), "| checkpoints kept:", save.get("checkpoints"),
          "| verification failures so far:", save.get("verify_failures"))
    times, replayed, equal, details = [], [], [], []
    checks = {"tracked": 0, "full": 0, "verified": 0, "mismatched_bytes": 0}

    def account(restored):
        if not restored:
            return
        checks["tracked" if restored.get("tracked") else "full"] += 1
        verification = restored.get("verification")
        if verification:
            checks["verified"] += 1
            checks["mismatched_bytes"] += verification["mismatched_bytes"]

    for index in range(BACKS):
        started = time.perf_counter()
        result = client.call("back", count=1)
        times.append(time.perf_counter() - started)
        replayed.append(result["replayed"])
        equal.append(fingerprint(describe_state(result["last"])) == reference[result["frame"]])
        account(result["restored"])
        if result["restored"] and len(details) < 3:
            details.append({k: round(v) for k, v in result["restored"].items()
                            if k.endswith("ms") or k in ("milliseconds", "written", "dirty_pages")})
    print(f"{BACKS} x back 1: seconds", [round(t, 3) for t in times[:40]])
    print("             replayed frames", replayed[:40])
    print(f"             median {statistics.median(times):.2f}s, first {times[0]:.2f}s, worst {max(times):.2f}s, all identical: {all(equal)}")
    print("restore breakdown (first three):", details)
    started = time.perf_counter()
    forward = client.call("goto", count=FRAMES)
    print(f"goto {FRAMES}: {time.perf_counter() - started:.2f}s identical: {fingerprint(describe_state(forward['last'])) == reference[FRAMES]}")
    for count in (10, 100, 500):
        started = time.perf_counter()
        result = client.call("back", count=count)
        took = time.perf_counter() - started
        account(result["restored"])
        print(f"back {count}: {took:.2f}s replayed {result['replayed']} identical: {fingerprint(describe_state(result['last'])) == reference[result['frame']]}")
        account(client.call("goto", count=FRAMES)["restored"])
    # Random walk: any mix of stepping, going back and jumping must always land on the reference state.
    if RANDOM:
        generator = random.Random(20261118)
        position, wrong, kinds, slow = FRAMES, 0, {"back": 0, "goto": 0, "step": 0}, 0.0
        started = time.perf_counter()
        for _ in range(RANDOM):
            roll = generator.random()
            before = time.perf_counter()
            if roll < 0.45 and position > 1:
                count = generator.choice((1, 1, 1, 2, 3, 5, 9, 17, 40, 130, 400))
                count = min(count, position - 1)
                result = client.call("back", count=count)
                kinds["back"] += 1
                position, last = result["frame"], result["last"]
                account(result["restored"])
            elif roll < 0.7:
                target = generator.randint(1, FRAMES)
                result = client.call("goto", count=target)
                kinds["goto"] += 1
                position, last = result["frame"], result["last"]
                account(result["restored"])
            else:
                count = min(generator.choice((1, 1, 2, 4, 8, 30)), FRAMES - position)
                if count < 1:
                    continue
                rows = client.call("step", count=count)
                kinds["step"] += 1
                position, last = rows[-1]["timelineFrame"], rows[-1]
            slow = max(slow, time.perf_counter() - before)
            if fingerprint(describe_state(last)) != reference[position]:
                wrong += 1
        print(f"random walk: {RANDOM} operations {kinds} in {time.perf_counter() - started:.1f}s, "
              f"slowest {slow:.2f}s, states differing from the reference: {wrong}")
    print("restores:", checks, "| engine verification failures:", client.call("save").get("verify_failures"))
    final = client.call("goto", count=0)
    print("back at frame", final["frame"], "| heal:", client.call("heal")["hp"])


if __name__ == "__main__":
    main()
