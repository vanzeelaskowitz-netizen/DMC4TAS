"""Try variants of a script and summarise each: which motions Nero went through and where an
Enemy Step happened. For finding the frames of a combo by experiment.

Used from Python:  summary(rows, first, last) -> text;  the caller builds the rows.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from client import Client  # noqa: E402

SCRATCH = ROOT / "runs" / "combo-search.tas"
client = Client()


def play(rows, first, last):
    """[(frame, action, motion, motion time, grounded, height, distance, enemy motion, can JC, stepped)]"""
    SCRATCH.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")
    client.call("load", path=str(SCRATCH))
    client.call("goto", count=first - 1)
    out = []
    for frame in range(first, last + 1):
        row = client.call("step", count=1)[-1]
        combat = row.get("combat") or {}
        who = combat.get("player") or {}
        enemies = sorted(combat.get("enemies") or [], key=lambda item: item.get("distance") or 0)
        enemy = enemies[0] if enemies else {}
        check = combat.get("stepCheck") or {}
        judged = (check.get("enemies") or {}).get(enemy.get("pointer")) or {}
        out.append((frame, who.get("action"), who.get("motion"), round(who.get("motionFrame") or 0, 1),
                    bool(who.get("grounded")), round((who.get("position") or [0, 0, 0])[1]),
                    round(enemy.get("distance") or 0), enemy.get("motion"),
                    bool(check.get("allowed") and judged.get("can")), bool(combat.get("enemyStep")),
                    enemy.get("hp"), [round(v) for v in (who.get("position") or [0, 0, 0])],
                    (who.get("exceed") or {}).get("gauge"), (row.get("keys") or {}).get("held") or [],
                    # 14: the enemy's step points [(centre, radius, Nero's step point inside)]
                    [([round(v) for v in s["center"]], s["radius"], s["inside"]) for s in enemy.get("spheres") or []
                     if s.get("center")],
                    (who.get("exceed") or {}).get("timer")))
    return out


def summary(rows, first, last):
    trace = play(rows, first, last)
    parts, previous = [], None
    for frame, action, motion, time, ground, height, distance, enemy, can, stepped, hp, *_ in trace:
        if (action, motion) != previous:
            parts.append(f"{frame}:{motion}")
            previous = (action, motion)
        if stepped:
            parts.append(f"{frame}:STEP")
    jc = [item[0] for item in trace if item[8]]
    return " ".join(parts) + f" | JC frames {jc[:1]}..{jc[-1:]} ({len(jc)}) | end y {trace[-1][5]} d {trace[-1][6]} em {trace[-1][7]}"
