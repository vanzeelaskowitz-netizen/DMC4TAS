"""Play a script from frame 0 of the current timeline and print what Nero does on each frame.

    combo_try.py SCRIPT.tas [first frame to print] [last frame]

Columns: frame, keys, action, motion@time, G(round)/A(ir), height, distance to the nearest enemy,
that enemy's motion, JC = an Enemy Step would work this frame, STEP = one happened, ex = Exceed gauge.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from client import Client  # noqa: E402


def main():
    path = Path(sys.argv[1]).resolve()
    first = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    client = Client()
    state = client.call("status")
    if state.get("mode") == "observe" or state.get("timelineFrame") is None:
        if client.call("record-scene")["scene"].get("paused"):
            client.call("unpause")
        client.call("pause")
    frames = client.call("load", path=str(path))["frames"]
    last = int(sys.argv[3]) if len(sys.argv) > 3 else frames
    client.call("goto", count=max(0, first - 1))
    previous = None
    for frame in range(first, last + 1):
        row = client.call("step", count=1)[-1]
        combat = row.get("combat") or {}
        who = combat.get("player") or {}
        enemies = sorted(combat.get("enemies") or [], key=lambda item: item.get("distance") or 0)
        enemy = enemies[0] if enemies else {}
        check = combat.get("stepCheck") or {}
        judged = (check.get("enemies") or {}).get(enemy.get("pointer")) or {}
        can = bool(check.get("allowed") and judged.get("can"))
        keys = " ".join((row.get("keys") or {}).get("held") or [])
        text = (f"{frame:4} {keys:14} act {who.get('action'):>3} mot {who.get('motion'):>4}@{who.get('motionFrame') or 0:5.1f} "
                f"{'G' if who.get('grounded') else 'A'} y {(who.get('position') or [0, 0, 0])[1]:6.0f} "
                f"d {enemy.get('distance') or 0:5.0f} em {enemy.get('motion')} "
                f"{'JC' if can else '  '} {'STEP' if combat.get('enemyStep') else '    '} ex {(who.get('exceed') or {}).get('gauge')}")
        mark = (who.get("action"), who.get("motion"))
        print(text + ("   <--" if mark != previous else ""), flush=True)
        previous = mark


if __name__ == "__main__":
    main()
