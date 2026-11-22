"""Check the four practice aids against the game: step frames with each one off and on.

Takes the game over without a timeline (m1 commands) and hands it back. Lowers the player's life
and devil gauge by a known amount first (written back to full by the aids, or by this test).
"""
import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from client import Client  # noqa: E402


def main():
    client = Client()
    pid = client.call("status")["gamePid"]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    handle = kernel.OpenProcess(0x0008 | 0x0010 | 0x0020 | 0x0400, False, pid)

    def f32(address):
        buffer = (ctypes.c_char * 4)()
        kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, 4, None)
        return struct.unpack("<f", bytes(buffer))[0]

    def u32(address):
        buffer = (ctypes.c_char * 4)()
        kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, 4, None)
        return struct.unpack("<I", bytes(buffer))[0]

    def poke(address, value):
        kernel.WriteProcessMemory(handle, ctypes.c_void_p(address), struct.pack("<f", value), 4, None)

    def step(count):
        return client.call("m1-step", count=count)[-1]

    def enemies(row):
        return [(item["type"], item["hp"]) for item in (row.get("combat") or {}).get("enemies") or []]

    scene = client.call("record-scene")["scene"]
    if scene["paused"]:
        print("unpause", client.call("unpause"))
    client.call("m1-pause")
    mediator = u32(0x1154B34)
    player = u32(mediator + 0x24)
    ok = True

    def verdict(label, good, detail):
        nonlocal ok
        ok = ok and good
        print(f"{'ok  ' if good else 'FAIL'} {label}: {detail}")

    # time
    before = f32(mediator + 0x384); step(30); off = f32(mediator + 0x384)
    # Read after a frame has run, the held value shows one frame's worth less; it must not move.
    client.call("cheat", name="time", on=True); step(30); first = f32(mediator + 0x384)
    step(120); on = f32(mediator + 0x384)
    verdict("时间不减", off < before and on == first and off - first < 2,
            f"关 30 帧 {before:.1f}->{off:.1f}  开 150 帧 {first:.1f}->{on:.1f}")
    client.call("cheat", name="time", on=False); step(10); again = f32(mediator + 0x384)
    verdict("时间不减 关掉后恢复倒数", again < on, f"{on:.1f}->{again:.1f}")

    # life and devil gauge
    hp_max, dt_max = f32(player + 0x1B04), f32(player + 0x2508)
    poke(player + 0x1B00, hp_max - 3000); poke(player + 0x2504, 0.0); step(2)
    hp_off, dt_off = f32(player + 0x1B00), f32(player + 0x2504)
    client.call("cheat", name="hp", on=True); client.call("cheat", name="dt", on=True); step(2)
    hp_on, dt_on = f32(player + 0x1B00), f32(player + 0x2504)
    verdict("锁血", hp_off < hp_max and hp_on == hp_max, f"上限 {hp_max:.0f}  关 {hp_off:.0f}  开 {hp_on:.0f}")
    verdict("无限魔力", dt_off < dt_max and dt_on == dt_max, f"上限 {dt_max:.0f}  关 {dt_off:.0f}  开 {dt_on:.0f}")
    client.call("cheat", name="hp", on=False); client.call("cheat", name="dt", on=False)

    # life lock on enemies: wound them, the lock fills them up again
    row = step(1)
    for item in (row.get("combat") or {}).get("enemies") or []:
        pointer = int(item["pointer"], 16)
        for off in (0x1A50, 0x1A58, 0x1A60, 0x1A70, 0x1A80, 0x2C30, 0x2D80, 0x3E38, 0x85E8):
            if u32(pointer + off) == 0xF2D184:
                poke(pointer + off + 0x30, f32(pointer + off + 0x34) / 2)
    wounded = enemies(step(1))
    client.call("cheat", name="hp", on=True); healed = enemies(step(2))
    client.call("cheat", name="hp", on=False)
    verdict("锁血（敌人）", bool(healed) and all(hp is not None and hp > half for (_, hp), (_, half) in zip(healed, wounded)),
            f"砍成一半 {wounded}  锁血后 {healed}")

    # one-hit kill
    row = step(1); was = enemies(row)
    state = client.call("cheat", name="kill", on=True); row = step(2); now = enemies(row)
    state = client.call("status")["cheats"]
    known = [hp for _, hp in now if hp is not None]
    verdict("一击必杀", bool(known) and all(hp <= 1 for hp in known),
            f"之前 {was}  之后 {now}  覆盖 {state['enemiesCovered']}/{state['enemies']}")
    client.call("cheat", name="kill", on=False)
    print("cheats now:", client.call("status")["cheats"])
    print("handed back:", client.call("live").get("mode"))
    print("PASS" if ok else "FAIL")


if __name__ == "__main__":
    main()
