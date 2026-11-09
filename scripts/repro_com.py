"""复现:练习模式 + 内置 COM(player2=3) 下按 4 键重置是否崩溃。

用法: python scripts/repro_com.py [--player2 N] [--restart]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from th155 import Th155Game                      # noqa: E402
from th155.mem import find_pid                   # noqa: E402
from th155.menu import MenuDriver                # noqa: E402
from th155.ops import BattleOps                  # noqa: E402
import game_session                              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--player2", type=int, default=3)
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--inject", action="store_true", help="启动时注入桥接 DLL")
    ap.add_argument("--watch", type=float, default=25.0)
    ap.add_argument("--mode", choices=["practice", "vs_com"], default="practice")
    a = ap.parse_args()

    if not a.no_start:
        try:
            find_pid("th155.exe")
            print("[i] 复用已运行的游戏")
        except LookupError:
            game_session.start(inject_dll=a.inject)

    g = Th155Game(writable=True)
    m = MenuDriver(g)
    ops = BattleOps(g)
    if g.battle_value("state") not in (2, 4, 8):
        ops.set_practice_config(player2=a.player2, difficulty=1)
        m.seed_last_device()
        t0 = time.time()
        if a.mode == "vs_com":
            ok = m.enter_vs_com(difficulty=1, timeout=90)
        else:
            ok = m.enter_practice(player2=a.player2, difficulty=1, timeout=60)
        print(f"[{'ok' if ok else 'x'}] 进入{a.mode}({time.time()-t0:.1f}s) player2={a.player2} "
              f"state={g.battle_value('state')}")
        if not ok:
            return 1

    t0 = time.time()
    alive = True
    while time.time() - t0 < a.watch:
        time.sleep(0.5)
        try:
            st = g.read_state(with_shots=False)
        except Exception as e:
            print(f"[x] {time.time()-t0:.1f}s 游戏崩溃: {e}")
            alive = False
            break
    if not alive:
        return 3
    print(f"[ok] 存活 {a.watch:.0f}s")
    st = g.read_state(with_shots=True)
    t1 = st.teams[1] if len(st.teams) > 1 else None
    print(f"[i] state={st.battle_state} frame={st.frame} "
          f"team0={st.teams[0].master_name if st.teams else None} "
          f"team1_master={(t1.master_name if t1 else None)} "
          f"com_difficulty={(t1.com_difficulty if t1 else None)}")

    if not a.restart:
        return 0

    # 统计重置后 COM 行为 + 是否崩溃
    print("[i] 触发练习重置(b0~b3 同时按下)...")
    t0 = time.time()
    ops.practice_reset()
    for i in range(40):
        time.sleep(0.2)
        try:
            s = g.read_state(with_shots=False)
        except Exception as e:
            print(f"[x] {time.time()-t0:.1f}s 读状态失败: {e}")
            return 2
        print(f"  {time.time()-t0:5.1f}s state={s.battle_state} frame={s.frame} "
              f"x0={s.teams[0].x if s.teams else None}")
    print("[ok] 重置后 8s 内存活")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
