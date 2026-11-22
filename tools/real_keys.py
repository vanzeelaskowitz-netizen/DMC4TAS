"""Send real key presses to the focused game window: real_keys.py KEY[:hold_ms[:wait_ms]] ...

Used for menu navigation and for testing takes the way a player would produce them. KEY is a
letter, or one of Escape Space Multiply Divide Add Subtract. Focuses the game first; pass --back
to give the focus back to the previous window afterwards.
"""
import ctypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tas"))

from client import Client  # noqa: E402

NAMES = {"Escape": 0x1B, "Space": 0x20, "Multiply": 0x6A, "Divide": 0x6F, "Add": 0x6B, "Subtract": 0x6D,
         "Enter": 0x0D}
user32 = ctypes.windll.user32


def focus(window):
    user32.keybd_event(0x12, 0, 0, 0)
    user32.SetForegroundWindow(window)
    user32.keybd_event(0x12, 0, 2, 0)


def main():
    arguments = [item for item in sys.argv[1:] if item != "--back"]
    client = Client()
    game = int(client.call("status")["gameWindow"], 16)
    before = user32.GetForegroundWindow()
    if before != game:
        focus(game)
        time.sleep(.5)
    for item in arguments:
        parts = item.split(":")
        key = parts[0]
        hold = int(parts[1]) / 1000 if len(parts) > 1 and parts[1] else .08
        wait = int(parts[2]) / 1000 if len(parts) > 2 else .3
        codes = [NAMES.get(name, ord(name.upper()) if len(name) == 1 else 0) for name in key.split("+")]
        for code in codes:
            user32.keybd_event(code, user32.MapVirtualKeyW(code, 0), 0, 0)
        time.sleep(hold)
        for code in reversed(codes):
            user32.keybd_event(code, user32.MapVirtualKeyW(code, 0), 2, 0)
        time.sleep(wait)
    if "--back" in sys.argv and before != game:
        focus(before)
    scene = client.call("record-scene")["scene"]
    print({k: scene[k] for k in ("gameClass", "missionClass", "roomClass", "loading", "cutscene", "playerPresent", "paused")})


if __name__ == "__main__":
    main()
