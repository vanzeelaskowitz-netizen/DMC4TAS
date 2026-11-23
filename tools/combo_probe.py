"""Print what a few input rows do from the current timeline frame.

    combo_probe.py BASE.tas START "Space I*2" "Space*60"      each argument: keys*count
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work"), str(ROOT / "tools")]

import combo_search as search  # noqa: E402
from timeline import expand_text  # noqa: E402


import os
ALL = bool(os.environ.get('PROBE_ALL'))


def main():
    base, start = Path(sys.argv[1]), int(sys.argv[2])
    state = {"timelinePath": str(base), "mode": "-"}
    rows = [line for line in expand_text(base.read_text(encoding="utf-8")).split("\n") if not line.startswith("@key")]
    rows = [line.split("#")[0].rstrip() for line in rows]
    rows = (rows + [""] * start)[:start]
    print("start", start, "mode", state.get("mode"), "file", Path(state["timelinePath"]).name, flush=True)
    extra = []
    for spec in sys.argv[3:]:
        keys, _, count = spec.partition("*")
        extra += [keys.strip()] * int(count or 1)
    trial = rows + extra
    trace = search.play(trial, start + 1, len(trial))
    last = None
    for r in trace:
        sig = (r[1], r[2], r[4], r[7], r[8], r[9])
        if sig != last or ALL:
            print(f"  f{r[0]} action {r[1]} motion {r[2]} ground {r[4]} h {r[5]:.0f} dist {r[6]:.0f} em {r[7]} can {r[8]} "
                  f"stepped {r[9]} hp {r[10]} keys '{r[13]}'" + (f" at {r[11]} points {r[14]}" if ALL else ""))
            last = sig
    r = trace[-1]
    print("  end: Nero", r[11], "dist", round(r[6]), "points", r[14])
    search.client.call("goto", count=start)


if __name__ == "__main__":
    main()
