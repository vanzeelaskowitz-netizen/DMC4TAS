"""Tests for playing a script with the game's own sound (agent.js: drainSound, rpc audible).

Nobody listens: the output level of the game's audio session is read from Windows (pycaw), so
"there was sound" means the session's peak meter moved. Commands, run in the order given:

    restart            hand the game back, restart the service (loads the current agent.js)
    takeover           close the game's pause screen if it is open, freeze the game
    load PATH          load a script
    silent N           step N frames the usual, silent way and report the level meanwhile
    play [N]           play frames 1..N (default: all) with sound, wait for it to finish
    cut N SECONDS      start playing with sound and stop it after SECONDS
    goto N | back N    move in the timeline
    live [SECONDS]     hand the game back, optionally let it run in the foreground for SECONDS
    status             print the sound counters
"""
import ctypes
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tas"), str(ROOT / "work")]

from client import Client  # noqa: E402
from tasctl import CRASH_LOG as LOG, start_service, target_pid  # noqa: E402
user32 = ctypes.WinDLL("user32")


class Meter:
    """Peak level of the game's audio session, sampled in the background."""
    def __init__(self, pid):
        self.pid, self.samples, self.running = pid, [], False

    def _run(self):
        import comtypes
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        comtypes.CoInitialize()
        meters = [s._ctl.QueryInterface(IAudioMeterInformation) for s in AudioUtilities.GetAllSessions()
                  if s.ProcessId == self.pid]
        while self.running:
            self.samples.append(max((m.GetPeakValue() for m in meters), default=0.0))
            time.sleep(.05)

    def __enter__(self):
        self.samples, self.running = [], True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.running = False
        self.thread.join(2)

    def text(self):
        if not self.samples:
            return "no level samples"
        loud = sum(1 for value in self.samples if value > .02)
        return f"level max {max(self.samples):.3f}, above 0.02 in {loud} of {len(self.samples)} samples"


def focus(window):
    """Bring a window to the front; True when Windows really did it."""
    user32.keybd_event(0x12, 0, 0, 0)
    user32.SetForegroundWindow(window)
    user32.keybd_event(0x12, 0, 2, 0)
    if user32.GetForegroundWindow() != window:
        user32.SwitchToThisWindow(window, True)
    end = time.monotonic() + 1.5
    while user32.GetForegroundWindow() != window and time.monotonic() < end:
        time.sleep(.02)
    return user32.GetForegroundWindow() == window


def crashes():
    return len(LOG.read_text(encoding="utf-8", errors="replace").splitlines()) if LOG.exists() else 0


def report(client, label):
    state = client.call("status")
    if state.get("mode") == "detached":
        print(f"{label}: THE GAME IS GONE")
        raise SystemExit(1)
    sound = state.get("sound") or {}
    print(f"{label}: mode {state['mode']} parked {state['parked']} frame {state.get('timelineFrame')} "
          f"sticky {state.get('soundSticky')} audible {state.get('audible')} failure {state.get('failure')} | "
          + " ".join(f"{key}={sound.get(key)}" for key in ("ready", "error", "voices", "created", "destroyCalls", "kept",
                                                         "drains", "drainLeft", "releaseSkipped", "submits", "reads",
                                                         "files", "positionsRestored", "wakes"))
          + f" | crash log lines {crashes()}", flush=True)
    return state


def wait_idle(client, limit):
    """Until a stretch with sound is over and the service has put the silent state back."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        state = client.call("status")
        if state.get("mode") == "detached":
            return state
        if state["mode"] == "paused" and state["parked"] and not state.get("audible"):
            time.sleep(.7)                       # the service finishes in its heartbeat
            again = client.call("status")
            if again["mode"] == "paused" and again["parked"] and again.get("soundSticky"):
                return again
        time.sleep(.2)
    return client.call("status")


def main():
    words = sys.argv[1:]
    client = Client() if words[:1] != ["restart"] else None
    while words:
        word = words.pop(0)
        if word == "restart":
            try:
                print("shutdown:", Client().call("shutdown"))
            except Exception as error:
                print("shutdown:", error)
            time.sleep(2)
            state = start_service(target_pid(), live=True)
            client = Client()
            report(client, "restarted")
        elif word == "takeover":
            if client.call("record-scene")["scene"].get("paused"):
                print("unpause:", client.call("unpause"))
            client.call("pause")
            report(client, "taken over")
        elif word == "load":
            print("load:", client.call("load", path=str(Path(words.pop(0)).resolve())))
        elif word == "silent":
            count = int(words.pop(0))
            with Meter(client.call("status")["gamePid"]) as meter:
                client.call("step", count=count)
            report(client, f"silent {count} frames ({meter.text()})")
        elif word in ("goto", "back"):
            client.call(word, count=int(words.pop(0)))
            report(client, word)
        elif word in ("play", "cut"):
            args = {}
            if words and words[0].isdigit():
                args["count"] = int(words.pop(0))
            seconds = float(words.pop(0)) if word == "cut" else None
            with Meter(client.call("status")["gamePid"]) as meter:
                started = time.monotonic()
                print("play-sound:", client.call("play-sound", **args), flush=True)
                if seconds is not None:
                    time.sleep(seconds)
                    client.call("play-sound-stop")
                state = wait_idle(client, 120)
            report(client, f"{word} with sound, {time.monotonic() - started:.1f}s ({meter.text()})")
        elif word == "live":
            seconds = float(words.pop(0)) if words and words[0].replace(".", "").isdigit() else 0
            state = client.call("status")
            print("live:", {key: value for key, value in client.call("live").items() if key in ("mode", "returned")})
            if seconds:
                game, previous = int(state["gameWindow"], 16), user32.GetForegroundWindow()
                before = client.call("status")
                with Meter(state["gamePid"]) as meter:
                    front = focus(game)
                    time.sleep(seconds)
                    after = client.call("status")
                    focus(previous)
                grew = {key: after["sound"][key] - before["sound"][key] for key in ("created", "submits", "reads")}
                report(client, f"live {seconds}s, game in front: {front}, frames run {after['entryCount'] - before['entryCount']}, "
                               f"sound activity {grew} ({meter.text()})")
            else:
                report(client, "handed back")
        elif word == "status":
            report(client, "status")
        else:
            raise SystemExit(f"unknown command {word}")


if __name__ == "__main__":
    main()
