"""Where does a page-tracked restore spend its time? Profiles the service-side call from inside the service."""
import cProfile
import io
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in ("tas", "work", "tools"):
    sys.path.insert(0, str(ROOT / folder))

from rollback import Rollback  # noqa: E402
from tasctl import Controller, target_pid  # noqa: E402
from timeline import read_script  # noqa: E402


def main():
    """Runs its own controller (the resident service must be stopped first)."""
    controller = Controller(target_pid(), start_parked=True)
    with controller:
        controller.pause()
        route = Rollback(controller)
        route.pause()
        route.save()
        route.load(str(ROOT / "runs" / "demo_hit.tas"))
        route.step(200)
        times = []
        profiler = cProfile.Profile()
        for index in range(40):
            started = time.perf_counter()
            profiler.enable()
            result = route.back(1)
            profiler.disable()
            times.append((time.perf_counter() - started) * 1000)
            if index % 8 == 7:
                route.goto(200)
        times.sort()
        print("back 1 ms: min %.0f median %.0f max %.0f" % (times[0], times[len(times) // 2], times[-1]))
        print("last restore:", {k: (round(v, 1) if isinstance(v, float) else v) for k, v in (result["restored"] or {}).items()})
        out = io.StringIO()
        pstats.Stats(profiler, stream=out).sort_stats("cumulative").print_stats(22)
        print(out.getvalue()[:5200])
        route.goto(0)


if __name__ == "__main__":
    main()
