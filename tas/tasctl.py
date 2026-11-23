"""Console and Python control API for DMC4SE M1."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import time

import frida

ROOT = Path(__file__).resolve().parents[1]
CRASH_LOG = ROOT / "logs" / "game-exception.log"
(ROOT / "logs").mkdir(exist_ok=True)           # not in the repository: a fresh download has none
sys.path.insert(0, str(ROOT / "work"))
sys.path.insert(0, str(ROOT / "tools"))

from dump_memory import export
from build_profile import identify, profiles
from keycodes import KEY_CODES
from timeline import validate_mapping
from window_mode import require_windowed


def target_pid(pid=None, *, game="se"):
    names = {profile["process"].casefold() for profile in profiles() if game in (None, profile["game"])}
    targets = [item.pid for item in frida.get_local_device().enumerate_processes()
               if item.name.casefold() in names]
    if pid in targets:
        return pid
    if pid is None and len(targets) == 1:
        return targets[0]
    raise RuntimeError(f"Select the running DMC4 PID with --pid. Found {targets}")


class Controller:
    def __init__(self, pid=None, *, start_parked=False):
        self.pid = target_pid(pid, game=None)
        self.build = identify(self.pid)
        self.start_parked = start_parked
        self.errors = []
        self.session = None
        self.script = None

    def attach(self):
        if self.session is not None:
            raise RuntimeError("Controller is already attached")
        self.build = identify(self.pid)
        se = self.build["game"] == "se"
        dump_dir = ROOT / "dump"
        try:
            manifest = "dump.json" if se else "dmc4_dx9.json"
            metadata = json.loads((dump_dir / manifest).read_text(encoding="utf-8"))
        except FileNotFoundError:
            metadata = {}
        image = "dmc4se_mem.exe" if se else "dmc4_dx9_mem.exe"
        if metadata.get("input_sha256") != self.build["sha256"] or not (dump_dir / image).is_file():
            export(self.pid, self.build["game"])
        self.errors.clear()
        require_windowed(self.pid, self.build["config"])
        self.session = frida.attach(self.pid)
        try:
            scripts = ["combat.js", "scene.js", "agent.js"]
            source = "const build = " + json.dumps(self.build) + ";\n" + "\n".join(
                (ROOT / "tas" / name).read_text(encoding="utf-8-sig") for name in scripts)
            if self.start_parked:
                source = "globalThis.TASStartParked = true;\n" + source
            source = f"globalThis.TASCrashLog = {json.dumps(str(CRASH_LOG))};\n" + source
            self.script = self.session.create_script(source)
            self.script.on("message", self._message)
            self.script.load()
            self.rpc = self.script.exports_sync
            self.wait_for(lambda state: state["entryCount"] > 0)
        except BaseException:
            self.session.detach()
            self.session = None
            raise
        return self

    def _message(self, message, data):
        if message["type"] == "error":
            self.errors.append(message.get("stack", message["description"]))

    def status(self):
        state = self.rpc.status()
        if state["failure"] or self.errors:
            raise RuntimeError(state["failure"] or self.errors[-1])
        return state

    def wait_for(self, predicate, timeout=5, *, cleanup=False):
        query = self.rpc.status if cleanup else self.status
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = query()
            if predicate(state):
                return state
            time.sleep(.001)
        raise TimeoutError(f"Timed out waiting for the frame boundary. Last state: {state}")

    def pause(self):
        require_windowed(self.pid, self.build["config"])
        self.rpc.pause()
        return self.wait_for(lambda state: state["parked"])

    def load(self, timeline, position=0):
        validate_mapping(timeline)
        bindings = KEY_CODES | timeline.bindings
        used = {name for _, _, keys in timeline.rows for name in keys}
        observed = sorted(bindings, key=lambda name: (name in used, name))
        rows = [{"count": end-start+1, "keys": [timeline.codes(name) for name in keys]}
                for start, end, keys in timeline.rows]
        return self.rpc.load([(name, *bindings[name]) for name in observed], rows, position)

    @contextmanager
    def frame_gate(self, collect=False):
        """Hold the parked game. `collect` also returns (and resets) the system's list of written pages."""
        deadline = time.monotonic() + 5
        while True:
            gate = self.rpc.holdgate(collect)
            if gate:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("The frame gate is still dispatching messages")
            time.sleep(.001)
        restore = {}
        try:
            yield gate, restore
        finally:
            reset = bool(restore.pop("__reset_watch__", False))
            self.rpc.releasegate(restore or None, reset)

    def step(self, n=1):
        start = self.status()["frame"]
        self.rpc.step(n)
        state = self.wait_for(lambda state: (state["parked"] and state["frame"] >= start+n)
                              or (self.build["game"] == "dx9" and state.get("savesLost") and state["mode"] == "observe"), timeout=max(5, n / 10))
        if not state["parked"]:
            self.rpc.drain()
            raise RuntimeError(f"旧快照已清除（{state['savesLost']}）")
        rows = self.rpc.drain()
        if len(rows) != n or [row["number"] for row in rows] != list(range(start+1, start+n+1)):
            raise RuntimeError("Unexpected number or sequence of completed frames")
        return rows

    def resume(self, until=None):
        return self.rpc.resume(until)

    def detach(self):
        if self.session is None:
            return None
        if self.session.is_detached:
            self.session = None
            return None
        self.rpc.shutdown()
        state = self.wait_for(lambda state: state["stopped"], cleanup=True)
        restored = self.rpc.unhook()
        self.session.detach()
        self.session = None
        if not restored:
            raise RuntimeError("At least one IAT slot did not restore to its original pointer")
        return state

    def __enter__(self):
        return self.attach()

    def __exit__(self, kind, value, trace):
        if self.session is not None:
            self.detach()


def start_service(pid, *, live=False):
    from client import Client

    try:
        return Client(pid).call("status")
    except (OSError, RuntimeError):
        pass
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    command = [sys.executable, "-B", "-u", str(ROOT / "tas" / "service.py")]
    if pid is not None:
        command += ["--pid", str(pid)]
    if live:
        command.append("--live")
    with (ROOT / "logs" / "tas-service.txt").open("a", encoding="utf-8") as out:
        with (ROOT / "logs" / "tas-service.err.txt").open("a", encoding="utf-8") as err:
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                       stdout=out, stderr=err, creationflags=flags)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            return Client(pid).call("status")
        except (OSError, RuntimeError):
            if process.poll() is not None:
                break
            time.sleep(.1)
    raise RuntimeError("Service startup failed. See logs/tas-service.err.txt.")


def run_command(pid, command, argument=""):
    from client import Client, STATE

    if command == "start":
        return start_service(pid)
    if command == "attach":
        try:
            return Client(pid).call("attach")
        except (OSError, RuntimeError):
            if STATE.exists() or identify(target_pid(pid, game=None))["game"] != "dx9":
                raise
        return start_service(pid, live=True)
    client = Client(pid)
    if command == "stop":
        return client.call("shutdown")
    if command in {"step", "resume", "back", "goto"}:
        count = int(argument or {"step": "1", "resume": "60"}.get(command, ""))
        return client.call(command, count=count)
    if command == "load":
        return client.call(command, path=str(Path(argument.strip().strip('"')).resolve()))
    if command in {"pause", "save", "status", "detach"}:
        return client.call(command)
    raise ValueError(f"Unknown command {command!r}")


def main():
    from client import Client
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--snapshot-interval", type=int)
    parser.add_argument("--snapshot-count", type=int)
    parser.add_argument("command", nargs="?")
    parser.add_argument("argument", nargs="?")
    args = parser.parse_args()
    configuration = {key: value for key, value in
                     [("interval", args.snapshot_interval), ("limit", args.snapshot_count)]
                     if value is not None}
    try:
        if args.command:
            if configuration and args.command == "start":
                start_service(args.pid)
            if configuration:
                Client(args.pid).call("configure", **configuration)
            print(json.dumps(run_command(args.pid, args.command, args.argument or ""), ensure_ascii=False))
            return 0
        if configuration:
            Client(args.pid).call("configure", **configuration)
        print("start | attach | pause | step [N] | resume [N] | load PATH | save | back N | goto F | status | detach | stop | quit")
        while True:
            try:
                command, _, argument = input("tas> ").strip().partition(" ")
                if command == "quit":
                    return 0
                if command:
                    print(json.dumps(run_command(args.pid, command, argument), ensure_ascii=False))
            except (OSError, ValueError, RuntimeError) as error:
                print(f"Error: {error}", file=sys.stderr)
            except EOFError:
                return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
