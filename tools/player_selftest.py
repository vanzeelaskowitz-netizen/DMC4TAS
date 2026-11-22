"""Drive the player window through its own methods and leave it open briefly for a screenshot."""
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tas"))

from player import Player  # noqa: E402

LOG = ROOT / "logs" / "player-selftest.txt"


def main():
    root = tk.Tk()
    player = Player(root)
    lines = []

    def record(label):
        lines.append(f"{label}: frame={player.frame} playing={player.playing} status={player.status.cget('text')!r} "
                     f"headline={player.headline.cget('text')!r}")
        LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")

    steps = [
        (900, lambda: player.load(ROOT / "runs" / "demo_hit.tas"), "load"),
        (300, lambda: player.goto(0), "goto 0"),
        (300, lambda: player.step(1), "step 1"),
        (300, lambda: player.step(10), "step 10"),
        (300, lambda: player.back(1), "back 1"),
        (300, lambda: player.goto(716), "goto 716"),
        (300, lambda: player.back(10), "back 10"),
        (300, player.toggle_play, "play"),
        (2000, player.toggle_play, "pause after 2 s"),
        (300, lambda: player.goto(716), "goto 716 again"),
    ]
    delay = 0
    for wait, action, label in steps:
        delay += wait
        root.after(delay, lambda action=action, label=label: (action(), record(label)))
    root.after(delay + 9000, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
