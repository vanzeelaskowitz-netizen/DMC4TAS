"""Start the game with write watch and attach the TAS service in live mode (the player keeps control).

If the game is already running it is left alone: the service just attaches to it. Fast step-back
then is not available (write watch can only be switched on at start-up) and the slower full-memory
path is used. Pass --restart to close the running game and start it again with write watch.
"""
import argparse
import ctypes
from ctypes import wintypes
import sys
import time

import watch_launch
from build_profile import identify
from client import Client, ROOT, STATE
from record_boot import ready_process
from tasctl import start_service


def say(text):
    print(text, flush=True)


LABELS = {"dx9": "原版 DX9", "se": "特别版"}
LAST_GAME = ROOT / "last_game.txt"


def choose(games, parent=None):
    import tkinter as tk
    try:
        default = LAST_GAME.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        default = None
    window = tk.Toplevel(parent) if parent else tk.Tk()
    window.title("选择 DMC4 版本")
    window.resizable(False, False)
    selected = None

    def pick(game):
        nonlocal selected
        selected = game
        window.destroy()

    for game in games:
        button = tk.Button(window, text=LABELS[game], width=14, command=lambda g=game: pick(g))
        button.pack(side="left", padx=12, pady=16)
        if game == (default if default in games else games[0]):
            button.configure(default="active")
            button.focus_set()
            window.bind("<Return>", lambda _, g=game: pick(g))
    if parent:
        window.transient(parent)
    window.grab_set()
    window.wait_window()
    if selected:
        LAST_GAME.write_text(selected + "\n", encoding="utf-8")
    return selected


def fingerprint(path):
    from hashlib import sha256
    return sha256(path.read_bytes()).hexdigest()


def refuse(path, digest, game):
    """Explain an executable this tool cannot drive: every feature relies on one build's addresses."""
    import tkinter
    from tkinter import messagebox
    supported = "\n".join(f"{LABELS[p['game']]}：{p['sha256']}" for p in watch_launch.profiles())
    text = (f"这个程序不是本工具支持的版本，不能使用：\n{path}\n\n它的 SHA-256：\n{digest}\n\n"
            f"支持的版本（Steam 版）：\n{supported}\n\n"
            "工具的所有功能都依赖某一个版本的内存地址，换了版本地址就对不上。"
            "想支持这个版本，请把上面它的 SHA-256 发给作者。")
    say(text)
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        messagebox.showwarning("不支持的游戏版本", text + "\n\n点“确定”后可以重新选择。", parent=root)
    finally:
        root.destroy()


def locate(game):
    """The executable of `game`: found through Steam or a remembered choice, else asked for.

    Only an executable with the build's exact SHA-256 is accepted (and remembered).
    """
    from pe_image import GAME_FILES, choose_game, forget, remember
    filename = GAME_FILES[game][0]
    expected = next(p for p in watch_launch.profiles() if p["game"] == game)["sha256"]
    path = watch_launch.find_game(game)
    if path.is_file():
        digest = fingerprint(path)
        if digest == expected:
            return path
        forget(game)
        refuse(path, digest, game)
        title = f"请重新选择 {filename}（{LABELS[game]}）"
    else:
        say(f"没有找到{LABELS[game]}，请在弹出的窗口里选择 {filename}…")
        title = f"找不到游戏，请选择 {filename}（{LABELS[game]}）"
    while True:
        picked = choose_game(game, title)
        if picked is None:
            raise RuntimeError("没有选择游戏程序")
        digest = fingerprint(picked)
        if digest == expected:
            remember(game, picked)
            say(f"已记住游戏位置：{picked}")
            return picked
        refuse(picked, digest, game)
        title = f"请重新选择 {filename}（{LABELS[game]}）"


def running_games():
    return {p["game"]: pid for p in watch_launch.profiles()
            if (pid := watch_launch.find_pid(p["process"])) is not None}


def select_game(running, parent=None):
    if len(running) > 1:
        user = ctypes.windll.user32
        user.GetForegroundWindow.restype = wintypes.HWND
        user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        front = wintypes.DWORD()
        user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(front))
        for game, pid in running.items():
            if front.value == pid:
                return game
    games = list(running) or [g for g in LABELS if watch_launch.find_game(g).is_file()]
    if not games:
        # Neither build found (not installed through Steam, or moved): ask which one, then where.
        return choose(list(LABELS), parent)
    return games[0] if len(games) == 1 else choose(games, parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--game", choices=("se", "dx9"))
    args = parser.parse_args()
    restart = args.restart
    processes = running_games()
    args.game = args.game or select_game(processes)
    if not args.game:
        return 0
    profile = next(p for p in watch_launch.profiles() if p["game"] == args.game)
    running = watch_launch.find_pid(profile["process"])
    if running:
        profile = identify(running)
    if running is not None and not restart:
        # Never take a working service off a running game: once a step back has happened the game
        # does not survive the tool being removed.
        try:
            if Client(running).call("status").get("mode") != "detached":
                say(f"游戏已经在运行（PID {running}），工具也已经挂着，什么都不用做。")
                say("要逐帧操作时，双击 tas_player.bat 打开播放器。")
                return 0
        except (OSError, RuntimeError):
            pass
    try:
        previous = Client()
        previous.call("status")
    except (OSError, RuntimeError):
        pass
    else:
        previous.call("shutdown", force=restart and previous.pid == running)
        deadline = time.monotonic() + 5
        while STATE.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        if STATE.exists():
            raise RuntimeError("旧服务尚未退出")
    if running is not None and not restart:
        say(f"游戏已经在运行（PID {running}），不重启它，直接挂上工具。")
        say("注意：这样启动的游戏没有写入监视，后退每次约 0.2 秒。想要 0.02 秒的快速后退，")
        say("先关掉游戏，再双击 start_game.bat。")
        pid = running
    else:
        if running is not None:
            say("正在关闭正在运行的游戏…")
        exe = locate(args.game)
        if args.game == "se":
            watch_launch.EXE_PATH = exe
        result = watch_launch.launch(log=say, game=args.game, restart=restart, exe=exe)
        if result["watched"] != len(profile["snapshot"]["slots"]):
            say("注意：写入监视没有全部打开，后退会用较慢的方式（每次约 0.2 秒）。")
        say("等待游戏初始化…")
        pid = ready_process(args.game, result["pid"])
    start_service(pid, live=True)
    say("")
    say("工具已挂上，游戏由你操作。")
    if args.game == "se":
        say("照常玩，走到想练的地方。切到别的窗口时游戏会原地冻结（不会弹暂停菜单），切回去就继续。")
    else:
        say("原版已连接，窗口保持实时。用播放器接管后可以逐帧前进和后退。")
    say("要逐帧操作时，双击 tas_player.bat 打开播放器，或直接按小键盘 +。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError) as error:
        print(f"\n启动失败：{error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
