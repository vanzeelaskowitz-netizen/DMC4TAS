"""Physical DirectInput keys and Windows virtual keys (US ANSI OEM defaults).

Names describe keys, not text produced by a keyboard layout. An alternate
layout or an extra device key can be bound explicitly in a timeline.
"""
from typing import NamedTuple


class KeyCodes(NamedTuple):
    dik: int
    vk: int


def _builtins():
    result = {}
    for row, first in (("1234567890",0x02), ("QWERTYUIOP",0x10),
                       ("ASDFGHJKL",0x1e), ("ZXCVBNM",0x2c)):
        for dik, char in enumerate(row, first):
            result[char] = KeyCodes(dik, ord(char))
    for n, dik in enumerate((*range(0x3b,0x45),0x57,0x58,0x64,0x65,0x66), 1):
        result[f"F{n}"] = KeyCodes(dik, 0x6f+n)
    for n, dik in enumerate((0x52,0x4f,0x50,0x51,0x4b,0x4c,0x4d,0x47,0x48,0x49)):
        result[f"Numpad{n}"] = KeyCodes(dik, 0x60+n)
    # Name, DIK, VK. NumpadEnter shares VK_RETURN with the main Enter.
    rows = """
    Escape 01 1B        Minus 0C BD         Equals 0D BB        Backspace 0E 08
    Tab 0F 09           LeftBracket 1A DB   RightBracket 1B DD  Enter 1C 0D
    LeftCtrl 1D A2      Semicolon 27 BA     Apostrophe 28 DE    Grave 29 C0
    LeftShift 2A A0     Backslash 2B DC     Comma 33 BC         Period 34 BE
    Slash 35 BF         RightShift 36 A1    NumpadMultiply 37 6A LeftAlt 38 A4
    Space 39 20         CapsLock 3A 14      NumLock 45 90       ScrollLock 46 91
    NumpadMinus 4A 6D   NumpadPlus 4E 6B    NumpadDecimal 53 6E OEM102 56 E2
    Kana 70 15          Convert 79 1C       NonConvert 7B 1D    PrevTrack 90 B1
    NextTrack 99 B0     NumpadEnter 9C 0D   RightCtrl 9D A3     Mute A0 AD
    Calculator A1 B7    PlayPause A2 B3     MediaStop A4 B2     VolumeDown AE AE
    VolumeUp B0 AF      WebHome B2 AC       NumpadDivide B5 6F  PrintScreen B7 2C
    RightAlt B8 A5      Pause C5 13         Home C7 24          Up C8 26
    PageUp C9 21        Left CB 25          Right CD 27         End CF 23
    Down D0 28          PageDown D1 22      Insert D2 2D        Delete D3 2E
    LeftWin DB 5B       RightWin DC 5C      Apps DD 5D          Sleep DF 5F
    WebSearch E5 AA     WebFavorites E6 AB  WebRefresh E7 A8    WebStop E8 A9
    WebForward E9 A7    WebBack EA A6       MyComputer EB B6    Mail EC B4
    MediaSelect ED B5
    """.split()
    for name, dik, vk in zip(rows[0::3], rows[1::3], rows[2::3], strict=True):
        result[name] = KeyCodes(int(dik,16), int(vk,16))
    return result


KEY_CODES = _builtins()
