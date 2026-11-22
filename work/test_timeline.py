import tempfile
import unittest
from pathlib import Path

from work.timeline import EXAMPLE, KEY_CODES, KeyCodes, Timeline, format_script, frames, parse_script, read_script, write_script


class TimelineTests(unittest.TestCase):
    def test_original_eight_frames(self):
        result = list(frames(EXAMPLE))
        self.assertEqual([x[0] for x in result], list(range(1,9)))
        self.assertEqual(result[0][1:], ({"Space","L"},{"Space","L"},set()))
        self.assertTrue(all(row[1:] == ({"Space","L"},set(),set()) for row in result[1:4]))
        self.assertEqual(result[4][1:], ({"Space","L","J"},{"J"},set()))
        self.assertEqual(result[5][1:], ({"Space","L"},set(),{"J"}))
        self.assertEqual(result[6][1:], ({"Space","L","K"},{"K"},set()))
        self.assertEqual(result[7][1:], (set(),set(),{"Space","L","K"}))

    def test_hold_across_ranges(self):
        rows = parse_script("1-2: Left A\n3-5: Left A").rows
        for _,held,pressed,released in list(frames(rows))[1:]:
            self.assertEqual((held,pressed,released), ({"Left","A"},set(),set()))

    def test_single_frame_tap_while_holding(self):
        rows = parse_script("1: LeftShift\n2: LeftShift F12\n3: LeftShift").rows
        result = list(frames(rows))
        self.assertEqual(result[1][2:], ({"F12"},set()))
        self.assertEqual(result[2][2:], (set(),{"F12"}))

    def test_invalid_ranges(self):
        for text in ("0: J","2: J","1-0: J","1-3: J\n3: K","1: J\n3: K","-1: J","1.2: J"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_script(text)
        with self.assertRaises(ValueError):
            list(frames([(1,3,["J"]),(3,4,["K"])]))

    def test_physical_key_codes(self):
        self.assertEqual([KEY_CODES[k] for k in ("Space","J","K","L","Left","A")],
                         [(0x39,32),(0x24,74),(0x25,75),(0x26,76),(0xcb,0x25),(0x1e,65)])
        self.assertNotEqual(KEY_CODES["LeftCtrl"], KEY_CODES["RightCtrl"])
        self.assertNotEqual(KEY_CODES["Enter"].dik, KEY_CODES["NumpadEnter"].dik)
        self.assertEqual(KEY_CODES["Enter"].vk, KEY_CODES["NumpadEnter"].vk)

    def test_arbitrary_symbol_and_file_round_trip(self):
        self.assertEqual(list(frames([(1,1,["侧键"])]))[0][1], {"侧键"})
        text = "\ufeff# comment\n@key 侧键 0x73 0xC1\n1-2: RightCtrl 侧键\n3: -"
        script = parse_script(text)
        self.assertEqual(script.codes("侧键"), KeyCodes(0x73,0xc1))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            path = Path(tmp)/"输入.txt"
            write_script(path,script)
            self.assertEqual(read_script(path),script)
        self.assertEqual(parse_script(format_script(Timeline(EXAMPLE))).rows[-1], (8,8,set()))

    def test_invalid_mapping(self):
        for text in ("1: Unknown","@key X 0x100 1","@key X 0 1","@key X 1 1\n@key X 1 1",
                     "@key Side 0x39 32\n1: Side\n2: Space","1: - J","1:"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_script(text)

    def test_layout_override(self):
        script = parse_script("1: Q\n@key Q 0x10 65")
        self.assertEqual(script.codes("Q"),(0x10,65))
        self.assertEqual(KEY_CODES["Q"],(0x10,81))


if __name__ == "__main__":
    unittest.main()
