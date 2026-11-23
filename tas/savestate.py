"""Named save states: a frame of a timeline kept under a name, to come back to later.

A save holds two whole-heap snapshots (frame 0 of its timeline and the frame it was made on), the
script as it was, and what the character did on every frame in between. Loading it puts the game
on that frame of that timeline again: the script window shows the same rows, the frame counter the
same number, and stepping back works all the way down to frame 0.

How long a save lasts. The snapshots are memory images. They hold pointers to Direct3D objects,
XAudio2 voices, threads and file handles, which exist only in this run of the game and only while
the room stays loaded. So a save ends with the room (any loading screen), with the game and with
the service; it cannot be written to disk and read back after a restart. Inside the room it
survives handing the game back and taking over again:

- GPU objects. The agent keeps its resource guard on for as long as a save exists (agent.js,
  savesKept), so nothing a save points to is destroyed while the player has the game.
- Sound. XAudio2 lives outside the snapshots. If the sound threads have run since the save was
  made, voices may hold buffers the save's memory knows nothing about, and their "buffer ended"
  reports would free blocks in a heap that never allocated them (the crash of 2026-11-20). Loading
  therefore waits until every such voice has played out; looping effects started in between are
  stopped and emptied first; a stream that will not end refuses the load. Afterwards the sound
  threads stay asleep and only wake in the clean state of the current take-over, exactly as after
  any step back (service_operations.hand_back).

Not tried in the game yet (2026-11-22): everything here was written with the game closed.
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import time

from rollback import input_prefix
from tasctl import ROOT
from timeline import parse_script

FILES = ROOT / "runs"
LIMIT = 12              # every save pins a few MB of pages, and all of them one 561 MB image
SOUND_WAIT = 6.0        # seconds to let voices play out before a load is refused


@dataclass(eq=False)
class SaveState:
    name: str
    first: object       # Snapshot: frame 0 of the timeline
    at: object          # Snapshot: the saved frame
    frame: int          # timeline frame of `at`
    script: str         # the script text as it was
    source: str         # the script file it was made from ("" when there was none)
    made: str
    ticks: int          # the agent's sound tick count when it was made
    note: str           # one line for the list: where the character stood, how many enemies
    trace: dict         # game frame -> what the character did (service_operations.keep_trace)


def clean_name(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", str(name or "")).strip()
    name = re.sub(r"\s+", " ", name)[:40].strip(" .")
    if not name:
        raise ValueError("存档要有个名字")
    return name


def states(server):
    """The saves that can still be loaded. The agent drops them when the room goes (a loading
    screen, another room); the snapshots are let go here as soon as that is seen."""
    saves = getattr(server, "saves", None)
    if saves is None:
        saves = server.saves = {}
    if saves:
        state = server.ctl.rpc.status()
        if not state.get("savesKept"):
            server.saves_lost = {"count": len(saves), "reason": state.get("savesLost") or "unknown"}
            saves.clear()
            server.save_base = None
    return saves


def describe(row):
    combat = (row or {}).get("combat") or {}
    player = combat.get("player") or {}
    position = player.get("position")
    enemies = combat.get("enemies") or []
    where = "位置 ({:.0f}, {:.0f}, {:.0f})".format(*position[:3]) if position and len(position) >= 3 else "位置未知"
    return f"{where}　敌人 {len(enemies)} 个"


def summary(save):
    return {"name": save.name, "frame": save.frame, "made": save.made, "note": save.note,
            "source": Path(save.source).name if save.source else ""}


def script_file(save):
    """The file the script window opens for a loaded save, and its text.

    The player's own file when it still leads to the saved frame (rows after that frame may have
    been worked on since: they are kept). Otherwise a copy of the script as it was when the save
    was made, under a name that does not overwrite anything the player has edited.
    """
    wanted = input_prefix(parse_script(save.script), save.frame)
    candidates = ([Path(save.source)] if save.source else []) + \
        [FILES / (f"save-{save.name}.tas" if number == 1 else f"save-{save.name}-{number}.tas")
         for number in range(1, 50)]
    for path in candidates:
        if not path.is_file():
            if path.parent != FILES:
                continue                    # the player's file is gone: do not recreate it
            FILES.mkdir(exist_ok=True)
            path.write_text(save.script, encoding="utf-8", newline="\n")
            return path, save.script
        try:
            text = path.read_text(encoding="utf-8-sig")
            if input_prefix(parse_script(text), save.frame) == wanted:
                return path, text
        except (OSError, ValueError):
            pass
    raise RuntimeError("给这个存档的脚本找不到可用的文件名")


def listing(server):
    saves = states(server)
    lost = getattr(server, "saves_lost", None)
    return {"saves": [summary(save) for save in saves.values()], "limit": LIMIT, "lost": lost}


def save_state(server, name):
    ctl, route = server.ctl, getattr(server, "route", None)
    name = clean_name(name)
    if route is None or route.origin is None or not route.checkpoints:
        raise RuntimeError("还没有时间线：先接管游戏（或录一段）再存档")
    saves = states(server)
    if name not in saves and len(saves) >= LIMIT:
        raise RuntimeError(f"存档最多 {LIMIT} 个，先删掉一个不用的")
    route.pause()
    first = route.checkpoints[0][0]
    if first.frame != route.origin:
        raise RuntimeError("这条时间线第 0 帧的快照不在了，存不了档")
    state = ctl.status()
    frame = state["frame"] - route.origin
    text = Path(route.path).read_text(encoding="utf-8-sig") if route.path else ""
    # The file is what the editor shows; the route's timeline is what the game was fed. A save of
    # a frame that the file would not reproduce is no use to anyone.
    if input_prefix(parse_script(text), frame) != input_prefix(route.timeline, frame):
        raise RuntimeError("脚本文件和游戏里正在跑的输入不一样（刚改过脚本还没生效？），先让脚本生效再存档")
    if frame == 0:
        at = first
    else:
        route.save(detail=False)
        at = route.last_saved
        if at.frame != state["frame"]:
            raise RuntimeError("存档时游戏的帧号变了，没有存上，再试一次")
    agent = ctl.rpc.keepsaves(True)
    trace = {number: entry for number, entry in getattr(server, "trace", {}).items()
             if first.frame <= number <= at.frame}
    # The sound this memory knows: that of the moment its line of states was taken over. After a
    # load the timeline carries the loaded save's value on (see load_state), not the present one.
    ticks = getattr(route, "sound_ticks", None)
    saves[name] = SaveState(name, first, at, frame, text, str(Path(route.path).resolve()) if route.path else "",
                            f"{datetime.now():%H:%M:%S}", agent.get("soundTicks", 0) if ticks is None else ticks,
                            describe(state.get("last")), trace)
    server.save_base = getattr(route.engine, "base", None)
    server.saves_lost = None
    return summary(saves[name]) | {"count": len(saves)}


def delete_state(server, name):
    saves = states(server)
    if saves.pop(name, None) is None:
        raise RuntimeError(f"没有叫“{name}”的存档")
    if not saves:
        server.save_base = None
        server.ctl.rpc.keepsaves(False)
    return {"count": len(saves)}


def quiet(ctl, since):
    """Wait until XAudio2 holds nothing that was queued after sound tick `since`."""
    with ctl.frame_gate():
        if getattr(ctl, "build", {}).get("game") == "dx9":
            # holdgate parks both DirectSound workers and stops the secondary buffer.
            # Keep today's cursor for hand_back, which restores this takeover's clean
            # image before the workers wake. No audio runs in the older saved state.
            return 0
    started = time.monotonic()
    calm = 0
    while True:
        result = ctl.rpc.soundbusy(since, False)
        if not result.get("ready"):
            raise RuntimeError(f"读不到游戏的声音状态（{result.get('error')}）。这个存档是上一次交还操作之前存的，"
                               "不确认声音放完就读它会崩，所以没有读")
        calm = calm + 1 if not result["busy"] else 0
        if calm >= 2:           # twice in a row, 50 ms apart
            return round(time.monotonic() - started, 2)
        if time.monotonic() - started >= SOUND_WAIT:
            break
        time.sleep(.05)
    if result["streams"]:
        raise RuntimeError("背景音乐的缓冲一直没放完，现在读这个存档不安全。过几秒再试一次")
    ctl.rpc.soundbusy(since, True)      # looping effects started since the save: stop and empty them
    time.sleep(.05)
    if ctl.rpc.soundbusy(since, False)["busy"]:
        raise RuntimeError("还有声音停不下来，现在读这个存档不安全。过几秒再试一次")
    return round(time.monotonic() - started, 2)


def load_state(server, name):
    from service_operations import new_route
    ctl = server.ctl
    saves = states(server)
    save = saves.get(name)
    if save is None:
        lost = getattr(server, "saves_lost", None)
        raise RuntimeError(f"没有叫“{name}”的存档" + ("（换过房间或读过盘，之前的存档都失效了）" if lost else ""))
    route = getattr(server, "route", None)
    if route is None:
        route = new_route(server)
    route.pause()
    route.running = False
    agent = ctl.rpc.status()
    if not route.checkpoints:
        # Taking over with this very command: as for "pause", the first snapshot is the state the
        # sound threads may later wake in.
        route.save(detail=False, clean=not agent.get("soundSticky"))
    if route.clean is None:
        if agent.get("soundSticky"):
            raise RuntimeError("这条时间线没有可以安全交还的起点（录制没有正常结束？）。先点“交还操作”，再接管、读档")
        route.save(detail=False, clean=True)    # nothing has been restored yet: this frame will do
    path, text = script_file(save)          # before anything in the game is touched
    waited = quiet(ctl, save.ticks) if agent.get("soundTicks") != save.ticks else 0
    try:
        restored = route.engine.restore(save.at)
    except RuntimeError as error:
        if "changed since this snapshot" in str(error):
            saves.pop(name, None)
            raise RuntimeError("房间重新读过盘（或换过房间），这个存档已经失效，已删除") from error
        raise
    route.adopt(save.first, save.at, parse_script(text), str(path))
    # Playing with sound wakes the sound threads on frame 0 of the timeline. That frame now is a
    # restored one from another stretch of sound, not the state they went to sleep in.
    route.sound_guarded = False
    route.sound_ticks = save.ticks
    server.trace = dict(save.trace)
    return summary(save) | {"path": str(path), "waited": waited, "last": ctl.status().get("last"),
                            "milliseconds": round(restored.get("milliseconds", 0)),
                            "tracked": restored.get("tracked")}


def dispatch(server, command, args):
    if command == "state-list":
        return listing(server)
    if command == "state-save":
        return save_state(server, args.get("name"))
    if command == "state-load":
        return load_state(server, clean_name(args.get("name")))
    if command == "state-delete":
        return delete_state(server, clean_name(args.get("name")))
    raise ValueError(f"Unknown command {command!r}")
