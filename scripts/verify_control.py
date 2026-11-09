"""实机验证「输入注入」:用内存写入设备字段驱动 1P 移动/攻击。

用法:
    python scripts/verify_control.py            # 自动确保进入对战
    python scripts/verify_control.py --team 1   # 驱动 2P

验收点:
    * 持续写 x=+1 后 1P 的 x 坐标显著增大(向右走)
    * 写 b0=1 后角色进入非待机 motion 或产生弹幕
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from th155 import Th155Game                     # noqa: E402
from th155.menu import MenuDriver               # noqa: E402

INPUT_X, INPUT_Y, INPUT_B0 = 0x04, 0x08, 0x0C


def hold(game, dev: int, offset: int, value: int, seconds: float):
    t0 = time.time()
    while time.time() - t0 < seconds:
        game.p.write_i32(dev + offset, value)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", type=int, default=0)
    ap.add_argument("--enter", action="store_true", help="必要时自动从标题进入对战")
    args = ap.parse_args()

    g = Th155Game(writable=True)
    st = g.read_state(with_shots=False)
    if args.enter and st.battle_state not in (2, 4, 8):
        MenuDriver(g).ensure_battle(1)
        st = g.read_state(with_shots=False)
    print(f"[i] battle.state={st.battle_state} frame={st.frame}")
    if st.battle_state not in (2, 4, 8):
        print("[FAIL] 当前不在对战状态(用 --enter 自动进入)", file=sys.stderr)
        return 1

    team = st.teams[args.team]
    dev = g.input_native(team.inst)
    print(f"[i] team{args.team} input device = {dev:#x}  master={team.master_name}")

    # --- 1. 移动 ---------------------------------------------------------
    before = g.read_state(with_shots=False).teams[args.team].master
    hold(g, dev, INPUT_X, 1, 0.8)
    time.sleep(0.2)
    after = g.read_state(with_shots=False).teams[args.team].master
    dx = after.x - before.x
    print(f"[1] x: {before.x:.1f} -> {after.x:.1f}  (dx={dx:+.1f})")
    move_ok = dx > 20

    # --- 2. 攻击/弹幕 ----------------------------------------------------
    s1 = g.read_state(with_shots=True)
    shot_before = len(s1.shots)
    hold(g, dev, INPUT_B0, 1, 0.5)
    time.sleep(0.4)
    s2 = g.read_state(with_shots=True)
    a2 = s2.teams[args.team].master
    shot_after = len(s2.shots)
    print(f"[2] motion: {before.motion} -> {a2.motion}  shots: {shot_before} -> {shot_after}")
    attack_ok = (a2.motion != before.motion) or (shot_after > shot_before)

    # --- 3. 停手后角色回到可控 -------------------------------------------
    time.sleep(0.6)
    a3 = g.read_state(with_shots=False).teams[args.team].master
    print(f"[3] 松开后 motion={a3.motion} x={a3.x:.1f}")

    ok = move_ok and attack_ok
    print("[OK] 输入注入验证通过" if ok else "[FAIL] 输入注入验证失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
