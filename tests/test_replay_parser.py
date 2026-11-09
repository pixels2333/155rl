"""Unit tests for src/th155/replay_parser.py (no game needed, no extraction)."""
from __future__ import annotations

import struct
import sys
import unittest
import zlib

from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from env import action_space  # noqa: E402
from th155.replay_parser import (  # noqa: E402
    EXACT,
    OTHER_MULTI,
    SPELL_ACTION,
    SPELL_DIR,
    TAG_ACTION,
    TAG_DIR,
    ReplayParseError,
    action_to_frame,
    classify_frame,
    decode_frame,
    frame_to_action,
    frame_to_action_projected,
    iter_zip,
    parse_bytes,
    parse_userdata,
    parse_zip_member,
)

ZIP_PATH = ROOT / "replay" / "1519replays - Dauphine 250101-251124 119.zip"
FIRST_REP = "250103/replay_212116.rep"
EXPECTED_KEYS = {
    "min", "mode", "slave_name", "player_name", "slave_color", "master_color",
    "spell", "background_id", "game_mode", "hour", "master_name", "day",
    "year", "bgm_id", "difficulty", "sec", "fav", "seed", "month",
}
VALID_NIBBLES = {0, 1, 2, 4, 5, 6, 8, 9, 10}


def _w_str(s: str) -> bytes:
    raw = s.encode("cp932")
    return struct.pack("<II", 0x08000010, len(raw)) + raw


def _w_int(v: int) -> bytes:
    return struct.pack("<Ii", 0x05000002, v)


def _w_bool(v: bool) -> bytes:
    return struct.pack("<I", 0x01000008) + bytes((1 if v else 0,))


def _w_arr(values: list[bytes]) -> bytes:
    out = struct.pack("<II", 0x08000040, len(values))
    for i, enc in enumerate(values):
        out += struct.pack("<Ii", 0x05000002, i) + enc
    return out + struct.pack("<I", 0x01000001)


def _build_rep(pairs: list[bytes], streams: list[list[int]], version: int = 1551210) -> bytes:
    blob = b"".join(pairs) + struct.pack("<I", 0x01000001)
    comp = zlib.compress(blob)
    out = b"TFRP\x00" + struct.pack("<IIII", version, len(comp) + 8, len(comp), len(blob))
    out += comp + struct.pack("<I", len(streams))
    for frames in streams:
        raw = struct.pack(f"<{len(frames)}H", *frames)
        out += struct.pack("<II", len(zlib.compress(raw)), len(raw)) + zlib.compress(raw)
    return out


class TestRealReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zf = ZipFile(ZIP_PATH)
        cls.names = sorted(n for n in cls.zf.namelist() if n.endswith(".rep"))

    @classmethod
    def tearDownClass(cls):
        cls.zf.close()

    def test_zip_member_count(self):
        self.assertEqual(len(self.names), 1518)

    def test_first_replay_userdata(self):
        rep = parse_zip_member(self.zf, FIRST_REP)
        self.assertEqual(rep.version, 1551210)
        self.assertEqual(set(rep.userdata), EXPECTED_KEYS)
        ud = rep.userdata
        self.assertEqual(ud["master_name"], ["kokoro", "udonge"])
        self.assertEqual(ud["slave_name"], ["kasen", "nitori"])
        self.assertEqual(ud["player_name"], ["Dauphine", "TheSuperiorBeing"])
        self.assertEqual(ud["game_mode"], 1)
        self.assertEqual(ud["min"], 21)  # filename replay_212116 -> 21:21:16
        self.assertEqual(ud["fav"], False)

    def test_sample_stream_invariants(self):
        picks = [0, 100, 500, 1000, 1500, len(self.names) - 1]
        for i in picks:
            with self.subTest(member=self.names[i]):
                rep = parse_zip_member(self.zf, self.names[i])
                self.assertEqual(rep.version, 1551210)
                self.assertEqual(len(rep.streams), 2)
                for stream in rep.streams:
                    self.assertGreater(len(stream), 1000)
                    for v in stream:
                        self.assertEqual(v & 0xFE00, 0)  # b5..b11 never set
                        self.assertIn(v & 0x0F, VALID_NIBBLES)  # no L+R / U+D

    def test_iter_zip_needs_no_extraction(self):
        count = 0
        for name, data in iter_zip(self.zf):
            rep = parse_bytes(data, source=name)
            self.assertEqual(len(rep.streams), 2)
            count += 1
            if count >= 3:
                break
        self.assertEqual(count, 3)


class TestBitfieldSemantics(unittest.TestCase):
    def test_decode_frame(self):
        # sub_567490: bit0 = LEFT (x=-1), bit1 = RIGHT (x=+1),
        # bit2 = UP (y=+1 in action_space dy convention), bit3 = DOWN (y=-1).
        self.assertEqual(decode_frame(0), (0, 0, 0, 0, 0, 0, 0))
        self.assertEqual(decode_frame(1), (-1, 0, 0, 0, 0, 0, 0))
        self.assertEqual(decode_frame(2), (1, 0, 0, 0, 0, 0, 0))
        self.assertEqual(decode_frame(4), (0, 1, 0, 0, 0, 0, 0))
        self.assertEqual(decode_frame(8), (0, -1, 0, 0, 0, 0, 0))
        self.assertEqual(decode_frame(16), (0, 0, 1, 0, 0, 0, 0))
        self.assertEqual(decode_frame(256), (0, 0, 0, 0, 0, 0, 1))
        self.assertEqual(decode_frame(258), (1, 0, 0, 0, 0, 0, 1))

    def test_classify_table(self):
        self.assertEqual(classify_frame(0), EXACT)
        self.assertEqual(classify_frame(16), EXACT)  # neutral A
        self.assertEqual(classify_frame(258), EXACT)  # dir + D
        self.assertEqual(classify_frame(48), EXACT)  # neutral A+B (Occult)
        self.assertEqual(classify_frame(49), EXACT)  # dir + Occult
        self.assertEqual(classify_frame(96), EXACT)  # neutral B+C (Spell)
        self.assertEqual(classify_frame(97), SPELL_DIR)  # dir + Spell
        self.assertEqual(classify_frame(128), EXACT)  # neutral Tag
        self.assertEqual(classify_frame(129), TAG_DIR)  # dir + Tag
        self.assertEqual(classify_frame(80), OTHER_MULTI)  # A+C
        self.assertEqual(classify_frame(272), OTHER_MULTI)  # D+A
        self.assertEqual(classify_frame(224), OTHER_MULTI)  # B+C+Tag
        self.assertEqual(classify_frame(1 << 9), OTHER_MULTI)  # b5

    def test_frame_to_action_spot(self):
        self.assertEqual(frame_to_action(0), 0)
        self.assertEqual(frame_to_action(1), 12)  # LEFT + none (dir idx 2)
        self.assertEqual(frame_to_action(2), 6)  # RIGHT + none (dir idx 1)
        self.assertEqual(frame_to_action(16), 1)  # neutral A
        self.assertEqual(frame_to_action(48), 5)  # neutral Occult
        self.assertEqual(frame_to_action(96), SPELL_ACTION)
        self.assertEqual(frame_to_action(128), TAG_ACTION)
        self.assertIsNone(frame_to_action(97))
        self.assertIsNone(frame_to_action(129))
        self.assertIsNone(frame_to_action(80))

    def test_frame_to_action_facing(self):
        # Facing left mirrors absolute x into relative dx.
        self.assertEqual(frame_to_action(1, -1), 6)  # LEFT abs -> "6" forward
        self.assertEqual(frame_to_action(2, -1), 12)  # RIGHT abs -> "4" back
        self.assertEqual(frame_to_action(1, +1), 12)
        self.assertEqual(frame_to_action(2, +1), 6)
        # Neutral Spell/Tag ignore facing.
        self.assertEqual(frame_to_action(96, -1), SPELL_ACTION)
        self.assertEqual(frame_to_action(128, -1), TAG_ACTION)

    def test_matches_action_space(self):
        """Every 56 action round-trips through the repo's action_space mapping,
        for both facings."""
        for facing in (+1, -1):
            for action in range(action_space.N_ACTIONS):
                with self.subTest(facing=facing, action=action):
                    x, y, b0, b1, b2, b3, b4 = action_space.to_input(action, facing)
                    v = action_to_frame(action, facing)
                    self.assertEqual(decode_frame(v), (x, y, b0, b1, b2, b3, b4))
                    self.assertEqual(classify_frame(v), EXACT)
                    self.assertEqual(frame_to_action(v, facing), action)

    def test_projected_strict_no_rewrite(self):
        # Exact + no mask -> (act, True).
        self.assertEqual(frame_to_action_projected(16, +1), (1, True))
        self.assertEqual(frame_to_action_projected(2, +1), (6, True))
        # Exact but masked out -> direction-only fallback, invalid.
        mask = [True] * action_space.N_ACTIONS
        mask[1] = False
        self.assertEqual(frame_to_action_projected(16, +1, mask), (0, False))
        # Non-exact frames are never rewritten, even neutral-Tag-shaped ones.
        self.assertEqual(frame_to_action_projected(97, +1), (12, False))  # spell+LEFT
        self.assertEqual(frame_to_action_projected(129, +1, None), (12, False))  # tag+LEFT
        self.assertEqual(frame_to_action_projected(80, +1), (0, False))  # A+C neutral
        # Fallback respects facing: absolute LEFT with facing -1 -> "6" (action 6).
        self.assertEqual(frame_to_action_projected(97, -1), (6, False))


class TestSyntheticRoundTrip(unittest.TestCase):
    def test_userdata_types(self):
        pairs = [
            _w_str("mode") + _w_arr([_w_int(0), _w_int(0)]),
            _w_str("master_name") + _w_arr([_w_str("reimu"), _w_str("marisa")]),
            _w_str("seed") + _w_int(17065),
            _w_str("fav") + _w_bool(False),
            _w_str("rate") + struct.pack("<If", 0x05000004, 1.5),
        ]
        data = _build_rep(pairs, streams=[[0, 1, 96, 128], [258, 80]])
        rep = parse_bytes(data, source="synthetic")
        self.assertEqual(rep.userdata["mode"], [0, 0])
        self.assertEqual(rep.userdata["master_name"], ["reimu", "marisa"])
        self.assertEqual(rep.userdata["seed"], 17065)
        self.assertEqual(rep.userdata["fav"], False)
        self.assertAlmostEqual(rep.userdata["rate"], 1.5)
        self.assertEqual(rep.streams, [[0, 1, 96, 128], [258, 80]])

    def test_userdata_blob_direct(self):
        blob = _w_str("a") + _w_int(3) + struct.pack("<I", 0x01000001)
        self.assertEqual(parse_userdata(blob), {"a": 3})

    def test_bad_magic(self):
        with self.assertRaises(ReplayParseError):
            parse_bytes(b"XXXX\x00" + bytes(32))

    def test_truncated_and_trailing(self):
        good = _build_rep([_w_str("seed") + _w_int(1)], [[0]])
        with self.assertRaises(ReplayParseError):
            parse_bytes(good[:-4])  # truncated stream
        with self.assertRaises(ReplayParseError):
            parse_bytes(good + b"\x00")  # trailing byte


class TestReplaySplits(unittest.TestCase):
    SPLITS_PATH = ROOT / "configs" / "replay_splits.json"

    @classmethod
    def setUpClass(cls):
        import json

        with open(cls.SPLITS_PATH, encoding="utf-8") as f:
            cls.manifest = json.load(f)
        cls.train_persp = [p for b in cls.manifest["splits"]["train"]["batches"].values()
                           for p in b]
        cls.val_persp = cls.manifest["splits"]["val"]
        cls.test_persp = cls.manifest["splits"]["test"]

    def test_counts(self):
        self.assertEqual(len(self.train_persp), 88)
        for name, batch in self.manifest["splits"]["train"]["batches"].items():
            with self.subTest(batch=name):
                self.assertEqual(len(batch), 22)
        self.assertEqual(len(self.val_persp), 20)
        self.assertEqual(len(self.test_persp), 20)

    def test_zero_file_overlap(self):
        files = [{p["file"] for p in ps}
                 for ps in (self.train_persp, self.val_persp, self.test_persp)]
        self.assertFalse(files[0] & files[1])
        self.assertFalse(files[0] & files[2])
        self.assertFalse(files[1] & files[2])
        total = sum(len(f) for f in files)
        self.assertEqual(len(files[0] | files[1] | files[2]), total)

    def test_mirrors_stay_together(self):
        where = {}
        for split, ps in (("train", self.train_persp), ("val", self.val_persp),
                           ("test", self.test_persp)):
            for p in ps:
                where.setdefault(p["file"], set()).add(split)
        for m in self.manifest["mirror_files"]:
            with self.subTest(mirror=m):
                self.assertEqual(len(where[m]), 1)  # one split only
                teams = sorted(p["team"] for ps in
                               (self.train_persp, self.val_persp, self.test_persp)
                               for p in ps if p["file"] == m)
                self.assertEqual(teams, [0, 1])  # both perspectives kept

    def test_all_split_sides_are_reimu_master(self):
        with ZipFile(ZIP_PATH) as zf:
            for p in self.train_persp + self.val_persp + self.test_persp:
                with self.subTest(persp=(p["file"], p["team"])):
                    rep = parse_zip_member(zf, p["file"])
                    self.assertEqual(rep.userdata["master_name"][p["team"]], "reimu")

    def test_marisa_index(self):
        idx = self.manifest["marisa_master_perspectives"]
        self.assertEqual(len(idx), 92)
        for p in idx:
            self.assertEqual(p["master"], "marisa")

if __name__ == "__main__":
    unittest.main()
