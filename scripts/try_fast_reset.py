"""试验:KO 之后直接改写 `battle.state`,看能否跳过演出、1 帧进入下一回合。

用法:
    python scripts/try_fast_reset.py --to 128     # KO(64) 时把 state 改成 128
    python scripts/try_fast_reset.py --to 4
    python scripts/try_fast_reset.py --to 32

判定:
    * 目标状态后 5 秒内回到 state=8 且双方 life 满 -> 快速重置成功;
    * 游戏进程消失 -> 该跳转不安全。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bridge.shm import BridgeShm          # noqa: E402
from th155 import Th155Game               # noqa: E402
from th155.ops import BattleOps           # noqa: E402
from th155.session import ensure_fighting  # noqa: E402

FIGHTING, KO, ROUND_END = 8, 64, 32


def _alive() -> bool:
    from th155.mem import find_pid
    try:
        find_pid("th155.exe")
        return True
    except LookupError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", type=int, default=128)
    ap.add_argument("--timeout", type=float, default=150.0)
    a = ap.parse_args()

    g = Th155Game(writable=True)
    if not ensure_fighting(g, timeout=120):
        print("无法进入对战")
        return 2
    ops = BattleOps(g)
    print(f"[i] 目标:在 KO/回合结束把 battle.state 改成 {a.to}")

    with BridgeShm.open(timeout=2.0) as shm:
        t0 = time.time()
        jumped = False
        last = None
        while time.time() - t0 < a.timeout:
            if not _alive():
                print(f"[X] 游戏已退出(崩溃),最后状态 {last}")
                return 1
            st = shm.read_state()
            state = int(st["battle_state"])
            if state != last:
                print(f"{time.time() - t0:6.1f}s state={state:3d} "
                      f"life={[t['life'] for t in st['teams']]} win={g.battle_value('win')}")
                last = state
            if not jumped and state in (KO, ROUND_END) and a.to != state:
                ops.set_table_int("battle", "state", value=a.to)
                print(f"       [i] 写入 battle.state={a.to} (原 {state})")
                jumped = True
                tj = time.time()
            if jumped and state == FIGHTING:
                dt = time.time() - tj
                lives = [t["life"] for t in st["teams"]]
                print(f"[OK] 跳过演出用了 {dt:.2f}s 回到 state=8,life={lives}")
                return 0
            time.sleep(0.02)
        print("[?] 超时")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
