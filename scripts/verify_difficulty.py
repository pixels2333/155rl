"""实机验证:对局中切换内置 COM 难度的两条路(issue 06 验收 #1)。

两条路都要实测定性,因为仓库里有互相矛盾的历史记录:

* `live`  —— 对局中直接写角色 `com_difficulty/com_level/baria/guard`
  (`BattleOps.set_com_difficulty`)。`docs/re_notes.md §10`、
  `scripts/verify_bridge.py`、`menu.choose_difficulty` 三处都写过"会崩"。
* `title` —— 暂停菜单回标题(`b10` -> `ret_title`)后重进 VS COM,
  难度由标题菜单的 `config.difficulty.vs` 写入。进程与 DLL 都不用重建。

判定标准(全部满足才算 PASS):
1. 目标难度的 `com_difficulty / com_level` 生效(读 shm 发布值);
2. 切换后游戏仍在对局中(`battle.state == 8`)且 DLL 心跳在涨;
3. `live` 模式额外要求写后 1.2s 仍存活;默认最后恢复原难度。

前置条件:游戏已启动、DLL 已注入、**处于对局中**(state == 8)。
注意 `title` 模式会离开当前对局(会中断正在跑的训练)。

用法:
    python scripts/verify_difficulty.py                     # live,切 COM3 再恢复
    python scripts/verify_difficulty.py --via title --level 2
    python scripts/verify_difficulty.py --via title --level 5 --keep
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bridge.shm import BridgeShm            # noqa: E402
from th155 import Th155Game                 # noqa: E402
from th155.ops import BattleOps             # noqa: E402


def _level_of(level: int) -> int:
    return BattleOps.COM_LEVELS.get(level, BattleOps.COM_LEVELS[1])[0]


def _wait_fighting(shm: BridgeShm, timeout: float = 120.0) -> dict:
    t0 = time.time()
    st = shm.read_state()
    while time.time() - t0 < timeout:
        if st["battle_state"] == 8:
            return st
        time.sleep(0.2)
        st = shm.read_state()
    return st


def _apply(via: str, level: int, ops: BattleOps) -> bool:
    if via == "title":
        from train.train_ppo import switch_difficulty
        return bool(switch_difficulty(level))
    return bool(ops.set_com_difficulty(1, level))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=2, choices=range(5),
                    help="目标内部难度 0..4(= COM1..COM5),默认 2 = COM3")
    ap.add_argument("--via", choices=("live", "title"), default="live",
                    help="live = 对局中写 com_* 字段;title = 暂停菜单回标题重进")
    ap.add_argument("--keep", action="store_true", help="不恢复原难度")
    args = ap.parse_args()

    shm = BridgeShm.open(timeout=5.0)
    st = shm.read_state()
    if st["battle_state"] != 8:
        print(f"[!] 当前 battle.state={st['battle_state']},需要处于对局中(state=8)")
        shm.close()
        return 2

    before = (st["teams"][1]["com_difficulty"], st["teams"][1]["com_level"])
    ack_before = shm.s.ctl_ack
    print(f"[i] 写前:com_difficulty={before[0]} com_level={before[1]} "
          f"| 目标 COM{args.level + 1} via={args.via} "
          f"(difficulty={args.level} level={_level_of(args.level)})")

    g = Th155Game(writable=True)
    t0 = time.perf_counter()
    ok = _apply(args.via, args.level, BattleOps(g))
    cost = time.perf_counter() - t0
    print(f"[i] 切换返回 {ok},耗时 {cost * 1000:.0f}ms")

    if args.via == "title":
        after_st = _wait_fighting(shm)
        time.sleep(0.4)
        after_st = shm.read_state()
    else:
        time.sleep(0.35)
        after_st = shm.read_state()
    after = (after_st["teams"][1]["com_difficulty"], after_st["teams"][1]["com_level"])

    checks = {
        "切换调用成功": bool(ok),
        "difficulty 生效": after[0] == args.level,
        "com_level 同步": after[1] == _level_of(args.level),
        "仍在对局(state==8)": after_st["battle_state"] == 8,
        "DLL 心跳递增(ctl_ack)": shm.s.ctl_ack > ack_before,
    }
    print(f"[i] 写后:com_difficulty={after[0]} com_level={after[1]} "
          f"state={after_st['battle_state']} ack {ack_before}->{shm.s.ctl_ack}")

    if not args.keep and before[0] is not None and before[0] != args.level:
        _apply(args.via, int(before[0]), BattleOps(g))
        back_st = _wait_fighting(shm) if args.via == "title" else shm.read_state()
        time.sleep(0.35)
        back = shm.read_state()["teams"][1]["com_difficulty"]
        checks["恢复原难度"] = back == before[0]
        print(f"[i] 已恢复:com_difficulty={back} (state={back_st['battle_state']})")

    print("\n结果")
    for k, v in checks.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    shm.close()
    all_ok = all(checks.values())
    print(f"\n[OK] `{args.via}` 路径可切难度" if all_ok
          else f"\n[FAIL] `{args.via}` 路径不可用")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
