"""Try input scripts from the start of the current timeline and report the animations they produce.

Each candidate is loaded, played from frame 0 for a number of frames, and its animation changes are
listed with what the character read from the keyboard. The script that was loaded before is put
back afterwards (same file, same frame). Usage: move_try.py <frames> <script file> [script file ...]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from client import Client  # noqa: E402

DIRECTIONS = {1: "前", 2: "后", 4: "左", 8: "右"}


def bits(value):
    return "".join(label for bit, label in DIRECTIONS.items() if value & bit) or "-"


def main():
    frames = int(sys.argv[1])
    client = Client()
    state = client.call("status")
    original, position = state.get("timelinePath"), state.get("timelineFrame") or 0
    try:
        for name in sys.argv[2:]:
            path = Path(name).resolve()
            client.call("load", path=str(path))
            client.call("goto", count=0)
            rows = client.call("step", count=frames)
            print(f"== {path.name}")
            last = None
            for row in rows:
                player = ((row.get("combat") or {}).get("player")) or {}
                motion = player.get("motion")
                held = " ".join(row["keys"]["held"]) or "-"
                direction = (player.get("direction") or {}).get("held") or 0
                mark = "  <-- 新动画" if motion != last else ""
                if motion != last or row["timelineFrame"] <= 14:
                    print(f"  帧{row['timelineFrame']:3d}  键 {held:14s} 角色方向 {bits(direction):3s} "
                          f"锁定 {'有' if player.get('lockTarget') else '无'}  动作 {player.get('action')} "
                          f"动画 {motion}{mark}")
                last = motion
    finally:
        if original:
            client.call("load", path=original)
            client.call("goto", count=position)
            print(f"put back {Path(original).name} at frame {position}")


if __name__ == "__main__":
    main()
