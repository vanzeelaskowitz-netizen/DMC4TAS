"""Drive the player's script editor through the real service and game, and check what the game did.

Needs the game parked in a room under the tool's control. Usage:
    editor_selftest.py [script to put back afterwards] [frame to return to]
Writes logs\\editor-selftest.txt; the last line says PASS or FAIL.
"""
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tas"))

from player import Player  # noqa: E402

LOG = ROOT / "logs" / "editor-selftest.txt"
RESTORE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
RETURN_TO = int(sys.argv[2]) if len(sys.argv) > 2 else 0


def main():
    root = tk.Tk()
    player = Player(root)
    lines, failures, memory = [], [], {}

    def held():
        last = player.client.call("status").get("last") or {}
        return sorted((last.get("keys") or {}).get("held") or [])

    def position():
        combat = (player.client.call("status").get("last") or {}).get("combat") or {}
        return (combat.get("player") or {}).get("position")

    def check(label, **expected):
        actual = {"frame": player.frame, "held": held(), "line": player.now_line, "error": player.script_error,
                  "length": player.length, "playing": player.playing, "rows": len(player.line_map),
                  "text": player.editor.get("1.0", "end-1c")}
        wrong = {key: (actual[key], value) for key, value in expected.items() if actual[key] != value}
        if wrong:
            failures.append(label)
        lines.append(f"{'FAIL' if wrong else 'ok  '} {label}: "
                     + " ".join(f"{k}={actual[k]!r}" for k in actual if k != "text")
                     + (f"  expected {wrong}" if wrong else "") + f"  | {player.status.cget('text')}")
        LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def type_text(text, where="end"):
        player.editor.insert(where, text)

    def replace_line(number, text):
        player.editor.delete(f"{number}.0", f"{number}.end")
        player.editor.insert(f"{number}.0", text)

    def remember():
        memory["position"] = position()
        memory["held"] = held()
        lines.append(f"     before: frame={player.frame} held={memory['held']} position={memory['position']}")

    def put_back():
        if RESTORE:
            player.load(RESTORE)
            player.goto(RETURN_TO)
            same = position() == memory["position"] and held() == memory["held"]
            if not same:
                failures.append("put back")
            lines.append(f"{'ok  ' if same else 'FAIL'} put back: frame={player.frame} held={held()} "
                         f"position={position()}")
        else:
            # The test took the game over itself: return to that moment and hand the game back.
            frame, before = player.frame, position()
            player.goto(0)
            player.goto(frame)
            again = position()
            player.goto(0)
            player.release()
            mode = player.client.call("status").get("mode")
            same = again == before and before is not None and mode == "observe"
            if not same:
                failures.append("hand back")
            lines.append(f"{'ok  ' if same else 'FAIL'} replay from 0 to {frame} gives {again} (was {before}); "
                         f"handed back, mode={mode}")

    first = 1        # a new script starts empty
    steps = [
        (2500, remember, None, {}),
        (300, player.new_script, "new script", {"error": None, "length": 0}),
        (300, lambda: player.goto(0), "goto 0", {"frame": 0, "line": None}),
        (300, lambda: type_text("S W\nj\n3 Space K\n2\nw,j\n"), "typed", {}),
        (900, lambda: None, "applied, every frame on its own row",
         {"error": None, "length": 8, "frame": 0, "text": "S W\nj\nSpace K\nSpace K\nSpace K\n\n\nw,j\n"}),
        (300, lambda: player.step(1), "frame 1", {"frame": 1, "held": ["S", "W"], "line": 1}),
        (300, lambda: player.step(1), "frame 2", {"frame": 2, "held": ["J"], "line": 2}),
        (300, lambda: player.step(3), "frame 5", {"frame": 5, "held": ["K", "Space"], "line": 5}),
        (300, lambda: player.step(2), "frame 7 blank", {"frame": 7, "held": [], "line": 7}),
        (300, lambda: player.step(1), "frame 8", {"frame": 8, "held": ["J", "W"], "line": 8}),
        (300, lambda: player.step(1), "frame 9 past the end", {"frame": 9, "held": [], "line": None}),
        (300, lambda: player.back(4), "back to 5", {"frame": 5, "held": ["K", "Space"], "line": 5}),
        (300, lambda: replace_line(first + 1, "L"), "edit an earlier line", {}),
        (900, lambda: None, "game follows the edit", {"frame": 5, "held": ["K", "Space"], "error": None}),
        (300, lambda: player.back(3), "frame 2 now L", {"frame": 2, "held": ["L"], "line": first + 1}),
        (300, lambda: type_text("foo\n", f"{first}.0"), "bad line typed", {}),
        (900, lambda: None, "bad line refused", {"frame": 2, "held": ["L"], "error": f"第 {first} 行：不认识的键名 foo"}),
        (300, lambda: player.editor.delete(f"{first}.0", f"{first + 1}.0"), "bad line removed", {}),
        (900, lambda: None, "script good again", {"frame": 2, "error": None, "length": 8}),
        (300, lambda: type_text("\n\n\n\n", f"{first}.0"), "insert 4 empty lines at the top", {}),
        (900, lambda: None, "empty lines are blank frames", {"frame": 2, "held": [], "length": 12, "line": first + 1}),
        (300, lambda: player.step(3), "frame 5 is the old frame 1", {"frame": 5, "held": ["S", "W"], "line": first + 4}),
        (300, player.toggle_play, "play", {"playing": True}),
        (1500, lambda: None, "play stops on the last frame of the script",
         {"frame": 12, "held": ["J", "W"], "playing": False}),
        (300, player.toggle_play, "play again", {"playing": True}),
        (1500, lambda: None, "played again from the start", {"frame": 12, "held": ["J", "W"], "playing": False}),
        (300, lambda: type_text("20 s\n", "end"), "20 more frames written", {}),
        (900, lambda: None, "script is longer, 20 rows of S", {"frame": 12, "length": 32, "rows": 32}),
        (300, player.toggle_play, "play continues from here", {"playing": True}),
        (1500, lambda: None, "stops on the new last frame", {"frame": 32, "held": ["S"], "playing": False}),
        (300, put_back, None, {}),
    ]
    def finish():
        import ctypes
        parent = ctypes.windll.user32.GetParent
        lines.append(f"WINDOWS main={parent(root.winfo_id())} script={parent(player.script_window.winfo_id())}")
        lines.append("HOLD")
        lines.append("FAIL: " + ", ".join(failures) if failures else "PASS")
        LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
        root.after(12000, root.destroy)

    def run(index=0):
        """One step after the other: each waits its time after the previous one has finished, so a
        slow game command cannot eat the pause an edit needs before it is applied."""
        if index == len(steps):
            root.after(600, finish)
            return
        wait, action, label, expected = steps[index]

        def go():
            try:
                action()
            except Exception as error:   # keep going so the game is put back
                failures.append(f"{label}: {error}")
                lines.append(f"FAIL {label}: {error!r}")
            if label:
                check(label, **expected)
            run(index + 1)
        root.after(wait, go)

    run()
    root.mainloop()


if __name__ == "__main__":
    main()
