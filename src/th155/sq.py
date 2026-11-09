"""Squirrel 3.x 对象图读取器（th155.exe 32 位实机验证）。"""
from __future__ import annotations

import struct
from typing import Iterator, Optional

from .mem import Process

RT_MASK = 0x00FFFFFF
OT_NULL = 0x01000001
OT_INTEGER = 0x05000002
OT_FLOAT = 0x05000004
OT_BOOL = 0x01000008
OT_STRING = 0x08000010
OT_TABLE = 0x0A000020
OT_ARRAY = 0x08000040
OT_CLOSURE = 0x08000100
OT_NATIVECLOSURE = 0x08000200
OT_CLASS = 0x08004000
OT_INSTANCE = 0x0A008000

RAW_NULL, RAW_INT, RAW_FLOAT, RAW_BOOL = 1, 2, 4, 8
RAW_STRING, RAW_TABLE, RAW_ARRAY = 0x10, 0x20, 0x40
RAW_CLOSURE, RAW_NATIVECLOSURE = 0x100, 0x200
RAW_CLASS, RAW_INSTANCE = 0x4000, 0x8000

# 结构偏移（见 configs/offsets.yaml）
TABLE_NODES, TABLE_NUM, NODE_STRIDE = 32, 36, 20
ARRAY_VALUES, ARRAY_SIZE = 24, 28
INST_CLASS, INST_UP, INST_VALUES, INST_STRIDE = 28, 32, 44, 8


class ObjRef:
    __slots__ = ("type", "ptr")

    def __init__(self, t: int, ptr: int):
        self.type = t
        self.ptr = ptr

    def __repr__(self) -> str:
        return f"ObjRef(type={self.type:#x}, ptr={self.ptr:#x})"


class SQReader:
    def __init__(self, proc: Process):
        self.p = proc
        self._strcache: dict[int, str] = {}

    # -- 基础 --------------------------------------------------------------
    def obj(self, addr: int) -> tuple[int, int]:
        return struct.unpack("<II", self.p.read(addr, 8))

    def string_at(self, ptr: int) -> str:
        if ptr in self._strcache:
            return self._strcache[ptr]
        n = struct.unpack("<i", self.p.read(ptr + 20, 4))[0]
        raw = self.p.read(ptr + 28, max(0, n))
        s = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
        self._strcache[ptr] = s
        return s

    def value(self, addr: int):
        t, v = self.obj(addr)
        raw = t & RT_MASK
        if raw == RAW_NULL:
            return None
        if raw == RAW_INT:
            return struct.unpack("<i", struct.pack("<I", v))[0]
        if raw == RAW_FLOAT:
            return struct.unpack("<f", struct.pack("<I", v))[0]
        if raw == RAW_BOOL:
            return bool(v)
        if raw == RAW_STRING:
            return self.string_at(v)
        return ObjRef(t, v)

    # -- 容器 --------------------------------------------------------------
    def table_nodes(self, table: int) -> Iterator[tuple[object, int, int]]:
        num = struct.unpack("<i", self.p.read(table + TABLE_NUM, 4))[0]
        nodes = struct.unpack("<I", self.p.read(table + TABLE_NODES, 4))[0]
        if not (0 < num < 1 << 24) or not nodes:
            return
        raw = self.p.read(nodes, num * NODE_STRIDE)
        for i in range(num):
            off = i * NODE_STRIDE
            kt = struct.unpack_from("<I", raw, off + 8)[0]
            if kt & RT_MASK == RAW_NULL:
                continue
            kv = struct.unpack_from("<I", raw, off + 12)[0]
            key = ("str", self.string_at(kv)) if (kt & RT_MASK) == RAW_STRING else (
                "other", kt, kv)
            yield key, nodes + off + 8, nodes + off

    def table_get(self, table: int, name: str) -> Optional[int]:
        """返回表中 name 对应 *值 SQObject 的地址*（不含委托）。"""
        h = hashstr(name)
        num = struct.unpack("<i", self.p.read(table + TABLE_NUM, 4))[0]
        nodes = struct.unpack("<I", self.p.read(table + TABLE_NODES, 4))[0]
        if not (0 < num < 1 << 24) or not nodes:
            return None
        idx = h & (num - 1)
        seen = 0
        while idx is not None and seen <= num:
            addr = nodes + idx * NODE_STRIDE
            kt, kv = self.obj(addr + 8)
            if kt & RT_MASK == RAW_NULL:
                return None
            if kt & RT_MASK == RAW_STRING and self.string_at(kv) == name:
                return addr
            nxt = struct.unpack("<I", self.p.read(addr + 16, 4))[0]
            idx = ((nxt - nodes) // NODE_STRIDE) if nxt else None
            seen += 1
        return None

    def array(self, arr: int) -> list[int]:
        """返回数组元素 SQObject 地址列表。"""
        vals = struct.unpack("<I", self.p.read(arr + ARRAY_VALUES, 4))[0]
        size = struct.unpack("<i", self.p.read(arr + ARRAY_SIZE, 4))[0]
        if size <= 0 or size > 100000 or not vals:
            return []
        return [vals + i * 8 for i in range(size)]

    def instance(self, inst: int) -> tuple[int, list[int]]:
        """返回 (class_ptr, 字段 SQObject 地址列表)。"""
        cls = struct.unpack("<I", self.p.read(inst + INST_CLASS, 4))[0]
        if not cls:
            return 0, []
        n = struct.unpack("<i", self.p.read(cls + 36, 4))[0]
        return cls, [inst + INST_VALUES + i * INST_STRIDE for i in range(max(0, min(n, 4096)))]

    def instance_slot(self, inst: int, name: str) -> Optional[int]:
        cls, _ = self.instance(inst)
        if not cls:
            return None
        members = struct.unpack("<I", self.p.read(cls + 24, 4))[0]
        slot = self.table_get(members, name)
        if not slot:
            return None
        idx = struct.unpack("<I", self.p.read(slot + 4, 4))[0]
        if not (idx & 0x02000000):  # MEMBER_TYPE_FIELD
            return None
        return inst + INST_VALUES + (idx & 0x00FFFFFF) * INST_STRIDE

    def class_name(self, cls: int) -> str:
        try:
            for name in ("__name", "_name"):
                slot = self.class_slot(cls, name)
                if slot:
                    v = self.value(slot)
                    if isinstance(v, str):
                        return v
        except OSError:
            pass
        return ""

    def class_slot(self, cls: int, name: str) -> Optional[int]:
        members = struct.unpack("<I", self.p.read(cls + 24, 4))[0]
        slot = self.table_get(members, name)
        if not slot:
            return None
        idx = struct.unpack("<I", self.p.read(slot + 4, 4))[0]
        if idx & 0x02000000:
            vals = struct.unpack("<I", self.p.read(cls + 32, 4))[0]
            return vals + (idx & 0xFFFFFF) * 8
        if idx & 0x01000000:
            meths = struct.unpack("<I", self.p.read(cls + 44, 4))[0]
            return meths + (idx & 0xFFFFFF) * 8
        return None


def hashstr(s: str) -> int:
    """Squirrel _hashstr（32 位）。"""
    b = s.encode("utf-8")
    l = len(b)
    h = l & 0xFFFFFFFF
    step = (l >> 5) | 1
    i = 0
    while l >= step:
        h = (h ^ (((h << 5) & 0xFFFFFFFF) + (h >> 2) + b[i])) & 0xFFFFFFFF
        i += step
        l -= step
    return h
