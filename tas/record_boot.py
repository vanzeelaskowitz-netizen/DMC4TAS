"""Record game-native keyboard input from the title screen into runs/boot_to_room."""
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread
import time

from client import Client
from tasctl import ROOT, start_service, target_pid
from keycodes import KEY_CODES, KeyCodes
from rollback_memory import ProcessMemory
from timeline import Timeline, format_script, parse_script
from window_mode import require_windowed


OUTPUT = ROOT / "runs" / "boot_to_room"
TITLE_TIMEOUT = 90


def dispatch_record(server, command, args):
    """Called by the single Frida-owning service."""
    ctl = server.ctl
    if command == "record-scene":
        return ctl.rpc.recordscene(bool(args.get("diagnostic")))
    if command == "record-replay-start":
        if getattr(server, "recording", False):
            raise RuntimeError("End the recording before replaying")
        require_windowed(ctl.pid)
        server.route = None
        return ctl.rpc.replaystart(args["segments"], int(args.get("patience", 3600)))
    if command == "record-replay-status":
        return ctl.rpc.replaystatus()
    if command in {"record-prepare", "record-start"}:
        if getattr(server, "recording", False):
            if command == "record-start":
                raise RuntimeError("A recording is already active")
            # A previous console died or was closed without finishing: drop its session and start over.
            try:
                ctl.rpc.recordfinish()
                deadline = time.monotonic() + 30
                while ctl.rpc.recordstop() is None and time.monotonic() < deadline:
                    time.sleep(.05)
            except Exception:
                pass
            server.recording = False
        route = getattr(server, "route", None)
        if route and route.origin is not None:
            raise RuntimeError("End the active TAS timeline before recording")
        require_windowed(ctl.pid)
        if command == "record-prepare":
            initial = ctl.rpc.recordscene()
            scene = initial["scene"]
            if not initial["ready"] and scene["playerPresent"]:
                raise RuntimeError("游戏已经在任务里了。请先退回标题画面，再开始录制。")
            ctl.rpc.live()
            return initial
        test = args.get("test") is True
        if test:
            ctl.pause()
        try:
            result = ctl.rpc.recordstart([(name, *codes) for name, codes in sorted(KEY_CODES.items())], test, args.get("advertise") is True,
                                         args.get("everywhere") is True)
        except Exception:
            if test:
                ctl.rpc.live()
            raise
        server.recording = "test" if test else "native"
        return result
    if not getattr(server, "recording", False):
        raise RuntimeError("No recording is active")
    if command == "record-test-step" and server.recording == "test":
        count = args["count"]
        if not isinstance(count, int) or not 1 <= count <= 120:
            raise ValueError("Test batch must contain 1–120 frames")
        ctl.load(parse_script(args["timeline"]))
        rows = ctl.step(count)
        return [dict(frame=row["number"], keys=row["keys"], presents=row["presents"]) for row in rows]
    if command == "record-poll":
        return ctl.rpc.recordpoll()
    if command == "record-stop":
        ctl.rpc.recordfinish()
        deadline = time.monotonic() + 60   # a loading frame can take many seconds to reach the gate
        result = None
        while result is None and time.monotonic() < deadline:
            result = ctl.rpc.recordstop()
            if result is None:
                time.sleep(.01)
        if result is None:
            raise TimeoutError("录制没能在帧边界上结束。")
        server.recording = False
        # The recording is kept wherever it ends. Healing only applies to a parked battle room.
        if args.get("heal") and not result["reason"] and result["parked"] and result["battle"]:
            from service import heal
            try:
                result["heal"] = heal(ctl)
            except Exception as error:
                result["healError"] = str(error)
        return result
    raise ValueError(f"Unknown recording command {command!r}")


def ready_process(game="se", pid=None):
    from build_profile import profiles
    build = next(p for p in profiles() if p["game"] == game)
    try:
        pid = target_pid(pid, game=game)
    except RuntimeError as error:
        if pid is not None or game == "dx9":
            raise
        if not str(error).endswith("[]"):
            raise
        os.startfile(f"steam://rungameid/{build['steamAppId']}")
        pid = None
    deadline = time.monotonic() + 60
    window_error = None
    while time.monotonic() < deadline:
        try:
            if pid is None:
                pid = target_pid(game=game)
            with ProcessMemory(pid) as memory:
                keyboard = memory.u32(0x400000 + build["roots"]["keyboard"])
                render = memory.u32(0x400000 + build["roots"]["renderer"])
                if keyboard and render and memory.u32(render + build["fields"]["renderDevice"]) and (
                        game == "dx9" or memory.u32(keyboard + build["fields"]["keyDevice"])):
                    try:
                        require_windowed(pid, build["config"])
                    except (OSError, RuntimeError) as error:
                        window_error = error
                    else:
                        return pid
        except (OSError, RuntimeError):
            pass
        time.sleep(.25)
    raise TimeoutError("未等到游戏键盘和渲染器初始化。")


def connect():
    pid = ready_process()
    try:
        client = Client()
        status = client.call("status")
        if client.pid == pid and status["mode"] != "detached":
            try:
                client.call("record-scene")
                return client
            except RuntimeError as error:
                if "Unknown command" not in str(error):
                    raise
        client.call("shutdown")
    except OSError:
        pass
    except RuntimeError as error:
        if "service is down" not in str(error):
            raise
    start_service(pid, live=True)
    return Client(pid)


@dataclass
class Recording:
    first_frame: int
    mapping: list
    timeline: Timeline = field(default_factory=Timeline)
    segments: list = field(default_factory=list)
    unmapped: dict = field(default_factory=dict)   # game key code -> first frame it was seen
    count: int = 0

    def keys(self, held):
        keys = set()
        for name in held:
            if name.startswith("Key0x"):
                code = int(name[5:], 16)
                scans = [index for index, mapped in enumerate(self.mapping) if index and mapped == code]
                if not scans:
                    # No keyboard scan code behind it (mouse buttons arrive this way). It cannot be
                    # replayed through the keyboard device, so leave it out and report it at the end.
                    self.unmapped.setdefault(code, self.count)
                    continue
                name = f"GameKey{code:02X}"
                self.timeline.bindings[name] = KeyCodes(scans[0], code)
            keys.add(name)
        return frozenset(keys)

    def add(self, batch):
        for row in batch:
            self.count += 1
            if row["frame"] != self.first_frame + self.count - 1:
                raise RuntimeError(f"录制帧不连续，收到游戏帧 {row['frame']}。")
            held = self.keys(row["held"])
            scene = row["scene"]
            if self.timeline.rows and self.timeline.rows[-1][2] == held:
                start, _, keys = self.timeline.rows[-1]
                self.timeline.rows[-1] = (start, self.count, keys)
            else:
                self.timeline.rows.append((self.count, self.count, held))
            if self.segments and self.segments[-1]["sync"]["equals"] == scene:
                self.segments[-1]["end"] = self.count
            else:
                self.segments.append({
                    "start": self.count, "end": self.count,
                    "sync": {"equals": scene},
                    "inputsThrough": None, "policy": "wait_for_next",
                })
            if held:
                self.segments[-1]["inputsThrough"] = self.count
                self.segments[-1]["policy"] = "input_then_wait"

    def write(self, path: Path, finish):
        path.parent.mkdir(parents=True, exist_ok=True)
        script = format_script(self.timeline)
        sidecar = {
            "version": 1, "build": 24329835, "frameBasis": "1-based inclusive",
            "sceneSample": "before input in each frame", "frames": self.count,
            "timelineSHA256": sha256(script.encode("utf-8")).hexdigest(),
            "segments": self.segments, "end": finish,
            "unmappedKeys": {f"{code:#x}": frame for code, frame in sorted(self.unmapped.items())},
        }
        # Install the sidecar last so mismatched generations fail the hash check.
        for suffix, content in ((".tas", script), (".json", json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n")):
            temporary = path.with_suffix(suffix + ".tmp")
            temporary.write_text(content, encoding="utf-8", newline="\n")
            temporary.replace(path.with_suffix(suffix))


def prompt_status(recording, parked):
    if not recording.segments:
        return
    scene = recording.segments[-1]["sync"]["equals"]
    label = scene["roomClass"] or scene["missionClass"] or scene["areaClass"] or "启动"
    context = "读盘" if scene["loading"] else "过场" if scene["cutscene"] else label
    hint = " · 等待游戏前台" if parked else ""
    print(f"\r帧 {recording.count:<7} 区段 {len(recording.segments):<4} {context}{hint:<18}",
          end="", flush=True)


def wait_title(client):
    state = client.call("record-prepare")
    deadline = time.monotonic() + TITLE_TIMEOUT
    while not state["ready"] and time.monotonic() < deadline:
        time.sleep(.25)
        state = client.call("record-scene")
    if not state["ready"]:
        raise TimeoutError("等了 90 秒，游戏还没到标题画面或开场宣传片。")
    return state


def record(client):
    wait_title(client)
    start = client.call("record-start")
    capture = Recording(start["frame"] + 1, start["mapping"])
    entered = Event()
    eof = Event()
    def wait_input():
        try:
            sys.stdin.readline() or eof.set()
        finally:
            entered.set()

    active = True
    try:
        print("现在可以开始操作。切回游戏（如果在放宣传片，按键跳过），从标题进任务 2，走到有敌人的那个房间。")
        print("只用键盘，录制期间不要动鼠标。到位后站在离敌人远一点的地方，切回本窗口按回车。")
        Thread(target=wait_input, daemon=True).start()
        while not entered.wait(.2):
            batch = client.call("record-poll")
            capture.add(batch["rows"])
            if batch["reason"]:
                # Keep what was captured so the failure can be diagnosed.
                capture.write(ROOT / "logs" / "record-boot-partial", {"reason": batch["reason"]})
                raise RuntimeError(f"{batch['reason']}（已录到第 {capture.count} 帧，现场记录在 logs\\record-boot-partial）")
            prompt_status(capture, batch["parked"])
        if eof.is_set():
            raise RuntimeError("控制台输入已结束。")
        finish = client.call("record-stop", heal=True)
        active = False
        capture.add(finish["rows"])
        if not capture.count:
            raise RuntimeError(finish["reason"] or "一帧都没有录到。")
        # Always keep what was recorded; say plainly where it ended.
        capture.write(OUTPUT, {key: finish.get(key) for key in ("scene", "frame", "battle", "parked", "heal", "reason")})
        scene = finish["scene"]
        where = f"任务 {scene['mission']} · {scene['roomClass'] or scene['gameClass']}"
        print(f"\n已保存 runs\\boot_to_room.tas 和 .json，共 {capture.count} 帧、{len(capture.segments)} 个区段，结束于 {where}。")
        if finish["reason"]:
            print(f"注意：录制提前结束，原因是 {finish['reason']}")
        if capture.unmapped:
            codes = "、".join(f"{code:#x}" for code in sorted(capture.unmapped))
            print(f"注意：有不属于键盘的输入没有录进去（键号 {codes}，多半是鼠标按键）。")
        if finish["parked"] and finish["battle"]:
            hp = finish.get("heal")
            print("游戏已冻结在战斗房间。" + ("生命已补满。" if hp else f"补血没成功：{finish.get('healError')}"))
        else:
            print("结束时不在有敌人的战斗房间，游戏没有冻结，仍在实时运行。")
    finally:
        if active:
            try:
                client.call("record-stop")
            except (OSError, RuntimeError) as error:
                print(f"\n结束录制时出错：{error}", file=sys.stderr)


def main():
    try:
        record(connect())
        return 0
    except KeyboardInterrupt:
        print("\n录制已取消。")
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(f"\n录制失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
