import subprocess, sys, time, ctypes, struct, re
from pathlib import Path
sys.path.insert(0, r"D:\programme\155rl\src")
from bridge.inject import k32, PROCESS_ALL_ACCESS, find_module_base
ROOT = Path(r"D:\programme\155rl")
MAP = ROOT/"src"/"bridge"/"th155_rl_bridge.map"
SECTION_BASE = {"0001":0x1000,"0002":0x2000,"0003":0x3000,"0004":0x4000}
syms={}
for line in MAP.read_text(encoding="utf-8",errors="replace").splitlines():
    m=re.match(r"\s*([0-9A-Fa-f]{4}):([0-9A-Fa-f]{8})\s+(\S+)\s+([0-9A-Fa-f]{8})",line)
    if m:
        sec,off,name,addr=m.groups()
        syms[name]=SECTION_BASE.get(sec,0)+int(off,16)
p=subprocess.Popen([str(ROOT/"src"/"bridge"/"test_host.exe"), str(ROOT/"src"/"bridge"/"th155_rl_bridge.dll")])
time.sleep(1.5)
try:
    h=k32.OpenProcess(PROCESS_ALL_ACCESS,False,p.pid)
    base=find_module_base(h,p.pid,"th155_rl_bridge.dll")
    def rd(a):
        b=ctypes.create_string_buffer(4); got=ctypes.c_size_t(0)
        if not k32.ReadProcessMemory(h,ctypes.c_void_p(a),b,4,ctypes.byref(got)): return -1
        return struct.unpack("<I",b.raw[:4])[0]
    print("host pid",p.pid,"dll base",hex(base))
    for k in ("?g_status@@3IC","?g_dbg_fn@@3IC","?g_dbg_k32@@3IC","?g_is_game@@3IC"):
        if k in syms: print("  ",k.split('@')[0][1:], hex(rd(base+syms[k])))
    k32.CloseHandle(h)
except Exception as e:
    print("read err",e)
print("host exit:", hex(p.wait(15)&0xFFFFFFFF))
