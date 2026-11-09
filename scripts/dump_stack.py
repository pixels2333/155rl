"""极简 minidump 解析器:打印崩溃线程的异常信息与调用栈候选。

用法: python scripts/dump_stack.py <dump> [--max-words 4000]
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

STREAM_THREAD_LIST = 3
STREAM_MODULE_LIST = 4
STREAM_MEMORY_LIST = 5
STREAM_EXCEPTION = 6
STREAM_MEMORY64_LIST = 9


def parse(path: Path):
    d = path.read_bytes()
    assert d[:4] == b"MDMP", "not a minidump"
    (nstreams,) = struct.unpack_from("<I", d, 8)
    (dir_rva,) = struct.unpack_from("<I", d, 12)
    streams = {}
    for i in range(nstreams):
        off = dir_rva + i * 12
        stype, size, rva = struct.unpack_from("<III", d, off)
        streams[stype] = (rva, size)

    modules = []
    if STREAM_MODULE_LIST in streams:
        rva, _ = streams[STREAM_MODULE_LIST]
        (n,) = struct.unpack_from("<I", d, rva)
        for i in range(n):
            off = rva + 4 + i * 108
            base, size = struct.unpack_from("<QI", d, off)
            name_rva, = struct.unpack_from("<I", d, off + 20)
            (nlen,) = struct.unpack_from("<I", d, name_rva)
            name = d[name_rva + 4:name_rva + 4 + nlen].decode("utf-16-le", "replace")
            modules.append({"base": base, "size": size, "name": name})

    exc = None
    if STREAM_EXCEPTION in streams:
        rva, _ = streams[STREAM_EXCEPTION]
        tid, = struct.unpack_from("<I", d, rva)
        rec = rva + 8
        code, flags = struct.unpack_from("<II", d, rec)
        addr, = struct.unpack_from("<Q", d, rec + 16)
        nparams, = struct.unpack_from("<I", d, rec + 24)
        params = struct.unpack_from("<15Q", d, rec + 32)
        ctx_size, ctx_rva = struct.unpack_from("<II", d, rec + 152)
        ctx = parse_x86_context(d[ctx_rva:ctx_rva + ctx_size])
        exc = {"tid": tid, "code": code, "flags": flags, "addr": addr,
               "params": params[:nparams], "ctx": ctx}

    ranges = []
    if STREAM_MEMORY_LIST in streams:
        rva, _ = streams[STREAM_MEMORY_LIST]
        (n,) = struct.unpack_from("<I", d, rva)
        for i in range(n):
            off = rva + 4 + i * 16
            start, = struct.unpack_from("<Q", d, off)
            size, data_rva = struct.unpack_from("<II", d, off + 8)
            ranges.append((start, size, data_rva))
    if STREAM_MEMORY64_LIST in streams:
        rva, _ = streams[STREAM_MEMORY64_LIST]
        (n, base_rva) = struct.unpack_from("<QI", d, rva)
        cur = base_rva
        for i in range(n):
            start, size = struct.unpack_from("<QI", d, rva + 16 + i * 16)
            ranges.append((start, size, cur))
            cur += size
    return d, modules, exc, ranges


def parse_x86_context(b: bytes) -> dict:
    # CONTEXT_i386 (0x2CC bytes): ContextFlags@0, Dr@4.., FloatSave@0x1C(112),
    # SegGs@0x8C, SegFs@0x90, SegEs@0x94, SegDs@0x98,
    # Edi@0x9C Esi@0xA0 Ebx@0xA4 Edx@0xA8 Ecx@0xAC Eax@0xB0
    # Ebp@0xB4 Eip@0xB8 SegCs@0xBC EFlags@0xC0 Esp@0xC4 SegSs@0xC8
    names = ["Edi", "Esi", "Ebx", "Edx", "Ecx", "Eax", "Ebp", "Eip", "SegCs",
             "EFlags", "Esp", "SegSs"]
    return {n: struct.unpack_from("<I", b, 0x9C + i * 4)[0] for i, n in enumerate(names)}


def read_mem(d, ranges, addr: int, size: int) -> bytes:
    for start, msize, fileoff in ranges:
        if start <= addr < start + msize:
            off = fileoff + (addr - start)
            return d[off:off + size]
    return b""


def mod_of(modules, addr: int):
    for m in modules:
        if m["base"] <= addr < m["base"] + m["size"]:
            return m
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--max-words", type=int, default=3000)
    a = ap.parse_args()
    d, modules, exc, ranges = parse(Path(a.dump))
    print(f"dump: {a.dump}  modules={len(modules)} mem_ranges={len(ranges)}")
    if exc:
        print(f"exception code={exc['code']:#x} addr={exc['addr']:#x} tid={exc['tid']} "
              f"params={[hex(p) for p in exc['params']]}")
        ctx = exc["ctx"]
        print("context: " + " ".join(f"{k}={v:#x}" for k, v in ctx.items()))
    for m in modules:
        if "th155" in m["name"].lower() or "bridge" in m["name"].lower():
            print(f"  module {m['name']} base={m['base']:#x} size={m['size']:#x}")

    ctx = exc["ctx"] if exc else None
    if not ctx:
        return 1
    esp, ebp = ctx["Esp"], ctx["Ebp"]

    print("\n--- EBP 链 ---")
    frame = ebp
    for i in range(24):
        if not frame:
            break
        buf = read_mem(d, ranges, frame, 8)
        if len(buf) < 8:
            break
        nxt, ret = struct.unpack("<II", buf)
        m = mod_of(modules, ret)
        tag = f"{m['name']}+{ret - m['base']:#x}" if m else "?"
        print(f"  #{i:02d} frame={frame:#010x} ret={ret:#010x} {tag}")
        if nxt <= frame:
            break
        frame = nxt

    print("\n--- 栈原始字 ---")
    raw = read_mem(d, ranges, esp, 0x100)
    for i in range(0, len(raw) - 4, 4):
        v = struct.unpack_from("<I", raw, i)[0]
        m = mod_of(modules, v)
        note = ""
        if m:
            note = f"{m['name'].split(chr(92))[-1]}+{v - m['base']:#x}"
        elif 0x800000 <= v < 0xA00000 or 0x400000 <= v < 0x516000 + 0x400000:
            note = "th155:static?"
        elif v > 0x10000:
            note = "ptr"
        print(f"  [esp+{i:#04x}] {v:#010x}  {note}")
    print("\n--- 栈扫描(可能是返回地址的字) ---")
    buf = read_mem(d, ranges, esp, a.max_words * 4)
    hits = []
    for i in range(0, len(buf) - 4, 4):
        v = struct.unpack_from("<I", buf, i)[0]
        m = mod_of(modules, v)
        if m and "th155" in m["name"].lower():
            hits.append((esp + i, v, v - m["base"]))
    for addr, v, rva in hits[:80]:
        print(f"  [{addr:#010x}] {v:#010x}  th155+{rva:#x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
