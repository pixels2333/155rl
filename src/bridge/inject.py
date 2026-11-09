"""把 th155_rl_bridge.dll 注入到正在运行的 th155.exe(32 位进程)。

要点:注入器本身是 64 位 Python,不能直接使用本进程的 kernel32!LoadLibraryW
地址(两个位数不同的进程地址空间不通用)。因此:
  1) 用 Toolhelp32(TH32CS_SNAPMODULE32)枚举目标进程的 32 位模块,拿到
     目标进程内 kernel32.dll 的基址;
  2) 用 ReadProcessMemory 解析目标进程内 kernel32 的导出表,取出
     LoadLibraryW 在**目标进程地址空间**中的地址;
  3) VirtualAllocEx + WriteProcessMemory 写入 DLL 路径,
     CreateRemoteThread(目标 LoadLibraryW, 路径)。

不修改游戏文件,不需要管理员权限(同用户即可)。
"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import time
from ctypes import wintypes

PROCESS_ALL_ACCESS = 0x001F0FFF
MEM_COMMIT_RESERVE = 0x3000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
WAIT_TIMEOUT = 258
MAX_PATH = 260

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.VirtualAllocEx.restype = ctypes.c_void_p
k32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                               wintypes.DWORD, wintypes.DWORD]
k32.WriteProcessMemory.restype = wintypes.BOOL
k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wintypes.BOOL
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.CreateRemoteThread.restype = wintypes.HANDLE
k32.CreateRemoteThread.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD)]
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.GetExitCodeThread.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", ctypes.c_void_p),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * MAX_PATH),
    ]


def _read(h, addr: int, size: int) -> bytes:
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        raise OSError(f"ReadProcessMemory({addr:#x}) 失败: {ctypes.get_last_error()}")
    return buf.raw[:got.value]


def find_module_base(h, pid: int, name: str) -> int:
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == wintypes.HANDLE(-1).value or not snap:
        raise OSError(f"CreateToolhelp32Snapshot 失败: {ctypes.get_last_error()}")
    try:
        me = MODULEENTRY32W()
        me.dwSize = ctypes.sizeof(MODULEENTRY32W)
        ok = k32.Module32FirstW(snap, ctypes.byref(me))
        while ok:
            if me.szModule.lower() == name.lower():
                return int(me.modBaseAddr)
            ok = k32.Module32NextW(snap, ctypes.byref(me))
    finally:
        k32.CloseHandle(snap)
    raise LookupError(f"目标进程中找不到模块 {name}")


def remote_export(h, module_base: int, func: str) -> int:
    """在目标进程内解析 32 位 PE 的导出表,返回函数的远程地址。"""
    dos = _read(h, module_base, 0x40)
    if dos[:2] != b"MZ":
        raise ValueError("目标模块不是 MZ")
    pe_off = struct.unpack_from("<I", dos, 0x3C)[0]
    pe = _read(h, module_base + pe_off, 0x78)
    if pe[:4] != b"PE\0\0":
        raise ValueError("目标模块不是 PE")
    opt = pe_off + 24
    opt_hdr = _read(h, module_base + opt, 0xE0)
    magic = struct.unpack_from("<H", opt_hdr, 0)[0]
    if magic != 0x10B:
        raise ValueError(f"目标模块不是 PE32 (magic={magic:#x})")
    exp_rva, exp_size = struct.unpack_from("<II", opt_hdr, 96)
    if not exp_rva:
        raise ValueError("目标模块没有导出表")
    ed = _read(h, module_base + exp_rva, 40)
    n_names = struct.unpack_from("<I", ed, 24)[0]
    # IMAGE_EXPORT_DIRECTORY: +24 NumberOfNames, +28 AddressOfFunctions,
    #                         +32 AddressOfNames,   +36 AddressOfNameOrdinals
    addr_funcs = struct.unpack_from("<I", ed, 28)[0]
    addr_names = struct.unpack_from("<I", ed, 32)[0]
    addr_ords = struct.unpack_from("<I", ed, 36)[0]
    names = _read(h, module_base + addr_names, n_names * 4)
    for i in range(n_names):
        nrva = struct.unpack_from("<I", names, i * 4)[0]
        raw = _read(h, module_base + nrva, len(func) + 1)
        if raw[:len(func)] == func.encode() and raw[len(func)] == 0:
            ordv = struct.unpack_from("<H", _read(h, module_base + addr_ords + i * 2, 2), 0)[0]
            frva = struct.unpack_from("<I", _read(h, module_base + addr_funcs + ordv * 4, 4), 0)[0]
            return module_base + frva
    raise LookupError(f"目标模块中找不到导出 {func}")


def inject(pid: int, dll_path: str, timeout: float = 15.0) -> int:
    dll_path = os.path.abspath(dll_path)
    if not os.path.isfile(dll_path):
        raise FileNotFoundError(dll_path)
    buf = (dll_path + "\0").encode("utf-16-le")

    h = k32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not h:
        raise OSError(f"OpenProcess 失败: {ctypes.get_last_error()}")
    try:
        kbase = find_module_base(h, pid, "kernel32.dll")
        load_lib = remote_export(h, kbase, "LoadLibraryW")
        remote = k32.VirtualAllocEx(h, None, len(buf), MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not remote:
            raise OSError("VirtualAllocEx 失败")
        written = ctypes.c_size_t(0)
        if not k32.WriteProcessMemory(h, remote, buf, len(buf), ctypes.byref(written)):
            raise OSError("WriteProcessMemory 失败")
        tid = wintypes.DWORD(0)
        th = k32.CreateRemoteThread(h, None, 0, ctypes.c_void_p(load_lib),
                                    ctypes.c_void_p(remote), 0, ctypes.byref(tid))
        if not th:
            raise OSError("CreateRemoteThread 失败")
        r = k32.WaitForSingleObject(th, int(timeout * 1000))
        if r == WAIT_TIMEOUT:
            raise TimeoutError("LoadLibraryW 未在超时前返回")
        code = wintypes.DWORD(0)
        k32.GetExitCodeThread(th, ctypes.byref(code))
        k32.CloseHandle(th)
        k32.VirtualFreeEx(h, ctypes.c_void_p(remote), 0, MEM_RELEASE)
        if not code.value:
            raise RuntimeError("LoadLibraryW 返回 0(注入失败)")
        return int(code.value)
    finally:
        k32.CloseHandle(h)


def main() -> int:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
    from th155.mem import find_pid
    dll = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "th155_rl_bridge.dll")
    pid = int(sys.argv[2]) if len(sys.argv) > 2 else find_pid("th155.exe")
    t0 = time.time()
    hmod = inject(pid, dll)
    print(f"[ok] injected pid={pid} hmod={hmod:#x} dll={dll} ({time.time()-t0:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
