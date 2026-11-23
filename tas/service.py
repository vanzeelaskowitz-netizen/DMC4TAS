"""Own the local Frida session and keep the game parked between requests."""
import argparse
import hmac
import json
import os
import secrets
import socketserver
import sys
import time
import traceback

from tasctl import Controller, ROOT
from rollback_memory import ProcessMemory, session_owner
import service_operations

STATE = ROOT / "logs" / "tas-service.json"


def heal(controller):
    with controller.frame_gate() as (gate, _), ProcessMemory(controller.pid, write=True) as memory:
        player = int(gate["combat"]["player"]["pointer"], 16)
        with memory.quiescent(gate["tid"]):
            old = memory.read(player + 0x1b00, 4)
            maximum = memory.read(player + 0x1b04, 4)
            memory.write(player + 0x1b00, maximum)
            result = memory.read(player + 0x1b00, 4)
            if result != maximum:
                raise RuntimeError("Player HP readback differs from its maximum")
    from struct import unpack
    return {"player": hex(player), "before": unpack("<f", old)[0], "hp": unpack("<f", result)[0],
            "frame": gate["frame"], "parked": controller.status()["parked"]}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            request = json.loads(self.rfile.readline(4 << 20))
            if not hmac.compare_digest(request.get("token", ""), self.server.token):
                raise ValueError("Invalid service token")
            result = {"result": self.server.dispatch(request["command"], request.get("args", {}))}
        except Exception as error:
            traceback.print_exc()
            result = {"error": str(error)}
        self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")


class Service(socketserver.TCPServer):
    timeout = .03

    def __init__(self, ctl):
        super().__init__(("127.0.0.1", 0), Handler)
        self.ctl = ctl
        self.token = secrets.token_hex(24)
        self.stopped = False
        self.m1_origin = None

    def dispatch(self, command, arguments):
        if command == "timeline-keys":
            return service_operations.timeline_keys(getattr(self, "route", None), **arguments)
        if self.ctl.build["game"] == "dx9" and command not in {
            "attach", "detach", "pause", "step", "status", "load", "resume", "live", "shutdown",
            "save", "back", "goto", "configure", "accept", "trace", "cheat", "m1-load", "m1-step", "m1-resume", "m1-pause", "m1-fallback-test", "record-scene",
            "take-start", "take-stop", "unpause", "state-save", "state-load", "state-list", "state-delete",
        }:
            raise RuntimeError("原版 DX9 不支持这项命令。")
        if (self.ctl.build["game"] == "dx9" and self.ctl.session
                and (getattr(self, "route", None) or command in ("back", "goto"))):
            state = self.ctl.rpc.status()
            if state["savesLost"] and not state["snapshotsAlive"]:
                self.route = None
                if command in ("back", "goto"):
                    raise RuntimeError(f"旧快照已清除（{state['savesLost']}）")
        if command == "status":
            if self.ctl.session is None:
                return {"mode": "detached", "parked": False, "gamePid": self.ctl.pid}
            result = self.ctl.status() | {"gamePid": self.ctl.pid, "servicePid": os.getpid()}
            if self.m1_origin is not None:
                result.update(timelineFrame=result["frame"] - self.m1_origin, timelineOrigin=self.m1_origin)
            route = getattr(self, "route", None)
            result["soundRouteReady"] = route is None or bool(getattr(route, "sound_guarded", False))
            result["playerProtocol"] = 10
            if route and route.origin is not None:
                result.update(timelineFrame=result["frame"] - route.origin, timelineOrigin=route.origin,
                              snapshots=[item.frame - route.origin for item, _ in route.checkpoints],
                              timelinePath=route.path)
            result["saves"] = list(getattr(self, "saves", None) or ())
            return result
        if command == "motions":
            path = ROOT / "tas" / "builds" / "motions" / (self.ctl.build["id"] + ".json")
            return json.loads(path.read_text(encoding="utf-8"))
        if command == "heal":
            if getattr(self, "recording", False):
                raise RuntimeError("End the recording before healing")
            self.ctl.pause()
            return heal(self.ctl)
        if command == "reload":
            # Pick up edited tool code without detaching. Detaching is not an option once a
            # snapshot has been restored (see "shutdown"), so this is how the service is updated
            # while a game session is worth keeping. Covers the script format and the commands;
            # the snapshot engine and the agent keep running as they are.
            import importlib
            import rollback
            import timeline
            importlib.reload(timeline)
            rollback.read_script, rollback.Timeline = timeline.read_script, timeline.Timeline
            for name in ("take", "savestate"):
                if name in sys.modules:
                    importlib.reload(sys.modules[name])
            importlib.reload(service_operations)
            return {"reloaded": ["timeline", "take", "savestate", "service_operations"]}
        if command == "shutdown":
            # After the first snapshot restore the game's sound threads are kept asleep for good:
            # their state no longer matches the restored memory. Removing the hooks wakes them and
            # the game crashes within a second (2026-11-19, DevilMayCry4SpecialEdition.exe+0x809957).
            # Returning to the first state of the timeline makes it safe again (hand_back).
            if not arguments.get("force") and self.ctl.session is not None \
                    and not self.ctl.session.is_detached and self.ctl.rpc.status().get("soundSticky"):
                try:
                    service_operations.hand_back(self)
                except Exception as error:
                    raise RuntimeError(f"做过后退之后不能直接卸下工具，游戏会崩溃（{error}）。更新工具代码用 "
                                       "reload；确实要卸下（游戏会关掉）用 shutdown force。") from error
            result = self.ctl.detach()
            self.stopped = True
            return {"stopped": True, "unhooked": result is not None}
        return service_operations.dispatch(self, command, arguments)

    def heartbeat(self):
        if self.ctl.session is None:
            return
        if self.ctl.session.is_detached:
            self.ctl.session = None
            self.route = None
            self.recording = False
            return
        self.ctl.status()
        service_operations.service(self)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--live", action="store_true", help="Attach without pausing")
    args = parser.parse_args()
    with session_owner():
        serve(args.pid, live=args.live)


def serve(pid, *, live=False):
    controller = Controller(pid, start_parked=not live)
    if controller.build["game"] == "dx9":
        live = True
        controller.start_parked = False
    with Service(controller) as server, controller:
        state = controller.status() if live else controller.pause()
        print(json.dumps({"initial_frame": state["frame"], "parked": state["parked"],
                          "game_pid": controller.pid, "service_pid": os.getpid()}), flush=True)
        info = {"pid": os.getpid(), "game_pid": controller.pid, "port": server.server_address[1],
                "token": server.token}
        temporary = STATE.with_suffix(".tmp")
        temporary.write_text(json.dumps(info) + "\n", encoding="utf-8")
        for attempt in range(80):          # a reader may hold the old file for a moment
            try:
                temporary.replace(STATE)
                break
            except PermissionError:
                time.sleep(.025)
        else:
            temporary.replace(STATE)
        try:
            last = 0
            while not server.stopped:
                server.handle_request()
                route = getattr(server, "route", None)
                # During continuous playback the route must be rescheduled as soon as it reaches a
                # checkpoint, or every 120 frames the picture would stall for half a second.
                if time.monotonic() - last > .5 or (route is not None and route.running):
                    server.heartbeat()
                    last = time.monotonic()
        finally:
            from client import remove_state
            try:
                remove_state()
            except RuntimeError:
                pass


if __name__ == "__main__":
    main()
