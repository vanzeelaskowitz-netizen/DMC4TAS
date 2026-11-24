"""Interface language: start_game.bat writes "en" or "zh" to language.txt next to the launchers.

Texts are written as pairs, L("中文", "English"), so both versions sit side by side in the code.
The file is re-read when it changes, so a service that outlives one start_game run follows the
next choice too.
"""
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "language.txt"
_seen = (None, "zh")


def current():
    global _seen
    try:
        stamp = FILE.stat().st_mtime_ns
    except OSError:
        return "zh"
    if stamp != _seen[0]:
        _seen = (stamp, "en" if FILE.read_text(encoding="utf-8", errors="ignore").strip() == "en" else "zh")
    return _seen[1]


def L(zh, en):
    return en if current() == "en" else zh
