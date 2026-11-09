"""复现/定位 KO 回合切换时的游戏崩溃。

用法:
    python scripts/repro_ko_crash.py --mode plain     # 只注入空输入,不写 win
    python scripts/repro_ko_crash.py --mode win       # 注入空输入 + 每步清零 battle.win
    python scripts/repro_ko_crash.py --mode dblko     # 把双方血量压到 1,制造双 KO

前置:游戏已启动并注入 DLL(脚本会自己 ensure_fighting 进 VS COM)。
输出:每秒打印一次 (state, round, life, win),崩溃/存活到下一回合后退出。
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

FIGHTING = 8


def _alive() -> bool:
    from th155.mem import find_pid
    try:
        find_pid("th155.exe")
        return True
    except LookupError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("plain", "talk", "win"), default="plain")
    ap.add_argument("--timeout", type=float, default=180.0)
    a = ap.parse_args()

    g = Th155Game(writable=True)
    if not ensure_fighting(g, timeout=120):
        print("无法进入对战")
        return 2
    ops = BattleOps(g)

    with BridgeShm.open(timeout=2.0) as shm:
        shm.set_action(team=0, x=0, y=0)      # 空输入,但保持注入开关打开
        t0 = time.time()
        last = None
        rounds_seen = 0
        while time.time() - t0 < a.timeout:
            if not _alive():
                print(f"[X] 游戏进程已退出(崩溃),最后状态 {last}")
                return 1
            st = shm.read_state()
            if a.mode == "win":
                ops.reset_win()          # 每轮循环清零一次(完整 tag,安全)
            if int(st["battle_state"]) == FIGHTING:
                shm.set_action(team=0, x=0, y=0)
            elif a.mode == "talk":
                # 胜负对话:持续按 b0,验证 talk 场景是否读 input_all
                shm.set_action(team=0, x=0, y=0, b0=1)
            else:
                shm.clear_action()
            key = (int(st["battle_state"]), int(st["round"]))
            if key != last:
                win = g.battle_value("win")
                print(f"{time.time() - t0:6.1f}s state={key[0]:3d} round={key[1]} "
                      f"life={[t['life'] for t in st['teams']]} win={win}")
                if key[0] == FIGHTING and key[1] > rounds_seen:
                    rounds_seen = key[1]
                    if rounds_seen >= 3:
                        print("[OK] 已稳定进入第 2 回合,未崩溃")
                        return 0
                last = key
            time.sleep(0.05)
        print("[?] 超时,未观察到崩溃")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
