"""Ticket 09 单元测试:暗转时停预缓冲与符卡确反打断引擎 (Time Stop Input Buffer & Spell Interruption Engine)

覆盖内容:
  1. 时停期间 ActionHoldEngine 倒计时被挂起冻结(不吃防守维持寿命);
  2. 暗转解冻瞬间缓冲动作(普攻打断 / 稳固防守)被锁定维持(reversal buffer);
  3. 真实受击格挡(guard_block)不受 0.45 的 guard_round_cap 死限截断;
  4. 成功打断对手符卡(spell_active / motion 4000..4999)触发 spell_interrupt 奖励与计数;
  5. Th155Env 集成时停状态提取与 info["time_stop_active"] 诊断透传。
"""
from __future__ import annotations

import unittest
from typing import Dict

from env import action_space
from env.reward import RewardCalculator, snapshot
from env.th155_env import Th155Env


class TestActionHoldEngineTimeStop(unittest.TestCase):
    """测试 ActionHoldEngine 的暗转时停与解冻缓冲机制。"""

    def setUp(self):
        self.engine = action_space.ActionHoldEngine(hold_ticks=20, reversal_hold_ticks=6)

    def test_hold_engine_time_stop_suspends_countdown(self):
        """1. 时停期间倒计时被挂起冻结, 维持寿命不扣减。"""
        guard_act = action_space.GUARD_HOLD_ACTION  # 12: 4 + none
        # 初始触发防守
        self.assertEqual(self.engine.step(guard_act, is_time_stop=False), guard_act)
        self.assertEqual(self.engine.hold_ticks_remaining, 20)
        self.assertEqual(self.engine.hold_action_id, guard_act)

        # 正常经过 5 步非时停(输入中立动作 0)
        for expected_rem in range(19, 14, -1):
            act = self.engine.step(0, is_time_stop=False)
            self.assertEqual(act, guard_act)
            self.assertEqual(self.engine.hold_ticks_remaining, expected_rem)

        self.assertEqual(self.engine.hold_ticks_remaining, 15)

        # 进入全屏暗转时停 30 帧(is_time_stop=True), 倒计时必须完全冻结
        for _ in range(30):
            act = self.engine.step(0, is_time_stop=True)
            self.assertEqual(act, guard_act)
            self.assertEqual(self.engine.hold_ticks_remaining, 15, "时停期间 hold 倒计时不得减少")
            self.assertTrue(self.engine._in_time_stop)

    def test_hold_engine_thaw_reversal_buffer_attack(self):
        """2A. 解冻瞬间触发 Reversal Buffer: 时停中缓冲的出拳动作稳固维持 6 帧。"""
        # 前轻拳 6A: 方向 1(6前), 按键 1(A) -> action = 1 * 6 + 1 = 7
        punch_act = 1 * len(action_space.BUTTONS) + 1  # 7: 6 + A

        # 时停期间模型决定出拳打断
        act = self.engine.step(punch_act, is_time_stop=True)
        self.assertTrue(self.engine._in_time_stop)
        self.assertEqual(self.engine.buffered_action, punch_act)

        # 画面解冻第一帧 (is_time_stop=False), 即使外部采样是中立动作 0 (松手)
        thaw_act = self.engine.step(0, is_time_stop=False)
        # 解冻第一帧立即执行 buffered_action (6A 出拳)
        self.assertEqual(thaw_act, punch_act)
        self.assertFalse(self.engine._in_time_stop)
        # 总共维持 6 帧 (第 1 帧解冻帧执行, 接下来 5 帧锁定维持)
        for _ in range(5):
            held_act = self.engine.step(0, is_time_stop=False)
            self.assertEqual(held_act, punch_act, "reversal 锁定期间应稳定维持出拳指令")

        # 第 7 帧(维持期满), 恢复自由中立
        free_act = self.engine.step(0, is_time_stop=False)
        self.assertEqual(free_act, 0)
        self.assertFalse(self.engine.is_holding())

    def test_hold_engine_thaw_reversal_buffer_guard(self):
        """2B. 解冻瞬间触发 Reversal Buffer: 时停中缓冲的拉后防守稳固维持 6 帧。"""
        guard_act = action_space.GUARD_HOLD_ACTION  # 12: 4 + none

        # 时停期间决定拉后防守
        self.engine.step(guard_act, is_time_stop=True)
        self.assertEqual(self.engine.buffered_action, guard_act)

        # 解冻第一帧
        thaw_act = self.engine.step(0, is_time_stop=False)
        self.assertEqual(thaw_act, guard_act)

        # 后续 5 帧稳固维持拉后防守
        for _ in range(5):
            self.assertEqual(self.engine.step(0, is_time_stop=False), guard_act)

        # 维持期满
        self.assertEqual(self.engine.step(0, is_time_stop=False), 0)

    def test_hold_engine_backward_compatibility(self):
        """向后兼容: 缺省 is_time_stop 时行为与此前常规逻辑完全一致。"""
        guard_act = action_space.GUARD_HOLD_ACTION
        # 不传 is_time_stop
        self.assertEqual(self.engine.step(guard_act), guard_act)
        self.assertEqual(self.engine.hold_ticks_remaining, 20)
        self.assertEqual(self.engine.step(0), guard_act)
        self.assertEqual(self.engine.hold_ticks_remaining, 19)

        # 高优先级打断
        punch_act = 1 * len(action_space.BUTTONS) + 1
        self.assertEqual(self.engine.step(punch_act), punch_act)
        self.assertEqual(self.engine.hold_ticks_remaining, 0)
        self.assertIsNone(self.engine.hold_action_id)


class TestRewardSpellDefense(unittest.TestCase):
    """测试真实格挡解耦与符卡确反打断激励。"""

    def _st(self, hp0: float = 10000.0, hp1: float = 10000.0,
            x0: float = 400.0, x1: float = 800.0,
            motion0: int = 0, motion1: int = 0,
            damage_stop0: int = 0, damage_stop1: int = 0,
            hit_state0: int = 0, hit_state1: int = 0,
            spell_active0: bool = False, spell_active1: bool = False) -> Dict:
        return {
            "time": 8000,
            "battle_state": 8,
            "winner": 0,
            "teams": [
                {
                    "life": hp0, "life_max": 10000, "regain": hp0,
                    "sp": 1000, "sp_max": 1000, "mp": 0, "op": 0,
                    "spell_active": spell_active0,
                    "combo_count": 0, "current": 0,
                    "master": {"x": x0, "y": 360.0, "dir": 1.0, "motion": motion0,
                               "damage_stop": damage_stop0, "hit_state": hit_state0},
                },
                {
                    "life": hp1, "life_max": 10000, "regain": hp1,
                    "sp": 1000, "sp_max": 1000, "mp": 0, "op": 0,
                    "spell_active": spell_active1,
                    "combo_count": 0, "current": 0,
                    "master": {"x": x1, "y": 360.0, "dir": -1.0, "motion": motion1,
                               "damage_stop": damage_stop1, "hit_state": hit_state1},
                },
            ],
        }

    def test_guard_block_not_truncated_by_round_cap(self):
        """3. 真实受击格挡(guard_block)不受 0.45 的 guard_round_cap 截断。"""
        rc = RewardCalculator({"guard_round_cap": 0.45, "w_guard_read": 0.03, "guard_read_cap": 60})
        rc.reset_combat()

        # 先通过空拉后架招把 guard_round_score 刷满至 0.45
        hold_back = 2 * len(action_space.BUTTONS) + 0
        close_st = self._st(x0=500.0, x1=600.0, motion1=3200)  # 对手出招, 距离近
        for _ in range(20):
            rc.compute(snapshot(close_st), close_st, team=0, action=hold_back)

        self.assertAlmostEqual(rc._guard_round_score[0], 0.45, places=5)

        # 此时若继续空拉后架招, read_r 应被 0.45 封顶截断为 0.0
        _, c_read, _ = rc.compute(snapshot(close_st), close_st, team=0, action=hold_back)
        self.assertAlmostEqual(c_read["guard"], 0.0, places=5, msg="guard_read 应被 round cap 截断")

        # 但此时若发生真正的格挡事件 (motion 0 -> 120 通常格挡 Guard_Init)
        st_pre = self._st(x0=500.0, x1=600.0, motion0=0, motion1=3200)
        st_blk = self._st(x0=500.0, x1=600.0, motion0=120, motion1=3200)
        _, c_blk, _ = rc.compute(snapshot(st_pre), st_blk, team=0, action=hold_back)

        # 关键验证: 真实格挡必须产生完整的 w_guard_block (0.05), 不得被 0.45 锁死截断!
        self.assertAlmostEqual(c_blk["guard"], rc.C["w_guard_block"], places=5,
                               msg="真实受击格挡不受 guard_round_cap 死限截断")

    def test_spell_interrupt_by_motion_counter(self):
        """4A. 对手处于符卡动作(4000..4999), 我方出招打康打断触发 spell_interrupt。"""
        rc = RewardCalculator({"w_spell_interrupt": 0.20, "combat_scale": 0.25})
        rc.reset_combat()

        # 对手正在释放符卡 (motion=4200), 我方攻击使其掉血受创并打出 Counter
        st_prev = self._st(hp0=10000.0, hp1=10000.0, motion0=1000, motion1=4200)
        st_cur = self._st(hp0=10000.0, hp1=9600.0, motion0=1000, motion1=4200, damage_stop1=10)

        total, comp, _ = rc.compute(snapshot(st_prev), st_cur, team=0)
        counts = rc.counts()

        # 验证 spell_interrupt 计数 +1
        self.assertEqual(counts["spell_interrupt"], 1)
        # combat 项中应包含 (w_counter + w_spell_interrupt) * combat_scale
        # (0.08 + 0.20) * 0.25 = 0.070
        expected_combat = (rc.C["w_counter"] + rc.C["w_spell_interrupt"]) * rc.C["combat_scale"]
        self.assertAlmostEqual(comp["combat"], expected_combat, places=5)

    def test_spell_interrupt_by_damage_stop_and_spell_active(self):
        """4B. 对手处于 spell_active=True, 我方使其进入 damage_stop 受创触发打断。"""
        rc = RewardCalculator({"w_spell_interrupt": 0.20, "combat_scale": 0.25})
        rc.reset_combat()

        # 对手开启符卡暗转激活期 spell_active=True, 我方让其进入 damage_stop (0 -> 15)
        st_prev = self._st(hp0=10000.0, hp1=10000.0, motion1=0, spell_active1=True, damage_stop1=0)
        st_cur = self._st(hp0=10000.0, hp1=10000.0, motion1=0, spell_active1=True, damage_stop1=15)

        total, comp, _ = rc.compute(snapshot(st_prev), st_cur, team=0)
        self.assertEqual(rc.counts()["spell_interrupt"], 1)
        expected_combat = rc.C["w_spell_interrupt"] * rc.C["combat_scale"]
        self.assertAlmostEqual(comp["combat"], expected_combat, places=5)

    def test_spell_interrupt_no_multi_frame_farming(self):
        """4C. 对手符卡硬直持续多帧期间, spell_interrupt 仅奖励 1 次, 防止多帧刷分。"""
        rc = RewardCalculator({"w_spell_interrupt": 0.20, "combat_scale": 0.25})
        rc.reset_combat()

        # 第 1 帧: 成功打断
        st0 = self._st(hp1=10000.0, motion1=4100, damage_stop1=0)
        st1 = self._st(hp1=9500.0, motion1=4100, damage_stop1=12)
        rc.compute(snapshot(st0), st1, team=0)
        self.assertEqual(rc.counts()["spell_interrupt"], 1)

        # 第 2~5 帧: 对手持续处于 damage_stop 中且血量持续被压制, 但属于同一波符卡打断, 不得重复加分
        for _ in range(4):
            st_next = self._st(hp1=9300.0, motion1=4100, damage_stop1=10)
            _, comp, _ = rc.compute(snapshot(st1), st_next, team=0)
            self.assertEqual(rc.counts()["spell_interrupt"], 1, "同一波符卡硬直不得重复累加")
            st1 = st_next

        # 对手完全退出符卡 (motion 归 0, spell_active=False)
        st_neutral = self._st(hp1=9300.0, motion1=0, damage_stop1=0, spell_active1=False)
        rc.compute(snapshot(st1), st_neutral, team=0)

        # 对手再次开出下一张符卡并再次被打断
        st_spell2 = self._st(hp1=9300.0, motion1=4500, damage_stop1=0, spell_active1=True)
        st_hit2 = self._st(hp1=8800.0, motion1=4500, damage_stop1=15, spell_active1=True)
        rc.compute(snapshot(st_spell2), st_hit2, team=0)
        self.assertEqual(rc.counts()["spell_interrupt"], 2, "下一张符卡应能再次被打断奖励")


class TestTh155EnvTimeStopIntegration(unittest.TestCase):
    """测试 Th155Env 环境层时停状态检测与 info 字典透传。"""

    def test_env_time_stop_detection_and_info(self):
        """5. 环境层提取 is_time_stop / time_stop_count 并透传至 hold_engine 与 info。"""
        current_state = {
            "frame": 100,
            "battle_state": 8,
            "time": 8000,
            "is_time_stop": True,
            "time_stop_count": 45,
            "teams": [
                {"life": 10000, "life_max": 10000, "regain": 10000, "sp": 1000, "sp_max": 1000, "op": 0, "current": 0,
                 "master": {"x": 400.0, "y": 360.0, "dir": 1.0, "motion": 0}},
                {"life": 10000, "life_max": 10000, "regain": 10000, "sp": 1000, "sp_max": 1000, "op": 0, "current": 0,
                 "master": {"x": 800.0, "y": 360.0, "dir": -1.0, "motion": 4000}},
            ],
        }

        def mock_state():
            return current_state

        def mock_action(_act):
            pass

        def mock_reset(_t):
            return True

        env = Th155Env(state_fn=mock_state, action_fn=mock_action, reset_fn=mock_reset)
        env.reset()

        # 执行动作, 传入出拳
        punch_act = 1 * len(action_space.BUTTONS) + 1
        obs, reward, done, truncated, info = env.step(punch_act)

        # 验证 info 中包含 time_stop_active 且为 True
        self.assertIn("time_stop_active", info)
        self.assertTrue(info["time_stop_active"])
        # 验证 hold_engine 处于时停状态
        self.assertTrue(env.hold_engine._in_time_stop)
        self.assertEqual(env.hold_engine.buffered_action, punch_act)


if __name__ == "__main__":
    unittest.main()
