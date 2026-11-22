"""Boxes over the game window that mark where enemies can be stepped on (Enemy Step / jump cancel).

Read-only: every tick the enemies' step spheres are read from the game's memory, projected with
the game camera's own matrices, and drawn as squares on a transparent, click-through window that
sits exactly over the game's picture. A box is red when a jump would be an Enemy Step: the player's
step point (85 above the feet) is inside that sphere and both sides allow a step. It is yellow when
the point is in reach but the player may not step right now (on the ground, the first frames of a
jump, cooldown), green otherwise. While the game is frozen on a frame the colours are the answer
for that frame taken where the game decides it (`stepCheck`, before that frame's movement); the
positions after the frame can differ by a frame's worth of movement (seen 2026-11-21).

Addresses are for Steam build 24329835 (see docs/MAP.md): enemy list [0x118D450]+0x194, next
+0x14; sphere table [E+0x1818] (32 bytes each: joint, radius, local centre at +16), joint remap
[E+0x4C8], joint matrices [E+0x4C4] + index*160 + 16; camera sCamera [0x118D474] with the view
matrix at +0xC0 and the projection at +0x100 (row vectors, found 2026-11-20).
"""
import ctypes
import struct
import tkinter as tk
from ctypes import wintypes

KEY = "#010203"                  # the colour that is made transparent
FREE, INSIDE, BLOCKED = "#35e06a", "#ff4040", "#ffd23a"   # yellow: in reach, but no step allowed now
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
user32.GetWindowLongW.restype = ctypes.c_long


class Overlay:
    def __init__(self, root):
        self.root = root
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-transparentcolor", KEY)
        self.window.configure(bg=KEY)
        self.canvas = tk.Canvas(self.window, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.window.withdraw()
        self.window.update_idletasks()
        # Clicks go through to the game, and the window never takes the focus.
        frame = user32.GetParent(self.window.winfo_id()) or self.window.winfo_id()
        style = user32.GetWindowLongW(frame, -20)
        user32.SetWindowLongW(frame, -20, style | 0x80000 | 0x20 | 0x08000000 | 0x80)
        self.enabled = False
        self.pid = self.handle = self.hwnd = None
        self.types = {}
        self.count = 0           # boxes drawn last tick, for the status line
        # While the game is frozen on a frame: that frame's `stepCheck` from the agent (what the
        # game itself would have answered to a jump on that frame). None while the game runs live.
        self.verdict = None

    # --- game memory -----------------------------------------------------------------------
    def attach(self, pid, hwnd):
        if pid != self.pid:
            if self.handle:
                kernel32.CloseHandle(self.handle)
            self.handle = kernel32.OpenProcess(0x0410, False, pid) if pid else None
            self.pid, self.types = pid, {}
        self.hwnd = hwnd

    def read(self, address, size):
        buffer = (ctypes.c_char * size)()
        got = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(self.handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(got)):
            return b""
        return bytes(buffer[:got.value])

    def u32(self, address):
        data = self.read(address, 4)
        return struct.unpack("<I", data)[0] if len(data) == 4 else 0

    def floats(self, address, count):
        data = self.read(address, 4 * count)
        return struct.unpack(f"<{count}f", data) if len(data) == 4 * count else None

    def is_enemy(self, pointer):
        table = self.u32(pointer)
        if table not in self.types:
            names = []
            code = self.read(self.u32(table + 16), 6)
            if len(code) == 6 and code[0] == 0xB8 and code[5] == 0xC3:
                dti = struct.unpack("<I", code[1:5])[0]
                while dti and len(names) < 32:
                    names.append(self.read(self.u32(dti + 4), 32).split(b"\0")[0])
                    dti = self.u32(dti + 0x10)
            self.types[table] = b"uEnemy" in names
        return self.types[table]

    def spheres(self):
        """[(centre, radius, player inside)] of every enemy's step spheres."""
        found = []
        player = self.u32(self.u32(0x1154B34) + 0x24)
        position = self.floats(player + 0x40, 3) if player else None
        # The player's side of the game's check (0x92A890): steps enabled, the action allows one
        # (only in the air, and not in the first frames of a jump), cooldown over.
        cooldown, limit = (self.floats(player + 0x25E4, 1), self.floats(0xF87BD4, 1)) if player else (None, None)
        allowed = bool(player and self.read(player + 0x25F5, 1) == b"\x01" and self.u32(player + 0x1A64) & 2
                       and cooldown and limit and cooldown[0] <= limit[0])
        enemy, seen = self.u32(self.u32(0x118D450) + 0x194), set()
        while enemy and enemy not in seen and len(seen) < 256:
            seen.add(enemy)
            if self.is_enemy(enemy):
                # The enemy's own conditions in the game's check (0x5EEEF0), without the sphere mask.
                state = self.u32(enemy + 0xC)
                steppable = bool(self.read(enemy + 0x1820, 1) not in (b"", b"\0") and state & 7 == 2 and state & 0x400
                                 and not self.u32(enemy + 0x11D8) & 0x80)
                table, joints, remap = self.u32(enemy + 0x1818), self.u32(enemy + 0x4C4), self.u32(enemy + 0x4C8)
                data = self.read(table, 32 * 32) if table and joints and remap else b""
                for index in range(len(data) // 32):
                    joint, radius = struct.unpack_from("<if", data, index * 32)
                    if joint == -1:
                        break
                    local = struct.unpack_from("<3f", data, index * 32 + 16)
                    slot = self.read(remap + (joint & 255), 1)
                    if not slot or slot[0] == 255:
                        continue
                    m = self.floats(joints + slot[0] * 160 + 16, 16)
                    if not m:
                        continue
                    v = [local[0] * m[k] + local[1] * m[4 + k] + local[2] * m[8 + k] + m[12 + k] for k in range(4)]
                    if v[3] == 0:
                        continue
                    centre = [v[k] / v[3] for k in range(3)]
                    inside = bool(position) and (centre[0] - position[0]) ** 2 + (centre[1] - position[1] - 85) ** 2 \
                        + (centre[2] - position[2]) ** 2 < radius * radius
                    colour = FREE if not (inside and steppable) else INSIDE if allowed else BLOCKED
                    judged = (self.verdict or {}).get("enemies", {}).get(f"{enemy:#x}")
                    if judged and judged.get("inside") and index < len(judged["inside"]):
                        # The game's own answer for the frame on screen, taken at the moment it
                        # decides (before that frame's movement), not the positions afterwards.
                        hit = judged["inside"][index] and judged.get("can")
                        colour = FREE if not hit else INSIDE if self.verdict.get("allowed") else BLOCKED
                    found.append((centre, radius, colour))
            enemy = self.u32(enemy + 0x14)
        return found

    # --- drawing ---------------------------------------------------------------------------
    def set(self, enabled):
        self.enabled = enabled
        if not enabled:
            self.window.withdraw()

    def tick(self):
        if not self.enabled or not self.handle or not self.hwnd:
            return
        if not user32.IsWindowVisible(self.hwnd) or user32.IsIconic(self.hwnd):
            self.window.withdraw()
            return
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        corner = wintypes.POINT(0, 0)
        user32.ClientToScreen(self.hwnd, ctypes.byref(corner))
        width, height = rect.right, rect.bottom
        if width < 16 or height < 16:
            return
        self.window.geometry(f"{width}x{height}+{corner.x}+{corner.y}")
        self.window.deiconify()
        self.window.attributes("-topmost", True)
        camera = self.u32(0x118D474)
        view = self.floats(camera + 0xC0, 16) if camera else None
        projection = self.floats(camera + 0x100, 16) if camera else None
        self.canvas.delete("all")
        self.count = 0
        if not view or not projection:
            return
        for centre, radius, colour in self.spheres():
            x, y, z = (centre[0] * view[k] + centre[1] * view[4 + k] + centre[2] * view[8 + k] + view[12 + k]
                       for k in range(3))
            depth = -z                       # the camera looks down its negative z axis
            if depth < 1:
                continue
            px = (x * projection[0] / depth * 0.5 + 0.5) * width
            py = (0.5 - y * projection[5] / depth * 0.5) * height
            # A small square on the JC point itself (the centre of the step sphere), not the whole
            # reach of the sphere: the user asked for the point. A little larger when close.
            half_x = half_y = min(22, max(9, 14 * 600 / depth))
            if px < 0 or px > width or py < 0 or py > height:
                continue
            # Only where the game's picture is really what is on screen there: a box must not float
            # over another program (or over the tool's own windows) that covers the game.
            spot = wintypes.POINT(int(corner.x + min(max(px, 0), width - 1)), int(corner.y + min(max(py, 0), height - 1)))
            if user32.GetAncestor(user32.WindowFromPoint(spot), 2) != self.hwnd:
                continue
            self.canvas.create_rectangle(px - half_x, py - half_y, px + half_x, py + half_y,
                                         outline=colour, width=2 if colour == FREE else 3)
            self.count += 1
