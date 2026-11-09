"""Capture the th155 window contents via PrintWindow (works while unfocused)."""
import ctypes
import ctypes.wintypes as wt
import sys

from PIL import Image

u = ctypes.WinDLL("user32", use_last_error=True)
gdi = ctypes.WinDLL("gdi32", use_last_error=True)


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def find_game_window() -> int:
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            if "Antinomy" in b.value:
                found.append(h)
        return True

    u.EnumWindows(cb, 0)
    if not found:
        raise LookupError("th155 window not found")
    return found[0]


def capture(path: str, hwnd: int | None = None) -> None:
    hwnd = hwnd or find_game_window()
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(hwnd)
    mdc = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)
    bi = BIH()
    bi.biSize = ctypes.sizeof(bi)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    Image.frombytes("RGBA", (w, h), buf.raw, "raw", "BGRA").save(path)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    hwnd = int(args[1], 0) if len(args) > 1 else None
    capture(args[0] if args else "shot.png", hwnd)
    print("captured")
