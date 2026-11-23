"""Frame player for the DMC4SE TAS service: an input script editor (one line per frame, as in
Celeste Studio) next to step, step back, play and jump, with a live state panel.

The editor is the timeline. Every edit is saved to the script file and sent to the service, which
replays the game to the frame it was on with the new inputs, so the picture always matches the text.
"""
import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from datetime import datetime
from math import dist
from pathlib import Path
from tkinter import filedialog, simpledialog, ttk

VK_ADD, VK_SUBTRACT, VK_MULTIPLY, VK_DIVIDE, WM_HOTKEY = 0x6B, 0x6D, 0x6A, 0x6F, 0x0312
HOTKEYS = (VK_ADD, VK_SUBTRACT, VK_MULTIPLY, VK_DIVIDE)   # numpad + - * /, system-wide

ROOT = Path(__file__).resolve().parents[1]
for folder in ("tas", "work", "tools"):
    sys.path.insert(0, str(ROOT / folder))

from client import Client  # noqa: E402
from overlay import Overlay  # noqa: E402
from tasctl import start_service  # noqa: E402
from start_game import LABELS, running_games, select_game
from timeline import (RANGE_LINE, expand_lines, expand_text, format_frames, line_frames,  # noqa: E402
                      parse_frames, parse_script)

RUNS = ROOT / "runs"
GUTTER = 104        # width of the frame-number column in the script window ("▸ 1234–1250")
# Practice aids: (name in the agent, virtual key of its hotkey, label). F1 F2 F3 F5 as in the
# trainer the user knows; the keys are only read while the game window has the focus.
CHEATS = (("hp", 0x70, "锁血（我方+敌人）"), ("dt", 0x71, "无限魔力"), ("time", 0x72, "时间不减"), ("kill", 0x74, "一击必杀"))
NEW_SCRIPT = ""     # a new script starts empty: the gutter already numbers the rows 1, 2, 3 …
BUTTONS = {0x01: "近战", 0x02: "枪", 0x04: "跳", 0x10: "锁定"}
DIRECTIONS = {1: "前", 2: "后", 4: "左", 8: "右"}
BG, PANEL, INK, DIM, ACCENT, WARN = "#14161a", "#1d2026", "#e6e8eb", "#8b93a1", "#5fb3ff", "#ffb454"


def bits(value, names):
    if not value:
        return "—"
    known = [label for bit, label in names.items() if value & bit]
    rest = value & ~sum(names)
    return "＋".join(known + ([f"{rest:#x}"] if rest else []))


class Player:
    def __init__(self, root):
        self.root = root
        self.client = None
        self.game = None
        self.legacy = False
        self.path = None
        self.length = 0
        self.playing = False
        self.frame = 0
        self.dragging = False
        self.line_map = []          # per editor line: (first frame, count), None, or an error text
        self.now_line = None        # editor line that holds the current frame
        self.pending = None         # timer of an edit that has not been applied yet
        self.unsent = False         # the service does not have the editor's script yet
        self.script_error = None
        root.title("DMC4 逐帧播放器")
        root.configure(bg=BG)
        root.attributes("-topmost", True)
        root.minsize(640, 430)
        root.geometry("760x560")
        ui = ("Microsoft YaHei UI", 10)
        mono = ("Consolas", 11)
        self.mono = mono

        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=12, pady=(12, 6))
        self.recording = False
        self.record_button = tk.Button(top, text="● 录制", command=self.toggle_record, font=ui, width=10,
                                       fg="#c0392b")
        self.record_button.pack(side="left", padx=(0, 6))
        # Off by default: replaying the whole take to compare it takes as long as the take.
        self.check_take = tk.BooleanVar(value=False)
        self.check_button = tk.Checkbutton(top, text="录完重放核对", variable=self.check_take, font=ui, bg=BG, fg=INK,
                       selectcolor=PANEL, activebackground=BG, activeforeground=INK, bd=0,
                       highlightthickness=0)
        self.check_button.pack(side="left", padx=(0, 10))
        # Play with the game's own sound: always from the first frame of the timeline, because that
        # is the state the sound threads can wake in (service_operations.audible_start).
        self.sound_play = tk.BooleanVar(value=False)
        self.sounding = False
        self.root.after(300, self.enable_drop)      # once both windows exist
        self.control_button = tk.Button(top, text="⏸ 接管", command=self.toggle_control, font=ui, width=10)
        self.control_button.pack(side="left", padx=(0, 6))
        tk.Button(top, text="脚本窗口", command=self.show_script_window, font=ui).pack(side="left")

        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=12, pady=6)
        def button(text, command, width=6):
            widget = tk.Button(bar, text=text, command=command, font=("Microsoft YaHei UI", 11), width=width)
            widget.pack(side="left", padx=3)
            return widget
        button("⏮ 起点", lambda: self.goto(0), 7)
        button("◀ 10", lambda: self.back(10))
        button("◀ 1", lambda: self.back(1))
        self.play_button = button("▶ 播放", self.toggle_play, 8)
        button("1 ▶", lambda: self.step(1))
        button("10 ▶", lambda: self.step(10))
        self.entry = None       # jumping to a frame lives in the script window (entry2, gutter)

        # Practice aids. The F keys work while the game window has the focus.
        aids = tk.Frame(root, bg=BG)
        aids.pack(fill="x", padx=12, pady=(2, 4))
        tk.Label(aids, text="练习辅助", bg=BG, fg=DIM, font=ui).pack(side="left", padx=(2, 8))
        self.cheat_vars = {}
        for name, key, label in CHEATS:
            var = tk.BooleanVar(value=False)
            # Drawn as a button that stays down and turns green while the aid is on.
            tk.Checkbutton(aids, text=f"F{key - 0x6F} {label}", variable=var, font=ui, indicatoron=False,
                           selectcolor="#7ee787", padx=8, pady=2,
                           command=lambda name=name, var=var: self.set_cheat(name, var.get())
                           ).pack(side="left", padx=(0, 6))
            self.cheat_vars[name] = var
        self.cheat_keys = {key: False for _, key, _ in CHEATS}
        self.game_window = None
        # Enemy Step boxes drawn over the game (overlay.py). F6 while the game window is in front.
        self.overlay = Overlay(root)
        self.overlay_var = tk.BooleanVar(value=False)
        self.overlay_button = tk.Checkbutton(aids, text="F6 踩头方框", variable=self.overlay_var, font=ui, indicatoron=False,
                       selectcolor="#7ee787", padx=8, pady=2,
                       command=lambda: self.overlay.set(self.overlay_var.get()))
        self.overlay_button.pack(side="left", padx=(0, 6))
        self.overlay_key = False
        root.after(500, self.overlay_tick)
        self.coverage = tk.StringVar()
        tk.Label(root, textvariable=self.coverage, bg=BG, fg=DIM, font=ui,
                 wraplength=620, justify="left").pack(anchor="w", padx=14)

        self.slider = ttk.Scale(root, from_=0, to=1, orient="horizontal")
        self.slider.pack(fill="x", padx=14, pady=(2, 6))
        self.slider.bind("<ButtonPress-1>", lambda _: setattr(self, "dragging", True))
        self.slider.bind("<ButtonRelease-1>", self.slider_released)

        # The bottom lines are packed first so the state panel gets what is left.
        tk.Label(root, text="本窗口内：← → 前后一帧   Shift+← → 十帧   空格 播放/暂停   Home 回起点   "
                            "接管期间游戏没有声音（脚本窗口勾“播放带声音”后，播放时有）", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(side="bottom", fill="x", padx=14, pady=(0, 10))
        tk.Label(root, text="小键盘（在任何窗口里都有效）：  *  开始/停止录制    /  播放/暂停    +  前进一帧    −  后退一帧",
                 bg=BG, fg=INK, font=("Microsoft YaHei UI", 9), anchor="w").pack(side="bottom", fill="x", padx=14)
        self.status = tk.Label(root, text="", bg=BG, fg=DIM, font=ui, anchor="w")
        self.status.pack(side="bottom", fill="x", padx=14, pady=(0, 4))

        # The input script has a window of its own: one row per frame, the gutter is the frame number.
        self.script_window = window = tk.Toplevel(root)
        window.title("DMC4 输入脚本")
        window.configure(bg=BG)
        window.attributes("-topmost", True)
        window.minsize(480, 360)
        window.geometry("600x760")
        window.protocol("WM_DELETE_WINDOW", window.withdraw)     # closing only hides it
        left = tk.Frame(window, bg=BG)
        left.pack(fill="both", expand=True, padx=12, pady=10)
        files = tk.Frame(left, bg=BG)
        files.pack(fill="x")
        tk.Button(files, text="新建", command=self.new_script, font=ui).pack(side="left")
        tk.Button(files, text="打开…", command=self.open_file, font=ui).pack(side="left", padx=6)
        tk.Button(files, text="另存为…", command=self.save_as, font=ui).pack(side="left")
        moves = tk.Frame(left, bg=BG)
        moves.pack(fill="x", pady=(6, 0))
        tk.Button(moves, text="⏮", command=lambda: self.goto(0), font=ui, width=3).pack(side="left")
        tk.Button(moves, text="◀ 1", command=lambda: self.back(1), font=ui, width=5).pack(side="left", padx=(6, 0))
        self.play_button2 = tk.Button(moves, text="▶ 播放", command=self.toggle_play, font=ui, width=8)
        self.play_button2.pack(side="left", padx=6)
        tk.Button(moves, text="1 ▶", command=lambda: self.step(1), font=ui, width=5).pack(side="left")
        # Sound effects and voices only: the music streaming thread crashed when it woke for a play
        # with sound (2026-11-21), so it stays asleep (agent.js, slot 6). Not yet tried in the game.
        self.sound_button = tk.Checkbutton(files, text="播放带音效（从头放，无背景音乐）", variable=self.sound_play, font=ui, bg=BG,
                       fg=INK, selectcolor=PANEL, activebackground=BG, activeforeground=INK, bd=0,
                       highlightthickness=0)
        self.sound_button.pack(side="right")
        self.fold_all = tk.BooleanVar(value=True)
        tk.Checkbutton(moves, text="折叠相同行", variable=self.fold_all, font=ui, bg=BG, fg=INK,
                       selectcolor=PANEL, activebackground=BG, activeforeground=INK, bd=0, highlightthickness=0,
                       command=lambda: (self.fold_flip.clear(), self.refold())).pack(side="right")
        tk.Button(files, text="↑ 同键全选", command=lambda: self.select_run(-1), font=ui).pack(side="left", padx=(14, 0))
        tk.Button(files, text="↓ 同键全选", command=lambda: self.select_run(+1), font=ui).pack(side="left", padx=6)
        tk.Label(moves, text="跳到", bg=BG, fg=DIM, font=ui).pack(side="left", padx=(12, 4))
        self.entry2 = tk.Entry(moves, width=7, font=mono, justify="right")
        self.entry2.pack(side="left")
        self.entry2.bind("<Return>", lambda _: self.goto_entry(self.entry2))
        tk.Label(moves, text="帧", bg=BG, fg=DIM, font=ui).pack(side="left", padx=4)
        # Save states: keep this frame of this script under a name and come back to it later, also
        # after handing the game back. They last until the room is left or the game closed.
        states = tk.Frame(left, bg=BG)
        states.pack(fill="x", pady=(6, 0))
        self.save_button = tk.Button(states, text="存档", command=self.save_state, font=ui, width=6)
        self.save_button.pack(side="left")
        self.state_button = tk.Button(states, text="读档 ▾", command=self.state_menu, font=ui, width=7)
        self.state_button.pack(side="left", padx=6)
        tk.Label(states, text="记下这一帧，之后随时回到这里（换房间、读盘或关游戏后失效）", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(side="left", padx=(4, 0))
        # The same frame slider and frame counter as the main window, so the script can be followed
        # without looking over there.
        self.slider2 = ttk.Scale(left, from_=0, to=1, orient="horizontal")
        self.slider2.pack(fill="x", pady=(8, 0))
        self.slider2.bind("<ButtonPress-1>", lambda _: setattr(self, "dragging", True))
        self.slider2.bind("<ButtonRelease-1>", self.slider_released)
        self.headline2 = tk.Label(left, text="", bg=BG, fg=ACCENT, font=("Consolas", 16, "bold"), anchor="w")
        self.headline2.pack(fill="x", pady=(2, 0))
        self.file_label = tk.Label(left, text="", bg=BG, fg=DIM, font=("Microsoft YaHei UI", 9), anchor="w")
        self.file_label.pack(fill="x", pady=(6, 0))
        self.script_note = tk.Label(left, text="", bg=BG, fg=DIM, font=("Microsoft YaHei UI", 9), anchor="w",
                                    justify="left", wraplength=370)
        self.script_note.pack(side="bottom", fill="x", pady=(4, 0))
        tk.Label(left, text="一行就是一帧，左边是帧号。S W ＝这一帧同时按 S 和 W\n"
                            "一个键要按住几帧，就连着几行都写它；空行＝这一帧不按\n"
                            "写 5 S 再换行，会自动列成 5 行 S　　# 后面是注释\n"
                            "右边一栏是角色在那一帧的动画：第几帧 / 这段动画共几帧（走过的帧才有）\n"
                            "金色“MAX点”/ 红色“红刀点”：Q 写在那一行 = 充满 3 格 / +1 格（尼禄，走过的帧才有）\n"
                            "双击左边的帧号，游戏直接跳到那一帧\n"
                            "光标放在一个键上按 Ctrl+Shift+↑/↓：连着的行里同一个键全选，可一起删或改",
                 bg=BG, fg=DIM, font=("Microsoft YaHei UI", 9), anchor="w", justify="left").pack(side="bottom", fill="x")
        box = tk.Frame(left, bg=PANEL)
        box.pack(fill="both", expand=True, pady=(4, 4))
        self.gutter = tk.Canvas(box, width=GUTTER, bg="#181a1f", highlightthickness=0, bd=0)
        self.gutter.pack(side="left", fill="y")
        self.gutter.bind("<Double-Button-1>", self.gutter_jump)
        self.gutter.bind("<Button-1>", self.toggle_fold)
        # Folding of identical rows: runs (first line -> last line), which of them are folded now,
        # the lines hidden by that, and the runs whose state was flipped by a click (by frame).
        self.runs, self.folded, self.hidden, self.fold_flip = {}, {}, set(), set()
        scroll = ttk.Scrollbar(box, orient="vertical")
        scroll.pack(side="right", fill="y")
        # Right of the text: what the character was doing on each frame (from frames already played).
        self.info = tk.Canvas(box, width=236, bg="#181a1f", highlightthickness=0, bd=0)
        self.info.pack(side="right", fill="y")
        self.trace = {}
        self.editor = tk.Text(box, width=26, bg=PANEL, fg=INK, font=mono, relief="flat", padx=8, pady=8,
                              wrap="none", undo=True, maxundo=-1, insertbackground=INK, insertwidth=2,
                              selectbackground="#3a5f8a", selectforeground=INK, inactiveselectbackground="#33485f")
        self.editor.pack(side="left", fill="both", expand=True)
        scroll.configure(command=self.editor.yview)
        self.editor.configure(yscrollcommand=lambda first, last: (scroll.set(first, last), self.draw_gutter()))
        self.editor.tag_configure("now", background="#274b6d")
        self.editor.tag_configure("bad", foreground="#ff6b6b", underline=True)
        self.editor.tag_configure("comment", foreground=DIM)
        self.editor.tag_raise("sel")
        self.editor.bind("<<Modified>>", self.edited)
        self.editor.bind("<Configure>", lambda _: self.draw_gutter())
        # Same key on a run of rows: Ctrl+Shift+Up / Down marks it on every row above / below that
        # also has it; then Delete removes it from all of them and typing replaces it in all.
        self.multi = []             # [(line, first column, end column)]
        self.editor.tag_configure("multi", background="#6b4f1d")
        self.editor.bind("<Control-Shift-Up>", lambda _: self.select_run(-1))
        self.editor.bind("<Control-Shift-Down>", lambda _: self.select_run(+1))
        self.editor.bind("<Escape>", self.editor_escape)
        self.editor.bind("<Key>", self.editor_key)
        self.editor.bind("<Button-1>", lambda event: (self.clear_multi(), self.click_row(event))[1])
        # A row written with a count ("5 S") is listed frame by frame as soon as the cursor leaves it.
        for event in ("<KeyRelease>", "<ButtonRelease-1>", "<FocusOut>"):
            self.editor.bind(event, lambda _: (self.expand_counts(), self.refold()), add="+")
        self.editor.tag_configure("fold", elide=True)
        self.show_file()

        # The main window: what the game did on the current frame.
        right = tk.Frame(root, bg=BG)
        right.pack(fill="both", expand=True, padx=12, pady=6)
        self.headline = tk.Label(right, text="", bg=BG, fg=ACCENT, font=("Consolas", 20, "bold"), anchor="w")
        self.headline.pack(fill="x", padx=2)
        self.panel = tk.Text(right, height=12, bg=PANEL, fg=INK, font=mono, relief="flat", padx=12, pady=10,
                             state="disabled", wrap="none", cursor="arrow")
        self.panel.pack(fill="both", expand=True, pady=(6, 0))
        self.panel.tag_configure("dim", foreground=DIM)
        self.panel.tag_configure("hot", foreground=WARN)
        self.panel.tag_configure("ok", foreground="#7ee787")
        for widget in (self.headline, self.panel, right):
            widget.bind("<Button-1>", lambda _: self.root.focus_set())
        root.after(300, self.place_script_window)

        for key, action in (("<Right>", lambda: self.step(1)), ("<Left>", lambda: self.back(1)),
                            ("<Shift-Right>", lambda: self.step(10)), ("<Shift-Left>", lambda: self.back(10)),
                            ("<space>", self.toggle_play), ("<Home>", lambda: self.goto(0))):
            root.bind(key, lambda event, run=action: self.hotkey(event, run))
        # Numpad + / - : one frame forward / back. Registered system-wide, so they work while the game
        # (or any other window) has the focus; if another program already owns them, they still work
        # inside this window.
        self.hotkeys = queue.SimpleQueue()
        self.global_keys = threading.Event()
        threading.Thread(target=self.listen_hotkeys, daemon=True).start()
        root.bind("<Key>", self.numpad_local)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(50, self.connect)
        root.after(120, self.tick)

    # --- service -------------------------------------------------------------------------------
    def connect(self):
        try:
            try:
                self.client = Client()
                self.client.call("status")
            except (OSError, RuntimeError):
                games = running_games()
                if not games:
                    raise RuntimeError("未找到运行中的游戏")
                game = select_game(games, self.root)
                if not game:
                    self.root.destroy()
                    return
                start_service(games[game], live=True)
                self.client = Client()
            # Connecting must not take the game away from the player: stay in whatever mode the
            # service is in. Taking over happens on the first step / back / play, or the button.
            state = self.client.call("status")
            mode = state.get("mode")
            if mode == "observe":
                self.note("已连接，游戏由你操作。")
            else:
                self.note("已连接，游戏停在帧闸上。")
            # A timeline that is already running (the window was closed and reopened): show its script.
            running = state.get("timelinePath")
            if running and self.path is None and Path(running).is_file():
                self.load(Path(running))
            self.refresh()
        except Exception as error:
            self.client = None
            self.note(f"连不上游戏：{error}。请双击 start_game.bat。", bad=True)
            self.root.after(3000, self.connect)

    def call(self, command, **arguments):
        if self.client is None:
            return None
        started = time.perf_counter()
        try:
            result = self.client.call(command, **arguments)
        except Exception as error:
            self.note(f"{command} 失败：{error}", bad=True)
            return None
        self.note(f"{command} 用时 {time.perf_counter() - started:.2f} 秒")
        return result

    def stop_playing(self):
        if self.playing:
            self.playing = False
            self.set_play_text("▶ 播放")
            if self.client is not None:
                try:
                    self.client.call("play-sound-stop" if self.sounding else "pause")
                except Exception:
                    pass
            self.sounding = False

    # --- numpad hotkeys ------------------------------------------------------------------------
    def listen_hotkeys(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        registered = [user32.RegisterHotKey(None, ident, 0, key) for ident, key in enumerate(HOTKEYS, 1)]
        if not all(registered):
            for ident in range(1, len(HOTKEYS) + 1):
                user32.UnregisterHotKey(None, ident)
            return
        self.global_keys.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY and 1 <= message.wParam <= len(HOTKEYS):
                self.hotkeys.put(HOTKEYS[message.wParam - 1])

    def numpad_local(self, event):
        if self.global_keys.is_set() or event.keycode not in HOTKEYS:
            return None
        self.hotkeys.put(event.keycode)
        return "break"

    def drain_hotkeys(self):
        """Key repeat can outrun a step back: fold the + / - that piled up into one move."""
        net, other = 0, []
        while not self.hotkeys.empty():
            key = self.hotkeys.get()
            if key == VK_ADD:
                net += 1
            elif key == VK_SUBTRACT:
                net -= 1
            elif key not in other:
                other.append(key)
        if VK_MULTIPLY in other:
            self.toggle_record()
            return
        if self.recording:
            return                      # nothing else while a take is running
        if net > 0:
            self.step(min(net, 10))
        elif net < 0:
            self.back(min(-net, 10))
        if VK_DIVIDE in other:
            self.toggle_play()

    # --- practice aids -------------------------------------------------------------------------
    def set_cheat(self, name, on):
        label = next(text for key, _, text in CHEATS if key == name)
        if self.client is None:
            self.cheat_vars[name].set(not on)
            return
        try:
            state = self.client.call("cheat", name=name, on=on)
        except Exception as error:
            self.cheat_vars[name].set(not on)
            self.note(f"{label} 没能切换：{error}", bad=True)
            return
        self.cheat_vars[name].set(bool(state.get(name)))
        self.note(f"{label}：{'开' if state.get(name) else '关'}")

    def overlay_tick(self):
        """Redraw the Enemy Step boxes about 30 times a second while they are switched on."""
        try:
            if self.overlay.enabled:
                self.overlay.tick()
        except Exception as error:
            self.overlay.set(False)
            self.overlay_var.set(False)
            self.note(f"踩头方框出错，已关闭：{error}", bad=True)
        self.root.after(33 if self.overlay.enabled else 250, self.overlay_tick)

    def poll_cheat_keys(self):
        """F1 F2 F3 F5 toggle the aids, but only while the game window is in front, so the keys
        keep their normal meaning everywhere else."""
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        front = user32.GetForegroundWindow() == self.game_window and self.game_window is not None
        for name, key, _ in CHEATS:
            down = bool(user32.GetAsyncKeyState(key) & 0x8000)
            if down and not self.cheat_keys[key] and front and not self.recording:
                self.set_cheat(name, not self.cheat_vars[name].get())
            self.cheat_keys[key] = down
        down = bool(user32.GetAsyncKeyState(0x75) & 0x8000)          # F6
        if down and not self.overlay_key and front and self.game == "se":
            self.overlay_var.set(not self.overlay_var.get())
            self.overlay.set(self.overlay_var.get())
        self.overlay_key = down

    # --- recording a take ----------------------------------------------------------------------
    def toggle_record(self):
        if self.client is None or self.record_button.cget("state") == "disabled":
            return
        self.stop_playing()
        if not self.recording:
            started = self.call("take-start")
            if started is None:
                return
            self.recording = True
            self.before_take = (self.path, self.editor.get("1.0", "end-1c"))
            self.path, self.length = None, 0
            self.set_editor("# 录制中… 停止后，你按过的键会逐帧出现在这里。\n")
            self.editor.configure(state="disabled")
            self.file_label.configure(text="录制中…", fg=WARN)
            self.record_button.configure(text="■ 停止录制")
            if started.get("continued") is not None:
                lead = f"从第 {started['continued']} 帧接着录：停止后，脚本是前面那 {started['continued']} 帧加上这次录的。"
            elif started.get("returned"):
                lead = "做过后退，所以游戏先回到了声音线程睡下的那一刻，录制从这里开始。"
            else:
                lead = ""
            self.note(lead + "开始录制。点游戏窗口照常操作；切走时游戏冻结、不计入录制。再按小键盘 * 停止。")
            self.refresh()
            return
        check = self.check_take.get()
        self.note("正在停止录制并从第 0 帧重放核对…" if check else "正在停止录制…")
        self.root.update_idletasks()
        report = self.call("take-stop", check=check)
        self.recording = False
        self.record_button.configure(text="● 录制")
        self.editor.configure(state="normal")
        if report is None:
            # Nothing was recorded: put back the script that was there before.
            self.path, text = self.before_take
            self.set_editor(text)
            self.unsent = self.path is not None
            self.show_file()
            return
        self.path = Path(report["path"])
        self.set_editor(expand_text(self.path.read_text(encoding="utf-8-sig")))
        self.unsent = True          # cheap when the service already replayed this very file
        self.push_script()
        self.show_file()
        extra = []
        if report.get("unmapped"):
            extra.append("有鼠标之类的输入没录进去")
        if report.get("reason"):
            extra.append(f"录制提前结束：{report['reason']}")
        joined = f"（接在原来的第 {report['continued']} 帧后面）" if report.get("continued") is not None else ""
        self.note(f"录了 {report['frames']} 帧{joined}，已写进脚本窗口，可以直接改。{report.get('check', '')}。"
                  + "；".join(extra) + "　按“⏮ 起点”再按小键盘 / 看回放。",
                  bad=report.get("consistent") is False)
        self.refresh()

    # --- who is in control ---------------------------------------------------------------------
    def take_over(self):
        """Freeze the game for frame work. Closes the game's own pause screen first if it is up."""
        if self.client is None:
            return False
        try:
            state = self.client.call("status")
            if state.get("mode") == "observe" or state.get("timelineFrame") is None:
                scene = self.client.call("record-scene")["scene"]
                if scene.get("paused"):
                    result = self.client.call("unpause")
                    if result["paused"]:
                        self.note("游戏的暂停画面关不掉，请切回游戏手动关掉它再试。", bad=True)
                        return False
            state = self.client.call("pause")
        except Exception as error:
            self.note(f"接管失败：{error}", bad=True)
            return False
        self.control_button.configure(text="⏵ 交还操作")
        self.push_script(quiet=True)
        if "snapshotUnavailable" in state:
            self.note(f"已接管，但这里不能后退：{state['snapshotUnavailable']}", bad=True)
        elif state.get("timelineFrame") in (None, 0):
            self.note("已接管，游戏停在帧闸上。这里是第 0 帧，脚本第一行是第 1 帧。")
        self.refresh()
        return True

    def release(self):
        """Give the game back to the player; the current timeline ends."""
        self.stop_playing()
        result = self.call("live")
        if result is None:
            return
        self.frame = 0
        self.unsent = self.path is not None     # the script stays; the next take-over starts it again
        self.highlight()
        self.control_button.configure(text="⏸ 接管")
        self.set_headline("你在操作游戏")
        if result.get("returned"):
            self.note("操作已交还，游戏回到了声音线程睡下的那一刻：接管的那一帧，或者最近一次录制结束的那一帧"
                      "（做过后退就必须回到这里，声音才能恢复、切场景才不会卡）。"
                      "脚本留着，下次接管从第 1 行重新开始。")
        else:
            self.note("操作已交还。点游戏窗口继续玩；切到别的窗口时游戏会自动冻结。脚本留着，下次接管从第 1 行重新开始。")

    def toggle_control(self):
        try:
            live = self.client is not None and self.client.call("status").get("mode") == "observe"
        except Exception:
            live = False
        if live:
            self.take_over()
        else:
            self.release()

    def ensure_control(self):
        """Any frame command implies taking over."""
        try:
            state = self.client.call("status") if self.client is not None else None
        except Exception:
            state = None
        if state is None:
            return False
        if state.get("mode") == "observe" or state.get("timelineFrame") is None:
            return self.take_over()
        return True

    # --- transport -----------------------------------------------------------------------------
    def hotkey(self, event, run):
        if event.widget is self.editor:
            return None                 # typing: arrows, space and Home belong to the text
        if event.widget is self.entry and event.keysym not in ("Right", "Left"):
            return None
        run()
        return "break"

    def ready(self):
        """Before any frame command: apply an edit that is still waiting, take over, send the script."""
        self.stop_playing()
        self.flush_edit()
        if not self.ensure_control():
            return False
        self.push_script(quiet=True)
        return True

    def step(self, count):
        if not self.ready():
            return
        rows = self.call("step", count=count)
        if rows:
            self.show(rows[-1], rows[-1].get("timelineFrame"))

    def back(self, count):
        self.stop_playing()
        self.flush_edit()
        count = min(count, self.frame)
        if count < 1:
            return
        result = self.call("back", count=count)
        if result:
            self.show(result["last"], result["frame"])

    def goto(self, frame):
        if not self.ready():
            return
        result = self.call("goto", count=max(0, int(frame)))
        if result:
            self.show(result["last"], result["frame"])

    def goto_entry(self, entry=None):
        entry = entry or self.entry2
        try:
            self.goto(int(entry.get()))
        except ValueError:
            self.note("请输入帧号（整数）。", bad=True)
        if entry is self.entry:
            self.root.focus_set()

    def gutter_jump(self, event):
        """Double-click on a frame number in the script window: jump the game to that frame."""
        rows = self.visible_rows()
        if not rows:
            return
        height = rows[1][0] - rows[0][0] if len(rows) > 1 else 20
        for top, frame in rows:
            if top <= event.y < top + height:
                self.goto(frame)
                return

    def set_headline(self, text):
        self.headline.configure(text=text)
        self.headline2.configure(text=text)

    def slider_released(self, event):
        self.dragging = False
        self.goto(round(float(event.widget.get())))

    def toggle_play(self):
        if self.playing:
            self.stop_playing()
            self.refresh()
            return
        if not self.ready():
            return
        self.refresh()                       # where the game is now, after a possible take-over
        if self.sound_play.get() and self.sound_button.cget("state") == "normal" and self.length:
            self.flush_edit()
            result = self.call("play-sound")
            if result is not None:
                self.playing = self.sounding = True
                self.play_target = result["to"]
                self.set_play_text("⏸ 暂停")
                self.note(f"带声音播放：第 1 帧到第 {result['to']} 帧。带声音只能从时间线的第 1 帧放起；"
                          "放完或按暂停后游戏回到无声状态。")
            return
        # Play the script: from the current frame to its last frame at game speed, then stop there.
        # At (or past) the end, play it again from the start. Without a script, just run.
        if self.length and self.frame >= self.length:
            result = self.call("goto", count=0)
            if not result:
                return
            self.show(result["last"], result["frame"])
        count = self.length - self.frame if self.length else 100000
        if self.call("resume", count=count) is not None:
            self.playing = True
            self.play_target = self.frame + count if self.length else None
            self.set_play_text("⏸ 暂停")
            if self.length:
                self.note(f"正在按脚本播放：第 {self.frame + 1} 帧到第 {self.length} 帧。")

    # --- save states ---------------------------------------------------------------------------
    def save_state(self):
        """Keep the current frame of this script under a name (service: state-save)."""
        if not self.ready():
            return
        if self.script_error or self.unsent:
            self.note("脚本还没有生效（有红色的行？），改好再存档。", bad=True)
            return
        name = simpledialog.askstring("存档", "给这个存档起个名字（同名会覆盖）：", parent=self.script_window,
                                      initialvalue=f"第{self.frame}帧")
        if not name or not name.strip():
            return
        result = self.call("state-save", name=name.strip())
        if result:
            self.note(f"已存档“{result['name']}”：第 {result['frame']} 帧，{result['note']}。现在共 {result['count']} 个存档。"
                      "交还操作后还能读；换房间、读盘或关游戏后失效。")

    def state_menu(self):
        """The saves that can be loaded, as a menu under the button."""
        if self.client is None:
            return
        try:
            listing = self.client.call("state-list")
        except Exception as error:
            self.note(f"读不到存档列表：{error}", bad=True)
            return
        saves = listing.get("saves") or []
        if not saves:
            if listing.get("lost"):
                self.note("之前的存档已经失效了（换过房间、读过盘，或者存档太久显卡资源快满了）。", bad=True)
            else:
                self.note("还没有存档。接管后走到想留住的那一帧，点“存档”。")
            return
        font = ("Microsoft YaHei UI", 10)
        menu = tk.Menu(self.script_window, tearoff=0, font=font)
        for save in saves:
            menu.add_command(label=f"{save['name']}　　第 {save['frame']} 帧　{save['note']}　存于 {save['made']}",
                             command=lambda name=save["name"]: self.load_state(name))
        menu.add_separator()
        remove = tk.Menu(menu, tearoff=0, font=font)
        for save in saves:
            remove.add_command(label=save["name"], command=lambda name=save["name"]: self.delete_state(name))
        menu.add_cascade(label="删除存档", menu=remove)
        button = self.state_button
        menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())

    def load_state(self, name):
        """Put the game back on a saved frame; the script window shows the script that leads there."""
        self.stop_playing()
        self.flush_edit()
        if not self.ensure_control():
            return
        self.note(f"正在读档“{name}”…（上次交还之后存的档要先等游戏里的声音放完，最多几秒）")
        self.root.update_idletasks()
        result = self.call("state-load", name=name)
        if not result:
            return
        self.load(Path(result["path"]))
        self.refresh()
        waited = f"，等声音放完用了 {result['waited']} 秒" if result.get("waited") else ""
        self.note(f"已读档“{name}”：游戏回到第 {result['frame']} 帧{waited}。脚本是 {Path(result['path']).name}，"
                  "这一帧之后的行可以接着改；这条时间线不能带声音播放。")

    def delete_state(self, name):
        result = self.call("state-delete", name=name)
        if result:
            self.note(f"已删除存档“{name}”，还剩 {result['count']} 个。")

    # --- the input script ----------------------------------------------------------------------
    def place_script_window(self):
        """Put the script window beside the main one (right of it, or left when there is no room)."""
        self.root.update_idletasks()
        x = self.root.winfo_x() + self.root.winfo_width() + 12
        if x + 600 > self.root.winfo_screenwidth():
            x = max(0, self.root.winfo_x() - 612)
        self.script_window.geometry(f"+{x}+{max(0, self.root.winfo_y())}")

    def show_script_window(self):
        self.script_window.deiconify()
        self.script_window.lift()
        self.editor.focus_set()

    def set_play_text(self, text):
        self.play_button.configure(text=text)
        self.play_button2.configure(text=text)

    def expand_counts(self):
        """List every frame on a row of its own: "5 S" becomes five rows of S. The row the cursor
        is on is left alone until the cursor moves away, so typing is never rewritten mid-word."""
        if self.recording:
            return False
        cursor = int(self.editor.index("insert").split(".")[0])
        try:
            if self.editor.focus_get() is not self.editor:
                cursor = None
        except KeyError:
            cursor = None
        changes = expand_lines(self.editor.get("1.0", "end-1c"), skip=cursor)
        for number, rows in reversed(changes):
            self.editor.replace(f"{number}.0", f"{number}.end", "\n".join(rows))
        return bool(changes)

    def set_editor(self, text):
        """Replace the editor's text (a file was opened, a take ended); not an edit by the user."""
        state = self.editor.cget("state")
        self.editor.configure(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self.editor.configure(state=state)
        if self.pending is not None:
            self.root.after_cancel(self.pending)
            self.pending = None
        self.mark_lines()
        self.highlight()

    def mark_lines(self):
        """Work out which frames each line stands for; colour comments and lines that do not parse."""
        text = self.editor.get("1.0", "end-1c")
        self.line_map = line_frames(text)
        self.editor.tag_remove("bad", "1.0", "end")
        self.editor.tag_remove("comment", "1.0", "end")
        self.script_error = None
        self.length = 0
        for number, (line, entry) in enumerate(zip(text.split("\n"), self.line_map), 1):
            if isinstance(entry, str):
                self.editor.tag_add("bad", f"{number}.0", f"{number}.end")
                if self.script_error is None:
                    self.script_error = f"第 {number} 行：{entry}"
            elif entry:
                self.length = entry[0] + entry[1] - 1
            if "#" in line:
                self.editor.tag_add("comment", f"{number}.{line.index('#')}", f"{number}.end")
        self.compute_folds(text)
        return text

    def compute_folds(self, text=None):
        """Fold runs of identical rows: a key held for 19 frames is shown as its first row, with
        "1–19" in the gutter, and the other 18 rows hidden. Display only; the text is untouched.
        The run the cursor is typing in stays open, so a row never vanishes under the cursor."""
        if text is None:
            text = self.editor.get("1.0", "end-1c")
        lines = text.split("\n")
        runs, start, previous = {}, None, None
        for number in range(1, len(self.line_map) + 2):
            entry = self.line_map[number - 1] if number <= len(self.line_map) else None
            key = None
            if isinstance(entry, tuple) and entry[1] == 1 and "#" not in lines[number - 1]:
                key = " ".join(sorted(item[0].casefold() for item in self.tokens(lines[number - 1])))
            if key is None or key != previous:
                if start is not None and number - start >= 3:
                    runs[start] = number - 1
                start = number if key is not None else None
            previous = key
        try:
            cursor = int(self.editor.index("insert").split(".")[0]) if self.editor.focus_get() is self.editor else 0
        except KeyError:
            cursor = 0
        folded = {}
        for first, last in runs.items():
            wanted = self.fold_all.get() != (self.line_map[first - 1][0] in self.fold_flip)
            if wanted and not first < cursor <= last:
                folded[first] = last
        self.runs = runs
        if folded != self.folded:
            self.folded = folded
            self.hidden = {line for first, last in folded.items() for line in range(first + 1, last + 1)}
            self.editor.tag_remove("fold", "1.0", "end")
            for first, last in folded.items():
                self.editor.tag_add("fold", f"{first + 1}.0", f"{last + 1}.0")

    def refold(self):
        before = self.folded
        self.compute_folds()
        if self.folded is not before:
            self.highlight()

    def toggle_fold(self, event):
        """Click on the gutter: open or close the run of identical rows that starts there."""
        for top, height, number, kind, frame in self.screen_rows():
            if top <= event.y < top + height and number in self.runs:
                self.fold_flip ^= {frame}
                self.editor.mark_set("insert", f"{number}.0")
                self.refold()
                return

    def highlight(self):
        """Mark the line whose input produced the frame on screen, and say how far into it we are."""
        self.editor.tag_remove("now", "1.0", "end")
        previous, self.now_line = self.now_line, None
        progress = ""
        for number, entry in enumerate(self.line_map, 1):
            if isinstance(entry, tuple) and entry[0] <= self.frame < entry[0] + entry[1]:
                shown = number
                if number in self.hidden:       # inside a folded run: light up the row that stands for it
                    shown = max(first for first in self.folded if first < number)
                    progress = f"（折叠在第 {self.line_map[shown - 1][0]}–{self.line_map[self.folded[shown] - 1][0]} 帧那一行里）"
                self.now_line = shown
                self.editor.tag_add("now", f"{shown}.0", f"{shown}.0 lineend +1c")
                if entry[1] > 1:
                    progress = f"　这一行的第 {self.frame - entry[0] + 1}/{entry[1]} 帧"
                break
        if self.now_line and self.now_line != previous:
            self.editor.see(f"{self.now_line}.0")
        if self.script_error:
            self.script_note.configure(text=f"{self.script_error}。改好之前游戏用的还是上一版脚本。", fg="#ff6b6b")
        elif self.now_line:
            self.script_note.configure(text=f"当前第 {self.frame} 帧 ＝ 第 {self.now_line} 行{progress}", fg=DIM)
        elif self.length and self.frame > self.length:
            self.script_note.configure(text=f"脚本共 {self.length} 帧，已经放完；后面的帧什么都不按。", fg=DIM)
        else:
            self.script_note.configure(text=f"脚本共 {self.length} 帧。" if self.length else "", fg=DIM)
        self.draw_gutter()

    def draw_gutter(self):
        """Next to each row: the frame it starts on. Rows that are still empty, down to the bottom
        of the box, carry the frame they would be, so the rows can be told apart before anything
        is written."""
        self.gutter.delete("all")
        for top, _, number, kind, frame in self.screen_rows():
            if kind == "ghost":
                self.gutter.create_text(GUTTER - 6, top, anchor="ne", text=str(frame), font=self.mono, fill="#596170")
            elif kind == "error":
                self.gutter.create_text(GUTTER - 6, top, anchor="ne", text="!", font=self.mono, fill="#ff6b6b")
            elif kind == "row":
                colour = ACCENT if number == self.now_line else DIM
                label = str(frame)
                if number in self.folded:       # a folded run: the frames it stands for
                    label = f"{frame}–{self.line_map[self.folded[number] - 1][0]}"
                self.gutter.create_text(GUTTER - 6, top, anchor="ne", text=label, font=self.mono, fill=colour)
                if number in self.runs:
                    self.gutter.create_text(4, top, anchor="nw", text="▸" if number in self.folded else "▾",
                                            font=self.mono, fill=WARN if number in self.folded else "#596170")
        self.draw_info()

    def screen_rows(self):
        """[(y, height, line, kind, frame)] of the rows shown in the script box. kind: "row" (a
        frame), "error", "comment", or "ghost" for the still empty rows down to the bottom of the
        box. Rows hidden inside a folded run are not on screen."""
        written = len(self.line_map)
        last_line = int(self.editor.index("end-1c").split(".")[0])
        number = int(self.editor.index("@0,0").split(".")[0])
        rows, top, height, ended = [], None, 0, False
        while len(rows) < 300:
            if number > last_line:
                ended = True
                break
            if number in self.hidden:
                number += 1
                continue
            box = self.editor.dlineinfo(f"{number}.0")
            if box is None:
                break
            top, height = box[1], box[3]
            entry = self.line_map[number - 1] if number <= written else None
            if number > written:
                rows.append((top, height, number, "ghost", self.length + number - written))
            elif isinstance(entry, tuple):
                rows.append((top, height, number, "row", entry[0]))
            else:
                rows.append((top, height, number, "error" if isinstance(entry, str) else "comment", None))
            number += 1
        if ended and top is not None and height > 0:
            while top + 2 * height <= self.editor.winfo_height() and number < 100000:
                top += height
                rows.append((top, height, number, "ghost", self.length + number - written))
                number += 1
        return rows

    def pad_to_frame(self):
        """Stepping past the end of the script: give each frame stepped into a row of its own (an
        empty one, which is what those frames got anyway), so the current frame always has a line
        to write on and the animation column follows it."""
        if self.playing or self.script_error or self.frame <= self.length \
                or str(self.editor.cget("state")) != "normal" or self.frame - self.length > 5000:
            return
        cursor = self.editor.index("insert")
        text = self.editor.get("1.0", "end-1c")
        padding = ("" if text.endswith("\n") or not text else "\n") + "\n" * (self.frame - self.length)
        self.editor.insert("end-1c", padding)
        self.editor.mark_set("insert", cursor)
        self.mark_lines()

    def visible_rows(self):
        """[(y, frame)] for every row shown in the script box, also the empty ones below the text."""
        return [(top, frame) for top, _, _, kind, frame in self.screen_rows() if kind in ("row", "ghost")]

    def fetch_trace(self):
        """Ask the service what the character did on the frames in view (and a stretch before
        them, to count how far into its animation each one is)."""
        if self.client is None:
            return
        rows = self.visible_rows()
        if not rows:
            return
        first, last = min(frame for _, frame in rows), max(frame for _, frame in rows)
        try:
            found = self.client.call("trace", first=max(1, first - 240), last=last + 240)
        except Exception:
            return
        self.trace = {int(frame): entry for frame, entry in found.items()}
        self.draw_info()

    def motion_at(self, frame):
        """(frame within its animation, length of that animation or None while it is not over yet)."""
        entry = self.trace.get(frame)
        if not entry:
            return None
        def same(a, b):
            """b is the frame after a, still in the same animation (its time did not start over)."""
            return a is not None and b is not None and a[1] == b[1] and b[2] >= a[2]
        start = frame
        while frame - start < 2000 and same(self.trace.get(start - 1), self.trace[start]):
            start -= 1
        end = frame
        while end - frame < 2000:
            following = self.trace.get(end + 1)
            if following is None:
                end = None
                break
            if not same(self.trace[end], following):
                break
            end += 1
        return frame - start + 1, (end - start + 1) if end is not None else None, entry

    def exceed_at(self, frame):
        """Nero's red blade on a frame that has been played: None, or (kind, done).

        kind "max": the first frame of the EX-Act window of a sword move, where the Exceed key
        fills all three bars; "ex": a later frame of that window (one bar); "rev": a bar filled by
        revving while standing. done: the gauge really went up on this frame. The window is read
        from the game's own timer (see combat.js), so a row shows what the key would have done on
        that frame as the script stands now."""
        entry, before = self.trace.get(frame), self.trace.get(frame - 1)
        if not entry or len(entry) < 5 or entry[4] is None:
            return None
        if not before or len(before) < 5 or before[4] is None:
            return None                         # the frame before is not known: cannot tell
        # The window is open on the frames on which the timer moves. It starts from 0, or from the
        # value an earlier window left behind: once the key has gone in, the timer just stops and
        # keeps its value until the next window starts it again (seen 2026-11-21).
        moved = entry[3] > 0 and entry[3] != before[3]
        first = moved and (before[3] == 0 or entry[3] < before[3])
        if entry[4] > before[4]:
            if entry[0] == 37:                  # action 37: revving on the spot
                return "rev", True
            return ("max" if first else "ex"), True
        if not moved:
            return None
        return ("max" if first else "ex"), False

    EXCEED_LOOK = {"max": ("#6b5210", "#ffd75a"), "ex": ("#5a1c1c", "#ff8a8a"), "rev": ("#33383f", "#c8ccd2")}

    def draw_info(self):
        self.info.delete("all")
        if not self.trace:
            return
        width = int(self.info.cget("width"))
        for top, height, number, kind, frame in self.screen_rows():
            if kind not in ("row", "ghost"):
                continue
            found = self.motion_at(frame)
            if not found:
                continue
            # Red blade: a coloured row with a label at its right end. A folded run of rows shows
            # the best frame inside it and which frame that is.
            last = self.line_map[self.folded[number] - 1][0] if number in self.folded else frame
            marks = [(at, self.exceed_at(at)) for at in range(frame, last + 1)]
            marks = [(at, mark) for at, mark in marks if mark]
            if marks:
                done = [item for item in marks if item[1][1]]
                at, (what, hit) = (done or sorted(marks, key=lambda item: item[1][0] != "max"))[0]
                back, ink = self.EXCEED_LOOK[what]
                label = {"max": "MAX", "ex": "红刀", "rev": "充能"}[what]
                label = ("★" + label + "成功") if hit else (label + "点")
                if last > frame:
                    label += f" 第{at}帧"
                self.info.create_rectangle(0, top, width, top + height, fill=back, outline="")
                badge = (label, back, ink)
            else:
                badge = None
            index, total, entry = found
            action, motion = entry[0], entry[1]
            first = index == 1
            text = f"动画{motion}  {index}/{total if total else '…'}"
            if first:
                text = f"▸ 动画{motion}  {index}/{total if total else '…'}"
            if number in self.folded:           # a folded run: how far the animation gets within it
                final = self.motion_at(self.line_map[self.folded[number] - 1][0])
                if final and final[2][1] == motion and final[0] > index:
                    text = text.replace(f"  {index}/", f"  {index}–{final[0]}/")
                elif final:
                    text += f" → 动画{final[2][1]}"
            self.info.create_text(8, top, anchor="nw", text=text, font=("Consolas", 10),
                                  fill=ACCENT if frame == self.frame else ("#e6e8eb" if first else DIM))
            if badge:                           # on top of the animation text, on its own patch
                label, back, ink = badge
                item = self.info.create_text(width - 4, top, anchor="ne", text=label,
                                             font=("Microsoft YaHei UI", 9, "bold"), fill=ink)
                left, upper, right, lower = self.info.bbox(item)
                self.info.create_rectangle(left - 6, top, width, top + height, fill=back, outline="")
                self.info.tag_raise(item)

    # --- the same key on a run of rows ---------------------------------------------------------
    @staticmethod
    def tokens(line):
        """[(key text, first column, end column)] of a script row, comment left out."""
        found, start = [], None
        code = line.partition("#")[0]
        for column, char in enumerate(code + " "):
            if char.isspace() or char == ",":
                if start is not None:
                    found.append((code[start:column], start, column))
                    start = None
            elif start is None:
                start = column
        return found

    def select_run(self, direction):
        """Mark the key under the cursor (or the selection) on this row and on every row directly
        above (-1) or below (+1) that holds the same key."""
        if str(self.editor.cget("state")) != "normal":
            return "break"
        try:
            first, last = self.editor.index("sel.first"), self.editor.index("sel.last")
        except tk.TclError:
            first = last = self.editor.index("insert")
        line, column = (int(part) for part in first.split("."))
        end_line, end_column = (int(part) for part in last.split("."))
        if end_line != line:
            end_column = 10 ** 6            # a selection over several rows: the keys on its first row
        lines = self.editor.get("1.0", "end-1c").split("\n")
        if first == last:                   # no selection: the key the cursor is on
            here = [item for item in self.tokens(lines[line - 1]) if item[1] <= column <= item[2]][:1]
        else:                               # every key the selection touches (e.g. "J M")
            here = [item for item in self.tokens(lines[line - 1]) if item[1] < end_column and item[2] > column]
        if not here:
            self.script_note.configure(text="先把光标放在（或选中）一个或几个键名上，再往上 / 往下全选。", fg=WARN)
            return "break"
        keys = list(dict.fromkeys(item[0].casefold() for item in here))
        self.clear_multi()
        number = line
        while 1 <= number <= len(lines):
            found = {}
            for item in self.tokens(lines[number - 1]):
                found.setdefault(item[0].casefold(), item)
            if not all(key in found for key in keys):
                break                       # the run ends at the first row that lacks one of the keys
            self.multi += [(number, found[key][1], found[key][2]) for key in keys]
            number += direction
        self.editor.tag_remove("sel", "1.0", "end")
        self.show_multi()
        self.editor.focus_set()
        rows = sorted({item[0] for item in self.multi})
        self.script_note.configure(
            text=f"选中了第 {rows[0]}–{rows[-1]} 行的 {' '.join(item[0] for item in here)}（{len(rows)} 行）。"
                 "Delete 全部删掉，直接打字全部替换，Esc 取消。", fg=ACCENT)
        return "break"

    def collapse_multi(self):
        """Delete everything that is marked; what stays marked is one cursor per row, where its
        first marked key was."""
        cursors = {}
        for line, first, end in sorted(self.multi, key=lambda item: (item[0], -item[1])):
            if end > first:
                self.editor.delete(f"{line}.{first}", f"{line}.{end}")
            cursors[line] = first           # rightmost first, so the leftmost one is what remains
        # Removing several keys leaves the blanks that were between them: tidy the row.
        self.multi = []
        for line, first in cursors.items():
            text = self.editor.get(f"{line}.0", f"{line}.end")
            head = text[:first].rstrip() + (" " if text[:first].strip() else "")
            tail = text[first:].lstrip()
            if head + tail != text:
                self.editor.replace(f"{line}.0", f"{line}.end", head + tail)
            self.multi.append((line, len(head), len(head)))

    def show_multi(self):
        self.editor.tag_remove("multi", "1.0", "end")
        for line, first, end in self.multi:
            self.editor.tag_add("multi", f"{line}.{first}", f"{line}.{max(end, first + 1)}" if end > first
                                else f"{line}.{first}")

    def clear_multi(self):
        if self.multi:
            self.multi = []
            self.editor.tag_remove("multi", "1.0", "end")

    def editor_escape(self, _):
        if self.multi:
            self.clear_multi()
        else:
            self.root.focus_force()
        return "break"

    def editor_key(self, event):
        """Typing while a run is marked edits every marked row at once."""
        if self.multi and not (event.state & 0x4):            # not with Ctrl
            marked = any(end > first for _, first, end in self.multi)
            if event.keysym in ("Delete", "BackSpace"):
                if marked:
                    self.collapse_multi()
                elif event.keysym == "BackSpace":
                    changed = []
                    for line, first, _ in self.multi:
                        if first > 0:
                            self.editor.delete(f"{line}.{first - 1}", f"{line}.{first}")
                            first -= 1
                        changed.append((line, first, first))
                    self.multi = changed
                self.show_multi()
                return "break"
            if len(event.char) == 1 and event.char >= " ":
                if marked:
                    self.collapse_multi()
                    # What is typed must not run into the key that follows it on the row.
                    for line, first, _ in self.multi:
                        if self.editor.get(f"{line}.{first}", f"{line}.{first} +1c").strip():
                            self.editor.insert(f"{line}.{first}", " ")
                changed = []
                for line, first, _ in self.multi:
                    self.editor.insert(f"{line}.{first}", event.char)
                    changed.append((line, first + 1, first + 1))
                self.multi = changed
                self.show_multi()
                return "break"
            if event.keysym not in ("Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"):
                self.clear_multi()
        return self.numpad_local(event)

    def click_row(self, event):
        """A click on a numbered row below the text: make the rows up to it and put the cursor
        there, so "press J on frame 10" is one click and one key. The rows in between stay empty,
        i.e. frames with nothing pressed."""
        if str(self.editor.cget("state")) != "normal":
            return None
        last = int(self.editor.index("end-1c").split(".")[0])
        box = self.editor.dlineinfo(f"{last}.0")
        if box is None or box[3] < 1:
            return None
        extra = min((event.y - box[1]) // box[3], 2000)
        if extra < 1:
            return None
        self.editor.insert("end-1c", "\n" * extra)
        self.editor.mark_set("insert", f"{last + extra}.0")
        self.editor.focus_set()
        return "break"

    def edited(self, _=None):
        if not self.editor.edit_modified():
            return
        self.editor.edit_modified(False)
        self.mark_lines()
        self.highlight()
        if self.pending is not None:
            self.root.after_cancel(self.pending)
        self.pending = self.root.after(400, self.apply_script)

    def flush_edit(self):
        if self.pending is not None:
            self.root.after_cancel(self.pending)
            self.apply_script()

    def apply_script(self):
        """The text changed: save it and, when it parses, have the game follow it."""
        self.pending = None
        text = self.mark_lines()
        if not self.script_error and self.expand_counts():
            self.editor.edit_modified(False)
            if self.pending is not None:        # the expansion is part of this very edit
                self.root.after_cancel(self.pending)
                self.pending = None
            text = self.mark_lines()
        try:
            if self.script_error:
                raise ValueError(self.script_error)
            parse_frames(text)
        except ValueError as error:
            self.script_error = self.script_error or str(error)
            self.highlight()
            return False
        if self.path is None:
            if not text.strip():
                return True
            RUNS.mkdir(exist_ok=True)
            self.path = RUNS / f"script-{datetime.now():%Y%m%d-%H%M%S}.tas"
        try:
            self.path.write_text(text if text.endswith("\n") or not text else text + "\n", encoding="utf-8",
                                 newline="\n")
        except OSError as error:
            self.note(f"脚本存不了：{error}", bad=True)
            return False
        self.show_file()
        self.unsent = True
        self.push_script()
        self.highlight()
        return True

    def in_control(self):
        try:
            state = self.client.call("status") if self.client is not None else None
        except Exception:
            return False
        return bool(state) and state.get("mode") not in ("observe", "detached", "record", "record-finish") \
            and state.get("timelineFrame") is not None

    def push_script(self, quiet=False):
        """Send the script to the service. When an edit touches frames already played, the service
        goes back and replays to the frame the game was on, so the game shows the edited inputs."""
        if not self.unsent or self.path is None or self.script_error or not self.in_control():
            return
        self.stop_playing()
        started = time.perf_counter()
        try:
            self.client.call("load", path=str(self.path))
        except Exception as error:
            self.note(f"脚本没能送进游戏：{error}", bad=True)
            return
        self.unsent = False
        self.fetch_trace()
        if not quiet:
            self.note(f"脚本已生效（{time.perf_counter() - started:.2f} 秒），游戏停在第 {self.frame} 帧。")
            self.refresh()

    def show_file(self):
        if self.path is None:
            self.file_label.configure(text="还没有脚本，直接在下面输入就行", fg=DIM)
        else:
            self.file_label.configure(text=f"{self.path.name}　共 {self.length} 帧　（自动保存）", fg=INK)

    def new_script(self):
        self.stop_playing()
        self.flush_edit()
        RUNS.mkdir(exist_ok=True)
        self.path = RUNS / f"script-{datetime.now():%Y%m%d-%H%M%S}.tas"
        self.path.write_text(NEW_SCRIPT, encoding="utf-8", newline="\n")
        self.set_editor(NEW_SCRIPT)
        self.show_file()
        self.unsent = True
        self.push_script()
        self.editor.mark_set("insert", "end")
        self.editor.focus_set()

    def enable_drop(self):
        """Let a .tas file be dragged from Explorer onto either window (needs the tkinterdnd2
        package; without it the windows simply do not accept drops)."""
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD
            TkinterDnD._require(self.root)
            for widget in (self.script_window, self.editor, self.gutter, self.info, self.panel):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.dropped)
        except Exception as error:
            self.note(f"拖放打开脚本不可用：{error}")

    def dropped(self, event):
        files = [Path(name) for name in self.root.tk.splitlist(event.data)]
        scripts = [path for path in files if path.suffix.lower() in (".tas", ".txt") and path.is_file()]
        if not scripts:
            self.note("拖进来的不是 .tas 脚本文件。", bad=True)
            return
        self.stop_playing()
        self.flush_edit()
        self.load(scripts[0])
        self.show_script_window()

    def open_file(self):
        self.stop_playing()
        self.flush_edit()
        name = filedialog.askopenfilename(parent=self.script_window, initialdir=str(RUNS),
                                          filetypes=[("输入脚本", "*.tas"), ("所有文件", "*.*")])
        if name:
            self.load(Path(name))

    def load(self, path):
        """Put a script file into the editor. Files in the older "1-4: Space L" form are shown as a
        frame list; the file itself is only rewritten once it is edited here."""
        try:
            text = path.read_text(encoding="utf-8-sig")
            if any(RANGE_LINE.match(line) for line in text.splitlines()):
                text = format_frames(parse_script(text))
            else:
                parse_frames(text)
            text = expand_text(text)            # one row per frame
        except (OSError, ValueError) as error:
            self.note(f"打不开 {path.name}：{error}", bad=True)
            return
        self.path = path
        self.set_editor(text)
        self.show_file()
        self.unsent = True
        self.push_script()
        if self.unsent:
            self.note(f"已打开 {path.name}。按小键盘 + 或“▶ 播放”时接管游戏，从第 1 行开始执行。")

    def save_as(self):
        self.flush_edit()
        name = filedialog.asksaveasfilename(parent=self.script_window, initialdir=str(RUNS), defaultextension=".tas",
                                            filetypes=[("输入脚本", "*.tas")])
        if not name:
            return
        text = self.editor.get("1.0", "end-1c")
        self.path = Path(name)
        try:
            self.path.write_text(text if text.endswith("\n") or not text else text + "\n", encoding="utf-8",
                                 newline="\n")
        except OSError as error:
            self.note(f"存不了：{error}", bad=True)
            return
        self.show_file()
        self.unsent = True
        self.push_script()

    # --- display -------------------------------------------------------------------------------
    def tick(self):
        # While playing follow the game closely; when idle still look twice a second, so frames
        # moved from the console or by another tool show up here too.
        self.drain_hotkeys()
        self.poll_cheat_keys()
        self.idle = getattr(self, "idle", 0) + 1
        if self.playing or self.recording or self.idle >= 8:
            self.idle = 0
            self.refresh()
        self.root.after(66, self.tick)

    def refresh(self):
        if self.client is None:
            return
        try:
            state = self.client.call("status")
        except Exception as error:
            self.note(f"读状态失败：{error}", bad=True)
            return
        if state.get("mode") == "detached":
            self.note("服务没有挂在游戏上。", bad=True)
            return
        try:
            self.game_window = int(state.get("gameWindow") or "0", 16) or None
        except ValueError:
            self.game_window = None
        self.set_version(state.get("game", "se"), state)
        self.overlay.attach(state.get("gamePid") if self.game == "se" else None, self.game_window)
        frozen = state.get("parked") and state.get("mode") not in ("observe", "record", "record-finish")
        self.overlay.verdict = ((state.get("last") or {}).get("combat") or {}).get("stepCheck") if frozen else None
        aids = state.get("cheats") or {}
        coverage = ""
        if aids.get("hp") or aids.get("kill"):
            coverage = f"敌人覆盖 {aids.get('enemiesCovered', 0)}/{aids.get('enemies', 0)}"
            if aids.get("skipped"):
                coverage += "　已跳过 " + "、".join(aids["skipped"])
        self.coverage.set(coverage)
        for name, value in aids.items():
            if name in self.cheat_vars:
                self.cheat_vars[name].set(bool(value))
        if state.get("mode") in ("record", "record-finish") or self.recording:
            frames = state.get("timelineFrame") or 0
            self.set_headline(f"● 录制中　{frames} 帧" + ("（已冻结，切回游戏继续）" if state.get("parked") else ""))
            if state.get("last"):
                self.show(state["last"], frames, headline=False)
            return
        if state.get("savesLost") and state.get("sceneEpoch") != getattr(self, "lost_epoch", None):
            self.lost_epoch = state.get("sceneEpoch")
            self.note(f"旧快照已清除（{state['savesLost']}）", bad=True)
        if state.get("mode") == "observe":
            self.control_button.configure(text="⏸ 接管")
            self.set_headline("你在操作游戏" + ("（已冻结，等你切回游戏）" if state.get("parked") else ""))
            # Also reached when the tool let go by itself (a door, a loading screen): the timeline
            # is over, so the script starts again from its first line at the next take-over.
            self.unsent = self.path is not None
            if self.frame:
                self.frame = 0
                self.highlight()
            return
        self.control_button.configure(text="⏵ 交还操作")
        self.show(state.get("last"), state.get("timelineFrame", 0))
        if state.get("sceneBlocked"):
            self.stop_playing()
            self.note("到切场景（读盘）的地方了，时间线不能跨过读盘。请后退几帧；要过门就按“交还操作”，"
                      "游戏回到接管那一刻后自己走过去。", bad=True)
        target = getattr(self, "play_target", None)
        if self.playing and target is not None and state.get("parked") and self.frame >= target:
            # The script has been played to its last frame; the game waits there.
            self.playing = self.sounding = False
            self.set_play_text("▶ 播放")
            self.note(f"脚本放完了，游戏停在第 {self.frame} 帧。再按播放从头重放；往下接着写就从这里继续放。")

    def set_version(self, game, state):
        reason = None
        if game == "dx9":
            reason = "混音恢复未验证"
        elif not state.get("guardVoices") or not (state.get("sound") or {}).get("ready"):
            reason = "声音保护未启用"
        elif not state.get("soundRouteReady", True):
            reason = "当前时间线未保护声音"
        if reason:
            self.sound_play.set(False)
        self.sound_button.configure(state="disabled" if reason else "normal",
                                    text=f"带音效（{reason}）" if reason else "播放带音效（从头放，无背景音乐）")
        legacy = game == "dx9" and state.get("playerProtocol", 0) < 10
        if self.game == game and self.legacy == legacy:
            return
        self.game, self.legacy = game, legacy
        self.root.title("DMC4 逐帧播放器 · " + LABELS[game] +
                        ("（旧服务，重新启动游戏后可录制和存读档）" if legacy else ""))
        for button in (self.record_button, self.check_button, self.save_button, self.state_button):
            button.configure(state="disabled" if legacy else "normal")
        if game == "se":
            self.overlay_button.configure(state="normal", text="F6 踩头方框")
        else:
            self.overlay_var.set(False)
            self.overlay.set(False)
            self.overlay_button.configure(state="disabled", text="F6 方框（屏幕投影未验证）")

    def show(self, row, frame, headline=True):
        changed = (frame or 0) != self.frame
        self.frame = frame or 0
        if changed and not self.recording:
            self.pad_to_frame()
            self.highlight()
            self.fetch_trace()
        total = max(self.length, self.frame, 1)
        for slider in (self.slider, self.slider2):
            slider.configure(to=total)
            if not self.dragging:
                slider.set(self.frame)
        if headline:
            self.set_headline(f"第 {self.frame} 帧" + (f" / {self.length}" if self.length else ""))
        row = row or {}             # frame 0 of a fresh timeline has no sampled frame behind it yet
        combat = row.get("combat") or {}
        player = combat.get("player")
        lines = [(f"键盘按住　{' '.join((row.get('keys') or {}).get('held') or []) or '—'}", None)]
        if not row:
            lines.append(("这是起点（第 0 帧），还没有执行任何一帧。按小键盘 + 走第 1 帧。", "dim"))
        elif self.game == "dx9":
            scene = row.get("scene") or {}
            lines.append((f"原版 DX9 · {scene.get('gameClass') or '场景未知'}　单帧 {row.get('deltaUnits')} 单位", None))
            if player:
                lines.append((f"动作 {player['action']}　动画 {player['motion']} @ {player['motionFrame']:.1f}"
                              f"　角色输入更新 {len(combat['inputUpdates'])} 次　生命 {player['hp']:.0f}", None))
                lines.append(("位置 ({:.0f}, {:.0f}, {:.0f})".format(*player["position"]), "dim"))
        elif not player:
            lines.append(("这一帧没有读到角色（不在战斗场景，或游戏弹了暂停画面）。", "hot"))
        else:
            b, d = player["buttons"], player["direction"]
            if not combat.get("inputUpdates"):
                lines.append(("角色这一帧没有读输入（游戏自己的暂停画面？按一帧 Escape 可关）", "hot"))
            lines.append((f"角色读到　按住 {bits(b['held'], BUTTONS)}　按下 {bits(b['pressed'], BUTTONS)}　"
                          f"松开 {bits(b['released'], BUTTONS)}　方向 {bits(d['held'], DIRECTIONS)}", None))
            lines.append((f"动作 {player['action']}　动画 {player['motion']} @ {player['motionFrame']:.1f}　"
                          f"{'地面' if player['grounded'] else '空中'}　踩敌冷却 {player['stepCooldown']:.0f}　"
                          f"生命 {player['hp']:.0f}", None))
            exceed = player.get("exceed")
            if exceed:
                mark = self.exceed_at(self.frame)
                text = {None: "这一帧不在红刀窗口",
                        ("max", False): "◆ 这一帧是 MAX 点：Q 写在这一行 = 充满 3 格",
                        ("ex", False): "◆ 这一帧在红刀窗口内：Q 写在这一行 = +1 格",
                        ("max", True): "★ 这一帧 MAX 红刀成功",
                        ("ex", True): "★ 这一帧红刀成功（+1 格）",
                        ("rev", True): "这一帧原地充能满了一格"}[mark]
                lines.append((f"红刀槽 {exceed['gauge']}/3　{text}", "ok" if mark else None))
            x, y, z = player["position"]
            lines.append((f"位置 ({x:.0f}, {y:.0f}, {z:.0f})　每帧推进 {player.get('workDelta') or 0:.2f} 单位", "dim"))
            if player.get("frozen") or (player.get("hitStopTimer") or 0) > 0:
                lines.append((f"命中停顿中　剩余 {player.get('hitStopTimer') or 0:.1f}", "hot"))
        lines.append(("", None))
        enemies = combat.get("enemies") or []
        if not enemies and player:
            lines.append(("没有敌人", "dim"))
        for index, enemy in enumerate(sorted(enemies, key=lambda item: item.get("distance") or 0), 1):
            hp = "?" if enemy.get("hp") is None else f"{enemy['hp']:.0f}"
            if self.game == "dx9":
                distance = enemy.get("distance")
                if distance is None and player:
                    distance = dist(player["position"], enemy["position"])
                distance_text = "?" if distance is None else f"{distance:.0f}"
                lines.append((f"敌人{index} {enemy['type']}　生命 {hp}　动作 {enemy['action']}　距离 {distance_text}", None))
                continue
            inside, can = enemy.get("inSphere"), enemy.get("canStep")
            text = (f"敌人{index} {enemy['type']}　生命 {hp}　距离 {enemy.get('distance') or 0:.0f}　"
                    f"在踩敌球内 {'是' if inside else '否'}　可踩 {'是' if can else '否'}"
                    f"{'　冻结' if enemy.get('frozen') else ''}")
            lines.append((text, "ok" if can else ("hot" if inside else None)))
        events = combat.get("events") or []
        if combat.get("enemyStep") or events:
            lines.append(("", None))
            lines.append((f"★ 这一帧触发了 Enemy Step　{events}", "ok"))
        self.panel.configure(state="normal")
        self.panel.delete("1.0", "end")
        for text, tag in lines:
            self.panel.insert("end", text + "\n", tag or ())
        self.panel.configure(state="disabled")

    def note(self, text, bad=False):
        self.status.configure(text=text, fg=WARN if bad else DIM)

    def close(self):
        self.stop_playing()
        self.root.destroy()


def main():
    root = tk.Tk()
    player = Player(root)
    if len(sys.argv) > 1:
        root.after(600, lambda: player.load(Path(sys.argv[1]).resolve()))
    root.mainloop()


if __name__ == "__main__":
    main()
