"""极简 PE32 工具:按 RVA 读取 th155.exe 的字节(用于校验 RVA/函数入口)。"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from .mem import find_game_dir  # noqa: F401  (仅用于默认路径)


class PE:
    def __init__(self, path: str):
        self.path = path
        self.data = Path(path).read_bytes()
        d = self.data
        e_lfanew = struct.unpack_from("<I", d, 0x3C)[0]
        assert d[e_lfanew:e_lfanew + 4] == b"PE\0\0", "not a PE"
        coff = e_lfanew + 4
        self.machine, self.nsections, self.timestamp = struct.unpack_from("<HHI", d, coff)
        opt = coff + 20
        self.image_base = struct.unpack_from("<I", d, opt + 28)[0]
        self.image_size = struct.unpack_from("<I", d, opt + 56)[0]
        self.sections = []
        s = opt + struct.unpack_from("<H", d, coff + 16)[0]
        for i in range(self.nsections):
            off = s + i * 40
            name = d[off:off + 8].rstrip(b"\0").decode("ascii", "replace")
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", d, off + 8)
            self.sections.append((name, vaddr, vsize, rawptr, rawsize))

    def rva_to_off(self, rva: int) -> int:
        for name, vaddr, vsize, rawptr, rawsize in self.sections:
            if vaddr <= rva < vaddr + max(vsize, rawsize):
                return rawptr + (rva - vaddr)
        raise ValueError(f"RVA {rva:#x} 不在任何节内")

    def read_rva(self, rva: int, size: int) -> bytes:
        off = self.rva_to_off(rva)
        return self.data[off:off + size]

    def u32_rva(self, rva: int) -> int:
        return struct.unpack("<I", self.read_rva(rva, 4))[0]

    def scan(self, pattern: bytes, limit: int = 0x200) -> list:
        """在 .text 中搜索字节串,返回 RVA 列表。"""
        hits = []
        for name, vaddr, vsize, rawptr, rawsize in self.sections:
            if name != ".text":
                continue
            blob = self.data[rawptr:rawptr + rawsize]
            start = 0
            while True:
                i = blob.find(pattern, start)
                if i < 0:
                    break
                hits.append(vaddr + i)
                start = i + 1
                if limit and len(hits) >= limit:
                    return hits
        return hits


if __name__ == "__main__":
    import os
    game = os.path.join(find_game_dir(), "th155.exe")
    pe = PE(sys.argv[1] if len(sys.argv) > 1 else game)
    print(f"image_base={pe.image_base:#x} image_size={pe.image_size:#x} "
          f"timestamp={pe.timestamp} sections={[(n, hex(v)) for n, v, *_ in pe.sections]}")
