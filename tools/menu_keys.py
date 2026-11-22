"""Tap keys in menus without holding the sound threads: menu_keys.py KEY[:idle frames] ...

Each key is held 4 frames, followed by `idle` empty frames (default 40). Keys go in through the
agent's scene-synchronised replay (menu_tap.py steps frames instead, which hangs the game when a
tap starts a loading screen). Prints the scene after each tap.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from boot_to_room import scene_key  # noqa: E402
from client import Client  # noqa: E402
from keycodes import KEY_CODES  # noqa: E402


def tap(client, name, idle=40):
    for _ in range(100):
        scene = client.call("record-scene")["scene"]
        if not scene["loading"]:
            break
        time.sleep(.2)
    rows = [{"count": 4, "keys": [list(KEY_CODES[name])]}, {"count": idle, "keys": []}]
    client.call("record-replay-start", segments=[{"key": scene_key(scene), "rows": rows}], patience=15)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not client.call("record-replay-status")["done"]:
        time.sleep(.1)
    if client.call("status")["mode"] not in ("observe", "paused"):
        client.call("live")
    scene = client.call("record-scene")["scene"]
    return scene_key(scene) + (" [loading]" if scene["loading"] else "")


if __name__ == "__main__":
    client = Client()
    for item in sys.argv[1:]:
        name, _, idle = item.partition(":")
        print(f"{name:8} -> {tap(client, name, int(idle or 40))}", flush=True)
