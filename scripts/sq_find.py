"""Find live Squirrel instances of a class by walking the object graph from the root table."""
import sys
from collections import deque

sys.path.insert(0, "scripts")
import sqread

VM_RVA = 0x4DACE4


def main():
    target_name = sys.argv[1] if len(sys.argv) > 1 else "PlayerTeamData"
    pid = sqread.find_pid("th155.exe")
    p = sqread.Process(pid)
    r = sqread.SQReader(p)
    base, _ = p.module_info("th155.exe")
    vm = p.u32(base + VM_RVA)
    root = vm + 48

    # resolve target class pointer through the root table path
    t, rt = r.obj(root)
    target = None
    for part in target_name.split("."):
        s = r.table_get(rt, part)
        if not s:
            raise SystemExit(f"missing root key {part}")
        t, v = r.obj(s)
        rt = v
    if t & sqread.RT_MASK != sqread.RAW_CLASS:
        raise SystemExit("not a class")
    target = rt
    print(f"target class {target_name} @{target:#x}")

    seen = set()
    q = deque([(root, "<root>")])
    hits = []
    while q:
        addr, path = q.popleft()
        if len(seen) > 200000:
            break
        t, v = r.obj(addr)
        raw = t & sqread.RT_MASK
        if raw == sqread.RAW_TABLE:
            if v in seen:
                continue
            seen.add(v)
            for key, ka, va in r.table_nodes(v):
                name = key[1] if key[0] == "str" else f"[{key[1]}]" if key[0] == "other" else "?"
                sub = f"{path}.{name}"
                t2, v2 = r.obj(va)
                if (t2 & sqread.RT_MASK) == sqread.RAW_INSTANCE:
                    cls = p.u32(v2 + 28)
                    if cls == target:
                        hits.append((sub, v2))
                        continue
                if (t2 & sqread.RT_MASK) in (sqread.RAW_TABLE, sqread.RAW_ARRAY, sqread.RAW_INSTANCE):
                    q.append((va, sub))
        elif raw == sqread.RAW_ARRAY:
            if v in seen:
                continue
            seen.add(v)
            for i, ea in enumerate(r.array(v)):
                t2, v2 = r.obj(ea)
                sub = f"{path}[{i}]"
                if (t2 & sqread.RT_MASK) == sqread.RAW_INSTANCE and p.u32(v2 + 28) == target:
                    hits.append((sub, v2))
                    continue
                if (t2 & sqread.RT_MASK) in (sqread.RAW_TABLE, sqread.RAW_ARRAY, sqread.RAW_INSTANCE):
                    q.append((ea, sub))
        elif raw == sqread.RAW_INSTANCE:
            cls = p.u32(v + 28)
            if cls in seen:
                continue
            seen.add(cls)
            members = p.u32(cls + 24)
            # map slot index -> field name
            names = {}
            for key, ka, va in r.table_nodes(members):
                idx = p.u32(va + 4)
                if idx & 0x02000000:
                    names[idx & 0xFFFFFF] = key[1] if key[0] == "str" else str(key)
            _, slots = r.instance(v)
            for i, sa in enumerate(slots):
                sub = f"{path}.{names.get(i, i)}"
                t2, v2 = r.obj(sa)
                if (t2 & sqread.RT_MASK) == sqread.RAW_INSTANCE and p.u32(v2 + 28) == target:
                    hits.append((sub, v2))
                    continue
                if (t2 & sqread.RT_MASK) in (sqread.RAW_TABLE, sqread.RAW_ARRAY, sqread.RAW_INSTANCE):
                    q.append((sa, sub))

    print(f"hits: {len(hits)}")
    for path, addr in hits[:20]:
        print(" ", path, hex(addr))


if __name__ == "__main__":
    main()
