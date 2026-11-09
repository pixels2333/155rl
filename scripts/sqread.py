"""Read-only Squirrel 3.x object graph from a live 32-bit process.

Layouts were derived from the Squirrel 3.0.6/3.1 sources shipped with the engine
and cross-checked against the constructors inside th155.exe (see docs/re/).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
from typing import Iterator, Optional

# ---------------------------------------------------------------- object types
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
OT_THREAD = 0x08001000
OT_FUNCPROTO = 0x08002000
OT_CLASS = 0x08004000
OT_INSTANCE = 0x0A008000

RAW_NULL, RAW_INT, RAW_FLOAT, RAW_BOOL = 1, 2, 4, 8
RAW_STRING, RAW_TABLE, RAW_ARRAY, RAW_CLOSURE = 0x10, 0x20, 0x40, 0x100
RAW_NATIVECLOSURE, RAW_THREAD, RAW_FUNCPROTO = 0x200, 0x1000, 0x2000
RAW_CLASS, RAW_INSTANCE = 0x4000, 0x8000

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class MODULEINFO(ctypes.Structure):
    _fields_ = [
        ("lpBaseOfDll", ctypes.c_void_p),
        ("SizeOfImage", wt.DWORD),
        ("EntryPoint", ctypes.c_void_p),
    ]


class Process:
    def __init__(self, pid: int, writable: bool = False):
        self.pid = pid
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.h = self.k32.OpenProcess(access, False, pid)
        if not self.h:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")

    # -- primitive reads ---------------------------------------------------
    def read(self, addr: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = self.k32.ReadProcessMemory(
            self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)
        )
        if not ok or got.value != size:
            raise OSError(f"ReadProcessMemory({addr:#x}, {size}) failed")
        return buf.raw

    def write(self, addr: int, data: bytes) -> None:
        k32 = self.k32
        k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_char_p,
                                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        wrote = ctypes.c_size_t(0)
        ok = k32.WriteProcessMemory(
            self.h, ctypes.c_void_p(addr), data, len(data), ctypes.byref(wrote)
        )
        if not ok or wrote.value != len(data):
            raise OSError(f"WriteProcessMemory({addr:#x}) failed")

    def write_i32(self, addr: int, value: int) -> None:
        self.write(addr, struct.pack("<i", value))

    def write_u32(self, addr: int, value: int) -> None:
        self.write(addr, struct.pack("<I", value))

    def u32(self, addr: int) -> int:
        return struct.unpack("<I", self.read(addr, 4))[0]

    def i32(self, addr: int) -> int:
        return struct.unpack("<i", self.read(addr, 4))[0]

    def f32(self, addr: int) -> float:
        return struct.unpack("<f", self.read(addr, 4))[0]

    # -- module ------------------------------------------------------------
    def module_info(self, name: str) -> tuple[int, int]:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        mods = (ctypes.c_void_p * 1024)()
        needed = wt.DWORD(0)
        if not psapi.EnumProcessModules(
            self.h, mods, ctypes.sizeof(mods), ctypes.byref(needed)
        ):
            raise OSError(ctypes.get_last_error(), "EnumProcessModules")
        count = needed.value // ctypes.sizeof(ctypes.c_void_p)
        for i in range(count):
            base = mods[i]
            buf = ctypes.create_unicode_buffer(260)
            psapi.GetModuleBaseNameW(self.h, ctypes.c_void_p(base), buf, 260)
            if buf.value.lower() == name.lower():
                mi = MODULEINFO()
                psapi.GetModuleInformation(
                    self.h, ctypes.c_void_p(base), ctypes.byref(mi), ctypes.sizeof(mi)
                )
                return base, mi.SizeOfImage
        raise LookupError(name)

    def regions(self):
        """Yield (base, size, protect) for committed private/image regions."""
        addr = 0
        mbi = MEMORY_BASIC_INFORMATION()
        while addr < 0x7FFF0000:
            if not self.k32.VirtualQueryEx(
                self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)
            ):
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize
            if size == 0:
                break
            if mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)):
                yield base, size, mbi.Protect
            addr = base + size

    def read_region(self, base: int, size: int) -> bytes:
        return self.read(base, size)


class SQReader:
    """Decode Squirrel objects from a Process."""

    def __init__(self, proc: Process):
        self.p = proc
        self._strcache: dict[int, str] = {}

    # -- low level ---------------------------------------------------------
    def obj(self, addr: int) -> tuple[int, int]:
        """Read a tagSQObject (type, value) at addr."""
        t, v = struct.unpack("<II", self.p.read(addr, 8))
        return t, v

    def string_at(self, ptr: int) -> str:
        if ptr in self._strcache:
            return self._strcache[ptr]
        n = struct.unpack("<i", self.p.read(ptr + 20, 4))[0]
        raw = self.p.read(ptr + 28, max(0, n))
        s = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
        self._strcache[ptr] = s
        return s

    def value(self, addr: int):
        """Decode the SQObject at addr into a Python value (best effort)."""
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

    # -- containers --------------------------------------------------------
    def table_nodes(self, table: int) -> Iterator[tuple[object, int, int]]:
        """Yield (key, key_obj_addr, val_obj_addr) for a SQTable."""
        num = struct.unpack("<i", self.p.read(table + 36, 4))[0]
        nodes = struct.unpack("<I", self.p.read(table + 32, 4))[0]
        if not (0 < num < 1 << 24) or not nodes:
            return
        raw = self.p.read(nodes, num * 20)
        for i in range(num):
            off = i * 20
            kt = struct.unpack_from("<I", raw, off + 8)[0]
            if kt & RT_MASK == RAW_NULL:
                continue
            kv = struct.unpack_from("<I", raw, off + 12)[0]
            key = ("str", self.string_at(kv)) if (kt & RT_MASK) == RAW_STRING else (
                "other", kt, kv)
            yield key, nodes + off + 8, nodes + off

    def table_get(self, table: int, name: str) -> Optional[int]:
        """Return address of the value SQObject for `name` in table (no delegate)."""
        h = hashstr(name)
        num = struct.unpack("<i", self.p.read(table + 36, 4))[0]
        nodes = struct.unpack("<I", self.p.read(table + 32, 4))[0]
        if not (0 < num < 1 << 24) or not nodes:
            return None
        idx = h & (num - 1)
        seen = 0
        while idx is not None and seen <= num:
            addr = nodes + idx * 20
            kt, kv = self.obj(addr + 8)
            if kt & RT_MASK == RAW_NULL:
                return None
            if kt & RT_MASK == RAW_STRING and self.string_at(kv) == name:
                return addr
            nxt = struct.unpack("<I", self.p.read(addr + 16, 4))[0]
            idx = ((nxt - nodes) // 20) if nxt else None
            seen += 1
        return None

    def array(self, arr: int) -> list[int]:
        vals = struct.unpack("<I", self.p.read(arr + 12, 4))[0]
        size = struct.unpack("<i", self.p.read(arr + 16, 4))[0]
        if size <= 0 or size > 100000:
            return []
        return [vals + i * 8 for i in range(size)]

    def instance(self, inst: int) -> tuple[int, list[int]]:
        """Return (class_ptr, field object addresses)."""
        cls = struct.unpack("<I", self.p.read(inst + 28, 4))[0]
        if not cls:
            return 0, []
        members = struct.unpack("<I", self.p.read(cls + 24, 4))[0]
        # number of default values == number of instance slots
        n = struct.unpack("<i", self.p.read(cls + 36, 4))[0]
        return cls, [inst + 44 + i * 8 for i in range(max(0, min(n, 4096)))]

    def instance_slot(self, inst: int, name: str) -> Optional[int]:
        cls, _ = self.instance(inst)
        if not cls:
            return None
        members = struct.unpack("<I", self.p.read(cls + 24, 4))[0]
        slot = self.table_get(members, name)
        if not slot:
            return None
        idx = struct.unpack("<i", struct.pack("<I", self.p.u32(slot + 4)))[0]
        if not (idx & 0x02000000):  # MEMBER_TYPE_FIELD
            return None
        return inst + 44 + (idx & 0x00FFFFFF) * 8

    def class_name(self, cls: int) -> str:
        # SQClass has _attributes table with "__name"/"__name" style entry? Try both.
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
        idx = struct.unpack("<i", struct.pack("<I", self.p.u32(slot + 4)))[0]
        if idx & 0x02000000:  # field -> default value
            vals = struct.unpack("<I", self.p.read(cls + 32, 4))[0]
            return vals + (idx & 0xFFFFFF) * 8
        if idx & 0x01000000:  # method
            meths = struct.unpack("<I", self.p.read(cls + 44, 4))[0]
            return meths + (idx & 0xFFFFFF) * 8
        return None

    def class_fields(self, cls: int) -> dict[str, int]:
        """Map field name -> slot index for an SQClass."""
        members = struct.unpack("<I", self.p.read(cls + 24, 4))[0]
        out = {}
        for key, kaddr, vaddr in self.table_nodes(members):
            if key[0] != "str":
                continue
            idx = struct.unpack("<i", self.p.read(vaddr + 4, 4))[0]
            if idx & 0x02000000:
                out[key[1]] = idx & 0xFFFFFF
        return out


class ObjRef:
    __slots__ = ("type", "ptr")

    def __init__(self, t: int, ptr: int):
        self.type = t
        self.ptr = ptr

    def __repr__(self):
        return f"ObjRef(type={self.type:#x}, ptr={self.ptr:#x})"


def hashstr(s: str) -> int:
    """Squirrel _hashstr (32-bit SQHash)."""
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


def find_pid(name: str = "th155.exe") -> int:
    """Enumerate processes and return the pid matching `name`."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    arr = (wt.DWORD * 4096)()
    needed = wt.DWORD(0)
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
        raise OSError("EnumProcesses")
    n = needed.value // 4
    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400
    for i in range(n):
        pid = arr[i]
        if not pid:
            continue
        h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            continue
        try:
            mods = (ctypes.c_void_p * 512)()
            got = wt.DWORD(0)
            if psapi.EnumProcessModules(h, mods, ctypes.sizeof(mods), ctypes.byref(got)):
                for j in range(got.value // ctypes.sizeof(ctypes.c_void_p)):
                    buf = ctypes.create_unicode_buffer(260)
                    psapi.GetModuleBaseNameW(h, ctypes.c_void_p(mods[j]), buf, 260)
                    if buf.value.lower() == name.lower():
                        return pid
        finally:
            k32.CloseHandle(h)
    raise LookupError(name)
