"""Gamepad tokens in scripts (original DX9 build only).

A frame row may hold pad tokens next to keyboard keys:

    PadA PadB PadX PadY PadLB PadRB PadBack PadStart PadL3 PadR3 PadUp PadDown PadLeft PadRight
    PadLS=x/y  PadRS=x/y    left / right stick, x right and y up, each -1..1
    PadLT PadRT             trigger fully pressed; PadLT=v / PadRT=v for v in 0..1

The tool then owns the game's pad for that frame (sDevil4Pad's first cPadInfo): a frame without
pad tokens is a pad at rest. Takes record the pad the same way. The state sent to the agent is
[buttons, lx, ly, rx, ry, lt, rt]; the game's own bits for triggers and stick directions are
derived in the agent from the pad's dead zones, as the game does.
"""
import re
import struct

BUTTONS = {"PadBack": 0x1, "PadL3": 0x2, "PadR3": 0x4, "PadStart": 0x8, "PadUp": 0x10, "PadRight": 0x20,
           "PadDown": 0x40, "PadLeft": 0x80, "PadLB": 0x100, "PadRB": 0x200, "PadY": 0x1000, "PadB": 0x2000,
           "PadA": 0x4000, "PadX": 0x8000}
BUTTON_MASK = sum(BUTTONS.values())
_FOLDED = {name.casefold(): name for name in BUTTONS}
_ANALOG = re.compile(r"pad(ls|rs|lt|rt)(?:=([^/]+)(?:/(.+))?)?", re.IGNORECASE)


def short(value):
    """The shortest decimal that reads back as the same float32."""
    exact = struct.unpack("<f", struct.pack("<f", value))[0]
    for digits in range(1, 10):
        text = f"{exact:.{digits}g}"
        if struct.unpack("<f", struct.pack("<f", float(text)))[0] == exact:
            return "0" if text in ("0", "-0") else text
    return repr(exact)


def canonical(token):
    """The accepted spelling of a pad token, "" for a pad at rest written out (PadLS=0/0), or None
    when the token is not a pad token at all."""
    folded = token.casefold()
    if folded in _FOLDED:
        return _FOLDED[folded]
    match = _ANALOG.fullmatch(token)
    if not match:
        return None
    part, first, second = match.group(1).upper(), match.group(2), match.group(3)
    stick = part in ("LS", "RS")
    try:
        if stick:
            if first is None or second is None:
                raise ValueError
            values = [float(first), float(second)]
        else:
            if second is not None:
                raise ValueError
            values = [1.0 if first is None else float(first)]
    except ValueError:
        raise ValueError(f"Pad{part} needs {'x/y' if stick else 'a value'}: {token}") from None
    # A stick's raw -32768 reads as -1.00003 in the game, so the bounds allow that much.
    low = -1.0001 if stick else 0.0
    if not all(low <= v <= 1.0001 for v in values):
        raise ValueError(f"Pad{part} values must lie in {'-1' if stick else '0'}..1: {token}")
    if not any(values):
        return ""
    if not stick and values[0] == 1.0:
        return f"Pad{part}"
    return f"Pad{part}=" + "/".join(short(v) for v in values)


def is_pad(name):
    return name.startswith("Pad") and canonical(name) == name


def state(names):
    """[buttons, lx, ly, rx, ry, lt, rt] for the pad tokens among `names`, or None without any."""
    pad = [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    found = False
    for name in names:
        if not is_pad(name):
            continue
        found = True
        if name in BUTTONS:
            pad[0] |= BUTTONS[name]
            continue
        part, _, values = name[3:].partition("=")
        numbers = [float(v) for v in values.split("/")] if values else [1.0]
        slot = {"LS": 1, "RS": 3, "LT": 5, "RT": 6}[part]
        pad[slot:slot + len(numbers)] = numbers
    return pad if found else None


def tokens(pad):
    """The tokens for a recorded pad state (see state); empty for None."""
    if not pad:
        return frozenset()
    buttons, lx, ly, rx, ry, lt, rt = pad
    names = {name for name, bit in BUTTONS.items() if buttons & bit}
    for part, values in (("LS", (lx, ly)), ("RS", (rx, ry)), ("LT", (lt,)), ("RT", (rt,))):
        token = canonical(f"Pad{part}=" + "/".join(repr(float(v)) for v in values))
        if token:
            names.add(token)
    return frozenset(names)


def keyboard(names):
    """The keyboard key names among `names` (everything that is not a pad token)."""
    return [name for name in names if not is_pad(name)]
