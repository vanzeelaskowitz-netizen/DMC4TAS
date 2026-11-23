"""Run candidate input rows against one parked timeline snapshot."""
from timeline import Timeline, canonical_names, frame_line, validate_mapping


def candidate(lines, length, bindings):
    if not isinstance(lines, list) or len(lines) > length:
        raise ValueError("Each candidate must be a list of up to last-first+1 frame rows")
    names = canonical_names(bindings)
    script = Timeline(bindings=bindings)
    for frame, line in enumerate(lines, 1):
        if not isinstance(line, str) or "\n" in line:
            raise ValueError(f"Expected one key row at candidate frame {frame}")
        parsed = frame_line(line, names)
        if parsed is None or parsed[0] != 1:
            raise ValueError(f"Expected exactly one frame at candidate frame {frame}")
        script.rows.append((frame, frame, parsed[1]))
    validate_mapping(script)
    return script


def select(row, paths):
    result = {}
    for name, parts in paths.items():
        value = row
        for part in parts:
            if value is None:
                break
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f"Unknown probe field {name!r}")
            value = value[part]
        result[name] = value
    return result


def sample(ctl, origin, length, paths):
    rows = []
    for offset in range(0, length, 120):
        for row in ctl.step(min(120, length - offset)):
            row["frame"] = row["number"] - origin
            rows.append(select(row, paths))
    return rows


def run(server, first, candidates, last, fields):
    route = getattr(server, "route", None)
    if not route or not route.checkpoints or route.running or getattr(server, "audible", None):
        raise ValueError("Probe requires a paused timeline with a retained snapshot")
    if type(first) is not int or type(last) is not int or not 1 <= first <= last:
        raise ValueError("Expected 1 <= first <= last")
    if not isinstance(fields, list) or not fields or not all(isinstance(f, str) and f for f in fields):
        raise ValueError("Expected a nonempty list of field names")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Expected a nonempty list of candidates")
    paths = {name: name.split(".") for name in fields}
    length = last - first + 1
    trials = [candidate(lines, length, route.timeline.bindings) for lines in candidates]
    ctl = server.ctl
    if not ctl.status()["parked"]:
        raise ValueError("Pause the timeline before probing")
    ctl.pause()  # Recheck windowed mode before any trial can park the game.
    original = route.engine.capture()
    trace, server.trace = server.trace, {}
    try:
        start = route.origin + first - 1
        if start != original.frame:
            route.seek_without_saving(start)
        origin = original if start == original.frame else route.engine.capture()
        results = []
        for index, trial in enumerate(trials):
            if index:
                route.engine.restore(origin)
            ctl.load(trial)
            results.append(sample(ctl, route.origin, length, paths))
        return results
    finally:
        server.trace = trace
        route.engine.restore(original)
        ctl.load(route.timeline, original.frame - route.origin)
