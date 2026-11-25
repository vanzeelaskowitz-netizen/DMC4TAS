"""Restart the game and replay runs/boot_to_room back into the battle room, then park and heal.

Usage: boot_to_room.py [--keep]      --keep replays into the running game instead of restarting it
"""
import argparse
import json
import os
import subprocess
import sys
import time
from hashlib import sha256

from client import Client, STATE, remove_state
from record_boot import OUTPUT, connect, wait_title
from tasctl import ROOT
from timeline import parse_script, wire_keys

GAME = "DevilMayCry4SpecialEdition.exe"
SYSTEM_KEYS = {"LeftAlt", "RightAlt", "Tab", "LeftWin", "RightWin"}   # window switching, never game input
TIMEOUT = 420


def scene_key(scene):
    """Must match sceneKeyOf() in agent.js."""
    menus = "+".join(f"{item['name']}:{item['state']}" for item in scene["menus"] if "::" not in item["name"])
    return "|".join(str(value) if not isinstance(value, bool) else str(value).lower() for value in (
        scene["gameClass"], scene["missionClass"], scene["roomClass"], scene["cutscene"],
        scene["playerPresent"], menus)).replace("None", "null")


def build_plan(path=OUTPUT):
    script = path.with_suffix(".tas").read_text(encoding="utf-8")
    sidecar = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    if sha256(script.encode("utf-8")).hexdigest() != sidecar["timelineSHA256"]:
        raise RuntimeError("boot_to_room.tas and .json are from different recordings")
    timeline = parse_script(script)
    held = {}
    last_input = 0
    for start, end, keys in timeline.rows:
        keys = frozenset(keys) - SYSTEM_KEYS
        for frame in range(start, end + 1):
            held[frame] = keys
        if keys:
            last_input = end
    segments = []
    for item in sidecar["segments"]:
        scene = item["sync"]["equals"]
        if scene["loading"]:
            continue                              # the replay waits through loading on its own
        key = scene_key(scene)
        frames = [held.get(frame, frozenset()) for frame in range(item["start"], min(item["end"], last_input) + 1)]
        if not frames:
            continue
        if segments and segments[-1]["key"] == key:
            segments[-1]["frames"] += frames
        else:
            segments.append({"key": key, "frames": frames})
    plan = []
    for segment in segments:
        rows = []
        for keys in segment["frames"]:
            wired = wire_keys(timeline, keys)
            if rows and {k: rows[-1][k] for k in wired} == wired:
                rows[-1]["count"] += 1
            else:
                rows.append({"count": 1, **wired})
        plan.append({"key": segment["key"], "rows": rows})
    return plan, sum(len(s["frames"]) for s in segments), last_input


def restart_game():
    try:
        Client().call("shutdown", force=True)     # the game is about to be closed anyway
    except (OSError, RuntimeError):
        pass
    remove_state()
    # watch_launch closes a running game itself and then waits for Steam to notice before launching.
    # Start through watch_launch so the allocator arenas get write watch (fast snapshots). If that
    # fails the game still starts and snapshots fall back to reading everything.
    import watch_launch
    watch_launch.launch()
    time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="do not restart the game first")
    parser.add_argument("--plan", action="store_true", help="print the replay plan and exit")
    args = parser.parse_args()
    plan, frames, last_input = build_plan()
    if args.plan:
        for index, segment in enumerate(plan):
            print(index, sum(row["count"] for row in segment["rows"]), segment["key"])
        print("frames", frames, "last input frame", last_input)
        return 0
    started = time.monotonic()
    if not args.keep:
        restart_game()
    client = connect()
    wait_title(client)
    client.call("record-replay-start", segments=plan)
    last = None
    while True:
        time.sleep(.5)
        state = client.call("record-replay-status")
        label = (state["segment"], state["loading"], state["key"])
        if label != last:
            print(f"{time.monotonic() - started:6.1f}s  区段 {state['segment'] + 1}/{state['segments']}  "
                  f"{'读盘中' if state['loading'] else state['key']}", flush=True)
            last = label
        if state["done"]:
            break
        if time.monotonic() - started > TIMEOUT:
            raise TimeoutError(f"回放超时，停在区段 {state['segment'] + 1}：{state['key']}")
    if state["reason"]:
        raise RuntimeError(f"回放没有到位：{state['reason']}（当前场景 {state['key']}，期望 {state['expected']}）")
    healed = client.call("heal")
    scene = client.call("record-scene")["scene"]
    print(f"已回到 任务 {scene['mission']} · {scene['roomClass']}，用时 {time.monotonic() - started:.0f} 秒，"
          f"跳段 {state['jumps']} 次。游戏已冻结，生命 {healed['hp']:.0f}。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as error:
        print(f"回到房间失败：{error}", file=sys.stderr)
        raise SystemExit(1)
