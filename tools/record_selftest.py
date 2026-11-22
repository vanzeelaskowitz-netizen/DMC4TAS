"""Self-test of the native record mode: restart the service live, record a few seconds, report timing."""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tas"))

from client import Client  # noqa: E402
from tasctl import start_service, target_pid  # noqa: E402

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0


def main():
    pid = target_pid()
    try:
        print("old service:", Client().call("shutdown"))
        time.sleep(1.0)
    except (OSError, RuntimeError) as error:
        print("no old service:", error)
    print("start:", {k: v for k, v in start_service(pid, live=True).items() if k in ("mode", "parked", "frame", "virtual")})
    client = Client(pid)
    scene = client.call("record-scene")
    print("scene:", json.dumps({k: scene["scene"][k] for k in ("gameClass", "missionClass", "roomClass", "loading", "cutscene", "playerPresent")}, ensure_ascii=False),
          "title:", scene["title"], "windowed:", scene["windowed"])
    if not scene["title"]:
        print("not at the title screen; skipping the record test")
        return 2
    everywhere = "everywhere" in sys.argv[2:]
    print("virtual clock everywhere (test):", everywhere)
    start = client.call("record-start", everywhere=everywhere)
    rows = []
    began = time.perf_counter()
    while time.perf_counter() - began < SECONDS:
        time.sleep(0.2)
        batch = client.call("record-poll")
        rows += batch["rows"]
        if batch["reason"]:
            print("recording ended early:", batch["reason"])
            break
    elapsed = time.perf_counter() - began
    finish = client.call("record-stop")
    rows += finish["rows"]
    deltas = [row["deltaSeconds"] for row in rows if row["deltaSeconds"] is not None]
    ticks = [b["qpcTicks"] - a["qpcTicks"] for a, b in zip(rows, rows[1:])]
    print(f"frames={len(rows)} first={start['frame'] + 1} wall={elapsed:.2f}s fps={len(rows) / elapsed:.1f}")
    if deltas:
        print(f"deltaSeconds min={min(deltas):.6f} max={max(deltas):.6f} (1/60={1 / 60:.6f})")
    if ticks:
        print(f"engine qpc step min={min(ticks)} max={max(ticks)}")
    units = [row["deltaUnits"] for row in rows if row.get("deltaUnits") is not None]
    print("virtualClock flags:", sorted({row.get("virtualClock") for row in rows}),
          "deltaUnits min/max:", (round(min(units), 5), round(max(units), 5)) if units else None)
    print("held keys seen:", sorted({key for row in rows for key in row["held"]}))
    print("reads per frame:", sorted({row["reads"] for row in rows}), "native results:", sorted({row["inputResult"] for row in rows}))
    print("end:", {k: finish[k] for k in ("battle", "title", "parked", "reason")})
    print("status:", {k: v for k, v in client.call("status").items()
                      if k in ("mode", "parked", "virtual", "frame", "engineHits", "engineUsed", "deltaEsi", "clockObject", "entryCount", "deltaHits", "siteHits")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
