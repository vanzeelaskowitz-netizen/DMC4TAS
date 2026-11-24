"""记录 DMC4SE 自然运行的调度帧和战斗数据。按 Ctrl+C 停止。"""
import argparse
from datetime import datetime
import json
from queue import Empty, Queue
import signal
from threading import Event
from time import monotonic

import frida
from client import Client, STATE
from tasctl import ROOT, target_pid
from build_profile import identify
from rollback_memory import session_owner


def summary(row):
    c = row["combat"]
    if c is None:
        print(f'{row["frame"]:7}  战斗字段读取失败', flush=True)
        return
    p = c["player"] or {}
    inputs = c["inputUpdates"]
    buttons = "/".join(f'{inputs[-1][k]:x}' if inputs and inputs[-1][k] is not None else "?"
                       for k in ("held", "pressed", "released"))
    near = min((e for e in c["enemies"] or [] if e["distance"] is not None),
               key=lambda e: e["distance"], default=None)
    dist = round(near["distance"], 1) if near else None
    frame = p.get("motionFrame")
    print(f'{row["frame"]:7}  {buttons:>12}  {p.get("action")}/{round(frame, 2) if frame is not None else "?"}'
          f'  {dist}  {near and near["inSphere"]}  {near and near["canStep"]}'
          f'  {c["enemyStep"]}  {c["timeMultiplier"]}', flush=True)


def run_passive(args):
    pid = target_pid()
    build = identify(pid)
    if build["game"] != "se":
        raise RuntimeError("当前程序只支持 Steam Build 24329835")
    log = ROOT / "logs" / f'combat-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl'
    mailbox = Queue()
    count = 0
    def write(message, stream):
        nonlocal count
        if message["type"] != "send":
            raise RuntimeError(message.get("stack", str(message)))
        data = message["payload"]
        for row in data.get("rows", []):
            if row["frame"] != count + 1:
                raise RuntimeError(f"帧号中断，最后写入 {count}")
            stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
            count += 1
            if count == 1 or count % args.every == 0 or (row["combat"] or {}).get("enemyStep"):
                summary(row)
        stream.flush()
        return data.get("end")

    session = frida.attach(pid)
    try:
        session.on("detached", lambda *info: mailbox.put({"type":"detached", "info":str(info)}))
        sources = [(ROOT / "tas" / name).read_text(encoding="utf-8-sig")
                   for name in ("combat.js", "combat_watch.js")]
        source = "\n".join([f"const build = {json.dumps(build)};", *sources])
        script = session.create_script(source)
        script.on("message", lambda message, _: mailbox.put(message))
        with log.open("x", encoding="utf-8") as stream:
            script.load()
            print(log, flush=True)
            print("帧  角色按住/按下/松开(hex)  动作/动画帧  最近距离  球内  原生判定  踩敌触发  单位倍率", flush=True)
            stop = Event()
            previous = signal.signal(signal.SIGINT, lambda *_: stop.set())
            try:
                while not stop.is_set() and (not args.frames or count < args.frames):
                    try:
                        write(mailbox.get(timeout=.2), stream)
                    except Empty:
                        continue
            finally:
                signal.signal(signal.SIGINT, previous)
            if not session.is_detached:
                last = script.exports_sync.stop()
                deadline = monotonic() + 5
                while True:
                    end = write(mailbox.get(timeout=max(.01, deadline-monotonic())), stream)
                    if end is not None:
                        if count != end or end != last:
                            raise RuntimeError("卸载时的末帧号不一致")
                        break
                script.unload()
    finally:
        if not session.is_detached:
            session.detach()
    print(f"已卸载，完整写入 {count} 帧。", flush=True)


def run_service(args):
    if args.frames < 1:
        raise RuntimeError("常驻服务已接管帧闸。指定 --frames N 才会主动放行并记录 N 帧。")
    client = Client()
    path = ROOT / "logs" / f'combat-service-{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl'
    print(path, flush=True)
    count = 0
    with path.open("x", encoding="utf-8") as stream:
        while count < args.frames:
            batch = client.call("step", count=min(60, args.frames - count))
            for row in batch:
                count += 1
                row = {"frame": row["number"], "combat": row["combat"]}
                stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
                if count == 1 or count % args.every == 0 or (row["combat"] or {}).get("enemyStep"):
                    summary(row)
            stream.flush()
    print(f"完整写入 {count} 帧，游戏停在帧闸。", flush=True)


def run(args):
    if STATE.is_file():
        try:
            Client().call("status")
        except OSError:
            pass
        else:
            return run_service(args)
    with session_owner():
        return run_passive(args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--every", type=int, default=10, help="控制台摘要间隔")
    parser.add_argument("--frames", type=int, default=0, help="采满指定帧数后停止（含末批），默认 Ctrl+C")
    args = parser.parse_args()
    if args.every < 1 or args.frames < 0:
        parser.error("every 必须大于零，frames 必须非负")
    run(args)
