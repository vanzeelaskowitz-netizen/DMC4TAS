"""Commands executed serially by the DMC4SE TAS service."""
from itertools import islice
import time
import traceback

from lang import L
from rollback import Rollback
from timeline import Timeline, frames, parse_script, read_script


TRACE_LIMIT = 200000


def keep_trace(server, row):
    """Remember what the character did on a frame: action, animation, animation time, and for
    Nero the Exceed window timer and gauge (None for other characters)."""
    player = ((row or {}).get("combat") or {}).get("player")
    number = (row or {}).get("number")
    if number is None:
        return
    exceed = (player or {}).get("exceed") or {}
    server.trace[number] = (player["action"], player["motion"], round(player["motionFrame"] or 0, 2),
                            round(exceed.get("timer") or 0, 2), exceed.get("gauge")) if player else None
    if len(server.trace) > TRACE_LIMIT:
        for key in sorted(server.trace)[:TRACE_LIMIT // 10]:
            del server.trace[key]


def install_trace(server):
    """Every frame the service sees passes through the controller's step (single steps, and the
    replays behind step back, jump and script edits) or its status (sampled while playing). Both
    are wrapped once so the script window can show, per frame, what the character was doing."""
    if not hasattr(server, "trace"):
        server.trace = {}
    ctl = server.ctl
    if getattr(ctl, "_trace_server", None) is server:
        return
    step, status = type(ctl).step, type(ctl).status

    def traced_step(n=1):
        rows = step(ctl, n)
        for row in rows:
            keep_trace(server, row)
        return rows

    def traced_status():
        state = status(ctl)
        keep_trace(server, state.get("last"))
        return state
    ctl.step, ctl.status, ctl._trace_server = traced_step, traced_status, server


def trace(server, args):
    """{timeline frame: [action, motion, motion time, Exceed timer, Exceed gauge]} for frames
    first..last of the current timeline."""
    route = getattr(server, "route", None)
    if route is None or route.origin is None:
        return {}
    first, last = max(1, int(args.get("first", 1))), int(args.get("last", 0))
    found = {}
    for frame in range(first, min(last, first + 2000) + 1):
        entry = server.trace.get(route.origin + frame)
        if entry:
            found[frame] = entry
    return found


def hand_back(server):
    """Give the game back to the player.

    Once a snapshot has been restored the game's sound threads are asleep, and they may only wake
    in the first state of the timeline (see live() in agent.js). Leaving them asleep is no option
    either: every loading screen waits for them, so the next door would hang the game. So after a
    step back, handing back first returns the game to the moment it was taken over.
    """
    ctl = server.ctl
    route = getattr(server, "route", None)
    clean = False
    if ctl.rpc.status().get("soundSticky"):
        if route is None or route.clean is None:
            raise RuntimeError(L("做过后退，但找不到声音线程睡下那一刻的存档，声音没法安全恢复。"
                                 "游戏可以继续玩，但切场景会卡住，需要重启游戏。",
                                 "A step back happened, but the state from the moment the sound threads were "
                                 "put to sleep is missing, so sound cannot be restored safely. You can keep "
                                 "playing, but a scene change will hang: restart the game."))
        route.return_clean()
        clean = True
    server.route = None
    return ctl.rpc.live(clean) | {"returned": clean}


def audible_start(server, args):
    """Play the timeline with the game's own sound, from its first frame.

    The sound threads sleep for the whole timeline (stepping, playing, recording). Every frame of
    it therefore carries the idea of the sound that the first frame has, and XAudio2 holds nothing
    (agent.js: drainSound). So the first frame is a state the threads can wake in, any number of
    times. From there the script runs at game speed with them awake; nothing is saved or restored
    on the way. When it stops, the threads go back to sleep and the game returns to the silent
    version of the frame it stopped on (audible_finish).
    """
    ctl, route = server.ctl, getattr(server, "route", None)
    if getattr(server, "audible", None):
        raise RuntimeError(L("已经在带声音播放了", "Already playing with sound"))
    if route is None or route.origin is None or not route.checkpoints:
        raise RuntimeError(L("还没有时间线：先接管游戏、载入脚本", "No timeline yet: take over the game and load a script"))
    if not ctl.rpc.status()["soundGuard"]:
        raise RuntimeError(L("声音保护没有启用，不能带声音播放（特别版默认关闭：它会让“交还操作”崩溃，修好之前先停用）",
                             "Sound protection is off, so playing with sound is not possible (off by default "
                             'on the Special Edition: it makes "Hand back" crash, disabled until that is fixed)'))
    if not getattr(route, "sound_guarded", False):
        raise RuntimeError(L("这条时间线不能带声音播放：它是读档得到的，或者声音保护是中途才打开的，"
                             "游戏的声音状态和第 0 帧对不上，放了会崩",
                             "This timeline cannot play with sound: it came from a loaded save state, or sound "
                             "protection was switched on halfway, so the game's sound no longer matches "
                             "frame 0 and playing would crash"))
    first = route.checkpoints[0][0]
    if first.frame != route.origin:
        raise RuntimeError(L("时间线第 0 帧的存档不在了，不能带声音播放",
                             "The state of the timeline's frame 0 is gone, so it cannot play with sound"))
    timeline = read_timeline(route)
    last = timeline.rows[-1][1] if timeline.rows else 0
    target = min(int(args.get("count") or last), last)
    if target < 1:
        raise RuntimeError(L("脚本是空的，没有可播放的帧", "The script is empty, there is nothing to play"))
    route.pause()
    route.running = False
    route.engine.restore(first)
    ctl.load(timeline, 0)
    route.timeline = timeline
    ctl.rpc.audible(True)
    ctl.resume(route.origin + target)
    server.audible = {"target": target}
    return {"from": 0, "to": target}


def read_timeline(route):
    from rollback import read_script
    return read_script(route.path) if route.path else route.timeline


def audible_finish(server, stop=False):
    """The stretch with sound is over (or is cut short with `stop`): silence again, and back to the
    state that the timeline's own checkpoints give for the frame reached."""
    ctl, route = server.ctl, getattr(server, "route", None)
    sound, server.audible = getattr(server, "audible", None), None
    if not sound or route is None:
        return None
    if stop:
        ctl.pause()
    state = ctl.rpc.status()
    ctl.rpc.audible(False)
    if state["mode"] == "observe":
        return None                     # the game let go by itself (a door, a loading screen)
    reached = max(0, state["frame"] - route.origin)
    # One frame back and forward again forces a restore: the memory left by the run with sound
    # differs from the silent one in everything the sound threads touched.
    if reached > 0:
        route.goto(reached - 1)
    return route.goto(reached)


def new_route(server, sticky=False):
    """A fresh timeline. The sound threads must not be asleep for good when it starts, or the new
    timeline would not know the state they may wake in; if they are, hand back first. `sticky`:
    the caller passes that state on from the timeline before (a take that continues one)."""
    from rollback import Rollback
    state = server.ctl.rpc.status()
    if state.get("soundSticky") and not sticky:
        raise RuntimeError(L("做过后退之后要先“交还操作”，才能开始新的时间线",
                             'After a step back, press "Hand back" before starting a new timeline'))
    server.route = Rollback(server.ctl)
    # Playing with sound restores the first frame and wakes the sound threads there. That is only
    # safe when the voice guard was on for the whole timeline: with it off, voices the game drops
    # are really destroyed, and the first frame still points at them (2026-11-22 crash at
    # exe+0x809957, a voice with no vtable, after the guard was switched on mid-timeline).
    server.route.sound_guarded = bool(state.get("soundGuard"))
    # While save states exist, new timelines store their snapshots against the same full image
    # as the saves do (when the arenas still lie where they did): one 561 MB image instead of one
    # per take-over, and a loaded save can be restored page by page.
    import savestate
    if savestate.states(server) and getattr(server, "save_base", None) is not None:
        server.route.engine.base = server.save_base
    return server.route


def first_change(old, new):
    """First timeline frame on which two scripts hold different keys (frames past a script's end
    hold nothing), or None when they are the same."""
    def held(timeline):
        return [keys for _, keys, _, _ in frames([(a, b, sorted(k)) for a, b, k in timeline.rows])]
    a, b = held(old), held(new)
    for index in range(max(len(a), len(b))):
        x = a[index] if index < len(a) else frozenset()
        y = b[index] if index < len(b) else frozenset()
        if x != y:
            return index + 1
    return None


def service(server):
    install_trace(server)
    if getattr(server, "take", None):
        import take
        try:
            take.poll(server)
        except Exception:
            traceback.print_exc()
    if getattr(server, "audible", None):
        try:
            state = server.ctl.rpc.status()
            if state["mode"] == "observe" or (state["parked"] and state["mode"] == "paused"):
                audible_finish(server)
        except Exception:
            server.audible = None
            traceback.print_exc()
    if getattr(server, "saves", None) and time.monotonic() - getattr(server, "saves_checked", 0) > 2:
        # Save states end with the room: let their snapshots go as soon as the agent says so.
        server.saves_checked = time.monotonic()
        try:
            import savestate
            savestate.states(server)
        except Exception:
            traceback.print_exc()
    route = getattr(server, "route", None)
    if route is not None and route.origin is not None and not getattr(server, "take", None):
        # The game side lets go by itself when a timeline reaches a loading screen or another room.
        try:
            state = server.ctl.rpc.status()
            if state["mode"] == "observe" and not state["snapshotsAlive"]:
                server.route = route = None
        except Exception:
            pass
    if route and route.running:
        try:
            route.service()
        except Exception:
            route.running = False
            traceback.print_exc()


def timeline_keys(route, first, last):
    if route is None or route.origin is None:
        raise ValueError("No loaded timeline")
    if type(first) is not int or type(last) is not int or not 0 <= first <= last:
        raise ValueError("Expected 0 <= first <= last")
    selected = islice(frames(route.timeline.rows), max(first - 1, 0), last)
    keys = ([[]] if first == 0 else []) + [sorted(held) for _, held, _, _ in selected]
    return keys + [[]] * (last - first + 1 - len(keys))     # past the script's last row nothing is pressed


def dispatch(server, command, args):
    ctl = server.ctl
    if getattr(server, "recording", False) and not command.startswith("record-"):
        raise RuntimeError("End the recording before issuing gameplay commands")
    if command.startswith("take-"):
        import take
        if command == "take-stop":
            return take.stop(server, bool(args.get("check", False)))
        return {"take-start": take.start, "take-status": take.status}[command](server)
    if getattr(server, "take", None) and command not in ("record-scene",):
        raise RuntimeError(L("正在录制，先停止录制（小键盘 * 或播放器的“停止录制”）",
                             'Recording is running: stop it first (numpad * or the player\'s "Stop recording")'))
    if command == "attach":
        if ctl.session is None:
            ctl.attach()
        return ctl.pause() if ctl.build["game"] == "se" else ctl.status()
    if ctl.session is None:
        raise RuntimeError("Enter attach first")
    if command.startswith("record-"):
        from record_boot import dispatch_record
        return dispatch_record(server, command, args)
    if command == "detach":
        if ctl.build["game"] == "dx9" and getattr(server, "route", None):
            hand_back(server)
        server.route = None
        server.m1_origin = None
        return ctl.detach()
    if command == "m1-fallback-test" and ctl.build["game"] == "dx9":
        return ctl.rpc.fallbacktest(args["on"])
    if command == "accept":
        if getattr(server, "route", None) and server.route.origin is not None:
            raise RuntimeError("Acceptance would invalidate the active timeline")
        from rollback_accept import run
        return run(server, **args)
    if command.startswith("m1-"):
        if getattr(server, "route", None) and server.route.origin is not None:
            raise RuntimeError("M1 would invalidate the active timeline")
        if command == "m1-load":
            return ctl.load(parse_script(args["text"]))
        if command == "m1-step":
            return ctl.step(args["count"])
        if command == "m1-resume":
            try:
                ctl.resume(args["until"])
                return ctl.wait_for(lambda state: state["parked"] and state["frame"] == args["until"], timeout=10)
            finally:
                ctl.pause()
        if command == "m1-pause":
            state = ctl.pause()
            # A boot replay can park itself on its last frame. Acceptance starts a new
            # measurement at this gate, after outstanding replay observations.
            ctl.rpc.drain()
            return state

    install_trace(server)
    if command == "probe":
        from probe import run
        return run(server, **args)
    if command == "trace":
        return trace(server, args)
    if command == "sound-detail":
        return ctl.rpc.sounddetail()
    if command == "play-sound":
        return audible_start(server, args)
    if command == "play-sound-stop":
        return audible_finish(server, stop=True) or {}
    if getattr(server, "audible", None) and command not in ("cheat", "guard"):
        audible_finish(server, stop=True)       # anything else ends the stretch with sound first
    if command == "cheat":
        # Practice aids applied by the agent every frame: hp, dt, time, kill (see combat.js).
        return ctl.rpc.cheat(args["name"], bool(args["on"]))
    if command == "guard":
        # Tests only: switch off the shared-texture part of the GPU resource guard, or with
        # which="sound" the voice guard (agent.js).
        return ctl.rpc.guard(bool(args["on"]), args.get("which", "gpu"))
    if command == "live":
        # Hand the game back to the player. Whatever they do next is not on any timeline, so the
        # current one (and its checkpoints) ends here; taking over again starts a fresh one.
        return hand_back(server)
    if command == "unpause":
        # The game opens its own pause screen when its window loses the focus (for instance when
        # the player switches to this tool). Close it with the game's own back key before any
        # timeline starts, so that frame 0 is real gameplay.
        if getattr(server, "route", None) and server.route.origin is not None:
            raise RuntimeError("Close the game's pause screen before starting a timeline")
        ctl.pause()
        taps = 0
        while ctl.rpc.recordscene()["scene"]["paused"] and taps < 6:
            ctl.load(parse_script("1-2: Escape\n3-24: -\n"))
            ctl.step(24)
            taps += 1
        return {"paused": ctl.rpc.recordscene()["scene"]["paused"], "taps": taps}
    if command.startswith("state-"):
        # Named save states: state-save, state-load, state-list, state-delete (savestate.py).
        import savestate
        return savestate.dispatch(server, command, args)

    route = getattr(server, "route", None)
    if route is None:
        route = new_route(server)
    if command == "configure":
        interval = args.get("interval", route.interval)
        limit = args.get("limit", route.limit)
        if interval < 1 or limit < 1:
            raise ValueError("Automatic interval and retention must be positive")
        route.pause()
        route.interval, route.limit = interval, limit
        route.checkpoints = route.checkpoints[:1] + route.checkpoints[1:][-(limit - 1):] if limit > 1 else route.checkpoints[-1:]
        return {"snapshot_interval": route.interval, "snapshot_count": route.limit, "profile": route.engine.profile}
    if command in {"pause", "load", "save", "step", "resume"}:
        state = route.pause()
        if not route.checkpoints:
            try:
                # Taking over: the game is parked and its sound threads are held from here on, so
                # this first snapshot is also the state they may later wake in.
                state["snapshot"] = route.save(clean=not state.get("soundSticky"))
            except Exception as error:
                state["snapshotUnavailable"] = str(error)
                if command == "save":
                    raise
            else:
                if command == "save":
                    return state["snapshot"]
        if command == "pause":
            return state
    if command == "save":
        return route.save()
    if command == "resume":
        return route.resume(args.get("count", 60))
    if command in {"step", "back", "goto"}:
        return getattr(route, command)(args["count"])
    if command == "load":
        before = route.timeline
        result = route.load(args["path"])
        # From the first frame whose input changed on, what was traced belongs to the old script.
        changed = first_change(before, route.timeline)
        if changed is not None and route.origin is not None:
            for number in [key for key in server.trace if key >= route.origin + changed]:
                del server.trace[number]
        return result
    raise ValueError(f"Unknown command {command!r}")
