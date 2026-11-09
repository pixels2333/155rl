"""探针:确认哪个输入位能真正触发"完全凭依换人"(team.current 改变)。

背景(见 docs/train_report_com3_full.md §8.6):训练日志的 `sw=` 计数显示 Agent 每局
按 Tag 100+ 次而 `team.current` 从不改变,只读采样也复现(tag_ready=1 的 42 帧内 0 次换人)。
本脚本用**独占输入**做闭环验证:在 op 满、角色空闲时逐个按候选按键,观察脚本层
`battle.team[0].current` 是否从 master 切到 slave。

用法(**必须先停训练**,否则两路输入互相覆盖):
    python scripts/probe_tag.py                      # 默认测 b3(当前映射)、b5、b4
    python scripts/probe_tag.py --buttons b3 --trials 5
    python scripts/probe_tag.py --hold-ms 200 --release-ms 120

退出码:0 = 至少有一个按键触发了换人;1 = 全部失败(说明输入位映射或就绪条件有误)。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bridge.shm import BridgeShm          # noqa: E402
from th155 import Th155Game              # noqa: E402

STATE_FIGHTING = 8


class Live:
    """脚本层只读视图:team0 的 current 实例与 master/slave 实例指针。"""

    def __init__(self) -> None:
        self.g = Th155Game(writable=False, verify_version=False)
        battle = self.g.get_table("battle")
        if not battle:
            raise RuntimeError("battle 表不存在(游戏未进入对战?)")
        sa = self.g.r.table_get(battle, "team")
        t, arr = self.g.r.obj(sa)
        self._team_slots = self.g.r.array(arr)

    def team(self, i: int = 0) -> int:
        return self.g.r.obj(self._team_slots[i])[1]

    def current_role(self, i: int = 0) -> str:
        t = self.team(i)
        cur = self.g.field_inst(t, "current")
        if cur is None:
            return "?"
        if cur == self.g.field_inst(t, "master"):
            return "master"
        if cur == self.g.field_inst(t, "slave"):
            return "slave"
        return "?"


def wait_ready(shm: BridgeShm, live: Live, want_op: int, timeout: float,
               build_op: bool = True) -> bool:
    """等到:对战中 + op 满 + 我方前台当前实例的 motion == 0(空闲)。

    `build_op=True` 时,若 op 不足会自己打一轮 b0(攻击)来刷怪奇槽 —— 训练停止后
    没有别的玩家/策略在造成伤害,槽不会自己涨,不主动充能会一直等到超时。
    """
    deadline = time.time() + timeout
    last_build = 0.0
    while time.time() < deadline:
        st = shm.read_state()
        in_fight = int(st["battle_state"]) == STATE_FIGHTING
        op = int(st["teams"][0]["op"])
        if in_fight and op >= want_op:
            team = live.team(0)
            role = live.current_role(0)
            player = live.g.field_inst(team, "master" if role == "master" else "slave")
            if player:
                motion = int(live.g.field(player, "motion") or 0)
                if motion == 0:
                    return True
        elif in_fight and build_op and time.time() - last_build > 0.45:
            # 打一串 A 充能;充能期间不做判定
            last_build = time.time()
            shm.set_action(team=0, x=1, y=0, b0=1)
            time.sleep(0.10)
            shm.clear_action()
        time.sleep(0.02)
    return False


def press(shm: BridgeShm, key: str, hold_ms: float, release_ms: float,
          combo: bool = False) -> None:
    """按下单个设备按键。

    `combo=False`(空闲模式):全松 → 单按目标键 → 全松。
    `combo=True`(取消模式):先按 b0(攻击)起手,再叠加目标键 —— 用于验证
    "换人只能作为攻击取消/受击脱离触发,站立空按无效"这一假设。
    """
    shm.clear_action()
    time.sleep(0.03)
    if combo:
        shm.set_action(team=0, x=0, y=0, b0=1)
        time.sleep(max(hold_ms, 60.0) / 1000.0)
        kwargs = {key: 1}
        shm.set_action(team=0, x=0, y=0, b0=1, **kwargs)
        time.sleep(hold_ms / 1000.0)
    else:
        kwargs = {key: 1}
        shm.set_action(team=0, x=0, y=0, **kwargs)
        time.sleep(hold_ms / 1000.0)
    shm.clear_action()
    time.sleep(release_ms / 1000.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buttons", default="b3,b5,b4", help="候选设备按键,按优先级逗号分隔")
    ap.add_argument("--trials", type=int, default=3, help="每个候选按键的尝试次数")
    ap.add_argument("--hold-ms", type=float, default=120.0)
    ap.add_argument("--release-ms", type=float, default=150.0)
    ap.add_argument("--combo", action="store_true",
                    help="起手按 b0(攻击)后再叠加目标键,验证'换人只能取消/脱离触发'")
    ap.add_argument("--wait", type=float, default=90.0, help="每次等待就绪的上限秒数")
    args = ap.parse_args()

    keys = [k.strip() for k in args.buttons.split(",") if k.strip()]
    shm = BridgeShm.open(timeout=5.0)
    live = Live()
    hits = {k: 0 for k in keys}

    try:
        for key in keys:
            for trial in range(args.trials):
                before = live.current_role(0)
                if not wait_ready(shm, live, want_op=1000, timeout=args.wait):
                    print(f"[w] {key}#{trial + 1}: 等待就绪超时(op 未满/非空闲/不在对局)", flush=True)
                    continue
                press(shm, key, args.hold_ms, args.release_ms, combo=args.combo)
                time.sleep(0.35)
                after = live.current_role(0)
                ok = after != before
                hits[key] += int(ok)
                print(f"[{key}#{trial + 1}] current: {before} -> {after} "
                      f"{'SWITCHED' if ok else 'no-op'}", flush=True)
                if ok:
                    time.sleep(2.0)          # 等换人演出结束再继续
    finally:
        shm.clear_action()
        shm.close()

    print("\n== 汇总 ==")
    for key in keys:
        print(f"  {key}: {hits[key]}/{args.trials} 次触发换人")
    worked = [k for k, v in hits.items() if v > 0]
    print(f"  可用按键: {worked or '无'}")
    return 0 if worked else 1


if __name__ == "__main__":
    raise SystemExit(main())
