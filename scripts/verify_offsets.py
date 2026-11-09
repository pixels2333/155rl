"""实机验证 configs/offsets.yaml：读取 1P/2P 血量、坐标、动作 ID、计时器、弹幕。

用法:
  python scripts/verify_offsets.py                # 采样 3 秒并打印
  python scripts/verify_offsets.py --seconds 6
  python scripts/verify_offsets.py --move-test    # 写输入让 1P 向右移动 0.5s
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from th155 import Th155Game  # noqa: E402
from th155.game import load_config  # noqa: E402


def fmt_actor(a) -> str:
    if a is None:
        return "  -  "
    return (f"x={a.x:7.1f} y={a.y:6.1f} vx={a.vx:6.2f} vy={a.vy:6.2f} "
            f"dir={a.direction:+.0f} motion={a.motion:<5d} frame={a.key_frame:<3d}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--move-test", action="store_true")
    ap.add_argument("--no-version-check", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    g = Th155Game(writable=args.move_test, verify_version=not args.no_version_check)
    print(f"[+] module base = {g.base:#x}  (preferred {cfg['meta']['preferred_image_base']:#x})")
    print(f"[+] SQVM         = {g.vm:#x}")
    print(f"[+] roottable    = {g.root:#x}")
    print(f"[+] battle table = {g.battle:#x}")

    st = g.read_state()
    print(f"[+] state={st.battle_state} time={st.time} time_unit={st.time_unit} "
          f"frame={st.frame} round={st.round} winner={st.winner}")
    if not st.teams:
        print("[!] battle.team 为空：请先进入一局对战", file=sys.stderr)
        return 2

    ok = True
    for t in st.teams:
        print(f"[team {t.index}] {t.master_name}+{t.slave_name} "
              f"life={t.life}/{t.life_max} regain={t.regain_life} sp={t.sp}/{t.sp_max} "
              f"mp={t.mp} op={t.op} spell={t.spell_active} combo={t.combo_count} "
              f"com_diff={t.com_difficulty} com_level={t.com_level}")
        print(f"   master {fmt_actor(t.master)}")
        print(f"   slave  {fmt_actor(t.slave)}")

    # 基本合理性检查（数值必须落在场地内，且 1P/2P 朝向相反）
    for t in st.teams:
        for a in (t.master, t.slave):
            if a is None:
                continue
            if not (-1000 <= a.x <= 3560 and -1000 <= a.y <= 2440):
                print(f"[!] 坐标越界: {a}", file=sys.stderr)
                ok = False
    if len(st.teams) == 2:
        d0 = st.teams[0].master.direction if st.teams[0].master else 0
        d1 = st.teams[1].master.direction if st.teams[1].master else 0
        if d0 * d1 > 0:
            print("[!] 双方朝向相同，可能读取错误", file=sys.stderr)
            ok = False

    if args.move_test:
        dev = g.input_native(st.teams[0].inst)
        print(f"[+] 1P input device = {dev:#x}")
        x0 = g.team_state(g.team_instances()[0]).master.x
        t0 = time.time()
        n = 0
        while time.time() - t0 < 0.5:
            g.write_input(dev, x=1)
            n += 1
        time.sleep(0.1)
        x1 = g.team_state(g.team_instances()[0]).master.x
        print(f"[+] move test: x {x0:.1f} -> {x1:.1f} ({n} writes)")
        if x1 - x0 < 20:
            print("[!] 输入注入无效：角色未移动", file=sys.stderr)
            ok = False

    # 连续采样，确认数值随画面实时变化
    frames = []
    t0 = time.time()
    while time.time() - t0 < args.seconds:
        s = g.read_state()
        frames.append((s.frame, s.time, s.teams[0].master.x if s.teams[0].master else 0,
                       s.teams[0].master.motion if s.teams[0].master else 0,
                       len(s.shots)))
        time.sleep(args.interval)
    uniq_frames = len({f[0] for f in frames})
    print(f"[+] {len(frames)} samples / {uniq_frames} distinct frames; "
          f"timer {frames[0][1]} -> {frames[-1][1]}; shots(max)={max(f[4] for f in frames)}")
    if uniq_frames < 2:
        print("[!] 帧计数未推进（游戏可能已暂停/未在战斗中）", file=sys.stderr)
        ok = False

    print("[OK] offsets 验证通过" if ok else "[FAIL] offsets 验证失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
