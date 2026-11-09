"""多实例启动器:同一台机器上并行跑多个 th155.exe,每个实例按 slot(0-9)编号。

游戏自带两处"单实例"机制,启动时在挂起状态下把相关字符串补丁成按 slot 区分(同长替换,
只改本进程的写时复制页,磁盘上的 exe 不动):
  * 0x841d38 "th155":CreateMutexA(0,1,"th155") 已存在(0xB7)就报错退出;同一串也是窗口类名
    → "th1r{slot}"
  * 0x832360 "%d":wsprintfA(buf,"%d",-1) 生成看门狗共享内存 "TOKEN_-1" 的后缀,子进程从
    命令行拿到同一个串 → "r{slot}"(字面串,不再含格式符),各实例的 TOKEN 映射互不相干
父进程(CreateProcess 返回的那个)跑游戏逻辑并持有窗口,桥接 DLL 注入它;它再拉起的
th155.exe 子进程是看门狗,结束实例时连同子进程一起结束。

可选加速(`period_us`):主循环 `sub_40DBB0` 的帧间隔是 0x40dbe8 处 `mov [esp+28h], 411Ah`
(16666us)的立即数。每秒统计时若全局最高 fps(0x8DACEC)仍 < 60,period 再 -33(启动追帧);
一旦跑到过 60fps 就不再变。战斗中锁步由策略定速,period 只决定每帧最少耗时;
菜单/回合间(不锁步)按约 1e6/period_us fps 自由运行(实测见 docs/multi_instance_feasibility.md §3.2)。

slot 归属直接读进程内存里的补丁串判定,不依赖任何外部登记表:学习者/worker 被杀后
重新接管同一 slot 也能找到旧实例。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from th155.mem import Process, find_game_dir, find_pids

# exe 首选基址 0x400000 下的地址 → RVA(DYNAMICBASE,实际基址从 PEB 取)
MUTEX_NAME_RVA = 0x841D38 - 0x400000
TOKEN_FMT_RVA = 0x832360 - 0x400000
MUTEX_NAME_ORIG = b"th155\0"
TOKEN_FMT_ORIG = b"%d\0"
FRAME_PERIOD_RVA = 0x40DBE8 - 0x400000
FRAME_PERIOD_OPCODE = bytes.fromhex("c7442428")      # mov dword ptr [esp+28h], imm32
FRAME_PERIOD_ORIG_US = 16666

DLL = Path(__file__).resolve().parent.parent / "bridge" / "th155_rl_bridge.dll"

CREATE_SUSPENDED = 0x00000004
DETACHED_PROCESS = 0x00000008
PAGE_READWRITE = 0x04
TH32CS_SNAPPROCESS = 0x00000002

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR,
                                ctypes.c_void_p, ctypes.c_void_p]
_k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_k32.VirtualProtectEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD,
                                  ctypes.POINTER(wt.DWORD)]
_k32.ResumeThread.argtypes = [wt.HANDLE]
_k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.c_void_p]
_k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.c_void_p]
_ntdll = ctypes.WinDLL("ntdll")
_ntdll.NtQueryInformationProcess.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wt.ULONG, ctypes.POINTER(wt.ULONG)]


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD),
                ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.POINTER(wt.BYTE)), ("hStdInput", wt.HANDLE),
                ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class _PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [("ExitStatus", ctypes.c_long), ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_size_t), ("BasePriority", ctypes.c_long),
                ("UniqueProcessId", ctypes.c_size_t),
                ("InheritedFromUniqueProcessId", ctypes.c_size_t)]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260)]


def _slot_tag(slot: int) -> str:
    if not 0 <= int(slot) <= 9:
        raise ValueError(f"slot 必须是 0-9(补丁串只有 1 个字符位): {slot}")
    return str(int(slot))


def _rpm(h, addr: int, size: int) -> bytes:
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not _k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)) \
            or got.value != size:
        raise OSError(ctypes.get_last_error(), f"ReadProcessMemory({addr:#x})")
    return buf.raw


def _patch(h, addr: int, expect: bytes, new: bytes) -> None:
    """校验原字节后写入同长补丁(临时把只读页改成可写)。"""
    cur = _rpm(h, addr, len(expect))
    if cur != expect:
        raise RuntimeError(f"{addr:#x} 处字节 {cur!r} 不是预期的 {expect!r}(exe 版本不对?)")
    old = wt.DWORD(0)
    if not _k32.VirtualProtectEx(h, ctypes.c_void_p(addr), len(new), PAGE_READWRITE,
                                 ctypes.byref(old)):
        raise OSError(ctypes.get_last_error(), "VirtualProtectEx")
    try:
        buf = ctypes.create_string_buffer(new, len(new))
        wrote = ctypes.c_size_t(0)
        if not _k32.WriteProcessMemory(h, ctypes.c_void_p(addr), buf, len(new),
                                       ctypes.byref(wrote)) or wrote.value != len(new):
            raise OSError(ctypes.get_last_error(), "WriteProcessMemory")
    finally:
        _k32.VirtualProtectEx(h, ctypes.c_void_p(addr), len(new), old.value,
                              ctypes.byref(wt.DWORD(0)))


def _image_base(h) -> int:
    """挂起进程的 exe 基址:读 PEB.ImageBaseAddress(本进程 64 位,读的是 64 位 PEB +0x10)。"""
    pbi = _PROCESS_BASIC_INFORMATION()
    st = _ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None)
    if st != 0 or not pbi.PebBaseAddress:
        raise OSError(f"NtQueryInformationProcess 失败: {st & 0xFFFFFFFF:#x}")
    return int.from_bytes(_rpm(h, pbi.PebBaseAddress + 0x10, 8), "little")


def _children(pid: int) -> List[int]:
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wt.HANDLE(-1).value:
        return []
    out: List[int] = []
    try:
        pe = _PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = _k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            if pe.th32ParentProcessID == pid and pe.th32ProcessID != pid:
                out.append(int(pe.th32ProcessID))
            ok = _k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        _k32.CloseHandle(snap)
    return out


def _window_pids(candidates: set) -> List[int]:
    """candidates 中拥有可见、带标题顶层窗口的 pid(含 Default 桌面)。"""
    u = ctypes.windll.user32
    hits: List[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _lp):
        owner = wt.DWORD(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in candidates and u.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(200)
            u.GetWindowTextW(hwnd, buf, 200)
            if buf.value and owner.value not in hits:
                hits.append(owner.value)
        return True

    u.EnumWindows(_cb, 0)
    if not hits:
        hdesk = u.OpenDesktopW("Default", 0, False, 0x01FF)
        if hdesk:
            try:
                u.EnumDesktopWindows(hdesk, _cb, 0)
            finally:
                u.CloseDesktop(hdesk)
    return hits


def instance_slot(pid: int) -> Optional[int]:
    """读进程内补丁串:launch_instance 启动的实例返回 slot,其它(原版/看门狗/已退出)返回 None。"""
    try:
        p = Process(pid)
        try:
            base, _size = p.module_info("th155.exe")
            raw = p.read(base + MUTEX_NAME_RVA, 6)
        finally:
            p.k32.CloseHandle(p.h)
    except (OSError, LookupError):
        return None
    if raw[:4] == b"th1r" and raw[5:6] == b"\0" and raw[4:5].isdigit():
        return int(raw[4:5])
    return None


def list_instances() -> Dict[int, int]:
    """{slot: pid}。"""
    out: Dict[int, int] = {}
    for pid in find_pids("th155.exe"):
        slot = instance_slot(pid)
        if slot is not None:
            out[slot] = pid
    return out


def slot_pid(slot: int) -> Optional[int]:
    _slot_tag(slot)
    return list_instances().get(int(slot))


def kill_instance(pid: int, timeout: float = 10.0) -> None:
    """结束实例及其看门狗子进程,等到全部退出。"""
    victims = {int(pid), *_children(int(pid))}
    subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = victims & set(find_pids("th155.exe"))
        if not alive:
            return
        for v in alive:
            subprocess.run(["taskkill", "/PID", str(v), "/F"], capture_output=True)
        time.sleep(0.2)
    raise TimeoutError(f"实例 pid={pid} 未在 {timeout:.0f}s 内退出: {sorted(alive)}")


def _spawn_patched(slot: int, period_us: Optional[int] = None) -> int:
    tag = _slot_tag(slot)
    game_dir = find_game_dir()
    exe = os.path.join(game_dir, "th155.exe")
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = "WinSta0\\Default"
    pi = _PROCESS_INFORMATION()
    cmd = ctypes.create_unicode_buffer(f'"{exe}"')
    if not _k32.CreateProcessW(exe, cmd, None, None, False,
                               CREATE_SUSPENDED | DETACHED_PROCESS, None, game_dir,
                               ctypes.byref(si), ctypes.byref(pi)):
        raise OSError(ctypes.get_last_error(), "CreateProcessW")
    try:
        base = _image_base(pi.hProcess)
        _patch(pi.hProcess, base + MUTEX_NAME_RVA, MUTEX_NAME_ORIG, f"th1r{tag}\0".encode())
        _patch(pi.hProcess, base + TOKEN_FMT_RVA, TOKEN_FMT_ORIG, f"r{tag}\0".encode())
        if period_us is not None and int(period_us) != FRAME_PERIOD_ORIG_US:
            _patch(pi.hProcess, base + FRAME_PERIOD_RVA,
                   FRAME_PERIOD_OPCODE + FRAME_PERIOD_ORIG_US.to_bytes(4, "little"),
                   FRAME_PERIOD_OPCODE + int(period_us).to_bytes(4, "little"))
        if _k32.ResumeThread(pi.hThread) == 0xFFFFFFFF:
            raise OSError(ctypes.get_last_error(), "ResumeThread")
    except BaseException:
        _k32.TerminateProcess(pi.hProcess, 1)
        raise
    finally:
        _k32.CloseHandle(pi.hThread)
        _k32.CloseHandle(pi.hProcess)
    return int(pi.dwProcessId)


def launch_instance(slot: int, timeout: float = 90.0, inject_dll: bool = True,
                    period_us: Optional[int] = None) -> int:
    """启动 slot 号实例,等到标题菜单可交互并注入桥接 DLL,返回游戏逻辑进程 pid。

    slot 上已有实例 → 先结束它(调用方要的是一个干净的新实例)。
    `period_us`:帧间隔(微秒),None = 原版 16666;须在 [1000, 16666] 内。
    """
    if period_us is not None and not 1000 <= int(period_us) <= FRAME_PERIOD_ORIG_US:
        raise ValueError(f"period_us 须在 [1000, {FRAME_PERIOD_ORIG_US}] 内: {period_us}")
    from bridge.inject import inject
    from bridge.shm import BridgeShm
    from th155 import Th155Game
    from th155.menu import MenuDriver

    old = slot_pid(slot)
    if old is not None:
        kill_instance(old)
    pid = _spawn_patched(slot, period_us)
    try:
        deadline = time.time() + timeout
        target = None
        while target is None:
            if pid not in find_pids("th155.exe"):
                raise RuntimeError(f"slot {slot} 实例 pid={pid} 启动后退出")
            wins = _window_pids({pid, *_children(pid)})
            target = wins[0] if wins else None
            if target is None:
                if time.time() > deadline:
                    raise TimeoutError(f"slot {slot} 实例 pid={pid} {timeout:.0f}s 内没有窗口")
                time.sleep(0.3)
        ready = False
        while not ready:
            try:
                ready = MenuDriver(Th155Game(verify_version=False, pid=target)) \
                    .wait_title_ready(timeout=1.0)
            except (OSError, RuntimeError, LookupError):
                pass                                  # VM 尚未初始化
            if not ready:
                if time.time() > deadline:
                    raise TimeoutError(f"slot {slot} 实例 pid={target} 标题菜单 {timeout:.0f}s 内未就绪")
                time.sleep(0.5)
        if inject_dll:
            inject(target, str(DLL))
            BridgeShm.open(target, timeout=5.0).close()
        return target
    except BaseException:
        try:
            kill_instance(pid)
        except Exception:                             # noqa: BLE001 - 清理失败不掩盖原始异常
            pass
        raise


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "launch":
        for s in sys.argv[2:]:
            t0 = time.time()
            print(f"slot {s}: pid={launch_instance(int(s))} ({time.time() - t0:.1f}s)", flush=True)
    elif cmd == "kill":
        for s in sys.argv[2:]:
            p = slot_pid(int(s))
            if p is not None:
                kill_instance(p)
            print(f"slot {s}: killed pid={p}")
    print(list_instances())
