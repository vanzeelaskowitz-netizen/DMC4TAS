"""Start the game with write watch and attach the TAS service in live mode (the player keeps control).

If the game is already running it is left alone: the service just attaches to it. Fast step-back
then is not available (write watch can only be switched on at start-up) and the slower full-memory
path is used. Pass --restart to close the running game and start it again with write watch.
"""
import sys

import watch_launch
from client import Client, STATE, remove_state
from record_boot import ready_process
from tasctl import start_service


def say(text):
    print(text, flush=True)


def main():
    restart = "--restart" in sys.argv[1:]
    running = watch_launch.find_pid()
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
        Client().call("shutdown", force=True)
    except (OSError, RuntimeError):
        pass
    remove_state()
    if running is not None and not restart:
        say(f"游戏已经在运行（PID {running}），不重启它，直接挂上工具。")
        say("注意：这样启动的游戏没有写入监视，后退每次约 0.2 秒。想要 0.02 秒的快速后退，")
        say("先关掉游戏，再双击 start_game.bat。")
        pid = running
    else:
        if running is not None:
            say("正在关闭正在运行的游戏…")
        result = watch_launch.launch(log=say)
        if result["watched"] != 8:
            say("注意：写入监视没有全部打开，后退会用较慢的方式（每次约 0.2 秒）。")
        say("等待游戏初始化…")
        pid = ready_process()
    start_service(pid, live=True)
    say("")
    say("工具已挂上，游戏由你操作。")
    say("照常玩，走到想练的地方。切到别的窗口时游戏会原地冻结（不会弹暂停菜单），切回去就继续。")
    say("要逐帧操作时，双击 tas_player.bat 打开播放器，或直接按小键盘 +。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError) as error:
        print(f"\n启动失败：{error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
