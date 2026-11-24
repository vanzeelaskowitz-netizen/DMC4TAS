# DMC4TAS

Frame-by-frame TAS (tool-assisted speedrun / showcase) tool for **Devil May Cry 4** on PC (Steam, 32-bit).
Single-player only. Two builds are supported, picked automatically from the game's executable:

| Build | Steam app | Executable | Build ID |
|---|---|---|---|
| Special Edition | 329050 | `DevilMayCry4SpecialEdition.exe` | 24329835 |
| Original (DX9) | 45700 | `DevilMayCry4_DX9.exe` | 21360 |

鬼泣4（PC Steam 版）的逐帧 TAS 工具，支持特别版和原版（DX9）。

Developed by vanzeelaskowitz-netizen together with Claude (Anthropic's AI assistant).
本工具由 vanzeelaskowitz-netizen 与 Anthropic 的 AI 助手 Claude 一起开发。

## Features

- Per-frame input scripts: one row per frame, edited in a script window while the game follows.
- Single step forward, step back and rewrite: memory snapshots of the game's heaps with deterministic replay.
- Recording takes from the real keyboard, then editing and replaying them.
- State display: animation and frame within it, Enemy Step (jump cancel) points drawn over the game,
  Nero's Exceed (red blade) MAX-Act and EX-Act frames.
- Practice aids: lock HP (player and enemies), infinite Devil Trigger, timer freeze, one-hit kill.
- Playback with the game's sound effects. On the original build the game window must be in front
  (DirectSound mutes background programs); the player brings it forward.
- Named save states, and a batch `probe` command for tools that search inputs. The MCP server in
  [DMC4TAS-MCP](https://github.com/vanzeelaskowitz-netizen/DMC4TAS-MCP) lets an AI use them.

On the original build, do not minimise the game window while a timeline is open: the D3D9 device is lost
and the tool drops its snapshots to stay safe. 原版使用时不要最小化游戏窗口，否则快照会被清掉。

## Requirements

- Windows 10/11, the game in windowed mode.
- One of the two Steam builds above, with Steam running.
- Python 3.13 (64-bit). Run `setup.bat` once: it creates `.venv` and installs `requirements.txt`.
- The game is found through Steam. If it is not (for example it was copied to another folder),
  `start_game.bat` asks for the executable and remembers it (`game_path.txt` / `game_path_dx9.txt`).
  Only the two Steam builds are supported, checked by SHA-256: every feature depends on that build's
  memory addresses. Another build is refused with its SHA-256 shown; send it to the author if you want
  that build supported.
  找不到游戏时（例如游戏放在别的文件夹），`start_game.bat` 会弹窗让你选择游戏程序并记住。只支持上面两个
  Steam 版本，靠 SHA-256 识别；其他版本会被拒绝并显示它的 SHA-256，想支持的话请把这串值发给作者。

## Use

1. `setup.bat` once, after downloading.
1. `start_game.bat` first asks for the language (1 English, 2 中文; after 5 s the last choice is
   kept, stored in `language.txt`), then starts the game with the tool attached (the player keeps
   control). The player and the script window use that language.
   启动时先选语言（1 英文，2 中文；5 秒不按就沿用上次的选择），播放器和脚本窗口跟着用这个语言。
1. In a room with a character, `tas_player.bat` opens the player and the script window.
1. Take over, record or write a script, step, step back, play; hand control back when done.

Full guide (Chinese): [docs/USAGE.md](docs/USAGE.md) · 使用说明见 [docs/USAGE.md](docs/USAGE.md)

Script format: see `docs/SCRIPT.md`. Memory maps: `docs/MAP.md` (Special Edition), `docs/MAP_DX9.md` (original).

### Decompiled functions / 反编译函数

`docs/MAP.md` cites decompiled game functions as evidence. They are Capcom's code and are not in this
repository; `tools/decompile.ps1` rebuilds them in `dump/decomp/` from your own copy of the game, with
Ghidra 12.2+ and a JDK it accepts (JDK 25). The function list is `tools/ghidra/functions.txt`.

`docs/MAP.md` 引用的反编译函数属于 Capcom 的代码，不放在仓库里。用 `tools/decompile.ps1` 从你自己的游戏生成到
`dump/decomp/`：先正常启动游戏（不要用 `start_game.bat`），然后运行

```
powershell -NoProfile -ExecutionPolicy Bypass -File tools\decompile.ps1 -GhidraDir <Ghidra 目录> -JavaHome <JDK 25 目录>
```

第一次会导入并分析整个程序，要十几到几十分钟；之后几秒钟就能重新导出。

`runs/` holds a few example scripts (`runs/dx9-exceed-demo.tas`: 11 Exceed-keeping jump cancels on the original build). `tools/` holds the probes and tests used while developing; they are
not needed to use the tool.

## Notes

- The tool attaches to the running game with Frida and changes its memory and code while it runs.
  It does not modify any game or save files.
- Not affiliated with Capcom. No game code or assets are included in this repository.
- Parts of the Enemy Step and hitbox approach follow dmc4_hook (MIT, https://github.com/muhopensores/dmc4_hook).

## License

MIT, see `LICENSE`.
