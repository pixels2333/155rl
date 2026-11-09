"""Win32 只读/读写进程内存访问 + 模块基址解析（32 位目标进程）。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct

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
    """封装 OpenProcess / ReadProcessMemory / WriteProcessMemory。"""

    def __init__(self, pid: int, writable: bool = False):
        self.pid = pid
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.h = self.k32.OpenProcess(access, False, pid)
        if not self.h:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")

    # -- 基本读写 ----------------------------------------------------------
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
        buf = ctypes.create_string_buffer(data, len(data))
        wrote = ctypes.c_size_t(0)
        ok = self.k32.WriteProcessMemory(
            self.h, ctypes.c_void_p(addr), buf, len(data), ctypes.byref(wrote)
        )
        if not ok or wrote.value != len(data):
            raise OSError(f"WriteProcessMemory({addr:#x}) failed")

    def u32(self, addr: int) -> int:
        return struct.unpack("<I", self.read(addr, 4))[0]

    def i32(self, addr: int) -> int:
        return struct.unpack("<i", self.read(addr, 4))[0]

    def f32(self, addr: int) -> float:
        return struct.unpack("<f", self.read(addr, 4))[0]

    def write_i32(self, addr: int, value: int) -> None:
        self.write(addr, struct.pack("<i", value))

    def write_u32(self, addr: int, value: int) -> None:
        self.write(addr, struct.pack("<I", value))

    def write_f32(self, addr: int, value: float) -> None:
        self.write(addr, struct.pack("<f", value))

    # -- 模块 --------------------------------------------------------------
    def module_info(self, name: str = "th155.exe") -> tuple[int, int]:
        """返回目标进程中指定模块的 (基址, 映像大小)。"""
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        mods = (ctypes.c_void_p * 1024)()
        needed = wt.DWORD(0)
        if not psapi.EnumProcessModulesEx(
            self.h, mods, ctypes.sizeof(mods), ctypes.byref(needed), 0x03
        ):
            raise OSError(ctypes.get_last_error(), "EnumProcessModulesEx")
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

    # -- 内存区域 ----------------------------------------------------------
    def regions(self):
        """枚举已提交且可读的区域：(base, size, protect)。"""
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


def find_pids(name: str = "th155.exe") -> list[int]:
    """按模块名找出**所有**匹配的 PID。

    需要返回全部而不是第一个:重启链路上只要漏掉一个残留实例,
    它就会继续占着共享内存写出冻结的帧号,采样侧表现为
    "frame 长时间不推进"、误判成游戏卡死而反复重启。
    实测就是两个 th155 并存导致的无限重启。
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    arr = (wt.DWORD * 4096)()
    needed = wt.DWORD(0)
    if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
        raise OSError("EnumProcesses failed")
    found: list[int] = []
    for i in range(needed.value // 4):
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
                        found.append(pid)
                        break
        finally:
            k32.CloseHandle(h)
    return found


def find_pid(name: str = "th155.exe") -> int:
    """按模块名找到进程 PID(取第一个)。"""
    pids = find_pids(name)
    if not pids:
        raise LookupError(f"process not found: {name}")
    return pids[0]


def find_game_dir() -> str:
    """定位游戏目录（目录名含全角括号，避免硬编码编码差异）。"""
    import os

    for entry in os.listdir("D:/"):
        if entry.startswith("[th15.5]"):
            return os.path.join("D:/", entry)
    raise LookupError("th155 game directory not found under D:/")
