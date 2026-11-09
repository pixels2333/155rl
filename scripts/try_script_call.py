"""试验:把 `battle.battleUpdate` 槽临时换成 `battle.RoundReset`,借游戏自己的
每帧脚本调用触发一次回合重置(不需要 Squirrel VM API)。

用法:
    python scripts/try_script_call.py            # 在对战中触发一次重置
    python scripts/try_script_call.py --hold 0.05

原理:
    游戏每帧从 `battle` 表里取 `battleUpdate` 闭包并调用它。把这个槽临时指向
    `RoundReset` 闭包,游戏下一帧就会调用 `RoundReset`,随后我们立刻还原。

风险:
    两个闭包的参数个数不一致时,Sqrat 会抛 `wrong number of parameters` 直接崩游戏
    (这正是 issue 04 里踩到的坑)。因此本脚本只做一次性试验。
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bridge.shm import BridgeShm          # noqa: E402
from th155 import Th155Game               # noqa: E402
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
    ap.add_argument("--hold", type=float, default=0.05)
    ap.add_argument("--wait", type=float, default=8.0, help="交换前先打一会儿,让计时器明显下降")
    ap.add_argument("--timeout", type=float, default=20.0)
    a = ap.parse_args()

    g = Th155Game(writable=True)
    if not ensure_fighting(g, timeout=120):
        print("无法进入对战")
        return 2

    upd = g.get_path("battle", "battleUpdate")
    rst = g.get_path("battle", "RoundReset")
    print("battleUpdate slot:", hex(upd) if upd else None)
    print("RoundReset  slot:", hex(rst) if rst else None)
    if not upd or not rst:
        return 2
    upd_obj = g.p.read(upd, 8)
    rst_obj = g.p.read(rst, 8)
    print("battleUpdate obj:", upd_obj.hex(), " RoundReset obj:", rst_obj.hex())
    if upd_obj == rst_obj:
        print("[i] 两个槽已经是同一个闭包")
        return 0

    with BridgeShm.open(timeout=2.0) as shm:
        print(f"[i] 先等 {a.wait:.0f}s,让计时器下降")
        time.sleep(a.wait)
        before = shm.read_state()
        print(f"before: state={before['battle_state']} time={before['time']} "
              f"life={[t['life'] for t in before['teams']]} frame={before['frame']}")

        # 借用游戏自己的每帧调用:只保持很短时间,然后立刻还原
        g.p.write(upd, rst_obj)
        time.sleep(a.hold)
        g.p.write(upd, upd_obj)
        print(f"[i] 已交换 {a.hold * 1000:.0f}ms 并还原")
        # 高频采样,捕捉"血量瞬间回满"的那一帧
        t_s = time.time()
        prev_life = [t["life"] for t in before["teams"]]
        while time.time() - t_s < 1.0:
            cur = shm.read_state()
            life = [t["life"] for t in cur["teams"]]
            if life != prev_life:
                print(f"  +{1000 * (time.time() - t_s):5.0f}ms frame={cur['frame']} "
                      f"state={cur['battle_state']} life={prev_life} -> {life} time={cur['time']}")
                prev_life = life
            time.sleep(0.01)

        t0 = time.time()
        last = None
        while time.time() - t0 < a.timeout:
            if not _alive():
                print("[X] 游戏已退出(崩溃) -> 参数个数不匹配,此路不通")
                return 1
            st = shm.read_state()
            key = int(st["battle_state"])
            if key != last:
                print(f"{time.time() - t0:5.1f}s state={key:3d} time={st['time']} "
                      f"life={[t['life'] for t in st['teams']]} frame={st['frame']}")
                last = key
            if key == FIGHTING and st["time"] > before["time"] + 100 and st["frame"] > before["frame"] + 30:
                print("[OK] 回合已被重置(计时器回满、状态回到 8)")
                return 0
            time.sleep(0.02)
        print("[?] 未观察到重置")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
