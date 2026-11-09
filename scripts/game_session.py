"""游戏会话管理:启动/重启 th155.exe,等待标题界面,注入桥接 DLL。

用法:
    python scripts/game_session.py restart      # 杀掉已有进程并重启 + 注入
    python scripts/game_session.py start        # 仅启动(若未运行)+ 注入
    python scripts/game_session.py kill         # 仅结束进程
    python scripts/game_session.py status       # 打印进程/共享内存状态
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from th155.mem import find_game_dir, find_pid, find_pids   # noqa: E402
from bridge.inject import inject                   # noqa: E402

DLL = ROOT / "src" / "bridge" / "th155_rl_bridge.dll"


def kill() -> None:
    """结束**所有** th155 实例。

    只杀一个是不够的:残留实例会继续写共享内存,采样侧读到冻结帧号,
    被判定成"画面停住"从而反复重启,形成死循环。实测踩过。
    """
    pids = find_pids("th155.exe")
    if not pids:
        print("[i] 没有正在运行的 th155.exe")
        return
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    for _ in range(50):
        if not find_pids("th155.exe"):
            print(f"[ok] 已结束 pid={pids}")
            return
        time.sleep(0.2)
    print(f"[w] 进程未在 10s 内退出: {find_pids('th155.exe')}")


def _spawn_on_default_desktop(exe_path: str, cwd: str) -> int:
    """使用 Win32 CreateProcessW 显式指定 lpDesktop='WinSta0\\Default' 在用户主桌面启动进程。"""
    import ctypes
    import ctypes.wintypes as wt

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wt.DWORD),
            ("lpReserved", wt.LPWSTR),
            ("lpDesktop", wt.LPWSTR),
            ("lpTitle", wt.LPWSTR),
            ("dwX", wt.DWORD),
            ("dwY", wt.DWORD),
            ("dwXSize", wt.DWORD),
            ("dwYSize", wt.DWORD),
            ("dwXCountChars", wt.DWORD),
            ("dwYCountChars", wt.DWORD),
            ("dwFillAttribute", wt.DWORD),
            ("dwFlags", wt.DWORD),
            ("wShowWindow", wt.WORD),
            ("cbReserved2", wt.WORD),
            ("lpReserved2", ctypes.POINTER(wt.BYTE)),
            ("hStdInput", wt.HANDLE),
            ("hStdOutput", wt.HANDLE),
            ("hStdError", wt.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wt.HANDLE),
            ("hThread", wt.HANDLE),
            ("dwProcessId", wt.DWORD),
            ("dwThreadId", wt.DWORD),
        ]

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    si.lpDesktop = "WinSta0\\Default"
    pi = PROCESS_INFORMATION()
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    cmd_buf = ctypes.create_unicode_buffer(f'"{exe_path}"')
    ok = k32.CreateProcessW(
        exe_path,
        cmd_buf,
        None,
        None,
        False,
        0x00000008,  # DETACHED_PROCESS
        None,
        cwd,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CreateProcessW failed")
    k32.CloseHandle(pi.hThread)
    k32.CloseHandle(pi.hProcess)
    return int(pi.dwProcessId)


def start(inject_dll: bool = True) -> int:
    pids = find_pids("th155.exe")
    if len(pids) > 1:
        # 多实例会让采样读到非当前实例的冻结帧号,必须先清理再启动。
        print(f"[w] 发现 {len(pids)} 个残留实例 {pids},先全部结束")
        kill()
        pids = []
    if pids:
        pid = pids[0]
        print(f"[i] 已在运行 pid={pid}")
    else:
        d = find_game_dir()
        pid = _spawn_on_default_desktop(os.path.join(d, "th155.exe"), d)
        print(f"[ok] 已启动 pid={pid}")
    # 立即唤醒并应用 koishi514 Mod 覆盖(含 spell_font.bmp 与各角色 .nut/.pat)
    try:
        mod_script = ROOT / "koishi514" / "apply_koishi514_mod.py"
        if mod_script.exists():
            time.sleep(0.6)
            subprocess.run([sys.executable, str(mod_script)], check=False)
    except Exception as e:
        print(f"[w] apply_koishi514_mod 执行失败: {e}")
    # 等待 Squirrel VM 初始化并且标题菜单可交互
    from th155 import Th155Game
    from th155.menu import MenuDriver
    deadline = time.time() + 90
    ready = False
    while time.time() < deadline:
        try:
            g = Th155Game(verify_version=False)
            if MenuDriver(g).wait_title_ready(timeout=1.0):
                ready = True
                print("[ok] 游戏已就绪(标题菜单可交互)")
                break
        except Exception:
            pass
        time.sleep(0.5)
    if not ready:
        print("[w] 标题菜单未在 90s 内就绪,继续尝试")
    if inject_dll:
        # 注入目标必须**按窗口选**,不能用 Popen().pid:
        # 游戏启动会产生两个 th155.exe —— 有窗口的那个才跑游戏逻辑,
        # 另一个是空壳。注入空壳会 CreateToolhelp32Snapshot 失败(错误 299),
        # 或者注进去了却没有任何效果。实测两种都踩到过。
        target = _game_window_pid() or pid
        if target != pid:
            print(f"[i] 注入目标改为有窗口的实例 pid={target}(启动返回 {pid})")
        hmod = inject(target, str(DLL))
        pid = target
        print(f"[ok] DLL 已注入 hmod={hmod:#x}")
        _show_game_window()
    return pid


def _show_game_window() -> None:
    """确保游戏主窗口处于恢复/前台显示状态，避免被最小化或遮挡。"""
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    pids = set(find_pids("th155.exe"))
    if not pids:
        return

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _lp):
        pid = wt.DWORD(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
            u.BringWindowToTop(hwnd)
        return True

    u.EnumWindows(_cb, 0)


def _game_window_pid() -> int | None:
    """返回拥有可见顶层窗口的 th155 进程 pid(即真正在跑游戏的那个)。"""
    import ctypes

    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    pids = set(find_pids("th155.exe"))
    hits: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _lp):
        pid = wt.DWORD(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(200)
            u.GetWindowTextW(hwnd, buf, 200)
            if buf.value:
                hits.append(pid.value)
        return True

    u.EnumWindows(_cb, 0)
    if not hits:
        hdesk = u.OpenDesktopW("Default", 0, False, 0x01FF)
        if hdesk:
            try:
                u.EnumDesktopWindows(hdesk, _cb, 0)
            finally:
                u.CloseDesktop(hdesk)
    return hits[0] if hits else None


def _has_bridge(pid: int) -> bool:
    """该进程是否已加载桥接 DLL(即是否是真正在跑游戏的那个实例)。"""
    sys.path.insert(0, str(ROOT / "src"))
    import bridge.inject as I
    h = I.k32.OpenProcess(I.PROCESS_ALL_ACCESS, False, pid)
    if not h:
        return False
    try:
        return bool(I.find_module_base(h, pid, DLL.name))
    except Exception:
        return False
    finally:
        I.k32.CloseHandle(h)


# 关于"游离实例清理":**不要做**。
# 游戏启动会创建两个 th155.exe(有窗口的真实例 + 一个无窗口的子进程/空壳),
# 它们是父子关系。对任一进程 taskkill /F 都会让整个游戏一起退出 ——
# 实测连续三次把刚启动的游戏清空,日志里没有任何崩溃记录。
# 因此正确做法只有一条:按窗口挑对注入目标,其余进程一律不碰。


def status() -> None:
    try:
        pid = find_pid("th155.exe")
        print(f"pid={pid}")
    except LookupError:
        print("进程:未运行")
        return
    try:
        from bridge.shm import BridgeShm
        with BridgeShm.open(timeout=0.5) as shm:
            st = shm.read_state()
            print(f"共享内存: OK  frame={st['frame']} state={st['battle_state']} "
                  f"attached={shm.s.attached} ack={shm.s.ctl_ack}")
    except Exception as e:
        print(f"共享内存: 不可用 ({e})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    no_inject = "--no-inject" in sys.argv
    if cmd == "restart":
        kill()
        start(inject_dll=not no_inject)
    elif cmd == "start":
        start(inject_dll=not no_inject)
    elif cmd == "kill":
        kill()
    else:
        status()
