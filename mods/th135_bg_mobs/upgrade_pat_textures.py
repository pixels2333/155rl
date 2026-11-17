import glob
import os
import struct

MOD_DATA = r"d:\programme\155rl\mods\th135_bg_mobs\mod\data"
SHARED_MOB_DIR = os.path.join(MOD_DATA, "background", "mob")


def get_image_size(img_path: str) -> tuple[int, int]:
    with open(img_path, "rb") as f:
        hdr = f.read(32)
    if hdr[:4] == b"TFBM":
        w, h = struct.unpack_from("<II", hdr, 6)
        return w, h
    if hdr[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack_from(">II", hdr, 16)
        return w, h
    if hdr[:2] == b"BM":
        w, h = struct.unpack_from("<ii", hdr, 18)
        return abs(w), abs(h)
    if hdr[:4] == b"DDS ":
        h, w = struct.unpack_from("<II", hdr, 12)
        return w, h
    raise ValueError(f"Unsupported image header {hdr[:8]!r} in {img_path}")


def upgrade_pat_file(pat_path: str) -> tuple[bool, int]:
    data = open(pat_path, "rb").read()
    if len(data) < 24:
        return False, 0
    ver = data[0]
    nb = data[1]
    if ver not in (12, 13):
        return False, 0

    hdr_end = 2 + nb * 5
    ntex = struct.unpack_from("<H", data, hdr_end)[0]
    if ntex == 0:
        return False, 0

    pos = hdr_end + 2
    out_chunks = [data[:pos]]
    upgraded_entries = 0
    pat_dir = os.path.dirname(pat_path)

    for _ in range(ntex):
        etype = data[pos]
        slen = struct.unpack_from("<I", data, pos + 1)[0]
        fn_bytes = data[pos + 5 : pos + 5 + slen]
        fn_str = fn_bytes.decode("ascii").replace("/", "\\")

        if etype == 4:
            entry_len = 5 + slen + 16
            out_chunks.append(data[pos : pos + entry_len])
            pos += entry_len
        elif etype == 0:
            cand1 = os.path.join(pat_dir, fn_str)
            cand2 = os.path.join(SHARED_MOB_DIR, os.path.basename(fn_str))
            img_path = cand1 if os.path.exists(cand1) else cand2
            if not os.path.exists(img_path):
                raise FileNotFoundError(f"Missing texture {fn_str!r} referenced by {pat_path}")
            w, h = get_image_size(img_path)
            if w <= 0 or h <= 0:
                raise ValueError(f"Invalid size {(w, h)} for {img_path}")
            out_chunks.append(b"\x04" + struct.pack("<I", slen) + fn_bytes + struct.pack("<IIII", 0, 0, w, h))
            pos += 5 + slen
            upgraded_entries += 1
        else:
            raise ValueError(f"Unexpected texture entry type {etype} in {pat_path} at offset {pos}")

    if upgraded_entries == 0:
        return False, 0

    out_chunks.append(data[pos:])
    new_data = b"".join(out_chunks)
    with open(pat_path, "wb") as f:
        f.write(new_data)
    return True, upgraded_entries


def main():
    pats = sorted(glob.glob(os.path.join(MOD_DATA, "**", "*.pat"), recursive=True))
    modified_files = 0
    total_entries = 0
    for p in pats:
        changed, count = upgrade_pat_file(p)
        if changed:
            modified_files += 1
            total_entries += count
    print(f"Scanned {len(pats)} .pat files: upgraded {total_entries} texture entries across {modified_files} .pat files.")


if __name__ == "__main__":
    main()
