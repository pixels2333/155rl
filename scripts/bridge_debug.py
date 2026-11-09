"""读取已注入 DLL 内部变量(借助 link /MAP 生成的符号表)。

用法:
    python scripts/bridge_debug.py                 # 打印全部调试符号
    python scripts/bridge_debug.py g_status g_dbg_k32
"""
from __future__ import annotations

import ctypes
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bridge.inject import k32, PROCESS_ALL_ACCESS, find_module_base   # noqa: E402
from th155.mem import find_pid                                       # noqa: E402

MAP = ROOT / "src" / "bridge" / "th155_rl_bridge.map"

def load_symbols() -> dict:
    """解析 link /MAP:Rva+Base 列: RVA = Rva+Base - 首选装载地址。"""
    text = MAP.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Preferred load address is ([0-9A-Fa-f]+)", text)
    preferred = int(m.group(1), 16) if m else 0x10000000
    out = {}
    for line in text.splitlines():
        m = re.match(r"\s*([0-9A-Fa-f]{4}):([0-9A-Fa-f]{8})\s+(\S+)\s+([0-9A-Fa-f]{8})", line)
        if not m:
            continue
        sec, off, name, addr = m.groups()
        if sec == "0000":
            continue
        out[name] = int(addr, 16) - preferred
    return out


def read_u32(h, addr: int) -> int:
    buf = ctypes.create_string_buffer(4)
    got = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, 4, ctypes.byref(got)):
        return -1
    return struct.unpack("<I", buf.raw[:4])[0]


def main() -> int:
    syms = load_symbols()
    want = sys.argv[1:] or [k for k in syms if k.startswith("?g_")]
    pid = find_pid("th155.exe")
    h = k32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    try:
        base = find_module_base(h, pid, "th155_rl_bridge.dll")
    except LookupError:
        print("[x] 桥接 DLL 未加载")
        return 1
    print(f"pid={pid} dll_base={base:#x}")
    for w in want:
        for key in (w, f"?{w}@@3IC", f"?{w}@@3IA"):
            if key in syms:
                rva = syms[key]
                print(f"  {w:22s} rva={rva:#08x} value={read_u32(h, base + rva):#x}")
                break
        else:
            print(f"  {w:22s} <symbol not found>")
    k32.CloseHandle(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
