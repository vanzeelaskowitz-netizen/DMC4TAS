"""Inspect the installed DMC4SE PE32 image, using file-backed RVAs only."""
from collections import Counter
from hashlib import sha256
import json
import math
from pathlib import Path
import struct

GAME = "DevilMayCry4SpecialEdition.exe"
APP_ID = "329050"
CHOSEN = Path(__file__).resolve().parents[1] / "game_path.txt"
# Where the user said each build's executable is (written by start_game, never published).
CHOSEN_FILES = {"se": CHOSEN, "dx9": CHOSEN.with_name("game_path_dx9.txt")}
GAME_FILES = {"se": (GAME, "鬼泣4特别版"), "dx9": ("DevilMayCry4_DX9.exe", "鬼泣4原版 DX9")}


def choose_game(game="se", title=None):
    """Ask the user where the game is (file dialog). None when cancelled; remember() stores the answer."""
    import tkinter
    from tkinter import filedialog
    filename, label = GAME_FILES[game]
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        picked = filedialog.askopenfilename(
            parent=root, title=title or f"找不到游戏，请选择 {filename}",
            filetypes=[(label, filename), ("程序", "*.exe")])
    finally:
        root.destroy()
    return Path(picked) if picked else None


def remember(game, path):
    CHOSEN_FILES[game].write_text(str(path) + "\n", encoding="utf-8")


def forget(game):
    try:
        CHOSEN_FILES[game].unlink()
    except FileNotFoundError:
        pass


def find_game(game="se"):
    """Resolve the selected build's executable, or return its bare process name."""
    import os
    import re
    filename, app, variable = {
        "se": (GAME, APP_ID, "DMC4SE_EXE"),
        "dx9": ("DevilMayCry4_DX9.exe", "45700", "DMC4_DX9_EXE"),
    }[game]
    chosen = os.environ.get(variable)
    if chosen:
        return Path(chosen)
    try:                                       # picked in the file dialog of start_game
        remembered = Path(CHOSEN_FILES[game].read_text(encoding="utf-8").strip())
        if remembered.is_file():
            return remembered
    except OSError:
        pass
    roots = []
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
            for value in ("SteamPath", "InstallPath"):
                try:
                    with winreg.OpenKey(hive, key) as handle:
                        roots.append(Path(winreg.QueryValueEx(handle, value)[0]))
                except OSError:
                    pass
    except ImportError:
        pass
    libraries = list(roots)
    for root in roots:
        try:
            text = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        libraries += [Path(item.replace("\\\\", "\\")) for item in re.findall(r'"path"\s+"([^"]+)"', text)]
    for library in libraries:
        apps = library / "steamapps"
        manifest = apps / f"appmanifest_{app}.acf"
        if manifest.is_file():
            folder = "Special Edition"
            if game == "dx9":
                try:
                    contents = manifest.read_text(encoding="utf-8-sig", errors="replace")
                except OSError:
                    continue
                install = re.search(r'"installdir"\s+"([^"]+)"', contents)
                if not install:
                    continue
                folder = install[1]
            candidate = apps / "common" / folder / filename
            if candidate.exists():
                return candidate
    return Path(filename)


EXE = find_game()


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


class Image:
    def __init__(self, path=EXE):
        self.data = path.read_bytes()
        pe = u32(self.data, 0x3c)
        if self.data[pe:pe+4] != b"PE\0\0":
            raise ValueError("Expected PE signature")
        self.machine, count = struct.unpack_from("<HH", self.data, pe+4)
        opt = pe + 24
        if struct.unpack_from("<H", self.data, opt)[0] != 0x10b:
            raise ValueError("Expected PE32")
        self.base = u32(self.data, opt+28)
        self.entry = u32(self.data, opt+16)
        self.flags = struct.unpack_from("<H", self.data, opt+70)[0]
        self.dirs = list(struct.iter_unpack("<II", self.data[opt+96:opt+96+8*min(16,u32(self.data,opt+92))]))
        self.sections = []
        first = opt + struct.unpack_from("<H", self.data, pe+20)[0]
        for i in range(count):
            pos = first + 40*i
            name = self.data[pos:pos+8].rstrip(b"\0").decode("ascii", "backslashreplace")
            vs, rva, rs, raw = struct.unpack_from("<4I", self.data, pos+8)
            flags = u32(self.data, pos+36)
            if rs and raw+rs > len(self.data):
                raise ValueError(f"Truncated section {name}")
            block = self.data[raw:raw+rs]
            entropy = sum(-n/rs * math.log2(n/rs) for n in Counter(block).values())
            self.sections.append(dict(name=name, rva=rva, virtual_size=vs, raw_size=rs, raw_offset=raw,
                flags=hex(flags), permissions="".join(c for c,b in zip("RWX",(30,31,29)) if flags & (1<<b)),
                entropy=round(entropy,4)))

    def offset(self, rva):
        for s in self.sections:
            rel = rva - s["rva"]
            if 0 <= rel < s["raw_size"]:
                return s["raw_offset"]+rel
        raise ValueError(f"RVA {rva:#x} has no file bytes")

    def string(self, rva):
        start = self.offset(rva)
        return self.data[start:self.data.index(b"\0", start)].decode("ascii", "replace")

    def imports(self, delay=False):
        rva, size = self.dirs[13 if delay else 1]
        result = []
        if not rva:
            return result
        width = 32 if delay else 20
        for i in range(0, size, width):
            d = struct.unpack_from("<8I" if delay else "<5I", self.data, self.offset(rva+i))
            if not any(d):
                break
            if delay:
                bias = 0 if d[0] & 1 else self.base
                name, iat, lookup = (d[x]-bias for x in (1,3,4))
            else:
                lookup, _, _, name, iat = d
                lookup = lookup or iat
            entries = []
            k = 0
            while (value := u32(self.data, self.offset(lookup+4*k))):
                symbol = f"#{value & 65535}" if value >> 31 else self.string(value+2-(bias if delay else 0))
                entries.append(dict(symbol=symbol, iat_rva=iat+4*k))
                k += 1
            result.append(dict(dll=self.string(name), entries=entries))
        return result

    def relocations(self):
        rva, size = self.dirs[5]
        end = rva+size
        while rva < end:
            pos = self.offset(rva)
            page, length = struct.unpack_from("<II", self.data, pos)
            if length < 8 or length % 2 or rva+length > end:
                raise ValueError("Invalid relocation block")
            for (value,) in struct.iter_unpack("<H", self.data[pos+8:pos+length]):
                if value >> 12:
                    yield value >> 12, page+(value & 4095)
            rva += length

    def report(self):
        return dict(path=str(EXE), sha256=sha256(self.data).hexdigest(), size=len(self.data),
            machine=hex(self.machine), image_base=hex(self.base), entry_rva=hex(self.entry),
            dll_characteristics=hex(self.flags), dynamic_base=bool(self.flags & 64),
            directories=self.dirs, sections=self.sections, imports=self.imports(),
            delayed=self.imports(True), reloc_types=Counter(k for k,_ in self.relocations()),
            markers={name:self.data.lower().count(name.encode().lower()) for name in
                     ("SteamStub",".bind","Enigma Protector",".enigma","UPX0","UPX1","VMProtect")})


if __name__ == "__main__":
    print(json.dumps(Image().report(), indent=2))
