"""Probe: locate the live Squirrel VM and dump the root table keys/values."""
import sys

sys.path.insert(0, "scripts")
import sqread

VM_RVA = 0x4DACE4  # dword_8DACE4 in WinMain, see docs/re

pid = sqread.find_pid("th155.exe")
proc = sqread.Process(pid)
base, size = proc.module_info("th155.exe")
print(f"pid={pid} base={base:#x} size={size:#x}")
vm = proc.u32(base + VM_RVA)
print(f"vm @{base + VM_RVA:#x} = {vm:#x}")
if not vm:
    raise SystemExit("VM pointer is null (game not fully initialised?)")

r = sqread.SQReader(proc)
rt_type, rt_ptr = r.obj(vm + 48)
print(f"roottable type={rt_type:#x} ptr={rt_ptr:#x}")
if rt_type & sqread.RT_MASK != sqread.RAW_TABLE:
    raise SystemExit("unexpected roottable type")

nodes = list(r.table_nodes(rt_ptr))
print(f"root keys: {len(nodes)}")
for key, ka, va in nodes:
    t, v = r.obj(va)
    raw = t & sqread.RT_MASK
    if raw == sqread.RAW_STRING:
        desc = f"str({r.string_at(v)[:40]!r})"
    elif raw in (sqread.RAW_INT, sqread.RAW_FLOAT, sqread.RAW_BOOL, sqread.RAW_NULL):
        desc = repr(r.value(va))
    else:
        desc = f"obj(type={t:#x}, ptr={v:#x})"
    name = key[1] if key[0] == "str" else repr(key)
    print(f"  {name:32s} {desc}")
