"""Restart the game and walk it into Bloody Palace (Nero), for tests when nobody is at the keyboard.

    enter_bp.py [--keep]     --keep: do not restart, start from where the game is (title or intro)

Menu path seen 2026-11-21: intro movie -> Escape; title -> Enter until the Mission Menu; the cursor
starts on GAME SELECT, Bloody Palace is 8 rows down; Enter, Enter (character), Enter through the
stage title. Leaves the game live in the room (not taken over).
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work"), str(ROOT / "tools")]

from client import Client  # noqa: E402
from menu_keys import tap  # noqa: E402


def scene(client):
    return client.call("record-scene")["scene"]


def main():
    if "--keep" not in sys.argv:
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tas" / "start_game.py"), "--restart"],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            print(result.stdout[-400:], result.stderr[-400:])
            return 1
    client = Client()
    end = time.monotonic() + 240
    downs = None
    while time.monotonic() < end:
        now = scene(client)
        if now["playerPresent"] and not now["loading"] and not now["cutscene"] and now["roomClass"]:
            print(f"in the room: {now['missionClass']} {now['roomClass']}")
            return 0 if now["missionClass"] == "aBloodyPalace" else 1
        if now["loading"] or now["gameClass"] == "aInit":
            time.sleep(.4)
            continue
        if now["gameClass"] == "aAdvertise":
            tap(client, "Escape", 90)
        elif now["gameClass"] == "aGame" and now["missionClass"] == "aMissionSelect" and downs is None:
            time.sleep(1.0)
            for _ in range(8):
                tap(client, "Down", 20)
            downs = 8
            tap(client, "Enter", 60)             # Bloody Palace
        else:
            tap(client, "Enter", 60)
    print("did not get into the room; scene:", scene(client))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
