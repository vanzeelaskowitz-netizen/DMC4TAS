"""Expand inclusive, contiguous 1-based ranges to held/pressed/released.

UTF-8 format: "1-4: Space L", "5: J", "6: -" (release all).
Whitespace between names, # for comments. Optional "@key NAME DIK VK"
declarations accept decimal or 0x-prefixed codes, including layout overrides.
Key names are case-sensitive non-whitespace tokens. Frame sets describe the
complete keyboard state. frames(rows) also accepts unbound symbolic keys for
offline editing; file interchange requires a known or declared mapping.
"""
from dataclasses import dataclass, field
from pathlib import Path
import re

if __package__:
    from .keycodes import KEY_CODES, KeyCodes
    from .pad import canonical as pad_token, is_pad, state as pad_state
    from tas.lang import L
else:
    from keycodes import KEY_CODES, KeyCodes
    from pad import canonical as pad_token, is_pad, state as pad_state
    from lang import L


KEYS = frozenset(KEY_CODES)
EXAMPLE = [(1, 4, ["Space", "L"]), (5, 5, ["Space", "L", "J"]),
           (6, 6, ["Space", "L"]), (7, 7, ["Space", "L", "K"]), (8, 8, [])]


def check_name(name):
    if not isinstance(name, str) or name == "-" or not re.fullmatch(r"[^\s:#]+", name):
        raise ValueError(f"Invalid key name: {name!r}")
    return name


def validate_row(start, end, keys, expected):
    if type(start) is not int or type(end) is not int or start != expected or end < start:
        raise ValueError(f"Expected range starting at {expected}, got {start!r}-{end!r}")
    if isinstance(keys, str):
        raise ValueError("Supply a collection of key names, not a string")
    return frozenset(check_name(name) for name in keys)


def frames(rows):
    previous = frozenset()
    expected = 1
    for start, end, keys in rows:
        held = validate_row(start, end, keys, expected)
        for frame in range(start, end + 1):
            yield frame, held, held - previous, previous - held
            previous = held
        expected = end + 1


@dataclass
class Timeline:
    rows: list = field(default_factory=list)
    bindings: dict[str, KeyCodes] = field(default_factory=dict)

    def codes(self, name):
        if name in self.bindings:
            return self.bindings[name]
        if name in KEY_CODES:
            return KEY_CODES[name]
        raise ValueError(f"No mapping for {name!r}; declare @key NAME DIK VK")


def validate_mapping(script):
    for name, codes in script.bindings.items():
        check_name(name)
        if len(codes) != 2 or any(type(x) is not int or not 1 <= x <= 255 for x in codes):
            raise ValueError(f"{name}: DIK and VK must each be in 1..255")
    # A physical key needs one spelling throughout a timeline so frame edges
    # remain unambiguous. Distinct DIKs may legitimately share a virtual key.
    used = {}
    expected = 1
    for start, end, keys in script.rows:
        for name in validate_row(start, end, keys, expected):
            if is_pad(name):
                continue
            dik, _ = script.codes(name)
            if used.setdefault(dik, name) != name:
                raise ValueError(f"{name} and {used[dik]} share DIK {dik:#x}; use one spelling")
        expected = end + 1


def wire_keys(script, keys):
    """One frame as the agent takes it: keyboard [DIK, VK] pairs and the pad state (pad.py)."""
    return {"keys": sorted(tuple(script.codes(name)) for name in keys if not is_pad(name)), "pad": pad_state(keys)}


RANGE_LINE = re.compile(r"\s*[0-9]+\s*(-\s*[0-9]+\s*)?:")
NO_KEYS = {"-", "."}


ALIASES = {"shift": "LeftShift", "ctrl": "LeftCtrl", "alt": "LeftAlt", "esc": "Escape"}


def canonical_names(bindings):
    """Lower-case spelling -> the one accepted spelling, for forgiving input in frame lists."""
    return ALIASES | {name.casefold(): name for name in (*KEY_CODES, *bindings)}


def token_name(token, names):
    """The accepted spelling of a key or pad token (see pad.py), "" for a pad value at rest, None
    for an unknown token. A malformed pad value raises ValueError."""
    name = names.get(token.casefold())
    return name if name is not None else pad_token(token)


def frame_line(line, names):
    """One line of a frame list -> (count, keys), or None for a line that is only a comment.

    "S W" is one frame with S and W held. "10 Space J" holds them for 10 frames. A number alone
    ("5") or "-" is that many frames with nothing pressed, and so is an empty line (one frame):
    in the editor every row is a frame. Commas work as separators too.
    """
    tokens = line.partition("#")[0].replace(",", " ").split()
    if not tokens:
        return None if "#" in line else (1, frozenset())
    count = 1
    if tokens[0].isdigit():
        count = int(tokens[0])
        tokens = tokens[1:]
        if not 1 <= count <= 100000:
            raise ValueError(L("帧数要在 1 到 100000 之间", "The frame count must be between 1 and 100000"))
    keys = set()
    for token in tokens:
        if token in NO_KEYS:
            continue
        name = token_name(token, names)
        if name is None:
            raise ValueError(L(f"不认识的键名 {token}", f"Unknown key name {token}"))
        if name:
            keys.add(name)
    return count, frozenset(keys)


def written_lines(text):
    """The rows of a frame list. Every row that ends in a line break counts, also an empty one
    (a frame with nothing pressed), so a recording that ends in idle frames keeps its length.
    What follows the last line break counts only when something is written there: in an editor
    that is the row the cursor waits on."""
    lines = text.removeprefix("﻿").split("\n")
    if lines and not lines[-1].strip():
        lines.pop()
    return lines


def parse_frames(text):
    """Frame list format (the one the player's editor uses): one line per stretch of frames.

        # comment
        S W          one frame, S and W held
        J            the next frame, only J
        10 Space K   ten frames
        5            five frames with nothing pressed (also: -)
                     an empty line is one frame with nothing pressed

    Lines follow each other in time, so inserting or deleting a line shifts everything after it.
    Key names are not case-sensitive. To
    press a digit key for one frame write "1 5", because a number alone means blank frames.
    """
    script = Timeline()
    lines = written_lines(text)
    for number, raw in enumerate(lines, 1):
        parts = raw.partition("#")[0].split()
        if parts and parts[0] == "@key":
            try:
                if len(parts) != 4:
                    raise ValueError("Expected @key NAME DIK VK")
                _, name, dik, vk = parts
                check_name(name)
                if name in script.bindings:
                    raise ValueError(f"Duplicate binding for {name}")
                script.bindings[name] = KeyCodes(int(dik, 0), int(vk, 0))
            except ValueError as err:
                raise ValueError(f"Line {number}: {err}") from err
    names = canonical_names(script.bindings)
    frame = 1
    for number, raw in enumerate(lines, 1):
        if raw.partition("#")[0].split()[:1] == ["@key"]:
            continue
        try:
            parsed = frame_line(raw, names)
        except ValueError as err:
            raise ValueError(f"Line {number}: {err}") from err
        if parsed is None:
            continue
        count, keys = parsed
        if script.rows and script.rows[-1][2] == keys:
            start, _, same = script.rows[-1]
            script.rows[-1] = (start, frame + count - 1, same)
        else:
            script.rows.append((frame, frame + count - 1, keys))
        frame += count
    validate_mapping(script)
    return script


def binding_names(lines):
    names = {}
    for raw in lines:
        parts = raw.partition("#")[0].split()
        if len(parts) == 4 and parts[0] == "@key":
            names[parts[1]] = None
    return canonical_names(names)


def expand_line(line, names):
    """A line written with a count ("5 S") -> one row per frame (["S"] * 5), the way the editor
    lists a script. None for a line that already is a single row, a comment, or does not parse."""
    code, _, comment = line.partition("#")
    tokens = code.replace(",", " ").split()
    if not tokens or tokens[0] == "@key" or not tokens[0].isdigit():
        return None
    try:
        count, _ = frame_line(line, names)
    except ValueError:
        return None
    keys = []
    for token in tokens[1:]:
        name = token_name(token, names)
        if name and name not in keys:
            keys.append(name)
    row = " ".join(keys)
    if keys and keys[0].isdigit():
        row = "1 " + row            # a row may not start with a digit key: that would read as a count
    rows = [row] * count
    if comment:
        rows[0] = f"{row or '-'} #{comment}"
    # A count of 1 ("   1 A J", the way takes are saved) is dropped too: the row is just "A J".
    return None if rows == [line] else rows


def expand_lines(text, skip=None):
    """[(line number, rows)] for every line of a frame list that stands for more than one frame,
    except line `skip` (the one being typed)."""
    lines = text.removeprefix("﻿").split("\n")
    names = binding_names(lines)
    found = []
    for number, line in enumerate(lines, 1):
        rows = expand_line(line, names) if number != skip else None
        if rows:
            found.append((number, rows))
    return found


def expand_text(text):
    """A frame list with every frame on a row of its own."""
    lines = text.removeprefix("﻿").split("\n")
    for number, rows in reversed(expand_lines(text)):
        lines[number - 1:number] = rows
    return "\n".join(lines)


def format_frames(script):
    """Write a timeline as a frame list."""
    validate_mapping(script)
    lines = [f"@key {name} 0x{dik:02X} 0x{vk:02X}" for name, (dik, vk) in sorted(script.bindings.items())]
    for start, end, keys in script.rows:
        lines.append(f"{end - start + 1:>4} {' '.join(sorted(set(keys)))}".rstrip())
    return "".join(line + "\n" for line in lines)


def line_frames(text):
    """For an editor: per text line, (first frame, count) of the frames it produces, None for lines
    that produce none, or the error message for a line that does not parse. Lines are split on
    "\\n" only, the way a text widget counts them; the empty lines at the end are left out."""
    lines = written_lines(text)
    bindings = {}
    for raw in lines:
        parts = raw.partition("#")[0].split()
        if len(parts) == 4 and parts[0] == "@key":
            bindings[parts[1]] = None
    names = canonical_names(bindings)
    result, frame = [], 1
    for raw in lines:
        if raw.partition("#")[0].split()[:1] == ["@key"]:
            result.append(None)
            continue
        try:
            parsed = frame_line(raw, names)
        except ValueError as err:
            result.append(str(err))
            continue
        if parsed is None:
            result.append(None)
        else:
            result.append((frame, parsed[0]))
            frame += parsed[0]
    return result


def parse_script(text):
    """Either format: absolute ranges ("1-4: Space L") or a frame list (see parse_frames)."""
    content = [line for line in text.removeprefix("\ufeff").splitlines() if line.partition("#")[0].strip()]
    if any(RANGE_LINE.match(line) for line in content):
        return parse_ranges(text)
    return parse_frames(text)


def parse_ranges(text):
    script = Timeline()
    expected = 1
    for number, raw in enumerate(text.removeprefix("\ufeff").splitlines(), 1):
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        try:
            parts = line.split()
            if parts[0] == "@key":
                if len(parts) != 4:
                    raise ValueError("Expected @key NAME DIK VK")
                _, name, dik, vk = parts
                check_name(name)
                if name in script.bindings:
                    raise ValueError(f"Duplicate binding for {name}")
                script.bindings[name] = KeyCodes(int(dik,0), int(vk,0))
                continue
            match = re.fullmatch(r"([0-9]+)(?:\s*-\s*([0-9]+))?\s*:\s*(.+)", line)
            if not match:
                raise ValueError("Expected START[-END]: KEY KEY or START[-END]: -")
            start, end, names = match.groups()
            start, end = int(start), int(end or start)
            tokens = [] if names == "-" else [pad_token(n) if pad_token(n) is not None else n for n in names.split()]
            held = validate_row(start, end, [n for n in tokens if n], expected)
            script.rows.append((start,end,held))
            expected = end+1
        except ValueError as err:
            raise ValueError(f"Line {number}: {err}") from err
    validate_mapping(script)
    return script


def format_script(script):
    validate_mapping(script)
    lines = [f"@key {name} 0x{dik:02X} 0x{vk:02X}" for name,(dik,vk) in sorted(script.bindings.items())]
    for start, end, keys in script.rows:
        span = str(start) if end == start else f"{start}-{end}"
        lines.append(f"{span}: {' '.join(sorted(set(keys))) or '-'}")
    return "".join(line+"\n" for line in lines)


def read_script(path):
    return parse_script(Path(path).read_text(encoding="utf-8-sig"))


def write_script(path, script):
    text = format_script(script)
    Path(path).write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    result = list(frames(EXAMPLE))
    assert result[4][2:] == ({"J"}, set())
    assert result[5][1:] == ({"Space", "L"}, set(), {"J"})
    assert result[6][2:] == ({"K"}, set())
    assert result[7][3] == {"Space", "L", "K"}
    print("8 frames verified")
