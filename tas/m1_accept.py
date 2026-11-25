"""Run DMC4SE M1 acceptance with the game in front, then with a test window in front."""
import argparse
import ctypes as ct
from ctypes import wintypes as wt
import json
import struct
import time
import traceback

from client import Client
from tasctl import ROOT
from build_profile import identify
from live_read import close, open_process, read
from timeline import EXAMPLE, Timeline, format_script, frames


BASE = 0x400000
USER = ct.WinDLL("user32", use_last_error=True)


def bind(name, result, *arguments):
    function = getattr(USER, name)
    function.argtypes, function.restype = arguments, result
    return function


foreground = bind("GetForegroundWindow", wt.HWND)
is_iconic = bind("IsIconic", wt.BOOL, wt.HWND)
show = bind("ShowWindow", wt.BOOL, wt.HWND, ct.c_int)
activate = bind("SetForegroundWindow", wt.BOOL, wt.HWND)
switch = bind("SwitchToThisWindow", None, wt.HWND, wt.BOOL)
ancestor = bind("GetAncestor", wt.HWND, wt.HWND, wt.UINT)
rect = bind("GetWindowRect", wt.BOOL, wt.HWND, ct.POINTER(wt.RECT))
from_point = bind("WindowFromPoint", wt.HWND, wt.POINT)
cursor = bind("GetCursorPos", wt.BOOL, ct.POINTER(wt.POINT))
move = bind("SetCursorPos", wt.BOOL, ct.c_int, ct.c_int)
mouse_event = bind("mouse_event", None, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ct.c_size_t)
keyboard_event = bind("keybd_event", None, wt.BYTE, wt.BYTE, wt.DWORD, ct.c_size_t)
async_key = bind("GetAsyncKeyState", ct.c_short, ct.c_int)
thread_msg = bind("PostThreadMessageW", wt.BOOL, wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM)
reply = bind("SendMessageTimeoutW", ct.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM,
             wt.LPARAM, wt.UINT, wt.UINT, ct.POINTER(ct.c_size_t))


def title(hwnd):
    text = ct.create_unicode_buffer(256)
    result = ct.c_size_t()
    if not reply(hwnd, 0x0D, len(text), ct.addressof(text), 0x02, 500, ct.byref(result)):
        raise RuntimeError("Game window failed to reply to WM_GETTEXT within 500 ms")
    if not result.value:
        raise RuntimeError("Game returned an empty window title")
    return text.value


def focus(hwnd):
    if is_iconic(hwnd):
        show(hwnd, 9)
    if foreground() != hwnd and not activate(hwnd):
        switch(hwnd, True)
    if foreground() != hwnd and not (async_key(0x12) & 0x8000):
        # Windows can deny a background console foreground activation. As with Alt+Tab,
        # holding Alt for this single activation releases the foreground lock.
        keyboard_event(0x12, 0, 0, 0)
        try:
            activate(hwnd)
        finally:
            keyboard_event(0x12, 0, 2, 0)
    deadline = time.monotonic()+2
    while foreground() != hwnd and time.monotonic() < deadline:
        time.sleep(.02)
    if foreground() != hwnd:
        raise RuntimeError(f"Windows did not foreground HWND {hwnd:#x}; foreground={foreground():#x}")


def require(value, description):
    if not value:
        raise RuntimeError(description)


def focus_test_window(hwnd):
    if foreground() != hwnd and not activate(hwnd):
        bounds = wt.RECT()
        saved = wt.POINT()
        require(rect(hwnd, ct.byref(bounds)) and cursor(ct.byref(saved)), "Could not locate test window and cursor")
        point = wt.POINT((bounds.left+bounds.right)//2, (bounds.top+bounds.bottom)//2)
        require(ancestor(from_point(point), 2) == hwnd, "Test window is obscured")
        try:
            require(move(point.x, point.y), "Could not move cursor inside test window")
            actual = wt.POINT()
            require(cursor(ct.byref(actual)) and ancestor(from_point(actual), 2) == hwnd,
                    "Cursor is outside the test window")
            mouse_event(0x02, 0, 0, 0, 0)
            mouse_event(0x04, 0, 0, 0, 0)
            time.sleep(.05)
        finally:
            move(saved.x, saved.y)
    focus(hwnd)


def verify_clock(row, prior, frequency, units, rate=60):
    ticks = row["qpcTicks"]-prior
    require(ticks in {frequency//rate, (frequency+rate-1)//rate}, f"QPC increment {ticks} at frame {row['number']}")
    require(abs(row["deltaSeconds"]-1/rate) <= 1/frequency+1e-8, f"Game delta mismatch: {row}")
    require(abs(row["deltaUnits"]-units) < 0.001, f"Game delta in frame units mismatch: {row}")
    return row["qpcTicks"]


def verify_iteration(row, hwnd, expected_foreground):
    require(int(row["gameWindow"], 16) == hwnd and int(row["foreground"], 16) == expected_foreground,
            f"Foreground changed inside a tested frame: {row}")
    require(not row["iconic"] and row["gameActive"] == 1, f"Game inactive or iconic: {row}")
    require(row["reads"] == 1 and row["presents"] == 1 and row["presentResult"] == 0,
            f"Expected one DirectInput call and one successful Present: {row}")


def labels(keys):
    return "{" + ",".join(sorted(keys, key=lambda key: (key != "Space", key))) + "}"


def dx9_menu(client, handle, build, result, advance):
    def word(address):
        return int.from_bytes(read(handle, address, 4), "little")

    def title_menu():
        scene = client.call("record-scene")["scene"]
        require(scene["gameClass"] == "aTitle" and scene["menus"], f"Expected DX9 title: {scene}")
        return scene["menus"][0]

    layout = build["scene"]
    menu = title_menu()
    if menu["state"] == 3:  # Leave the press-any-key prompt without choosing a menu command.
        client.call("m1-load", text="1: Left\n2: -")
        advance(2)
    for _ in range(20):
        menu = title_menu()
        if (menu["state"], menu["phase"]) == (13, 2):
            break
        require(menu["state"] in (0, 1, 2, 3, 4, 5, 6, 13), "Unexpected title submenu")
        advance(10)
        if title_menu()["state"] == 3:
            client.call("m1-load", text="1: Left\n2: -")
            advance(2)
    require((menu["state"], menu["phase"]) == (13, 2), "Title menu did not become interactive")
    cursor = int(menu["address"], 16) + layout["cursor"]
    keyboard = word(BASE + build["roots"]["keyboard"]) + build["fields"]["keyBits"]
    result["menu"] = {}
    for fallback in (False, True):
        client.call("m1-fallback-test", on=fallback)
        try:
            start = word(cursor)
            key, undo, change = ("Down", "Up", 1) if start == 0 else ("Up", "Down", -1)
            client.call("m1-load", text=f"1: -\n2: {key}\n3: -\n4: {undo}\n5: -")
            rows = result["menu"]["GetKeyboardState" if fallback else "DirectInput"] = []
            for pressed, released, expected in [(None,None,start),(key,None,start+change),
                                                (None,key,start+change),(undo,None,start),(None,undo,start)]:
                row, = advance()
                bits = read(handle, keyboard, 128)
                actual_cursor = word(cursor)
                rows.append(dict(frame=row["number"], held=pressed, released=released, cursor=actual_cursor,
                                 expectedCursor=expected, rawBits=bits.hex(), reads=row["reads"],
                                 fallbackReads=row.get("fallbackReads",0), presents=row["presents"]))
                fields = ("held", "previous", "pressed", "released")
                require(bits.hex() == "".join(row["keys"][k+"Bits"] for k in fields), "Independent bitmap mismatch")
                require(row["keys"]["pressed"] == ([pressed] if pressed else []) and
                        row["keys"]["released"] == ([released] if released else []), "Wrong menu key edge")
                require(actual_cursor == expected and row["reads"] == row["presents"] == 1 and
                        row.get("fallbackReads",0) == int(fallback), f"Menu step failed: {rows[-1]}")
        finally:
            client.call("m1-fallback-test", on=False)


def verify(client, handle, hwnd, expected_foreground, result, build, update=lambda: None):
    def fetch(address, length):
        return read(handle, address, length)

    def word(address, length=4):
        return int.from_bytes(fetch(address, length), "little")

    initial = title(hwnd)
    result.update(window=hex(hwnd), foreground=hex(expected_foreground), title=initial)
    # The resident service retains its hooks after both runs.
    sites = {build["sites"][name]:12 for name in ("frame", "focus", "sample")}
    sites.update({rva:4 for rva in build["imports"].values()})
    before_hooks = {hex(rva): fetch(BASE+rva, size).hex() for rva, size in sites.items()}
    result["sitesBefore"] = before_hooks
    def advance(count=1):
        return client.call("m1-step", count=count)

    def state():
        return client.call("status")

    def load(plan):
        return client.call("m1-load", text=format_script(plan))

    def accept_attached():
        client.call("m1-pause")
        load(Timeline())
        a = state()
        observed_at = time.monotonic()
        b = client.call("m1-resume", until=a["frame"] + 12)
        baseline = {key:b[key]-a[key] for key in ("entryCount", "readCount", "presentCount")}
        baseline["seconds"] = time.monotonic()-observed_at
        baseline["gameActive"] = b["gameActive"]
        baseline["foreground"] = b["foreground"]
        result["baseline"] = baseline
        require(int(b["foreground"], 16) == expected_foreground and not is_iconic(hwnd), "Focus baseline failed")
        combat = (b["last"].get("combat") or {}).get("player") is not None
        rate = b.get("frameRate", 60)      # follows the game's Frame Rate setting (agent gameFrameRate)
        units = 60/rate if combat or build["game"] == "dx9" else b["last"]["deltaUnits"]
        baseline.update(combat=combat, engineUnits=units)
        require(0 < units <= 4, "Invalid engine frame scale")
        require(baseline["entryCount"] == 12 and baseline["presentCount"] >= 12
                and (not combat or abs(baseline["entryCount"]-baseline["presentCount"]) <= 1),
                "Frame boundary and Present counts did not match")
        print(f"实机基线 {baseline['seconds']:.3f}s：更新={baseline['entryCount']}，"
              f"读键={baseline['readCount']}，Present={baseline['presentCount']}，激活={b['gameActive']}")

        warm = advance(2)
        require(not any(warm[-1]["keys"][name] for name in ("held", "previous", "pressed", "released")),
                "Keyboard was not empty before acceptance")
        pause = state()
        clock = int(pause["clockObject"], 16)
        if build["game"] == "dx9":
            require(clock == word(BASE+build["roots"]["clock"]), "Frame receiver differs from game clock singleton")
        raw_at_pause = word(clock+build["clock"]["previous"], 8)
        started = time.monotonic()
        require(thread_msg(pause["tid"], 0, 0, 0), "Could not queue WM_NULL to the gameplay thread")
        replies = []
        for deadline in (0, 1.5, 3):
            while time.monotonic() < started+deadline:
                update()
                time.sleep(.025)
            replies.append(title(hwnd))
        elapsed = time.monotonic()-started
        still = state()
        raw_still = word(clock+build["clock"]["previous"], 8)
        require(still["parked"] and all(pause[key] == still[key] for key in
                ("frame", "entered", "entryCount", "readCount", "presentCount")), "Game updated during pause")
        require(raw_at_pause == raw_still and replies == [initial]*3
                and still["pumpMessages"] > pause["pumpMessages"], "Game clock or message response failed while paused")
        result["pause"] = {"seconds":elapsed, "before":pause, "after":still,
                           "qpc":[raw_at_pause,raw_still], "titles":replies}
        print(f"暂停 {elapsed:.3f}s：帧 {pause['frame']}→{still['frame']}，窗口回复 3/3，"
              f"游戏线程处理消息 {still['pumpMessages']-pause['pumpMessages']}，PASS")

        prior = raw_still
        result["singleSteps"] = []
        for _ in range(10):
            row, = advance()
            result["singleSteps"].append(row)
            prior = verify_clock(row, prior, still["frequency"], units, rate)
            verify_iteration(row, hwnd, expected_foreground)
            if build["game"] == "dx9":
                raw_seconds = fetch(clock+build["clock"]["seconds"],4)
                raw_units = fetch(clock+build["clock"]["units"],4)
                row["clockReadback"] = [raw_seconds.hex(),raw_units.hex()]
                require(raw_seconds == struct.pack("<f",1/rate) and raw_units == struct.pack("<f",60/rate),
                        "Independent engine clock readback failed")
        print(f"单步 10 次：每次 +1，读键 1 次，Present 1 次，游戏步长 1/{rate} 秒，PASS")

        if build["game"] == "dx9":
            dx9_menu(client, handle, build, result, advance)
            prior = word(clock+build["clock"]["previous"],8)
        load(Timeline(EXAMPLE))
        result["timeline"] = []
        expected_previous = set()
        print("帧 | 期望按住/按下/松开 | 实读按住/按下/松开 | 结果")
        for i, held, pressed, released in frames(EXAMPLE+[(9, 10, [])]):
            row, = advance()
            keys = row["keys"]
            prior = verify_clock(row, prior, still["frequency"], units, rate)
            verify_iteration(row, hwnd, expected_foreground)
            names = ("held", "pressed", "released")
            expected = (held, pressed, released)
            actual = tuple(set(keys[name]) for name in names)
            correct = actual == expected and set(keys["previous"]) == expected_previous
            correct = correct and row["index"] == (i if i <= 8 else None)
            if i > 8:
                correct = correct and all(keys[name+"Bits"] == "0"*64 for name in (*names, "previous"))
            expected_previous = held
            result["timeline"].append({"testFrame":i, "actual":row, "expected":[sorted(s) for s in expected], "pass":correct})
            a, b = ("/".join(map(labels, group)) for group in (expected, actual))
            print(f"{i:2} | {a} | {b} | {'PASS' if correct else 'FAIL'}")
            require(correct, f"Game keyboard object mismatch at timeline frame {i}")

        parked = state()
        resumed = client.call("m1-resume", until=parked["frame"] + 12)
        verify_iteration(resumed["last"], hwnd, expected_foreground)
        advanced = resumed["frame"]-parked["frame"]
        submitted = resumed["presentCount"]-parked["presentCount"]
        require(submitted >= advanced and (not combat or submitted <= advanced+1), "Present did not recover")
        elapsed_ticks = resumed["last"]["qpcTicks"]-prior
        require(abs(elapsed_ticks-advanced*still["frequency"]/rate) <= 1, "Resume charged paused wall time")
        require(title(hwnd) == initial, "Window did not respond after resume")
        result["resume"] = {"frames":advanced, "qpcTicks":elapsed_ticks, "state":resumed}
        print(f"恢复：推进 {advanced} 帧，虚拟时钟推进 {elapsed_ticks} ticks，窗口有响应，PASS")
    try:
        accept_attached()
    finally:
        client.call("m1-pause")
    after_hooks = {hex(rva):fetch(BASE+rva, size).hex() for rva, size in sites.items()}
    require(before_hooks == after_hooks and state()["parked"], "Service lost its hooks or frame gate")
    result["sitesAfter"] = after_hooks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int)
    args = parser.parse_args()
    client = Client(args.pid)
    pid = client.pid
    handle = open_process(0x410, False, pid)
    if not handle:
        raise ct.WinError(ct.get_last_error())
    original = foreground()
    document = {"pid":pid, "runs":{}}
    test_window = None
    try:
        build = identify(pid)
        document["build"] = build["id"]
        if "hwnd" in build["roots"]:
            window_slot = BASE + build["roots"]["hwnd"]
        else:
            main_object = int.from_bytes(read(handle, BASE+build["roots"]["main"], 4), "little")
            window_slot = main_object+build["fields"]["hwnd"]
        hwnd = int.from_bytes(read(handle, window_slot, 4), "little")
        focus(hwnd)
        print("[前台]", flush=True)
        document["runs"]["foreground"] = {}
        verify(client, handle, hwnd, hwnd, document["runs"]["foreground"], build)

        import tkinter as tk
        test_window = tk.Tk()
        test_window.title("DMC4SE M1 foreground test window")
        test_window.geometry("400x130+50+50")
        test_window.attributes("-topmost", True)
        test_window.update()
        test_window.focus_force()
        test_window.update()
        test_hwnd = ancestor(test_window.winfo_id(), 2)
        focus_test_window(test_hwnd)
        time.sleep(.2)
        print("[后台]")
        document["runs"]["background"] = {}
        verify(client, handle, hwnd, test_hwnd, document["runs"]["background"], build, test_window.update)
        document["result"] = "PASS"
    except Exception:
        document["result"] = "FAIL"
        document["error"] = traceback.format_exc()
        print(document["error"])
    finally:
        if document.get("build") == "dx9_21360":
            try:
                client.call("live")
            except (OSError, RuntimeError) as error:
                document.update(result="FAIL", cleanupError=str(error))
        if test_window is not None:
            test_window.destroy()
        if original and foreground() != original:
            activate(original)
        close(handle)
        filename = "m1-dx9-accept.json" if document.get("build") == "dx9_21360" else "m1-service-accept.json"
        (ROOT/"logs"/filename).write_text(json.dumps(document, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(document["result"], flush=True)
    return int(document["result"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
