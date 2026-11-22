"""Does step-back work in a room with no enemies? Kill what is there with scripted input, then test."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("tas", "work", "tools"):
    sys.path.insert(0, str(ROOT / folder))

from client import Client  # noqa: E402
from tasctl import start_service, target_pid  # noqa: E402

FIGHT = ROOT / "runs" / "clear_room.tas"
WALK = ROOT / "runs" / "empty_walk.tas"


def digest(row):
    """State that exists without enemies: the player and the random number generator."""
    combat = row["combat"]
    player = combat["player"]
    return (tuple(player["position"]), player["action"], player["motion"], player["motionFrame"],
            player["hp"], tuple(combat["rng"]))


def enemies(row):
    return [(e["type"], e["hp"], round(e["distance"])) for e in row["combat"]["enemies"]]


def main():
    try:
        Client().call("shutdown")
        time.sleep(.5)
    except (OSError, RuntimeError):
        pass
    start_service(target_pid())
    client = Client()
    client.call("pause")
    # Lock on, run in, slash for a long time; repeat until nothing is left.
    lines, frame = [], 1
    for _ in range(12):
        lines.append(f"{frame}-{frame + 179}: Space W")
        frame += 180
        for _ in range(30):
            lines.append(f"{frame}: Space J")
            lines.append(f"{frame + 1}-{frame + 13}: Space")
            frame += 14
    FIGHT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    client.call("load", path=str(FIGHT))
    row = None
    for chunk in range(40):
        rows = client.call("step", count=200)
        row = rows[-1]
        client.call("heal")
        if not row["combat"]["enemies"]:
            break
    print("after the fight: timeline frame", row.get("timelineFrame"), "enemies left", enemies(row),
          "player present", bool(row["combat"]["player"]))
    if row["combat"]["enemies"]:
        print("could not clear the room; test not run")
        return 1
    for _ in range(5):                       # let death effects and any room event settle
        row = client.call("step", count=120)[-1]
    scene = client.call("record-scene")["scene"]
    print("scene:", {k: scene[k] for k in ("missionClass", "roomClass", "loading", "cutscene", "playerPresent")},
          "enemies", enemies(row))
    # Fresh timeline in the empty room.
    client.call("detach")
    client.call("attach")
    state = client.call("pause")
    print("snapshot in the empty room:", state.get("snapshot") or state.get("snapshotUnavailable"))
    WALK.write_text("1-60: W\n61-120: D\n121: K\n122-180: -\n181-240: S\n241-300: A\n", encoding="utf-8", newline="\n")
    client.call("load", path=str(WALK))
    rows = client.call("step", count=300)
    reference = {r["timelineFrame"]: digest(r) for r in rows}
    moved = reference[1][0] != reference[300][0]
    results = []
    for count in (1, 7, 50, 250, 299):
        back = client.call("back", count=count)
        same = digest(back["last"]) == reference[back["frame"]]
        again = client.call("goto", count=300)
        results.append((count, back["frame"], same, digest(again["last"]) == reference[300],
                        bool(back["restored"] and back["restored"].get("tracked"))))
    print("player moved during the walk:", moved)
    print("back N -> (N, frame, identical there, identical after returning to 300, page-tracked):")
    for item in results:
        print("  ", item)
    WALK.write_text("1-60: W\n61-120: D\n121-180: -\n181-240: S\n241-300: A\n", encoding="utf-8", newline="\n")
    edited = client.call("goto", count=300)
    print("after removing the jump at frame 121, state at 300 differs:", digest(edited["last"]) != reference[300])
    print("save stats:", {k: v for k, v in client.call("save").items() if k in ("tracked", "checkpoints", "verify_failures")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
