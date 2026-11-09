"""对局会话控制:保证"随时能回到一个可操作的回合"。

训练/评估循环里 reset 的语义是"下一回合开始",而真实游戏在回合之间还有:
  * KO 演出(state 64) -> 回合结束演出(32) -> 下一回合(128 -> 4 -> 8),约 11s
  * 整场比赛结束时会出现胜负对话,需要按确认键推进,之后回到标题
  * 回到标题后必须重新走 标题 -> VS COM -> 选人 的流程(约 10s)

本模块把这三种情况收敛成一个 `ensure_fighting()`。
"""
from __future__ import annotations

import time
from typing import Optional

from .menu import MenuDriver
from .ops import BattleOps

STATE_FIGHTING = 8
# 回合内/结算/载入等"仍在战斗场景里"的状态
BATTLE_STATES = (2, 4, 8, 32, 64, 128)


def ensure_fighting(g, difficulty: int = 1, timeout: float = 60.0,
                    max_taps: int = 30) -> bool:
    """确保当前处于可操作回合(state=8)。"""
    ops = BattleOps(g)
    m = MenuDriver(g)
    deadline = time.time() + timeout

    st = g.battle_value("state")
    if st == STATE_FIGHTING and not ops.match_over():
        return True

    # 比赛已打满 2 胜:胜负对话用按键/注入都推进不了(见 ops.skip_demo 注释),
    # 直接返回 False,让调用方重启游戏(训练器会自动 kill+start+注入)。
    if st != STATE_FIGHTING and ops.match_over():
        return False

    # 1) 正常回合切换。KO→下一回合单实例实测 11.0~11.7s(贴着旧的 12s 上限),
    #    多实例抢 CPU 时更长,上限给 25s。标题/菜单下(新启动实例 state=0)等不到
    #    state=8,直接去走菜单。
    if st in BATTLE_STATES and ops.wait_next_round(timeout=min(25.0, timeout)):
        return True

    # 2) 胜负对话:按确认键逐屏推进(只在战斗场景内按,避免在标题上误触)
    taps = 0
    while taps < max_taps and time.time() < deadline:
        st = g.battle_value("state")
        if st == STATE_FIGHTING:
            return True
        if st not in BATTLE_STATES:
            break                     # 已经回到标题/菜单
        m.tap()
        taps += 1
        if ops.wait_next_round(timeout=1.0):
            return True

    # 3) 标题 -> VS COM
    if g.battle_value("state") not in BATTLE_STATES:
        m.seed_last_device()
        ok = m.ensure_battle(difficulty=difficulty, mode="vs_com",
                             timeout=max(30.0, deadline - time.time()))
        if not ok:
            return False
        # ensure_battle 在开场倒计时(state 2)就会返回,这里再等到真正可操作
        return ops.wait_next_round(timeout=max(20.0, deadline - time.time()))
    return False
