"""Run snapshot acceptance through the resident DMC4SE TAS service."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from statistics import median
from time import perf_counter
import traceback

from client import Client
from tasctl import ROOT
from rollback import DEFAULT_PROFILE, SNAPSHOT_PROFILES, SnapshotEngine, describe_state, fingerprint
from rollback_memory import MEM_PRIVATE, ProcessMemory, changed_runs
from timeline import Timeline, format_script, frames, parse_script


BASELINE = """1-18: Space S
19: Space J
20-54: Space
55: Space K
56-86: Space
87: Space K
88-120: Space
"""


def census(controller, timeline):
    start = perf_counter()
    with controller.frame_gate() as (gate, _), ProcessMemory(controller.pid) as memory:
        base = int(gate["base"], 16)
        select = lambda r: r.writable and (r.kind == MEM_PRIVATE or r.allocation == base)
        with memory.quiescent(gate["tid"]) as threads:
            original = {r: memory.read(r.base, r.end - r.base) for r in memory.regions() if select(r)}
    capture_ms = (perf_counter() - start) * 1000
    controller.load(timeline)
    rows = controller.step(120)
    changed = []
    with controller.frame_gate() as (end, _), ProcessMemory(controller.pid) as memory:
        with memory.quiescent(end["tid"]):
            current = {r for r in memory.regions() if select(r)}
            for region, before in original.items():
                if region not in current:
                    changed.append(asdict(region) | {"mapping_changed": True})
                    continue
                after = memory.read(region.base, len(before))
                ranges = [(region.base + a, b - a) for a, b in changed_runs(before, after)]
                if ranges:
                    changed.append(asdict(region) | {"ranges": ranges,
                                                    "changed_bytes": sum(n for _, n in ranges)})
    result = {"pid": controller.pid, "regions": len(original), "bytes": sum(map(len, original.values())),
              "threads": threads, "capture_ms": capture_ms, "changed": changed,
              "added": [asdict(r) for r in sorted(current - original.keys(), key=lambda r: r.base)],
              "before": gate["combat"], "after": end["combat"], "frames": rows}
    write_log("rollback-combat-census.json", result)
    return {"regions": len(original), "bytes": result["bytes"], "changed_regions": len(changed),
            "changed_bytes": sum(r.get("changed_bytes", 0) for r in changed),
            "added_regions": len(result["added"]),
            "combat_updates": sum(bool(row["combat"]["inputUpdates"]) for row in rows)}


def write_log(name, data):
    (ROOT / "logs" / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def serial(rows):
    return [describe_state(row) for row in rows]


def first_difference(a, b):
    for index, (old, new) in enumerate(zip(a, b, strict=True), 1):
        if old != new:
            delta = {key: [old.get(key), new.get(key)] for key in dict.fromkeys((*old, *new))
                     if old.get(key) != new.get(key) or (key in old) != (key in new)}
            return {"frame": index, "fields": delta}
    return None


def events(rows):
    return {
        "enemy_step": [i for i, row in enumerate(rows, 1) if row["combat"]["enemyStep"]],
        "player_hitstop": [i for i, row in enumerate(rows, 1) if row["combat"]["player"]["frozen"]],
        "enemy_hitstop": [i for i, row in enumerate(rows, 1)
                          if any(enemy["frozen"] == 1 for enemy in row["combat"]["enemies"] or ())],
    }


def earlier_jump(timeline, jump):
    rows = {number: held for number, held, _, _ in frames(timeline.rows)}
    if jump <= 1 or "K" in rows[jump-1] or "K" not in rows[jump] or "K" in rows.get(jump+1, ()):
        raise ValueError("Choose a one-frame K tap preceded and followed by a release")
    rows[jump-1] = rows[jump-1] | {"K"}
    rows[jump] = rows[jump] - {"K"}
    return Timeline([(i, i, keys) for i, keys in rows.items()], timeline.bindings)


def play(controller, plan, count):
    controller.load(plan)
    return controller.step(count)


def presents(rows):
    return sum(row["presents"] == 1 and row["presentResult"] == 0 for row in rows)


def run(server, mode="core", profile=DEFAULT_PROFILE, text=BASELINE, jump=55, effects_text=None):
    from service import heal

    controller = server.ctl
    controller.pause()
    if mode == "inspect":
        with controller.frame_gate() as (gate, _):
            return gate
    timeline = parse_script(text)
    if mode == "census":
        return census(controller, timeline)

    engine = SnapshotEngine(controller, profile)
    log = {"pid": controller.pid, "profile": profile}
    name = f"rollback-{profile}-{mode}.json"
    s0 = None
    def restore(label):
        log["last_action"] = label
        def trace(patches):
            log["write_plan"] = {"ranges": patches, "bytes": sum(size for _, size in patches)}
            write_log(name, {key: value for key, value in log.items() if key not in {"A", "B", "C"}})
        return engine.restore(s0, trace)

    try:
        log["heal"] = heal(controller)
        s0 = engine.capture()
        log["snapshot"] = {"frame": s0.frame, "ms": s0.milliseconds, "bytes": s0.bytes,
                           "threads": s0.threads, "spans": [asdict(span) for span, _ in s0.blocks]}
        write_log(name, log)
        log["input"] = text
        a = play(controller, timeline, 120)
        log["A"] = a
        write_log(name, log)
        log["restore_a"] = restore("restore S0 after run A")
        b = play(controller, timeline, 120)
        log["B"] = b
        state_a, state_b = serial(a), serial(b)
        log["fingerprints"] = {"A": list(map(fingerprint, state_a)), "B": list(map(fingerprint, state_b))}
        delta = first_difference(state_a, state_b)
        log["baseline"] = {"result": "PASS" if log["fingerprints"]["A"] == log["fingerprints"]["B"]
                           and all(r["combat"]["inputUpdates"] for r in a + b)
                           else "FAIL", "first_difference": delta, "presents": [presents(a), presents(b)]}
        write_log(name, log)
        log["restore_b"] = restore("restore S0 for one-frame jump edit")
        c = play(controller, earlier_jump(timeline, jump), 120)
        log["C"] = c
        state_c = serial(c)
        log["fingerprints"]["C"] = list(map(fingerprint, state_c))
        delta = first_difference(state_a, state_c)
        # The key moves from frame `jump` to `jump - 1`. A ground jump shows up in the tracked state
        # one frame after the key (measured 2026-11-18: key at 54, action changes at 55), so the
        # first difference may be the new key frame or the one after it; everything earlier is equal.
        log["edit"] = {"expected_frame": [jump - 1, jump], "first_difference": delta,
                       "result": "PASS" if delta and delta["frame"] in (jump - 1, jump) else "FAIL"}
        ea, eb = events(a), events(b)
        if effects_text:
            effects = parse_script(effects_text)
            count = effects.rows[-1][1] if effects.rows else 0
            if not 1 <= count <= 10000:
                raise ValueError("Effects timeline must contain between 1 and 10000 frames")
            restore("restore S0 for effects run A")
            ea = events(play(controller, effects, count))
            restore("restore S0 for effects run B")
            eb = events(play(controller, effects, count))
        log["effects"] = {"result": "PASS" if ea == eb and ea["enemy_step"]
                          and (ea["player_hitstop"] or ea["enemy_hitstop"]) else "FAIL",
                          "A": ea, "B": eb}
        if mode == "full":
            span_tests(controller, engine, s0, timeline, log, name)
            stability(controller, engine, s0, timeline, log, name)
    except Exception:
        log["error"] = traceback.format_exc()
    finally:
        try:
            if controller.session is not None and not controller.session.is_detached:
                controller.pause()
                if s0 is not None:
                    log["reset_to_s0"] = restore("restore S0 for end-of-test parking")
                controller.load(Timeline())
        except Exception:
            log["reset_error"] = traceback.format_exc()
        write_log(name, log)
    keys = ["snapshot", "baseline", "edit", "effects", "spans", "stability", "error", "reset_error"]
    summary = {key: log[key] for key in keys if key in log}
    required = keys[1:6] if mode == "full" else keys[1:4]
    summary["result"] = "PASS" if all(log.get(key, {}).get("result") == "PASS" for key in required) and not (
        log.get("error") or log.get("reset_error")) else "FAIL"
    return {"log": f"logs/{name}", **summary}


def span_tests(controller, engine, s0, timeline, log, name):
    samples = []
    log["spans"] = {"samples": samples, "result": "FAIL"}
    for distance in (1, 60, 300, 1200):
        log["last_action"] = f"span {distance}: restore S0, run {distance}, then restore"
        write_log(name, log)
        engine.restore(s0)
        play(controller, timeline, distance)
        restored = engine.restore(s0)
        a = play(controller, timeline, 300)
        engine.restore(s0)
        b = play(controller, timeline, 300)
        delta = first_difference(serial(a), serial(b))
        visual = [presents(a), presents(b)]
        samples.append({"interval": distance, "restore": restored, "first_difference": delta, "presents": visual,
                        "result": "PASS" if delta is None and visual == [300, 300] else "FAIL"})
    log["spans"]["result"] = "PASS" if all(s["result"] == "PASS" for s in samples) else "FAIL"


def stability(controller, engine, s0, timeline, log, name):
    engine.restore(s0)
    baseline = serial(play(controller, timeline, 60))
    times, failures = [], []
    log["stability"] = {"cycles": 0, "failures": failures, "restore_ms": times, "result": "FAIL"}
    for index in range(1, 51):
        log["last_action"] = f"stability cycle {index}: restore S0 and replay 60"
        write_log(name, {k: v for k, v in log.items() if k not in {"A", "B", "C"}})
        times.append(engine.restore(s0)["milliseconds"])
        rows = play(controller, timeline, 60)
        delta = first_difference(baseline, serial(rows))
        if delta is not None or presents(rows) != 60:
            failures.append({"cycle": index, "divergence": delta, "presents": presents(rows)})
        log["stability"]["cycles"] = index
    log["stability"] = {"cycles": len(times), "failures": failures, "restore_ms": times,
                        "median_ms": median(times), "result": "FAIL" if failures else "PASS"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--mode", choices=("inspect", "census", "core", "full"), default="core")
    parser.add_argument("--profile", choices=SNAPSHOT_PROFILES, default=DEFAULT_PROFILE)
    parser.add_argument("--timeline", type=Path)
    parser.add_argument("--effects-timeline", type=Path)
    parser.add_argument("--jump", type=int, default=55)
    args = parser.parse_args()
    text = format_script(parse_script(args.timeline.read_text(encoding="utf-8-sig"))) if args.timeline else BASELINE
    effects = args.effects_timeline.read_text(encoding="utf-8-sig") if args.effects_timeline else None
    result = Client(args.pid).call("accept", mode=args.mode, profile=args.profile, text=text, jump=args.jump,
                                 effects_text=effects)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return int(result.get("result") == "FAIL")


if __name__ == "__main__":
    raise SystemExit(main())
