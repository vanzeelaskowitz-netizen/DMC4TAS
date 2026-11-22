# DMC4SE TAS 常驻服务

适用 Steam Build `24329835`。在项目根目录执行。服务独占 Frida 会话，默认停在帧闸上，退出控制台后继续维持窗口消息处理与心跳。

```powershell
& '.\.venv\Scripts\python.exe' -B 'tas\tasctl.py' start
& '.\.venv\Scripts\python.exe' -B 'tas\tasctl.py' status
& '.\.venv\Scripts\python.exe' -B 'tas\tasctl.py'
```

控制台命令也接受在 `tasctl.py` 后直接传参。

- `attach` 附加并停帧。 `pause` 停在下一帧入口。 `step N` 放行 N 帧后停住。 `resume [N]` 按 60 Hz 虚拟时钟放行 N 帧后停住，默认 60 帧。
- `load 路径` 读取 UTF-8 时间线。 `save` 在本房间创建快照。 `goto F` 停在时间线第 F 帧执行之后，`back N` 从当前进度退 N 帧。要停在第 106 帧执行前，输入 `goto 105`。两条命令都会重读时间线文件，后续帧以新内容重放。
- `status` 返回服务帧号和最近一帧。建立时间线后还会显示时间线帧号、起点与保留的快照。 `quit` 关闭客户端。 `detach` 干净卸载并恢复游戏的原生时钟与输入，服务可再次 `attach`。 `stop` 干净卸载并关闭服务。

默认每 120 帧快照一次，保留第一份和最近 120 份，含手动快照。参数示例为 `tasctl.py --snapshot-interval 60 --snapshot-count 6 status`。快照用 `heaps` 方案：`.data`、引擎的八个内存池连同描述符、sApp 和 sDevil4Main，第一份约 561 MB，之后每份只存差量，约 3.5 MB，都放在控制端的内存里，抓取约 0.25 秒，恢复约 0.33 秒。实测见[回退报告](../docs/REPORT_05.md)。

注意三点：

- TAS 接管期间没有声音。声音线程在后台解码音乐，内存被换回旧状态后会崩，所以接管时让它们空转；做过一次恢复之后，这次游戏运行期间声音不再打开。
- 每次切出游戏窗口，游戏会自己弹暂停画面。接管时程序会拦住它；如果发现角色不动，用时间线按一下 `Escape` 关掉。
- 快照只在同一个房间里有效。换房间、读盘、过场之后要重新开始时间线。

双击入口在项目根目录：`tas_player.bat`（逐帧播放器窗口：前后一帧、十帧、按 60 帧每秒播放、拖进度条或输入帧号跳转，并显示当前帧的键盘、角色读到的按钮、动作、敌人距离、是否在踩敌球内、是否可踩、Enemy Step 事件；改了时间线文件后点“按文件重放到当前帧”；小键盘 `+` 前进一帧、小键盘 `-` 后退一帧，是全局热键，焦点在游戏或别的窗口时也有效）、`tas_console.bat`（控制台）、`record_boot.bat`（录一段从标题到房间的路）、`boot_to_room.bat`（重启游戏并自动回到房间，约 80 秒）。

时间线的每一行给出完整按住集合。区间含首尾，`-` 表示全松开。轨道结束后继续按空键态运行。

```text
1-4: Space L
5: Space L J
6: Space L
7: Space L K
8: -
```

验收命令如下。战斗回退需在有可锁定敌人的房间内运行。

- M1 前台和后台验收 `& '.\.venv\Scripts\python.exe' -B 'tas\m1_accept.py'`
- 120 帧基线、改写和事件验收 `& '.\.venv\Scripts\python.exe' -B 'tas\rollback_accept.py'`。 `--mode full` 包含恢复间隔和 50 次稳定性试验。 `--timeline`、`--jump`、`--effects-timeline` 可为当前站位指定输入和跳跃帧。
