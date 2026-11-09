"""Parse th155.exe ``.rep`` replay files without the game running.

Binary layout (``TF4::InputRecorder``, reverse-engineered against the Dauphine
archive ``replay/1519replays - Dauphine 250101-251124 119.zip`` — all 1518
``.rep`` entries parse with this layout)::

    [0x00..0x03]  magic ``b'TFRP'``
    [0x04]        ``0x00`` pad byte
    [0x05..0x08]  replay ``version`` uint32 LE (archive files: 1551210)
    [0x09..0x0C]  ``comp_len + 8`` uint32 LE (informational, see below)
    [0x0D..0x10]  ``comp_len`` uint32 LE (zlib blob that follows)
    [0x11..0x14]  ``decomp_len`` uint32 LE
    [0x15..]      zlib blob (``comp_len`` bytes) -> Squirrel userdata table
    next u32      input stream count (archive files: always 2 = 1P + 2P)
    per stream:   u32 ``clen``, u32 ``dlen``, zlib blob (``clen`` bytes)
                  -> ``dlen`` bytes = ``dlen // 2`` LE uint16 input frames

``[0x09..0x0C]`` is always ``comp_len + 8`` in the observed files; the parser
treats it as informational only (never used for slicing).

Userdata is a flat sequence of ``(key, value)`` pairs where keys are
``OT_STRING`` and values are one of ``OT_INTEGER`` (i32), ``OT_FLOAT`` (f32),
``OT_BOOL`` (1 byte), ``OT_STRING`` (Shift-JIS bytes) or ``OT_ARRAY``.  Arrays
are ``u32 count`` followed by ``(OT_INTEGER index, value)`` per element and a
bare ``OT_NULL`` end marker.  Bare ``OT_NULL`` tags (array end markers, plus
one trailing marker at end of blob) carry no payload.

Per-frame input bitfield (uint16, only bits 0..8 ever set in the archive).
Exact mapping from ``sub_567490`` (``0x567490`` in ``th155.exe``, called by
``TF4::InputRecorder`` during replay playback)::

    bit 0 (0x01) : LEFT  (writes ``dev->x < 0``)   bit 4 : b0 (A 打撃)
    bit 1 (0x02) : RIGHT (writes ``dev->x > 0``)   bit 5 : b1 (B 射撃)
    bit 2 (0x04) : UP    (writes ``dev->y < 0``)   bit 6 : b2 (C 必殺)
    bit 3 (0x08) : DOWN  (writes ``dev->y > 0``)   bit 7 : b3 (Tag 憑依換人)
    bit 8 (0x100): b4 (D 衝刺/結界)                bits 9..15 : b5..b11, never set

Evidence: low nibbles ``0,1,2,4,5,6,8,9,10`` occur (single directions plus
valid diagonals) while ``3`` (left+right), ``12`` (up+down), ``7,11,13..15``
never occur — bits 0/1 and 2/3 are opposite pairs.  Neutral + single button
values ``16/32/64/128/256`` all occur, as do ``48`` (A+B = Occult) and ``96``
(B+C = Spell).

.. note::
    ``decode_frame`` returns ``(x, y)`` in ``src/env/action_space.py``
    convention (``x = +1`` is RIGHT, ``y = +1`` is Up/``"8"``), matching
    ``action_space.DIRECTIONS`` and ``to_input``.  Note the sign flip on the
    y axis relative to the game internals: ``sub_567490`` writes ``dev->y < 0``
    for UP (bit 2) and ``dev->y > 0`` for DOWN (bit 3), while the action space
    (and this parser) use ``dy = +1`` for Up.  All census categories are
    invariant under x/y sign flips; the mapping is cross-checked against
    ``action_space.to_input`` for both facings in the unit tests.
"""

from __future__ import annotations

import struct
import zlib

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from zipfile import ZipFile

MAGIC = b"TFRP"
PAD_BYTE = 0x00
HEADER_LEN = 21

# Replay versions seen in the wild.
REPLAY_VERSION_DECOMP_119B4 = 1551191  # version.nut (1.19beta4 decomp)
REPLAY_VERSION_ARCHIVE = 1551210  # every .rep in the Dauphine archive

# Squirrel object tags (match src/th155/sq.py memory tags).
OT_NULL = 0x01000001
OT_INTEGER = 0x05000002
OT_FLOAT = 0x05000004
OT_BOOL = 0x01000008
OT_STRING = 0x08000010
OT_ARRAY = 0x08000040

_STR_ENCODING = "cp932"  # game is Shift-JIS; ASCII names are unaffected.

# Mirrors src/env/action_space.py (kept local so this module stays stdlib-only).
DIRECTIONS = (
    (0, 0),  # 0: 5 neutral
    (1, 0),  # 1: 6
    (-1, 0),  # 2: 4
    (0, 1),  # 3: 8 (up: input.y = +1)
    (0, -1),  # 4: 2
    (1, 1),  # 5: 9
    (-1, 1),  # 6: 7
    (1, -1),  # 7: 3
    (-1, -1),  # 8: 1
)
N_BUTTONS = 6  # none, A, B, C, D, Occult(A+B)
SPELL_ACTION = 9 * 6  # 54: neutral + B+C
TAG_ACTION = 54 + 1  # 55: neutral + b3
N_ACTIONS = 56

# Frame categories for census/action-space coverage.
EXACT = "exact"  # representable by one of the 56 discrete actions
SPELL_DIR = "spell_dir"  # B+C plus a non-neutral direction
TAG_DIR = "tag_dir"  # b3 plus a non-neutral direction
OTHER_MULTI = "other_multi"  # any other multi-button combination


class ReplayParseError(ValueError):
    """Raised when ``.rep`` bytes violate the expected layout."""


@dataclass
class ReplayData:
    """One parsed replay: userdata table + per-device input frame streams."""

    version: int
    userdata: dict
    streams: list[list[int]]  # streams[d][f] = uint16 bitfield
    source: str = ""
    header: dict = field(default_factory=dict)


def _u32(buf: bytes | memoryview, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _parse_value(buf: bytes, off: int):
    """Parse one userdata value at ``off``; return ``(value, new_off)``."""
    if off + 4 > len(buf):
        raise ReplayParseError(f"truncated value tag at offset {off}")
    tag = _u32(buf, off)
    off += 4
    if tag == OT_INTEGER:
        if off + 4 > len(buf):
            raise ReplayParseError("truncated integer payload")
        return struct.unpack_from("<i", buf, off)[0], off + 4
    if tag == OT_FLOAT:
        if off + 4 > len(buf):
            raise ReplayParseError("truncated float payload")
        return struct.unpack_from("<f", buf, off)[0], off + 4
    if tag == OT_BOOL:
        if off + 1 > len(buf):
            raise ReplayParseError("truncated bool payload")
        return bool(buf[off]), off + 1
    if tag == OT_STRING:
        if off + 4 > len(buf):
            raise ReplayParseError("truncated string length")
        ln = _u32(buf, off)
        off += 4
        if off + ln > len(buf):
            raise ReplayParseError("truncated string payload")
        return bytes(buf[off:off + ln]).decode(_STR_ENCODING, errors="replace"), off + ln
    if tag == OT_ARRAY:
        if off + 4 > len(buf):
            raise ReplayParseError("truncated array length")
        count = _u32(buf, off)
        off += 4
        items: list[tuple[int, object]] = []
        for _ in range(count):
            if off + 8 > len(buf):
                raise ReplayParseError("truncated array index")
            if _u32(buf, off) != OT_INTEGER:
                raise ReplayParseError("array index is not an integer")
            idx = struct.unpack_from("<i", buf, off + 4)[0]
            value, off = _parse_value(buf, off + 8)
            items.append((idx, value))
        if off + 4 > len(buf) or _u32(buf, off) != OT_NULL:
            raise ReplayParseError("array missing NULL end marker")
        items.sort(key=lambda kv: kv[0])
        return [v for _, v in items], off + 4
    if tag == OT_NULL:
        return None, off  # bare end marker, no payload
    raise ReplayParseError(f"unknown userdata tag {tag:#010x} at offset {off - 4}")


def parse_userdata(blob: bytes) -> dict:
    """Parse a decompressed userdata blob into a flat ``{key: value}`` dict."""
    out: dict = {}
    off = 0
    while off < len(blob):
        tag = _u32(blob, off)
        if tag == OT_NULL:  # array end marker / trailing marker: no payload
            off += 4
            continue
        if tag != OT_STRING:
            raise ReplayParseError(f"expected string key at offset {off}, got {tag:#010x}")
        if off + 8 > len(blob):
            raise ReplayParseError("truncated key header")
        ln = _u32(blob, off + 4)
        key = bytes(blob[off + 8:off + 8 + ln]).decode("ascii", errors="replace")
        value, off = _parse_value(blob, off + 8 + ln)
        out[key] = value
    return out


def decode_frame(v: int) -> tuple[int, int, int, int, int, int, int]:
    """Split a uint16 frame into ``(x, y, b0, b1, b2, b3, b4)``.

    ``(x, y)`` use ``action_space`` convention (``x = +1`` RIGHT, ``y = +1``
    Up/``"8"``): bit 0 = LEFT (``x = -1``), bit 1 = RIGHT (``x = +1``),
    bit 2 = UP (``y = +1``), bit 3 = DOWN (``y = -1``).  All outputs are
    0/1 except x/y which range over -1..1.
    """
    v &= 0xFFFF
    x = (1 if v & 0x02 else 0) - (1 if v & 0x01 else 0)
    y = (1 if v & 0x04 else 0) - (1 if v & 0x08 else 0)
    return (x, y, (v >> 4) & 1, (v >> 5) & 1, (v >> 6) & 1, (v >> 7) & 1, (v >> 8) & 1)


def classify_frame(v: int) -> str:
    """Bucket a frame into ``exact`` / ``spell_dir`` / ``tag_dir`` / ``other_multi``.

    ``exact`` means the frame is representable by one of the 56 discrete
    actions in ``src/env/action_space.py``: any direction with none/A/B/C/D or
    A+B (Occult), neutral + B+C (Spell), or neutral + b3 (Tag).
    """
    if v & 0xFE00:  # b5..b11: never observed; outside the 56-action model
        return OTHER_MULTI
    x, y, b0, b1, b2, b3, b4 = decode_frame(v)
    pressed = b0 + b1 + b2 + b3 + b4
    neutral = x == 0 and y == 0
    if pressed == 0:
        return EXACT
    if pressed == 1:
        if b3:
            return EXACT if neutral else TAG_DIR
        return EXACT
    if b1 and b2 and pressed == 2:
        return EXACT if neutral else SPELL_DIR
    if b0 and b1 and pressed == 2:  # Occult (A+B), any direction
        return EXACT
    return OTHER_MULTI


def frame_to_action(v: int, facing_sign: int = 1) -> int | None:
    """Map an ``exact`` frame to its 0..55 action id; ``None`` if not exact.

    ``facing_sign`` is +1 when the opponent is to the right, -1 when to the
    left.  The absolute ``x`` from :func:`decode_frame` is converted to the
    relative ``dx = x * facing_sign`` (``dx = +1`` = toward opponent, i.e.
    ``"6"``) before looking up ``(dx, dy)`` in ``DIRECTIONS``.
    """
    if classify_frame(v) != EXACT:
        return None
    x, y, b0, b1, b2, b3, b4 = decode_frame(v)
    if b1 and b2 and b0 + b3 + b4 == 0:
        return SPELL_ACTION
    if b3 and b0 + b1 + b2 + b4 == 0:
        return TAG_ACTION
    dx = x * (1 if facing_sign >= 0 else -1)
    try:
        d = DIRECTIONS.index((dx, y))
    except ValueError:
        return None  # unreachable for valid opposite-pair bits
    if b0 and b1 and b2 + b3 + b4 == 0:
        b = 5  # Occult
    elif b0 + b1 + b2 + b3 + b4 == 0:
        b = 0
    elif b0 and b1 + b2 + b3 + b4 == 0:
        b = 1
    elif b1 and b0 + b2 + b3 + b4 == 0:
        b = 2
    elif b2 and b0 + b1 + b3 + b4 == 0:
        b = 3
    elif b4 and b0 + b1 + b2 + b3 == 0:
        b = 4
    else:
        return None
    return d * N_BUTTONS + b


def frame_to_action_projected(
    v: int, facing_sign: int = 1, action_mask: list[bool] | None = None
) -> tuple[int, bool]:
    """Project a frame onto the 56-action space for BC training.

    Strict no-rewrite rule: returns ``(act, True)`` (``bc_valid``) ONLY when
    ``classify_frame(v) == EXACT`` and the mask allows it (``action_mask is
    None`` or ``action_mask[act]`` is True).  Otherwise — including
    ``SPELL_DIR``/``TAG_DIR``/``OTHER_MULTI`` frames, or masked-out exact
    actions — returns ``(fallback_dir_only_action, False)`` where the fallback
    keeps the frame's relative direction with no buttons (``bc_mask = 0``).
    """
    act = frame_to_action(v, facing_sign)
    if act is not None and (action_mask is None or action_mask[act]):
        return act, True
    x, y, _, _, _, _, _ = decode_frame(v)
    dx = x * (1 if facing_sign >= 0 else -1)
    d = DIRECTIONS.index((dx, y))
    return d * N_BUTTONS + 0, False


def action_to_frame(action: int, facing_sign: int = 1) -> int:
    """Inverse of :func:`frame_to_action` for exact actions (neutral Spell/Tag ignore facing)."""
    if action == SPELL_ACTION:
        return (1 << 5) | (1 << 6)
    if action == TAG_ACTION:
        return 1 << 7
    if not 0 <= action < SPELL_ACTION:
        raise ValueError(f"action out of range: {action}")
    d, b = divmod(action, N_BUTTONS)
    dx, dy = DIRECTIONS[d]
    x = dx * (1 if facing_sign >= 0 else -1)
    v = 0
    if x > 0:  # RIGHT = bit 1 (sub_567490: bit 1 sets dev->x positive)
        v |= 0x02
    elif x < 0:  # LEFT = bit 0 (sub_567490: bit 0 sets dev->x negative)
        v |= 0x01
    if dy > 0:
        v |= 0x04
    elif dy < 0:
        v |= 0x08
    if b == 1 or b == 5:
        v |= 1 << 4
    if b == 2 or b == 5:
        v |= 1 << 5
    if b == 3:
        v |= 1 << 6
    if b == 4:
        v |= 1 << 8
    return v


def parse_bytes(data: bytes, source: str = "") -> ReplayData:
    """Parse raw ``.rep`` bytes; raises :class:`ReplayParseError` on violation."""
    if len(data) < HEADER_LEN:
        raise ReplayParseError(f"file too short ({len(data)} bytes)")
    if data[0:4] != MAGIC:
        raise ReplayParseError(f"bad magic {data[0:4]!r}, expected {MAGIC!r}")
    if data[4] != PAD_BYTE:
        raise ReplayParseError(f"bad pad byte {data[4]:#x}")
    version = _u32(data, 5)
    cplus = _u32(data, 9)
    comp_len = _u32(data, 13)
    decomp_len = _u32(data, 17)
    if cplus != comp_len + 8:
        raise ReplayParseError(f"header length mismatch: @9={cplus} != @13+8={comp_len + 8}")
    if HEADER_LEN + comp_len > len(data):
        raise ReplayParseError("userdata blob overruns file")
    try:
        blob = zlib.decompress(data[HEADER_LEN:HEADER_LEN + comp_len])
    except zlib.error as e:
        raise ReplayParseError(f"userdata zlib error: {e}") from e
    if len(blob) != decomp_len:
        raise ReplayParseError(f"userdata length {len(blob)} != header {decomp_len}")
    userdata = parse_userdata(blob)
    off = HEADER_LEN + comp_len
    if off + 4 > len(data):
        raise ReplayParseError("missing stream count")
    n_streams = _u32(data, off)
    off += 4
    if n_streams > 8:
        raise ReplayParseError(f"implausible stream count {n_streams}")
    streams: list[list[int]] = []
    for _ in range(n_streams):
        if off + 8 > len(data):
            raise ReplayParseError("truncated stream header")
        clen, dlen = _u32(data, off), _u32(data, off + 4)
        off += 8
        if off + clen > len(data):
            raise ReplayParseError("stream blob overruns file")
        try:
            raw = zlib.decompress(data[off:off + clen])
        except zlib.error as e:
            raise ReplayParseError(f"stream zlib error: {e}") from e
        off += clen
        if len(raw) != dlen:
            raise ReplayParseError(f"stream length {len(raw)} != header {dlen}")
        if dlen % 2:
            raise ReplayParseError(f"odd stream length {dlen}")
        streams.append(list(struct.unpack(f"<{dlen // 2}H", raw)))
    if off != len(data):
        raise ReplayParseError(f"{len(data) - off} trailing bytes")
    header = {
        "magic": MAGIC.decode("ascii"),
        "version": version,
        "comp_len": comp_len,
        "decomp_len": decomp_len,
        "stream_count": n_streams,
    }
    return ReplayData(version=version, userdata=userdata, streams=streams,
                      source=source, header=header)


def parse_file(path: str | Path) -> ReplayData:
    """Parse a ``.rep`` file from disk."""
    path = Path(path)
    return parse_bytes(path.read_bytes(), source=str(path))


def parse_zip_member(zf: ZipFile, name: str) -> ReplayData:
    """Parse a ``.rep`` entry directly from an open zip (no extraction)."""
    with zf.open(name, "r") as f:
        data = f.read()
    return parse_bytes(data, source=name)


def iter_zip(zf: ZipFile):
    """Yield ``(name, data)`` for every ``.rep`` entry in archive order."""
    for info in zf.infolist():
        if info.filename.endswith(".rep") and info.file_size > 0:
            with zf.open(info.filename, "r") as f:
                yield info.filename, f.read()


def load_stream_file(f: BinaryIO, name: str = "") -> ReplayData:
    """Parse from an already-open binary file object."""
    return parse_bytes(f.read(), source=name)
