"""Tap keys in the game through the frame stepper (for menu navigation): menu_tap.py KEY[:idle] ...

Each argument is a key name (or '-' for no key) held for 3 frames, followed by `idle` empty frames
(default 60). Prints the scene state afterwards.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tas"))

from client import Client  # noqa: E402


def main():
    client = Client()
    client.call("m1-pause")
    for item in sys.argv[1:]:
        key, _, idle = item.partition(":")
        idle = int(idle or 60)
        text = (f"1-3: {key}\n" if key != "-" else "1-3: -\n") + f"4-{3 + idle}: -\n"
        client.call("m1-load", text=text)
        client.call("m1-step", count=3 + idle)
    scene = client.call("record-scene")["scene"]
    print({k: scene[k] for k in ("gameClass", "missionClass", "roomClass", "loading", "cutscene", "playerPresent", "paused")},
          [m["name"] + ":" + str(m["state"]) for m in scene["menus"]])


if __name__ == "__main__":
    main()
