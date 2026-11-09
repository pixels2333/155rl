#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_koishi_spells.py — 古明地恋《东方心绮楼》(TH13.5) Boss 普通符卡独立替换 Mod
========================================================================
替换内容：
  - 符卡槽 0 (Spell_A_Init): 本能「フロウディアン」 (TH13.5 Boss 符卡 1: 双阶段斥力+引力弹幕)
  - 符卡槽 1 (Spell_B_Init): 茨符「コンファインドイノセント」 (TH13.5 Boss 符卡 2: 全屏荆棘蔷薇)
  - 符卡槽 2 (Spell_C_Init): 夢符「ご先祖様が見ているぞ」 (保留 TH15.5 原版)
  - 包含选人界面名称显示映射 (`animation_spell.nut` + `koishi.csv`) 与黑体字模
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import struct
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
PAGE_EXECUTE_READWRITE = 0x40
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010

RVA_PACKAGE_MANAGER = 0x4DAD18
RVA_GATE_CHECK = 0x026120
GATE_ORIG_BYTES = bytes.fromhex("558bec5153")
GATE_PATCH_BYTES = bytes.fromhex("b001c20800")  # mov al, 1; ret 8


def get_mod_files(mod_root: str) -> list[str]:
    mod_data_dir = os.path.join(mod_root, "mod", "data")
    files = []
    for root, _dirs, filenames in os.walk(mod_data_dir):
        for fn in filenames:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, os.path.join(mod_root, "mod")).replace("\\", "/")
            files.append(rel)
    files.sort()
    return files


def th155_pak_hash(path: str) -> int:
    s = path.replace("/", "\\").lower().encode("shift_jis")
    h = 0x811C9DC5
    for b in s:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return (-h) & 0xFFFFFFFF


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", ctypes.c_char * 256),
        ("szExePath", ctypes.c_char * 260),
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def find_th155_process():
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == ctypes.c_void_p(-1).value:
        return None
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    pids = []
    if kernel32.Process32First(snap, ctypes.byref(pe)):
        while True:
            exe = pe.szExeFile.decode("mbcs", errors="ignore").lower()
            if exe == "th155.exe":
                pids.append(pe.th32ProcessID)
            if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                break
    kernel32.CloseHandle(snap)
    return pids[-1] if pids else None


def get_module_info(pid: int):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == ctypes.c_void_p(-1).value:
        return None, None
    try:
        me = MODULEENTRY32()
        me.dwSize = ctypes.sizeof(MODULEENTRY32)
        if not kernel32.Module32First(snap, ctypes.byref(me)):
            return None, None
        while True:
            mod_name = me.szModule.decode("mbcs", errors="ignore").lower()
            if mod_name == "th155.exe":
                exe_path = me.szExePath.decode("mbcs", errors="ignore")
                return me.modBaseAddr, exe_path
            if not kernel32.Module32Next(snap, ctypes.byref(me)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return None, None


def read_mem(hproc, addr: int, size: int) -> bytes:
    buf = ctypes.create_string_buffer(size)
    read_bytes = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        hproc, ctypes.c_void_p(addr), buf, size, ctypes.byref(read_bytes)
    )
    if not ok or read_bytes.value != size:
        raise RuntimeError(f"ReadProcessMemory failed at 0x{addr:08X}")
    return buf.raw


def write_mem(hproc, addr: int, data: bytes):
    written = ctypes.c_size_t(0)
    ok = kernel32.WriteProcessMemory(
        hproc, ctypes.c_void_p(addr), data, len(data), ctypes.byref(written)
    )
    if not ok or written.value != len(data):
        raise RuntimeError(f"WriteProcessMemory failed at 0x{addr:08X}")


def write_code_mem(hproc, addr: int, data: bytes):
    old_prot = wt.DWORD(0)
    if not kernel32.VirtualProtectEx(
        hproc, ctypes.c_void_p(addr), len(data), PAGE_EXECUTE_READWRITE, ctypes.byref(old_prot)
    ):
        raise RuntimeError(f"VirtualProtectEx failed at 0x{addr:08X}")
    try:
        write_mem(hproc, addr, data)
        kernel32.FlushInstructionCache(hproc, ctypes.c_void_p(addr), len(data))
    finally:
        kernel32.VirtualProtectEx(
            hproc, ctypes.c_void_p(addr), len(data), old_prot.value, ctypes.byref(old_prot)
        )


def read_u32(hproc, addr: int) -> int:
    return struct.unpack("<I", read_mem(hproc, addr, 4))[0]


def write_u32(hproc, addr: int, val: int):
    write_mem(hproc, addr, struct.pack("<I", val & 0xFFFFFFFF))


def find_rbtree_node_by_hash(hproc, head_ptr: int, target_hash: int):
    root = read_u32(hproc, head_ptr + 0x04)
    visited = set()
    stack = [root]
    alt_hash = target_hash ^ 0x00000001
    while stack:
        node = stack.pop()
        if not node or node == head_ptr or node in visited:
            continue
        visited.add(node)
        data = read_mem(hproc, node, 0x14)
        left, _parent, right, _color, is_nil = struct.unpack("<IIIBB2x", data[:0x10])
        if is_nil:
            continue
        key = struct.unpack("<I", data[0x10:0x14])[0]
        if key == target_hash or key == alt_hash:
            return node, key
        stack.append(left)
        stack.append(right)
    return None, None


def sync_mod_files(mod_root: str, game_dir: str):
    rel_files = get_mod_files(mod_root)
    game_mod_dir = os.path.join(game_dir, "mods", "koishi_boss_spells")
    targets = [
        ("<game>/mods/koishi_boss_spells", game_mod_dir),
        ("<game>", game_dir),
    ]
    print(f"[*] 正在同步 koishi_boss_spells 共 {len(rel_files)} 个 Mod 文件...")
    for label, base_dst in targets:
        for rel_path in rel_files:
            src = os.path.join(mod_root, "mod", *rel_path.split("/"))
            dst = os.path.join(base_dst, *rel_path.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        print(f"    [+] 已同步 {len(rel_files)} 个文件 -> {label} ({base_dst})")


def apply_mod(restore: bool = False, game_dir_override: str | None = None, sync_only: bool = False):
    mod_root = os.path.dirname(os.path.abspath(__file__))
    default_game_dir = r"D:\[th15.5] 东方凭依华（英文版+日文版）"
    game_dir = game_dir_override or default_game_dir

    if sync_only:
        sync_mod_files(mod_root, game_dir)
        print("[*] 纯同步模式完成。")
        return 0

    pid = find_th155_process()
    if not pid:
        sync_mod_files(mod_root, game_dir)
        print("[!] 未检测到运行中的 th155.exe，已完成磁盘文件镜像同步。")
        return 0

    base_addr, exe_path = get_module_info(pid)
    if not base_addr:
        print(f"[!] 无法获取 th155.exe (PID={pid}) 模块基址。")
        return 1

    game_dir = game_dir_override or os.path.dirname(exe_path)
    print(f"[*] 已连接 th155.exe (PID={pid}, ImageBase=0x{base_addr:08X})")
    print(f"[*] 游戏目录: {game_dir}")
    if not restore:
        sync_mod_files(mod_root, game_dir)

    hproc = kernel32.OpenProcess(
        PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION | PROCESS_QUERY_INFORMATION,
        False,
        pid,
    )
    if not hproc:
        raise RuntimeError("OpenProcess failed")
    try:
        pkg_mgr = 0
        for _ in range(40):
            pkg_mgr = read_u32(hproc, base_addr + RVA_PACKAGE_MANAGER)
            if pkg_mgr and read_u32(hproc, pkg_mgr + 0x24) > 1000:
                break
            time.sleep(0.25)
        if not pkg_mgr:
            raise RuntimeError("g_PackageManager 未就绪")
        head_ptr = read_u32(hproc, pkg_mgr + 0x20)
        gate_addr = base_addr + RVA_GATE_CHECK
        cur_gate = read_mem(hproc, gate_addr, 5)
        target_gate = GATE_ORIG_BYTES if restore else GATE_PATCH_BYTES
        write_code_mem(hproc, gate_addr, target_gate)
        new_gate = read_mem(hproc, gate_addr, 5)
        print(f"[*] GateCheck (0x{gate_addr:08X}): {cur_gate.hex()} -> {new_gate.hex()}")

        rel_files = get_mod_files(mod_root)
        for rel_path in rel_files:
            h = th155_pak_hash(rel_path)
            node, cur_key = find_rbtree_node_by_hash(hproc, head_ptr, h)
            if not node:
                print(f"    [*] {rel_path:<48} | 新增磁盘文件")
                continue
            desired_key = h if restore else (h ^ 1)
            if cur_key != desired_key:
                write_u32(hproc, node + 0x10, desired_key)
            verify_key = read_u32(hproc, node + 0x10)
            mode_str = "原版 Pak" if verify_key == h else "磁盘直读 (Mod 激活)"
            print(f"    [+] {rel_path:<48} | Key=0x{verify_key:08X} ({mode_str})")
    finally:
        kernel32.CloseHandle(hproc)
    return 0


def main():
    parser = argparse.ArgumentParser(description="古明地恋心绮楼普通符卡独立替换工具")
    parser.add_argument("--game-dir", type=str, default=None, help="手动指定 TH15.5 游戏根目录")
    parser.add_argument("--restore", action="store_true", help="恢复原版 Pak 加载")
    parser.add_argument("--sync-only", action="store_true", help="仅同步文件，不注入运行中进程")
    args = parser.parse_args()
    sys.exit(apply_mod(restore=args.restore, game_dir_override=args.game_dir, sync_only=args.sync_only))


if __name__ == "__main__":
    main()
