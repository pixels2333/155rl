"""Convert TH13.5 multiplicative white-corner shadow*.bmp TFBM files and .pat shadow layers
to standard alpha-blended (blend_mode = 0) transparent-corner shadows for TH15.5.

Why this is needed:
In TH15.5's sprite pixel shader, vertex color (actor.red, actor.green, actor.blue) multiplies
SRC_COLOR before multiplicative blending (blend_mode = 3). Whenever a background mob has
actor RGB < 1.0 (e.g. bg04 indoor Shou, bg05, bg09 sunset tanukis, bg11 night villagers),
the opaque white (255,255,255) corners of shadow*.bmp become < 1.0 and darken a visible
rectangular box around the character's feet. Converting shadow*.bmp to alpha-blended dark
cool shadows (alpha = 0 at white corners) with blend_mode = 0 eliminates the box completely.
"""

from __future__ import annotations

import glob
import os
import struct
import zlib


def convert_shadow_tfbm(path: str) -> bool:
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < 22 or raw[:5] != b"TFBM\x00" or raw[5] != 32:
        return False

    w, h, pitch, clen = struct.unpack_from("<IIII", raw, 6)
    pix = bytearray(zlib.decompress(raw[22 : 22 + clen]))
    if len(pix) != pitch * h * 4:
        raise ValueError(f"{path}: unexpected decompressed size {len(pix)} != {pitch * h * 4}")

    # Idempotency guard: original TH13.5 multiplicative shadow has opaque white (ff ff ff ff) at (0,0)
    if pix[0:4] != b"\xff\xff\xff\xff":
        return False

    out = bytearray(len(pix))
    for y in range(h):
        row_base = y * pitch
        for x in range(w):
            o = (row_base + x) * 4
            b, g, r, _a = pix[o], pix[o + 1], pix[o + 2], pix[o + 3]
            # Original darkness is carried primarily in R and G channels (~126..255 -> up to ~50% alpha)
            darkness = 255.0 - 0.5 * (r + g)
            if darkness <= 1.0:
                out[o : o + 4] = b"\x00\x00\x00\x00"
            else:
                alpha = int(round(min(255.0, max(0.0, darkness * 0.92))))
                # Dark cool navy-indigo shadow RGB (BGRA order)
                out[o] = 38      # B
                out[o + 1] = 8   # G
                out[o + 2] = 0   # R
                out[o + 3] = alpha
        # Zero padding columns (w .. pitch-1) so bilinear filtering at right border never bleeds
        for x in range(w, pitch):
            o = (row_base + x) * 4
            out[o : o + 4] = b"\x00\x00\x00\x00"

    comp = zlib.compress(bytes(out), 9)
    new_raw = raw[:6] + struct.pack("<IIII", w, h, pitch, len(comp)) + comp
    with open(path, "wb") as f:
        f.write(new_raw)
    return True


def patch_pat_shadow_blend(path: str) -> int:
    with open(path, "rb") as f:
        b = bytearray(f.read())
    if len(b) < 4:
        return 0

    nb = b[1]
    pos = 2 + nb * 5
    ntex = struct.unpack_from("<H", b, pos)[0]
    pos += 2
    tex_names: list[str] = []
    for _ in range(ntex):
        et = b[pos]
        slen = struct.unpack_from("<I", b, pos + 1)[0]
        name = b[pos + 5 : pos + 5 + slen].decode("cp932", errors="replace")
        tex_names.append(name)
        pos += 5 + slen + (16 if et == 4 else 0)

    _unk16, nmot = struct.unpack_from("<HI", b, pos)
    pos += 6
    patched = 0
    for _ in range(nmot):
        _mid, _mflags, _munk, _mloop, nk = struct.unpack_from("<IIbHI", b, pos)
        pos += 15
        for _ in range(nk):
            nl = b[pos + 2]
            pos += 3 + nl + 1
            for _li in range(nl):
                tid = struct.unpack_from("<i", b, pos)[0]
                if not (0 <= tid < ntex):
                    raise ValueError(f"{path}: invalid tex_id {tid} (ntex={ntex})")
                has_xf = b[pos + 16]
                if has_xf == 2:
                    blend = struct.unpack_from("<h", b, pos + 17)[0]
                    if "shadow" in tex_names[tid].lower() and blend == 3:
                        struct.pack_into("<h", b, pos + 17, 0)
                        patched += 1
                    pos += 34
                else:
                    pos += 18
            pos += 83

    if pos != len(b):
        raise ValueError(f"{path}: parser ended at {pos} != EOF {len(b)}")

    if patched > 0:
        with open(path, "wb") as f:
            f.write(b)
    return patched


def main() -> None:
    mod_data = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mod", "data")
    shadow_files = sorted(glob.glob(os.path.join(mod_data, "**", "*shadow*.bmp"), recursive=True))
    converted_bmps = sum(1 for sf in shadow_files if convert_shadow_tfbm(sf))

    pat_files = sorted(glob.glob(os.path.join(mod_data, "**", "*.pat"), recursive=True))
    patched_pats = 0
    patched_layers = 0
    for pf in pat_files:
        n = patch_pat_shadow_blend(pf)
        if n > 0:
            patched_pats += 1
            patched_layers += n

    print(
        f"Shadow fix complete: converted {converted_bmps}/{len(shadow_files)} shadow .bmp files; "
        f"updated {patched_layers} shadow layers across {patched_pats}/{len(pat_files)} .pat files."
    )


if __name__ == "__main__":
    main()
