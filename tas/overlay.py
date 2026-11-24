"""Read-only, click-through Enemy Step markers over the game client."""
import ctypes
from ctypes import wintypes
import struct
import tkinter as tk

from build_profile import profiles
from camera import project

KEY = "#010203"                  # the colour that is made transparent
FREE, INSIDE, BLOCKED = "#35e06a", "#ff4040", "#ffd23a"   # yellow: in reach, but no step allowed now
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
user32.GetWindowLongW.restype = ctypes.c_long


def visible_mask(mask, balls, threshold):
    """The DX9 helper keeps the higher top when two spheres overlap in XZ."""
    for i, (index, a, radius, _) in enumerate(balls):
        if mask & (1 << index):
            for other, b, other_radius, _ in balls[i + 1:]:
                if (a[0]-b[0])**2 + (a[2]-b[2])**2 < threshold:
                    lower = other if b[1] + other_radius <= a[1] + radius else index
                    mask &= ~(1 << lower)
    return mask


class Overlay:
    def __init__(self, root):
        self.profiles = {p["game"]: p for p in profiles()}
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
    def attach(self, game, pid, hwnd):
        self.build = self.profiles[game]
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

    def enemy_balls(self, enemy, position, step):
        table, joints, remap = (self.u32(enemy + step[key]) for key in ("table", "joints", "remap"))
        data = self.read(table, 32 * 32) if table and joints and remap else b""
        balls = []
        for index in range(len(data) // 32):
            joint, radius = struct.unpack_from("<if", data, index * 32)
            if joint == -1:
                break
            local = struct.unpack_from("<3f", data, index * 32 + 16)
            slot = self.read(remap + (joint & 255), 1)
            if not slot or slot[0] == 255:
                continue
            m = self.floats(joints + slot[0] * step["stride"] + step["matrix"], 16)
            if not m:
                continue
            v = [local[0] * m[k] + local[1] * m[4 + k] + local[2] * m[8 + k] + m[12 + k] for k in range(4)]
            if v[3] == 0:
                continue
            centre = [v[k] / v[3] for k in range(3)]
            inside = bool(position) and (centre[0] - position[0]) ** 2 + (centre[1] - position[1] - 85) ** 2 \
                + (centre[2] - position[2]) ** 2 < radius * radius
            balls.append((index, centre, radius, inside))
        return balls

    def spheres(self):
        """Read world centres, radii and colours for the active build."""
        found = []
        b = self.build
        step, battle = b["step"], b["battle"]
        player = self.u32(self.u32(0x400000 + b["roots"]["mediator"]) + b["scene"]["player"])
        position = self.floats(player + battle["position"], 3) if player else None
        cooldown = self.floats(player + battle["cooldown"], 1) if player else None
        limit = self.floats(0x400000 + step["limit"], 1)
        allowed = bool(player and self.read(player + step["enabled"], 1) == b"\x01"
                       and self.u32(player + step["flags"]) & 2 and cooldown and limit and cooldown[0] <= limit[0])
        if allowed and step["skill"] is not None:
            controls = b["controls"]
            character = self.u32(player + controls["character"])
            save = self.u32(0x400000 + controls["save"])
            allowed = character < len(step["skill"]) and self.read(save + step["skill"][character], 1) == b"\x01"
        enemy = self.u32(self.u32(0x400000 + b["roots"]["unit"]) + battle["enemyLine"])
        seen = set()
        while enemy and enemy not in seen and len(seen) < 256:
            seen.add(enemy)
            if self.is_enemy(enemy):
                state = self.u32(enemy + step["enemyState"])
                steppable = bool(self.read(enemy + step["enemyEnabled"], 1) not in (b"", b"\0") and state & 7 == 2 and state & 0x400
                                 and not self.u32(enemy + step["enemyFlags"]) & 0x80)
                balls = self.enemy_balls(enemy, position, step)
                mask = -1
                if step["overlapLimit"] is not None:
                    threshold = self.floats(0x400000 + step["overlapLimit"], 1)
                    if threshold:
                        mask = visible_mask(self.u32(enemy + step["mask"]), balls, threshold[0])
                judged = (self.verdict or {}).get("enemies", {}).get(f"{enemy:#x}")
                for index, centre, radius, inside in balls:
                    hit = inside and steppable and mask & (1 << index)
                    permitted = allowed
                    if judged and judged.get("inside") and index < len(judged["inside"]):
                        # The game's own answer for the frame on screen, taken at the moment it
                        # decides (before that frame's movement), not the positions afterwards.
                        hit = judged["inside"][index] and judged.get("can") and (judged.get("mask", mask) & (1 << index))
                        permitted = self.verdict["allowed"]
                    colour = (INSIDE if permitted else BLOCKED) if hit else FREE
                    found.append((centre, radius, colour))
            enemy = self.u32(enemy + b["scene"]["next"])
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
        camera = self.u32(0x400000 + self.build["camera"]["slot"])
        view = self.floats(camera + self.build["camera"]["view"], 16) if camera else None
        projection = self.floats(camera + self.build["camera"]["projection"], 16) if camera else None
        self.canvas.delete("all")
        self.count = 0
        if not view or not projection:
            return
        for centre, radius, colour in self.spheres():
            point = project(centre, view, projection)
            if point is None:
                continue
            px, py, depth = point["x"] * width, point["y"] * height, point["depth"]
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
