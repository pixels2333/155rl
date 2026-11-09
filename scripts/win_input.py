"""Send keyboard input to the th155 window (focus via thread attach + SendInput)."""
import ctypes
import ctypes.wintypes as wt
import sys
import time

u = ctypes.WinDLL("user32", use_last_error=True)

KEYEVENTF_KEYUP = 0x0002
VK = {
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "enter": 0x0D, "esc": 0x1B, "shift": 0x10, "ctrl": 0x11,
    "space": 0x20, "tab": 0x09,
}
for _ch in "abcdefghijklmnopqrstuvwxyz":
    VK[_ch] = ord(_ch.upper())
for _d in "0123456789":
    VK[_d] = ord(_d)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


def find_game_window() -> int:
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if "Antinomy" in b.value:
                found.append((h, b.value))
        return True

    u.EnumWindows(cb, 0)
    if not found:
        raise LookupError("th155 window not found")
    return found[0][0]


def focus(hwnd: int) -> None:
    u.ShowWindow(hwnd, 9)
    fg = u.GetForegroundWindow()
    tid_fg = u.GetWindowThreadProcessId(fg, None)
    tid_t = u.GetWindowThreadProcessId(hwnd, None)
    if tid_fg != tid_t:
        u.AttachThreadInput(tid_fg, tid_t, True)
    u.BringWindowToTop(hwnd)
    u.SetForegroundWindow(hwnd)
    u.SetFocus(hwnd)
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # TOPMOST, NOSIZE|NOMOVE
    u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)  # NOTOPMOST
    if tid_fg != tid_t:
        u.AttachThreadInput(tid_fg, tid_t, False)
    time.sleep(0.15)


KEYEVENTF_SCANCODE = 0x0008
SCAN = {"up": 0x48, "down": 0x50, "left": 0x4B, "right": 0x4D,
        "z": 0x2C, "x": 0x2D, "c": 0x2E, "a": 0x1E, "s": 0x1F,
        "enter": 0x1C, "esc": 0x01, "shift": 0x2A, "ctrl": 0x1D,
        "space": 0x39, "tab": 0x0F}


def _send(vk: int, up: bool) -> None:
    inp = INPUT(type=1)
    inp.u.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _send_scan(vk: int, up: bool) -> None:
    """Send a hardware scancode (DirectInput/raw-input friendly)."""
    name = {v: k for k, v in VK.items()}.get(vk)
    scan = SCAN.get(name, 0)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=1)
    inp.u.ki = KEYBDINPUT(0, scan, flags, 0, 0)
    u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def press(name: str, hold: float = 0.06) -> None:
    vk = VK[name.lower()]
    _send_scan(vk, False)
    _send(vk, False)
    time.sleep(hold)
    _send_scan(vk, True)
    _send(vk, True)
    time.sleep(0.12)


if __name__ == "__main__":
    hwnd = find_game_window()
    focus(hwnd)
    for k in sys.argv[1:]:
        press(k)
        print("sent", k)
    time.sleep(0.3)
