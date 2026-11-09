"""Scan process memory for Squirrel instances of a given class pointer."""
import struct
import sys

sys.path.insert(0, "scripts")
import sqread

VM_RVA = 0x4DACE4


def find_instances(p: "sqread.Process", base: int, class_ptr: int):
    vt = base + 0x389C84  # ??_7SQInstance@@6B@
    out = []
    pat = struct.pack("<I", vt)
    for rbase, rsize, _ in p.regions():
        if rsize > 64 << 20:
            continue
        try:
            data = p.read_region(rbase, rsize)
        except OSError:
            continue
        pos = 0
        while True:
            i = data.find(pat, pos)
            if i < 0:
                break
            addr = rbase + i
            if addr + 48 <= rbase + rsize:
                try:
                    if p.u32(addr + 28) == class_ptr:
                        out.append(addr)
                except OSError:
                    pass
            pos = i + 4
    return out


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "actor.actor_list.master0.player_class"
    p = sqread.Process(sqread.find_pid("th155.exe"))
    r = sqread.SQReader(p)
    base, _ = p.module_info("th155.exe")
    vm = p.u32(base + VM_RVA)
    cur = vm + 48
    for part in name.split("."):
        t, v = r.obj(cur)
        raw = t & sqread.RT_MASK
        s = r.table_get(v, part) if raw == sqread.RAW_TABLE else r.instance_slot(v, part)
        if s is None:
            raise SystemExit(f"missing {part}")
        cur = s
    t, cls = r.obj(cur)
    print(f"{name} = type {t:#x} ptr {cls:#x}")
    if t & sqread.RT_MASK != sqread.RAW_CLASS:
        raise SystemExit("not a class")
    hits = find_instances(p, base, cls)
    print(f"instances: {len(hits)}")
    for h in hits[:10]:
        print("  ", hex(h))
