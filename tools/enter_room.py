"""Walk the running game from wherever it is (intro movie, title, menus, cutscenes) into a room with
a playable character, by tapping the confirm key in menus and the skip key in movies. For tests
that need a room when nobody is at the keyboard; it takes whatever the menus offer first.

    enter_room.py [seconds]     default limit 240

Keys go in through the scene-synchronised replay of the agent, which (unlike frame stepping) does
not hold the sound threads, so loading screens get through.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from boot_to_room import scene_key  # noqa: E402
from client import Client  # noqa: E402
from keycodes import KEY_CODES  # noqa: E402


def in_room(scene):
    return bool(scene["playerPresent"] and not scene["loading"] and not scene["cutscene"] and scene["roomClass"])


def main():
    limit = time.monotonic() + (int(sys.argv[1]) if len(sys.argv) > 1 else 240)
    client = Client()
    last = None
    while time.monotonic() < limit:
        scene = client.call("record-scene")["scene"]
        if in_room(scene):
            state = client.call("pause")
            print(f"in a room: mission {scene['mission']} {scene['missionClass']} {scene['roomClass']}; "
                  f"frozen: {state.get('parked')}")
            return 0
        if scene["loading"]:
            time.sleep(.3)
            continue
        key = scene_key(scene)
        movie = scene["cutscene"] or scene["gameClass"] == "aAdvertise" or "Demo" in str(scene["missionClass"])
        # Menus: arrow keys move, Enter confirms (seen 2026-11-21). The keyboard key "A" is not a
        # confirm key: mashing it wandered into Options, and "L" there reset the options to default.
        tap = "Escape" if movie else "Enter"
        if key != last:
            print(f"{key}  -> {tap}", flush=True)
            last = key
        rows = [{"count": 4, "keys": [list(KEY_CODES[tap])]}, {"count": 50, "keys": []}]
        client.call("record-replay-start", segments=[{"key": key, "rows": rows}], patience=20)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not client.call("record-replay-status")["done"]:
            time.sleep(.1)
        if client.call("status")["mode"] not in ("observe", "paused"):
            client.call("live")
    print("did not reach a room in time; last scene:", last)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
