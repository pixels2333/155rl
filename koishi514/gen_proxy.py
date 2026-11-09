import os, struct

syswow_dll = r'C:\Windows\SysWOW64\winmm.dll'
with open(syswow_dll, 'rb') as f:
    data = f.read()

pe_off = struct.unpack('<I', data[0x3C:0x40])[0]
export_rva = struct.unpack('<I', data[pe_off+0x78:pe_off+0x7C])[0]
num_sections = struct.unpack('<H', data[pe_off+6:pe_off+8])[0]
sec_off = pe_off + 0x18 + 96 + 16*8
sections = []
for i in range(num_sections):
    s = data[sec_off+i*40:sec_off+(i+1)*40]
    vsize, vaddr, rsize, raddr = struct.unpack('<IIII', s[8:24])
    sections.append((vaddr, vsize, raddr, rsize))

def rva2off(rva):
    for vaddr, vsize, raddr, rsize in sections:
        if vaddr <= rva < vaddr + max(vsize, rsize):
            return raddr + (rva - vaddr)
    return None

exp_off = rva2off(export_rva)
ord_base = struct.unpack('<I', data[exp_off+16:exp_off+20])[0]
num_funcs = struct.unpack('<I', data[exp_off+20:exp_off+24])[0]
num_names = struct.unpack('<I', data[exp_off+24:exp_off+28])[0]
funcs_rva = struct.unpack('<I', data[exp_off+28:exp_off+32])[0]
names_rva = struct.unpack('<I', data[exp_off+32:exp_off+36])[0]
ord_rva = struct.unpack('<I', data[exp_off+36:exp_off+40])[0]

names_off = rva2off(names_rva)
ord_off = rva2off(ord_rva)
funcs_off = rva2off(funcs_rva)

all_exports = []
for i in range(num_funcs):
    matched_name = None
    for j in range(num_names):
        o = struct.unpack('<H', data[ord_off+j*2:ord_off+j*2+2])[0]
        if o == i:
            nrva = struct.unpack('<I', data[names_off+j*4:names_off+j*4+4])[0]
            noff = rva2off(nrva)
            matched_name = data[noff:data.find(b'\x00', noff)].decode('latin1')
            break
    all_exports.append((i, matched_name, i + ord_base))

print(f'Parsed {len(all_exports)} total functions.')

# Generate DEF file
def_lines = ['LIBRARY winmm.dll', 'EXPORTS']
for idx, name, ord_val in all_exports:
    if name:
        def_lines.append(f'    {name} = Proxy_{idx} @{ord_val}')
    else:
        def_lines.append(f'    Proxy_{idx} @{ord_val} NONAME')

def_path = r'D:\programme\155rl\koishi514\loader\winmm.def'
with open(def_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(def_lines) + '\n')
print(f'Wrote {def_path}')

# Generate proxy_winmm.h
h_content = '''#pragma once
#include <windows.h>

void InitRealWinmm();
'''
h_path = r'D:\programme\155rl\koishi514\loader\src\proxy_winmm.h'
with open(h_path, 'w', encoding='utf-8') as f:
    f.write(h_content)
print(f'Wrote {h_path}')

# Generate proxy_winmm.cpp
cpp_lines = [
    '#include "proxy_winmm.h"',
    '#include <stdio.h>',
    '',
    'HMODULE g_hRealWinmm = NULL;',
    f'FARPROC g_pfn[{len(all_exports)}] = {{0}};',
    '',
    'void InitRealWinmm() {',
    '    if (g_hRealWinmm) return;',
    '    char sysDir[MAX_PATH];',
    '    UINT len = GetSystemDirectoryA(sysDir, MAX_PATH);',
    '    if (len > 0) {',
    '        strcat_s(sysDir, sizeof(sysDir), "\\\\winmm.dll");',
    '        g_hRealWinmm = LoadLibraryA(sysDir);',
    '    }',
    '    if (!g_hRealWinmm) {',
    '        g_hRealWinmm = LoadLibraryA("C:\\\\Windows\\\\System32\\\\winmm.dll");',
    '    }',
    '    if (!g_hRealWinmm) return;',
    '',
]

for idx, name, ord_val in all_exports:
    if name:
        cpp_lines.append(f'    g_pfn[{idx}] = GetProcAddress(g_hRealWinmm, "{name}");')
    else:
        cpp_lines.append(f'    g_pfn[{idx}] = GetProcAddress(g_hRealWinmm, MAKEINTRESOURCEA({ord_val}));')

cpp_lines.append('}')
cpp_lines.append('')
cpp_lines.append('extern "C" {')
for idx, name, ord_val in all_exports:
    cpp_lines.append(f'__declspec(naked) void Proxy_{idx}() {{ __asm jmp dword ptr [g_pfn + {idx} * 4] }}')
cpp_lines.append('}')
cpp_lines.append('')

cpp_path = r'D:\programme\155rl\koishi514\loader\src\proxy_winmm.cpp'
with open(cpp_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(cpp_lines))
print(f'Wrote {cpp_path}')
