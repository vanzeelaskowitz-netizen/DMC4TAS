"""Self-test of the save-state bookkeeping (tas/savestate.py) without the game.

    .venv\\Scripts\\python.exe -B tools\\savestate_selftest.py

The snapshot engine and the game are replaced by stand-ins that only remember frame numbers, so
this checks the logic around them: which snapshot is restored, what the timeline looks like
afterwards, which script file is opened, when a load waits for the sound or is refused. Whether a
restored save really runs in the game can only be seen in the game.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work"), str(ROOT / "tools")]

import lang  # noqa: E402
import rollback  # noqa: E402
import savestate  # noqa: E402
import service_operations  # noqa: E402
import take  # noqa: E402
import timeline  # noqa: E402

lang.current = lambda: "zh"     # the checks below look for words of the Chinese messages

class Shot:
    def __init__(self, frame):
        self.frame = frame
        self.clocks = {"frame": frame}
        self.pages, self.base, self.bytes, self.milliseconds = None, None, 0, 0


class Engine:
    live = None
    verify_failures = 0

    def __init__(self, controller, profile=None):
        self.controller, self.restored = controller, []

    def capture(self):
        return Shot(self.controller.frame)

    def restore(self, snapshot, trace=None):
        if snapshot.clocks.get("epoch", self.controller.agent.epoch) != self.controller.agent.epoch:
            raise RuntimeError("Room, actor set or rendering device changed since this snapshot")
        self.controller.frame = snapshot.frame
        self.controller.agent.sticky = True
        self.restored.append(snapshot)
        return {"frame": snapshot.frame, "milliseconds": 1.0, "tracked": False}


class Agent:
    """What the service asks the agent (agent.js) in these code paths."""
    def __init__(self):
        self.kept, self.lost, self.ticks, self.sticky, self.epoch = False, None, 7, False, 0
        self.busy = []              # answers of soundbusy still to give
        self.loops = 0              # looping effects started since the save
        self.asked = []

    def status(self):
        return {"savesKept": self.kept, "savesLost": self.lost, "soundTicks": self.ticks,
                "soundSticky": self.sticky, "soundGuard": False}

    def keepsaves(self, on):
        self.kept = bool(on)
        if on:
            self.lost = None
        return self.status()

    def soundbusy(self, since, empty):
        self.asked.append((since, empty))
        answer = self.busy.pop(0) if self.busy else {"busy": self.loops, "streams": 0}
        if empty and self.loops:        # a looping effect plays until it is stopped and emptied
            answer = {"busy": self.loops, "streams": 0, "emptied": self.loops}
            self.loops = 0
        return {"ready": True, "ticks": self.ticks, "emptied": 0} | answer

    # a take (take.py): the rows are handed over when it stops
    def recordscene(self):
        return {"scene": {"playerPresent": True, "loading": False, "cutscene": False, "paused": False}}

    def recordstart(self, bindings, test, advertise, everywhere, in_room):
        return {"frame": self.controller.frame, "mapping": [0] * 256}

    def recordpoll(self):
        rows, self.recorded = self.recorded, []
        return {"rows": rows, "reason": None, "parked": True, "frame": self.controller.frame}

    def recordfinish(self):
        return self.status()

    def recordstop(self):
        return {"rows": [], "reason": None, "parked": True, "frame": self.controller.frame}


class Controller:
    def __init__(self):
        self.frame, self.agent, self.loaded = 100, Agent(), None
        self.rpc = self.agent
        self.agent.controller, self.agent.recorded = self, []

    def status(self):
        return {"frame": self.frame, "parked": True, "mode": "paused",
                "last": {"number": self.frame, "combat": {"player": {"position": [1.0, 2.0, 3.0]},
                                                          "enemies": [{}, {}]}}}

    def pause(self):
        return self.status()

    def load(self, timeline, position=0):
        self.loaded = (timeline, position)

    def step(self, n=1):
        rows = [{"number": self.frame + index + 1} for index in range(n)]
        self.frame += n
        return rows

    @contextmanager
    def frame_gate(self, collect=False):
        yield {}, {}


def expect(condition, text):
    print(("ok   " if condition else "FAIL ") + text)
    if not condition:
        raise SystemExit(1)


def refused(call, part):
    try:
        call()
    except (RuntimeError, ValueError) as error:
        return part in str(error)
    return False


def main():
    rollback.SnapshotEngine = Engine
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        savestate.FILES = take.TAKES = folder / "runs"
        script = folder / "mine.tas"
        script.write_text("".join(f"Space{' K' if 5 <= row <= 6 else ''}\n" for row in range(1, 31)), encoding="utf-8")
        ctl = Controller()
        server = SimpleNamespace(ctl=ctl, route=None, trace={})

        # a take-over, a script, twenty frames in
        route = service_operations.new_route(server)
        route.pause()
        route.save(detail=False, clean=True)
        route.load(str(script))
        route.step(20)
        for number in range(100, 121):
            server.trace[number] = ("action", number)
        expect(refused(lambda: savestate.save_state(server, "  "), "名字"), "a save needs a name")
        made = savestate.save_state(server, "tail/side")
        expect(made["name"] == "tail side" and made["frame"] == 20 and made["count"] == 1, "save on frame 20, name cleaned")
        expect(ctl.agent.kept, "the agent is told to keep GPU resources")
        expect(made["note"].startswith("位置 (1, 2, 3)") and "2 个" in made["note"], "the list line says where and how many")
        save = server.saves["tail side"]
        expect(save.first.frame == 100 and save.at.frame == 120 and len(save.trace) == 21, "both snapshots and the trace are kept")
        route.goto(0)
        zero = savestate.save_state(server, "start")
        expect(zero["frame"] == 0 and server.saves["start"].at is server.saves["start"].first, "a save on frame 0 uses the first snapshot")

        # no sound in between: straight back, same file, same frame numbers
        route.goto(26)
        loaded = savestate.load_state(server, "tail side")
        expect(loaded["frame"] == 20 and ctl.frame == 120 and loaded["waited"] == 0, "load: game on frame 20, no waiting")
        expect(Path(loaded["path"]) == script.resolve(), "the player's own script file is opened")
        expect(route.origin == 100 and [item.frame for item, _ in route.checkpoints] == [100, 120], "timeline: frame 0 and the save")
        expect(ctl.loaded[1] == 20 and route.sound_guarded is False, "script position 20; no play with sound")
        expect(set(server.trace) == set(range(100, 121)), "the trace of the saved frames is back")
        route.goto(0)
        expect(ctl.frame == 100, "step back to frame 0 of the save's timeline works")
        route.goto(23)
        expect(ctl.frame == 123, "and forward past the save")

        # a take after a load goes on from the frame the game is on and continues that timeline
        clean = route.clean
        started = take.start(server)
        expect(started["continued"] == 23 and not started["returned"], "a take after a load starts on the current frame")
        expect(server.route is not route and server.route.clean is clean, "its timeline inherits the state to hand back in")
        ctl.agent.recorded = [{"frame": 124 + index, "held": ["Space", "J"] if index < 3 else ["Space"], "scene": {}}
                              for index in range(10)]
        ctl.frame += 10
        report = take.stop(server)
        route = server.route
        rows = Path(report["path"]).read_text(encoding="utf-8")
        held = [keys for _, keys, _, _ in timeline.frames([(a, b, sorted(k)) for a, b, k in timeline.parse_script(rows).rows])]
        expect(report["frames"] == 10 and report["continued"] == 23 and len(held) == 33, "the file is 23 old rows plus 10 recorded")
        expect("K" in held[4] and "J" in held[23] and "J" in held[25] and "J" not in held[26], "old keys and new keys where they belong")
        expect(route.origin == 100 and ctl.loaded[1] == 33 and route.clean is clean, "the timeline is the old one again, at frame 33")
        expect([item.frame for item, _ in route.checkpoints] == [100, 123, 133], "with frame 0, the take's first frame and now")
        route.goto(5)
        expect(ctl.frame == 105, "stepping back before the take works")

        # hand back, play (sound runs), take over again
        server.route = None
        ctl.agent.sticky = False
        ctl.agent.ticks += 40
        ctl.frame = 500
        ctl.agent.busy = [{"busy": 2, "streams": 1}, {"busy": 1, "streams": 0}, {"busy": 0, "streams": 0}]
        loaded = savestate.load_state(server, "tail side")
        route = server.route
        expect(ctl.agent.asked[0] == (7, False) and len(ctl.agent.asked) >= 4, "after sound ran, the load waits for the voices")
        expect(route.clean is not None and route.clean.frame == 500, "the take-over frame is the state to hand back in")
        expect(ctl.frame == 120 and route.origin == 100, "then the game is on the saved frame")

        # a stream that never ends: refused, nothing restored
        ctl.agent.ticks += 1
        savestate.SOUND_WAIT = .2
        ctl.agent.busy = [{"busy": 1, "streams": 1}] * 50
        before = len(route.engine.restored)
        expect(refused(lambda: savestate.load_state(server, "start"), "背景音乐"), "a stream that keeps playing refuses the load")
        expect(len(route.engine.restored) == before, "and nothing was restored")
        ctl.agent.busy = []

        # looping effects started in between are emptied
        ctl.agent.loops = 1
        ctl.agent.asked.clear()
        savestate.load_state(server, "start")
        expect(any(empty for _, empty in ctl.agent.asked), "a looping effect is stopped and emptied before the load")
        expect(ctl.frame == 100, "then frame 0 is restored")

        # the player's file no longer leads to the saved frame: a copy of the old script is opened
        script.write_text("".join(f"Space{' J' if row == 3 else ''}\n" for row in range(1, 31)), encoding="utf-8")
        ctl.agent.ticks = server.saves["tail side"].ticks
        loaded = savestate.load_state(server, "tail side")
        copy = Path(loaded["path"])
        expect(copy.name == "save-tail side.tas" and "K" in copy.read_text(encoding="utf-8"), "an edited file is left alone, a copy is used")
        copy.write_text(copy.read_text(encoding="utf-8") + "Space L\n", encoding="utf-8")
        loaded = savestate.load_state(server, "tail side")
        expect(Path(loaded["path"]) == copy and "L" in copy.read_text(encoding="utf-8"), "rows added after the saved frame are kept")

        # the list, deleting, and the end of the room
        names = [item["name"] for item in savestate.listing(server)["saves"]]
        expect(names == ["tail side", "start"], "the list")
        expect(savestate.delete_state(server, "start")["count"] == 1 and ctl.agent.kept, "one deleted, the guard stays")
        ctl.agent.kept, ctl.agent.lost = False, "scene change"
        listing = savestate.listing(server)
        expect(listing["saves"] == [] and listing["lost"]["reason"] == "scene change", "the room went: no saves, and why")
        expect(refused(lambda: savestate.load_state(server, "tail side"), "失效"), "loading a lost save says so")
        savestate.save_state(server, "again")
        expect(savestate.delete_state(server, "again")["count"] == 0 and not ctl.agent.kept, "last one deleted: the guard is released")
    print("all passed")


if __name__ == "__main__":
    main()
