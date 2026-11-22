"""Takes: the player plays, every frame's keyboard state is recorded, then it can be replayed and edited.

A take starts at the frame gate in a room with a character. That frame becomes timeline frame 0 and
gets a snapshot. The game then runs at 60 frames per second on the fixed clock while the player
plays with the real keyboard; each frame's held keys are read back from the game's own keyboard
object. When the take stops the game parks, the keys are written as a timeline file, and the take
is replayed once from frame 0. The replay must end in exactly the state the player produced; the
result of that check is reported, never assumed.
"""
from datetime import datetime
import time

from keycodes import KEY_CODES
from record_boot import Recording
from rollback import describe_state, fingerprint
from tasctl import ROOT
from timeline import Timeline, format_frames

# Keys the TAS tool itself listens to (numpad * / + - and F1 F2 F3 F5 for the practice aids).
# They are taken out of the recording.
TOOL_DIKS = {0x37, 0xB5, 0x4E, 0x4A, 0x3B, 0x3C, 0x3D, 0x3F}
TAKES = ROOT / "runs"


def in_room(scene):
    return scene["playerPresent"] and not scene["loading"] and not scene["cutscene"]


def start(server):
    ctl = server.ctl
    if getattr(server, "take", None):
        raise RuntimeError("已经在录制了")
    # Look before touching anything: a refused take must leave the game exactly as it was.
    scene = ctl.rpc.recordscene()["scene"]
    if not in_room(scene):
        raise RuntimeError("要在能操作角色的地方才能录制（不能在菜单、读盘、过场里）")
    from service_operations import dispatch, hand_back, new_route
    returned = False
    if ctl.rpc.status().get("soundSticky"):
        # The sound threads have been asleep since a step back. A take lets them run, so first go
        # back to the state they may wake in (the take then starts there).
        hand_back(server)
        returned = True
    try:
        ctl.pause()
    except Exception:
        # The game did not stop at the frame gate: do not leave it in a frozen mode.
        try:
            ctl.rpc.live(False)
        except Exception:
            pass
        raise RuntimeError("游戏没能停在帧闸上，录制没有开始。再按一次试试。")
    if scene.get("paused"):
        server.route = None
        dispatch(server, "unpause", {})
    route = new_route(server)
    # Not the sound-clean state: the sound threads run while the player records.
    route.save(detail=False)
    bindings = [(name, *codes) for name, codes in sorted(KEY_CODES.items())]
    begun = ctl.rpc.recordstart(bindings, False, False, False, True)
    server.take = {"first": begun["frame"] + 1, "mapping": begun["mapping"], "rows": [],
                   "origin": route.origin, "reason": None, "started": time.time()}
    return {"origin": route.origin, "frame": 0, "returned": returned}


def poll(server):
    """Called from the service heartbeat so the game-side buffer never fills."""
    take = getattr(server, "take", None)
    if not take:
        return
    batch = server.ctl.rpc.recordpoll()
    take["rows"] += batch["rows"]
    if batch["reason"] and not take["reason"]:
        take["reason"] = batch["reason"]


def status(server):
    take = getattr(server, "take", None)
    if not take:
        return {"recording": False}
    poll(server)
    return {"recording": True, "frames": len(take["rows"]), "reason": take["reason"],
            "parked": server.ctl.rpc.status()["parked"]}


def strip_tool_keys(timeline):
    """Drop the tool's own hotkeys from a recorded timeline and merge rows that become equal."""
    cleaned = Timeline()
    cleaned.bindings.update(timeline.bindings)
    for start_frame, end_frame, keys in timeline.rows:
        kept = frozenset(name for name in keys if timeline.codes(name)[0] not in TOOL_DIKS)
        if cleaned.rows and cleaned.rows[-1][2] == kept and cleaned.rows[-1][1] == start_frame - 1:
            first, _, same = cleaned.rows[-1]
            cleaned.rows[-1] = (first, end_frame, same)
        else:
            cleaned.rows.append((start_frame, end_frame, kept))
    return cleaned


def stop(server, check=False):
    """End the take and save it. With `check` the take is also replayed from frame 0 and compared
    with what the player produced (slow for long takes); without it the game stays where the take
    ended and the timeline is simply attached to the route, so stepping back replays these keys."""
    ctl = server.ctl
    take = getattr(server, "take", None)
    if not take:
        raise RuntimeError("现在没有在录制")
    ctl.rpc.recordfinish()
    deadline = time.monotonic() + 60
    result = None
    while result is None and time.monotonic() < deadline:
        poll(server)
        result = ctl.rpc.recordstop()
        if result is None:
            time.sleep(.01)
    server.take = None
    if result is None:
        raise RuntimeError("录制没能在帧边界上结束")
    rows = take["rows"] + result["rows"]
    if not rows:
        raise RuntimeError("一帧都没有录到（录制期间游戏窗口一直不在前台？）")
    recording = Recording(take["first"], take["mapping"])
    recording.add(rows)
    timeline = strip_tool_keys(recording.timeline)
    frames = recording.count
    TAKES.mkdir(exist_ok=True)
    path = TAKES / f"take-{datetime.now():%Y%m%d-%H%M%S}.tas"
    path.write_text(format_frames(timeline), encoding="utf-8", newline="\n")
    route = server.route
    state = ctl.status()
    report = {"frames": frames, "path": str(path), "file": path.name,
              "unmapped": sorted(f"{code:#x}" for code in recording.unmapped),
              "reason": take["reason"] or result.get("reason"), "parked": result["parked"]}
    if not result["parked"]:
        report["check"] = "录制结束时不在可冻结的房间里，没有做重放核对"
        return report
    if state["frame"] - route.origin != frames:
        report["check"] = f"帧数对不上（游戏 {state['frame'] - route.origin}，录到 {frames}），没有做重放核对"
        return report
    # The game already is in the state these keys produce: attach them without replaying, and keep
    # this moment. The sound threads ran during the take and are held from here on, so this (and
    # not frame 0) is the state they may wake in after any step back.
    route.timeline, route.path = timeline, str(path)
    ctl.load(timeline, frames)
    route.save(detail=False, clean=True)
    if not check:
        report["check"] = "没有做重放核对"
        return report
    try:
        played = fingerprint(describe_state(state["last"]))
    except Exception as error:
        report["check"] = f"读不到录制结束时的状态（{error}），没有做重放核对"
        return report
    started = time.perf_counter()
    try:
        route.goto(0)             # back to frame 0 ...
        route.goto(frames)        # ... and replay the whole take
    except Exception as error:
        report["check"] = f"重放失败：{error}"
        return report
    replayed = fingerprint(describe_state(ctl.status()["last"]))
    report["replay_seconds"] = round(time.perf_counter() - started, 2)
    report["consistent"] = replayed == played
    report["check"] = "重放结果与你打出来的完全一致" if replayed == played else \
        "重放结果与你打出来的不一致（录制期间用了鼠标？或换了房间？）"
    return report
