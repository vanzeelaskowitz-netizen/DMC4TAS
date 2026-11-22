# DMC4TAS

Frame-by-frame TAS (tool-assisted speedrun / showcase) tool for **Devil May Cry 4 Special Edition** on PC
(Steam, 32-bit, build 24329835). Single-player only.

鬼泣4特别版（PC Steam 版）的逐帧 TAS 工具。

Developed by vanzeelaskowitz-netizen together with Claude (Anthropic's AI assistant).
本工具由 vanzeelaskowitz-netizen 与 Anthropic 的 AI 助手 Claude 一起开发。

## Features

- Per-frame input scripts: one row per frame, edited in a script window while the game follows.
- Single step forward, step back and rewrite: memory snapshots of the game's heaps with deterministic replay.
- Recording takes from the real keyboard, then editing and replaying them.
- State display: animation and frame within it, Enemy Step (jump cancel) points drawn over the game,
  Nero's Exceed (red blade) MAX-Act and EX-Act frames.
- Practice aids: lock HP (player and enemies), infinite Devil Trigger, timer freeze, one-hit kill.
- Playback with the game's sound effects (background music stays silent during playback).

## Requirements

- Windows 10/11, the game in windowed mode.
- Python 3.13 with a project virtual environment: `py -m venv .venv`, then
  `.venv\Scripts\python.exe -m pip install frida frida-tools pefile keystone-engine capstone pycaw psutil`.
- The game is found through Steam; set the `DMC4SE_EXE` environment variable to the executable if it is not.

## Use

1. `start_game.bat` starts the game with the tool attached (the player keeps control).
2. In a room with a character, `tas_player.bat` opens the player and the script window.
3. Take over, record or write a script, step, step back, play; hand control back when done.

Full guide (Chinese): [docs/USAGE.md](docs/USAGE.md) · 使用说明见 [docs/USAGE.md](docs/USAGE.md)

Script format: see `docs/SCRIPT.md`. Memory map of the build: `docs/MAP.md`.

`runs/` holds a few example scripts. `tools/` holds the probes and tests used while developing; they are
not needed to use the tool.

## Notes

- The tool attaches to the running game with Frida and changes its memory and code while it runs.
  It does not modify any game or save files.
- Not affiliated with Capcom. No game code or assets are included in this repository.
- Parts of the Enemy Step and hitbox approach follow dmc4_hook (MIT, https://github.com/muhopensores/dmc4_hook).

## License

MIT, see `LICENSE`.
