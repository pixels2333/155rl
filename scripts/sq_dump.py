"""Dump Squirrel tables/classes/instances from the live th155 process.

usage: python scripts/sq_dump.py [--depth N] name [name.subkey ...]
"""
import sys

sys.path.insert(0, "scripts")
import sqread

VM_RVA = 0x4DACE4


def main(argv):
    depth = 2
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--depth":
            depth = int(argv[i + 1])
            i += 2
        else:
            args.append(argv[i])
            i += 1
    pid = sqread.find_pid("th155.exe")
    proc = sqread.Process(pid)
    base, _ = proc.module_info("th155.exe")
    r = sqread.SQReader(proc)
    vm = proc.u32(base + VM_RVA)
    rt_addr = vm + 48
    rt_type, rt = r.obj(rt_addr)
    if rt_type & sqread.RT_MASK != sqread.RAW_TABLE:
        raise SystemExit(f"root table unavailable (type={rt_type:#x})")
    cur = rt_addr
    label = "<root>"

    for part in args:
        t, v = r.obj(cur)
        raw = t & sqread.RT_MASK
        if raw == sqread.RAW_INSTANCE:
            slot = r.instance_slot(v, part)
            if not slot:
                raise SystemExit(f"{label}: instance has no field {part!r}")
            cur = slot
        elif raw == sqread.RAW_TABLE:
            slot = r.table_get(v, part)
            if not slot:
                raise SystemExit(f"{label}: table has no key {part!r}")
            cur = slot
        else:
            raise SystemExit(f"{label}: cannot index type {raw:#x}")
        label = part

    dump(r, cur, label, depth)


def dump(r, addr, label, depth):
    t, v = r.obj(addr)
    raw = t & sqread.RT_MASK
    if raw == sqread.RAW_TABLE:
        print(f"{label}: TABLE @{v:#x}")
        if depth <= 0:
            return
        for key, ka, va in sorted(r.table_nodes(v), key=lambda x: str(x[0])):
            print_obj(r, va, f"  {key[1] if key[0]=='str' else key}", depth - 1)
    elif raw == sqread.RAW_CLASS:
        print(f"{label}: CLASS @{v:#x}")
        print("  fields:", r.class_fields(v))
        if depth > 0:
            members = r.p.u32(v + 24)
            for key, ka, va in sorted(r.table_nodes(members), key=lambda x: str(x[0])):
                idx = r.p.u32(va + 4)
                kind = "field" if idx & 0x02000000 else ("method" if idx & 0x01000000 else "?")
                if kind != "field":
                    print(f"    {kind:6s} {key[1] if key[0]=='str' else key}")
    elif raw == sqread.RAW_INSTANCE:
        print(f"{label}: INSTANCE @{v:#x}")
        cls, slots = r.instance(inst=v)
        members = r.p.u32(cls + 24)
        names = {}
        for key, ka, va in r.table_nodes(members):
            idx = r.p.u32(va + 4)
            if idx & 0x02000000:
                names[idx & 0xFFFFFF] = key[1] if key[0] == "str" else str(key)
        for i, sa in enumerate(slots):
            if depth > 0:
                print_obj(r, sa, f"  [{i}] {names.get(i,'?')}", depth - 1)
    else:
        print_obj(r, addr, label, depth)


def print_obj(r, addr, label, depth):
    t, v = r.obj(addr)
    raw = t & sqread.RT_MASK
    if raw == sqread.RAW_STRING:
        print(f"{label} = {r.string_at(v)!r}")
    elif raw in (sqread.RAW_INT, sqread.RAW_FLOAT, sqread.RAW_BOOL, sqread.RAW_NULL):
        print(f"{label} = {r.value(addr)!r}")
    elif raw == sqread.RAW_TABLE:
        print(f"{label} = TABLE @{v:#x} ({sum(1 for _ in r.table_nodes(v))} keys)")
        if depth > 0:
            for key, ka, va in sorted(r.table_nodes(v), key=lambda x: str(x[0]))[:40]:
                print_obj(r, va, f"  {label}.{key[1] if key[0]=='str' else key}", depth - 1)
    elif raw == sqread.RAW_CLASS:
        print(f"{label} = CLASS @{v:#x} fields={r.class_fields(v)}")
    elif raw == sqread.RAW_INSTANCE:
        print(f"{label} = INSTANCE @{v:#x} class=@{r.instance(v)[0]:#x}")
    elif raw == sqread.RAW_ARRAY:
        print(f"{label} = ARRAY @{v:#x} size={len(r.array(v))}")
    elif raw == sqread.RAW_CLOSURE:
        print(f"{label} = CLOSURE @{v:#x}")
    elif raw == sqread.RAW_NATIVECLOSURE:
        print(f"{label} = NATIVECLOSURE @{v:#x}")
    else:
        print(f"{label} = type={t:#x} ptr={v:#x}")


if __name__ == "__main__":
    main(sys.argv[1:])
