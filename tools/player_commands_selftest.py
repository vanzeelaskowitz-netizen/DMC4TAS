"""Offline player/service/agent contract. Run with the project's Python."""
import ast
import ctypes
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
TAS = ROOT / "tas"
sys.path[:0] = [str(TAS), str(ROOT / "work"), str(ROOT / "tools")]

from player import Player
from service import Service
import start_game
import savestate_selftest as fake
import rollback
import take
import savestate


# Service names and the agent methods behind them (including route management).
GATE = {"status", "holdgate", "releasegate", "pause"}
STEP = GATE | {"step", "drain", "load"}
RPC = {
    "back": STEP, "cheat": {"cheat"}, "goto": STEP, "live": GATE | {"live", "load"},
    "load": STEP, "pause": GATE, "play-sound": STEP | {"audible", "resume"},
    "play-sound-stop": STEP | {"audible"}, "record-scene": {"recordscene"},
    "resume": GATE | {"resume"}, "state-delete": {"status", "keepsaves"},
    "state-list": {"status"}, "state-load": GATE | {"load"}, "state-save": GATE | {"keepsaves"},
    "status": {"status"}, "step": STEP, "take-start": STEP | {"recordscene", "recordstart", "live"},
    "take-stop": STEP | {"recordfinish", "recordpoll", "recordstop"}, "trace": set(),
    "unpause": STEP | {"recordscene"},
}


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def literal_commands(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return literal_commands(node.body) | literal_commands(node.orelse)
    raise AssertionError(f"Unclassified command expression at line {node.lineno}")


def inventory():
    commands, direct = set(), set()
    for name in ("player", "take", "savestate", "overlay"):
        tree = ast.parse((TAS / (name + ".py")).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "call":
                arg = node.args[0]
                if isinstance(arg, ast.Name) and arg.id == "command" and name == "player":
                    continue  # Player.call forwards its callers' first argument
                commands.update(literal_commands(arg))
            if isinstance(func, ast.Name) and func.id == "dispatch" and name == "take":
                commands.update(literal_commands(node.args[1]))
            if isinstance(func, ast.Attribute) and getattr(func.value, "attr", None) == "rpc":
                direct.add(func.attr)
    check(commands == RPC.keys(), f"service inventory differs: {commands ^ RPC.keys()}")
    check(direct <= set.union(*RPC.values()) | {"soundbusy"}, f"unclassified RPC: {direct}")


def agent_exports():
    # Evaluate only the export declarations and the actual version filter in Node.
    # No Frida runtime, game, or exported function is invoked.
    source = (TAS / "agent.js").read_text(encoding="utf-8-sig")
    js = source.split("rpc.exports = {", 1)[1]
    declarations, guard = js.split("// Keep every SE-only RPC", 1)
    live_room = source[source.index("function liveRoom()"):source.index("function pinFocus()")]
    script = ("""const rpc = {}, status = () => {};
              function inspect(se) {
              const sceneState = {sample: () => ({playerPresent:true, roomClass:'aRoom', loading:false, cutscene:false})};
              let mode='paused', parked=true, recording=null, gateHeld=false, recordEverywhere=false;
              const windowed=()=>true, build={fields:{keyMap:0}}, frame=12;
              const keyboard=()=>({add:()=>({readByteArray:()=>new Uint8Array(256)})});
              let keyMap=[], targetWall=0;""" + live_room + "rpc.exports = {"
              + declarations + "\nconst before = {...rpc.exports};\n// Keep every SE-only RPC"
              + guard + """
              if (!se) {
                const start = rpc.exports.recordstart([], false, false, false, true);
                if (start.frame !== frame || mode !== 'record' || !recording.inRoom) throw Error('DX9 take');
                mode = 'observe';
                if (liveRoom()) throw Error('DX9 idle freeze');
              }
              return Object.keys(before).filter(k => before[k] === rpc.exports[k]);}"""
              + "console.log(JSON.stringify({se:inspect(true),dx9:inspect(false)}));")
    result = subprocess.run(["node", "-"], input=script, text=True, encoding="utf-8",
                            capture_output=True)
    check(result.returncode == 0, result.stderr)
    return json.loads(result.stdout)


def dx9_session():
    class Controller(fake.Controller):
        build = {"game": "dx9"}
        session = True

        def step(self, n):
            if paused["paused"]:
                check(self.loaded[0].rows[0][2] == {"Escape"}, "unpause must send Escape")
                paused["paused"] = False
            return super().step(n)

        def status(self):
            result = super().status()
            combat = result["last"]["combat"]
            combat["player"].update(action=1, motion=2, motionFrame=self.frame, hp=100,
                                    stepCooldown=0, hitStopTimer=0)
            combat.update(enemies=[], enemiesComplete=True, rng=str(self.frame))
            return result

    ctl = Controller()
    ctl.pid = 0
    raw = ctl.rpc.status
    ctl.rpc.status = lambda: raw() | {"snapshotsAlive": True, "mode": "paused"}
    server = SimpleNamespace(ctl=ctl, m1_origin=None)
    call = lambda cmd, **args: Service.dispatch(server, cmd, args)
    paused = {"paused": True, "playerPresent": True, "roomClass": "aRoom", "loading": False, "cutscene": False}
    ctl.rpc.recordscene = lambda: {"scene": paused}
    with TemporaryDirectory(dir=ROOT / "logs") as folder, patch.object(rollback, "SnapshotEngine", fake.Engine), \
            patch.object(take, "TAKES", Path(folder)), patch.object(savestate, "FILES", Path(folder)):
        start = call("take-start")
        check(not paused["paused"] and start["origin"] == ctl.frame, "paused-menu take")
        ctl.rpc.recorded = [{"frame": ctl.frame + i, "held": ["Space"] if i < 3 else ["J"],
                             "scene": paused.copy()} for i in range(1, 5)]
        ctl.frame += 4
        result = call("take-stop", check=True)
        check(result["consistent"] and result["frames"] == 4, "replay fingerprint and count")
        check(call("state-save", name="example")["frame"] == 4, "save")
        check(call("state-list")["saves"][0]["name"] == "example", "list")
        call("goto", count=0)
        ctl.agent.ticks += 3
        check(call("state-load", name="example")["frame"] == 4 and not ctl.agent.asked,
              "DX9 load must avoid XAudio2")
        check(call("state-delete", name="example")["count"] == 0, "delete")
    print("DX9 synthetic pause, take, replay fingerprint, save/list/load/delete passed")


def main():
    inventory()
    dx9_session()
    exports = agent_exports()
    for game in ("se", "dx9"):
        ui = SimpleNamespace(root=Mock(), game=None, sound_play=Mock(), overlay_var=Mock(), overlay=Mock(),
                             overlay_button=Mock(), sound_button=Mock(), record_button=Mock(),
                             check_button=Mock(), save_button=Mock(), state_button=Mock())
        Player.set_version(ui, game, {"soundGuard": True, "playerProtocol": 10})
        sound = ui.sound_button.configure.call_args.kwargs["state"] == "normal"
        check(sound, f"{game}: sound checkbox")
        check(not ui.overlay_button.configure.called, f"{game}: F6 stays enabled")
        build = {"game": game, "id": "dx9_21360" if game == "dx9" else "se_24329835"}
        service = SimpleNamespace(ctl=SimpleNamespace(build=build, session=None, pid=0))
        check(Service.dispatch(service, "motions", {})["nero"]["action"]["enemyStep"] == 25, f"{game}: JC table")
        with patch("service.service_operations.dispatch", return_value=True):
            for command, methods in sorted((RPC | {"probe": STEP}).items()):
                disabled = command.startswith("play-sound") and not sound
                try:
                    Service.dispatch(service, command, {})
                except RuntimeError:
                    if not disabled:
                        raise
                if not disabled:
                    required = methods | ({"soundbusy"} if game == "se" and command == "state-load" else set())
                    check(required <= set(exports[game]), f"{game} {command}: missing {required - set(exports[game])}")
                print(game, command, "disabled" if disabled else "allowed")
    with patch.object(start_game, "choose", return_value="dx9") as choose:
        user = Mock()
        running = {"dx9": 10, "se": 20}
        with patch.object(start_game.ctypes, "windll", SimpleNamespace(user32=user)):
            for pid, expected in ((10, "dx9"), (20, "se"), (30, "dx9")):
                def owner(_, output):
                    ctypes.cast(output, ctypes.POINTER(start_game.wintypes.DWORD))[0] = pid
                user.GetWindowThreadProcessId.side_effect = owner
                check(start_game.select_game(running) == expected, "two games, foreground or dialog")
        for game in ("se", "dx9"):
            check(start_game.select_game({game: 1}) == game, "one running game")
            with patch.object(start_game.watch_launch, "find_game",
                              side_effect=lambda g: Mock(is_file=lambda: g == game)):
                check(start_game.select_game({}) == game, "one installed game")
        with patch.object(start_game.watch_launch, "find_game", return_value=Mock(is_file=lambda: True)):
            check(start_game.select_game({}) == "dx9" and choose.called, "two installations")
    print("all passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise
