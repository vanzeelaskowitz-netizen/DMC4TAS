"""Takes: the player plays, every frame's keyboard state is recorded, then it can be replayed and edited.

A take starts at the frame gate in a room with a character. That frame becomes timeline frame 0 and
gets a snapshot. The game then runs at 60 frames per second on the fixed clock while the player
plays with the real keyboard; each frame's held keys are read back from the game's own keyboard
object. When the take stops the game parks, the keys are written as a timeline file, and the take
is replayed once from frame 0. The replay must end in exactly the state the player produced; the
result of that check is reported, never assumed.
"""
from datetime import datetime
from pathlib import Path
import time

from keycodes import KEY_CODES
from lang import L
from record_boot import Recording
from rollback import describe_state, fingerprint, input_prefix
from tasctl import ROOT
from timeline import Timeline, format_frames, parse_script

# Keys the TAS tool itself listens to (numpad * / + - and F1 F2 F3 F5 for the practice aids).
# They are taken out of the recording.
TOOL_DIKS = {0x37, 0xB5, 0x4E, 0x4A, 0x3B, 0x3C, 0x3D, 0x3F, 0x40}
TAKES = ROOT / "runs"


def in_room(scene):
    return scene["playerPresent"] and not scene["loading"] and not scene["cutscene"]


def start(server):
    ctl = server.ctl
    if getattr(server, "take", None):
        raise RuntimeError(L("已经在录制了", "Already recording"))
    # Look before touching anything: a refused take must leave the game exactly as it was.
    scene = ctl.rpc.recordscene()["scene"]
    if not in_room(scene) or (getattr(ctl, "build", {}).get("game") == "dx9" and not scene.get("roomClass")):
        raise RuntimeError(L("要在能操作角色的地方才能录制（不能在菜单、读盘、过场里）",
                             "Recording only works where you control the character (not in menus, "
                             "loading screens or cutscenes)"))
    from service_operations import dispatch, hand_back, new_route
    returned = False
    carried = None
    if ctl.rpc.status().get("soundSticky"):
        # Something has been restored (a step back, a loaded save), so the sound threads stay
        # asleep until the game is handed back. A take in a room is silent anyway, so it can go on
        # from the frame the game is on: it then continues the timeline that led here (the script
        # becomes those rows plus what is recorded) and inherits the state to hand back in.
        old = getattr(server, "route", None)
        usable = (old is not None and old.origin is not None and old.clean is not None and old.checkpoints
                  and old.checkpoints[0][0].frame == old.origin and not scene.get("paused"))
        if usable:
            frame = ctl.status()["frame"] - old.origin
            text = Path(old.path).read_text(encoding="utf-8-sig") if old.path else ""
            if input_prefix(parse_script(text), frame) != input_prefix(old.timeline, frame):
                raise RuntimeError(L("脚本文件和游戏里正在跑的输入不一样（刚改过脚本还没生效？），先让脚本生效再录制",
                                     "The script file differs from the inputs the game is running (an edit "
                                     "not applied yet?). Let the script apply, then record"))
            carried = {"first": old.checkpoints[0][0], "frame": frame, "script": text, "clean": old.clean,
                       "ticks": getattr(old, "sound_ticks", None)}
        else:
            # No timeline to continue: go back to the state the sound threads may wake in; the
            # take then starts there, as it always did.
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
        raise RuntimeError(L("游戏没能停在帧闸上，录制没有开始。再按一次试试。",
                             "The game did not stop at the frame gate, recording did not start. Try again."))
    if scene.get("paused"):
        server.route = None
        if dispatch(server, "unpause", {})["paused"]:
            ctl.rpc.live(False)
            raise RuntimeError(L("游戏暂停菜单仍然打开，录制没有开始",
                                 "The game's pause menu is still open, recording did not start"))
    route = new_route(server, sticky=carried is not None)
    if carried:
        route.clean = carried["clean"]
        route.sound_ticks = carried["ticks"]
        route.sound_guarded = False     # frame 0 of this take is a restored state
    # The take's own first frame. Whether it also is a state to hand back in is settled when the
    # take stops.
    try:
        route.save(detail=False)
        bindings = [(name, *codes) for name, codes in sorted(KEY_CODES.items())]
        begun = ctl.rpc.recordstart(bindings, False, False, False, True)
    except Exception:
        if carried is not None:
            server.route = old
        else:
            hand_back(server)
        raise
    server.take = {"first": begun["frame"] + 1, "mapping": begun["mapping"], "rows": [],
                   "origin": route.origin, "reason": None, "started": time.time(), "carried": carried}
    return {"origin": route.origin, "frame": 0, "returned": returned,
            "continued": carried["frame"] if carried else None}


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


def joined(before, frame, recorded):
    """The timeline `before` up to `frame`, then `recorded` (whose rows count from 1)."""
    result = Timeline()
    result.bindings.update(before.bindings)
    result.bindings.update(recorded.bindings)
    position = 0
    for start, end, keys in before.rows:
        if start > frame:
            break
        position = min(end, frame)
        result.rows.append((start, position, keys))
    if position < frame:                # the old script ended earlier: nothing was held since
        result.rows.append((position + 1, frame, frozenset()))
    for start, end, keys in recorded.rows:
        result.rows.append((start + frame, end + frame, keys))
    return result


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
        raise RuntimeError(L("现在没有在录制", "Not recording"))
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
        raise RuntimeError(L("录制没能在帧边界上结束", "The recording could not end on a frame boundary"))
    rows = take["rows"] + result["rows"]
    if not rows:
        raise RuntimeError(L("一帧都没有录到（录制期间游戏窗口一直不在前台？）",
                             "Not a single frame was recorded (was the game window never in front?)"))
    recording = Recording(take["first"], take["mapping"])
    recording.add(rows)
    timeline = strip_tool_keys(recording.timeline)
    frames = recording.count
    # A take that went on from frame F of an earlier timeline is written as that timeline's rows
    # up to F followed by the take, so the file replays from that timeline's frame 0.
    carried = take.get("carried")
    before = carried["frame"] if carried else 0
    written = joined(parse_script(carried["script"]), before, timeline) if carried else timeline
    TAKES.mkdir(exist_ok=True)
    path = TAKES / f"take-{datetime.now():%Y%m%d-%H%M%S-%f}.tas"
    path.write_text(format_frames(written), encoding="utf-8", newline="\n")
    route = server.route
    state = ctl.status()
    report = {"frames": frames, "path": str(path), "file": path.name, "continued": before if carried else None,
              "unmapped": sorted(f"{code:#x}" for code in recording.unmapped),
              "reason": take["reason"] or result.get("reason"), "parked": result["parked"]}
    if not result["parked"]:
        report["check"] = L("录制结束时不在可冻结的房间里，没有做重放核对",
                            "The recording did not end in a room that can be frozen, so it was not verified")
        return report
    if route is None:
        report["check"] = L("场景已变化，录制已保存，起点快照已失效",
                            "The scene changed: the recording is saved, its start snapshot is gone")
        return report
    if state["frame"] - route.origin != frames:
        report["check"] = L(f"帧数对不上（游戏 {state['frame'] - route.origin}，录到 {frames}），没有做重放核对",
                            f"Frame counts differ (game {state['frame'] - route.origin}, recorded {frames}), "
                            "so it was not verified")
        return report
    # The game already is in the state these keys produce: attach them without replaying, and keep
    # this moment. The sound threads ran during the take and are held from here on, so this (and
    # not frame 0) is the state they may wake in after any step back.
    if carried:
        # Back onto the earlier timeline: its frame 0, the frame the take began on, and now.
        route.adopt(carried["first"], route.checkpoints[0][0], written, str(path))
        ctl.load(written, before + frames)
        route.save(detail=False)
    else:
        route.timeline, route.path = timeline, str(path)
        ctl.load(timeline, frames)
        route.save(detail=False, clean=True)
    if not check:
        report["check"] = L("没有做重放核对", "Not verified by replay")
        return report
    try:
        played = fingerprint(describe_state(state["last"]))
    except Exception as error:
        report["check"] = L(f"读不到录制结束时的状态（{error}），没有做重放核对",
                            f"Cannot read the state at the end of the recording ({error}), so it was not verified")
        return report
    started = time.perf_counter()
    try:
        route.goto(before)                # back to the frame the take began on ...
        route.goto(before + frames)       # ... and replay the whole take
        replayed = fingerprint(describe_state(ctl.status()["last"]))
    except Exception as error:
        report["check"] = L(f"重放失败：{error}", f"Replay failed: {error}")
        return report
    report["replay_seconds"] = round(time.perf_counter() - started, 2)
    report["consistent"] = replayed == played
    report["check"] = L("重放结果与你打出来的完全一致", "The replay matches what you played exactly") \
        if replayed == played else L("重放结果与你打出来的不一致（录制期间用了鼠标？或换了房间？）",
                                     "The replay differs from what you played (mouse used while recording? "
                                     "room changed?)")
    return report
