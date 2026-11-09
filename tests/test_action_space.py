"""Ticket 02 验收测试: 60Hz 逐帧决策反应、55 动作空间裁剪与持续格挡状态机 (Hold Guard Engine)。

Seams:
  * ActionHoldSeam: 动作空间裁剪、掩码计算、持续格挡状态机倒计时与中断
  * ActionRepeat60HzSeam: 60Hz (action_repeat=1) 环境步进、背向虚拟输入维持、按键脉冲与前向推理
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Dict

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents.ppo_agent import PolicyNet, to_batch
from env import (
    GUARD_ACTIONS,
    GUARD_HOLD_ACTION,
    GUARD_HOLD_LOW_ACTION,
    N_ACTIONS,
    ActionHoldEngine,
    Th155Env,
    action_space,
    is_guard_action,
    is_interrupt_action,
    obs_builder,
)


def _actor(x: float = 400.0, y: float = 300.0, dir_: float = 1.0) -> Dict:
    return {
        "x": x, "y": y, "vx": 0.0, "vy": 0.0, "dir": dir_,
        "motion": 0, "key_take": 0, "key_frame": 0,
        "hit_state": 0, "damage_stop": 0, "guard_state": 0,
        "dash_count": 0, "graze_state": 0,
        "box_l": x - 20, "box_t": y - 60, "box_r": x + 20, "box_b": y + 50,
        "cb_group": 1, "atk_id": 0, "hit_count": 0, "hit_result": 0,
        "atk_lv": 0, "atk_cancel": 0, "atk_guard_break": 0,
        "hit_back_flag": 0, "hit_id": 0, "is_attack": 0,
        "endure_count": 0, "endure_max": 0, "stan_count": 0, "recover": 0,
        "center_stop": 0, "gravity": 0.0, "slide_speed": 0.0,
        "atk_range": 0, "target_dist": 0.0, "atk_boxes": [],
    }


def _team(team_id: int, actor_dict: Dict, **kwargs) -> Dict:
    t = {
        "id": team_id, "current": 0, "life": 10000, "life_max": 10000,
        "regain": 10000, "sp": 1000, "sp_max": 1000, "mp": 0, "op": 0,
        "combo_count": 0, "combo_damage": 0, "damage_scale": 1.0,
        "combo_stun": 0, "master": actor_dict, "slave": _actor(640.0),
        "tag_ready": False, "spell_ready": False,
    }
    t.update(kwargs)
    return t


def _state(x0: float = 400.0, x1: float = 880.0, frame: int = 100) -> Dict:
    return {
        "frame": frame, "battle_state": 8, "time": 8000, "time_unit": 90,
        "round": 1, "win": [0, 0], "winner": 0, "match_num": 1,
        "attached": 1, "projectiles": [],
        "teams": [_team(0, _actor(x0, dir_=1.0)), _team(1, _actor(x1, dir_=-1.0))],
    }


class ActionHoldSeam(unittest.TestCase):
    """状态机持续格挡 (Hold Guard Engine) 与 55 动作空间契约测试。"""

    def test_action_space_dimensions_and_tag_removal(self):
        """验收标准: BUTTONS 保持 6 键, N_ACTIONS 扩展为 56, SPELL_ACTION 为 54, TAG_ACTION 为 55。"""
        self.assertNotIn("Tag", action_space.BUTTONS)
        self.assertEqual(len(action_space.BUTTONS), 6)
        self.assertEqual(action_space.BUTTONS, ("none", "A", "B", "C", "D", "Occult"))
        self.assertEqual(action_space.N_ACTIONS, 56)
        self.assertEqual(action_space.SPELL_ACTION, 54)
        self.assertEqual(action_space.TAG_ACTION, 55)

    def test_decode_and_to_input_all_56_actions(self):
        """所有 56 个动作均能合法解码并翻译为 7 维物理输入 (x, y, b0..b4)。"""
        for a in range(action_space.N_ACTIONS):
            d, b, dir_name, btn_name = action_space.decode(a)
            self.assertIsInstance(d, int)
            self.assertIsInstance(b, int)
            self.assertIsInstance(dir_name, str)
            self.assertIsInstance(btn_name, str)
            inp = action_space.to_input(a, facing_sign=1)
            self.assertEqual(len(inp), 7)
            if a == action_space.TAG_ACTION:
                self.assertEqual(inp[5], 1, f"TAG_ACTION 必须产生 b3=1")
                self.assertEqual(inp[:5], (0, 0, 0, 0, 0))
                self.assertEqual(inp[6], 0)
            else:
                self.assertEqual(inp[5], 0, f"action {a} 产生了非零 Tag 按键位")

        # 符卡动作特殊测试
        d_sp, b_sp, dn_sp, bn_sp = action_space.decode(action_space.SPELL_ACTION)
        self.assertEqual(bn_sp, "Spell")
        self.assertEqual(b_sp, -1)
        inp_sp = action_space.to_input(action_space.SPELL_ACTION, facing_sign=1)
        self.assertEqual(inp_sp[2:], action_space.SPELL_KEYS)

        # 换人动作特殊测试
        d_tag, b_tag, dn_tag, bn_tag = action_space.decode(action_space.TAG_ACTION)
        self.assertEqual(bn_tag, "Tag")
        self.assertEqual(b_tag, -1)
        inp_tag = action_space.to_input(action_space.TAG_ACTION, facing_sign=1)
        self.assertEqual(inp_tag, (0, 0, 0, 0, 0, 1, 0))

    def test_guard_action_predicates(self):
        """防守动作判定: 4 后(12)、1 后下(48)、7 后上(36)不出招为 True; 其余为 False。"""
        self.assertTrue(is_guard_action(GUARD_HOLD_ACTION))         # 4 + none = 12
        self.assertTrue(is_guard_action(GUARD_HOLD_LOW_ACTION))     # 1 + none = 48
        self.assertTrue(is_guard_action(6 * 6 + 0))                 # 7 + none = 36
        for g in GUARD_ACTIONS:
            self.assertTrue(is_guard_action(g))

        # 出招不算防守
        self.assertFalse(is_guard_action(2 * 6 + 1))  # 4 + A
        self.assertFalse(is_guard_action(1 * 6 + 0))  # 6 + none (前走)
        self.assertFalse(is_guard_action(0))          # 5 + none (中立)
        self.assertFalse(is_guard_action(action_space.SPELL_ACTION))
        self.assertFalse(is_guard_action(action_space.TAG_ACTION))
        self.assertFalse(is_guard_action(-1))
        self.assertFalse(is_guard_action(100))

    def test_interrupt_action_predicates(self):
        """高优先级打断指令判定: 攻击键 (A/B/C/Occult)、符卡 (54)、换人 (55) 或向上跳跃 (8 上=18, 9 前上=30)。"""
        self.assertTrue(is_interrupt_action(action_space.SPELL_ACTION))
        self.assertTrue(is_interrupt_action(action_space.TAG_ACTION))
        self.assertTrue(is_interrupt_action(3 * 6 + 0))  # 8 上
        self.assertTrue(is_interrupt_action(5 * 6 + 0))  # 9 前上
        self.assertTrue(is_interrupt_action(0 * 6 + 1))  # 5A
        self.assertTrue(is_interrupt_action(1 * 6 + 1))  # 6A
        self.assertTrue(is_interrupt_action(1 * 6 + 2))  # 6B
        self.assertTrue(is_interrupt_action(1 * 6 + 3))  # 6C
        self.assertTrue(is_interrupt_action(0 * 6 + 5))  # 5 Occult
        self.assertFalse(is_interrupt_action(GUARD_HOLD_ACTION))
        self.assertFalse(is_interrupt_action(0))
        self.assertFalse(is_interrupt_action(1 * 6 + 0))  # 6 none (前走无攻击)
        self.assertFalse(is_interrupt_action(4 * 6 + 0))  # 2 none (蹲下无攻击)
        self.assertFalse(is_interrupt_action(2 * 6 + 4))  # 4D

    def test_hold_engine_trigger_countdown_and_expiry(self):
        """持续格挡状态机: 触发后维持指定 K 帧, 忽略高频跳变动作, 倒计时归零后释放。"""
        K = 20
        engine = ActionHoldEngine(hold_ticks=K)
        self.assertFalse(engine.is_holding())
        self.assertIsNone(engine.hold_action_id)
        self.assertEqual(engine.hold_ticks_remaining, 0)

        # 第 0 步: 智能体选择防守动作 4 (12)
        eff0 = engine.step(GUARD_HOLD_ACTION)
        self.assertEqual(eff0, GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        self.assertEqual(engine.hold_action_id, GUARD_HOLD_ACTION)
        self.assertEqual(engine.hold_ticks_remaining, K)

        # 第 1 ~ 20 步: 智能体每步独立采样到随机跳变动作 (攻击、前冲、中立等)
        # 状态机必须全部拦截并锁定为 GUARD_HOLD_ACTION, 且倒计时每步减 1
        rng = np.random.default_rng(42)
        for step_idx in range(1, K + 1):
            rand_act = int(rng.integers(0, 50))
            if is_guard_action(rand_act) or is_interrupt_action(rand_act):
                rand_act = 0  # 5 + none (中立非打断动作)
            eff = engine.step(rand_act)
            self.assertEqual(eff, GUARD_HOLD_ACTION,
                             f"第 {step_idx} 步应维持持续格挡, 实际返回 {eff}")
            expected_rem = K - step_idx
            self.assertEqual(engine.hold_ticks_remaining, expected_rem)
            if expected_rem > 0:
                self.assertTrue(engine.is_holding())

        # 第 21 步: 倒计时已归零, 状态机释放, 智能体恢复自由决策
        self.assertFalse(engine.is_holding())
        self.assertIsNone(engine.hold_action_id)
        normal_act = 1 * 6 + 1  # 6A (前攻击)
        eff_free = engine.step(normal_act)
        self.assertEqual(eff_free, normal_act)

    def test_hold_engine_interruption_by_spell_and_jump(self):
        """持续格挡状态机在维持期间能被高优先级符卡或跳跃立刻打断。"""
        engine = ActionHoldEngine(hold_ticks=20)
        engine.step(GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        engine.step(0)
        self.assertEqual(engine.hold_ticks_remaining, 19)

        # 符卡爆气打断
        eff = engine.step(action_space.SPELL_ACTION)
        self.assertEqual(eff, action_space.SPELL_ACTION)
        self.assertFalse(engine.is_holding())
        self.assertEqual(engine.hold_ticks_remaining, 0)
        self.assertIsNone(engine.hold_action_id)

        # 跳跃打断
        engine.step(GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        jump_up = 3 * 6 + 0  # 8 上
        eff_jump = engine.step(jump_up)
        self.assertEqual(eff_jump, jump_up)
        self.assertFalse(engine.is_holding())

        # 凭依换人打断
        engine.step(GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        eff_tag = engine.step(action_space.TAG_ACTION)
        self.assertEqual(eff_tag, action_space.TAG_ACTION)
        self.assertFalse(engine.is_holding())

    def test_hold_engine_interrupted_by_attack_buttons(self):
        """Ticket 08: 持续格挡在维持期间能被任何有效攻击动作 (A/B/C/Occult) 立即打断并清空倒计时。"""
        engine = ActionHoldEngine(hold_ticks=20)

        # 1. 5A 打断 (中立 + A)
        engine.step(GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        eff_5a = engine.step(0 * 6 + 1)  # 5A
        self.assertEqual(eff_5a, 0 * 6 + 1)
        self.assertFalse(engine.is_holding())
        self.assertEqual(engine.hold_ticks_remaining, 0)
        self.assertIsNone(engine.hold_action_id)

        # 2. 6A 打断 (推前出拳确反)
        engine.step(GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        eff_6a = engine.step(1 * 6 + 1)  # 6A
        self.assertEqual(eff_6a, 1 * 6 + 1)
        self.assertFalse(engine.is_holding())

        # 3. 2B 必杀技打断 (下 + B)
        engine.step(GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        eff_2b = engine.step(4 * 6 + 2)  # 2B
        self.assertEqual(eff_2b, 4 * 6 + 2)
        self.assertFalse(engine.is_holding())

        # 4. 6C 强攻击打断 (前 + C)
        engine.step(GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        eff_6c = engine.step(1 * 6 + 3)  # 6C
        self.assertEqual(eff_6c, 1 * 6 + 3)
        self.assertFalse(engine.is_holding())

        # 5. Occult 怪奇必杀打断
        engine.step(GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        eff_occult = engine.step(0 * 6 + 5)  # 5 Occult
        self.assertEqual(eff_occult, 0 * 6 + 5)
        self.assertFalse(engine.is_holding())

        # 6. 中立 5 与后退动作仍正常维持 / 刷新
        engine.step(GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        eff_5 = engine.step(0)  # 5 中立
        self.assertEqual(eff_5, GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())
        self.assertEqual(engine.hold_ticks_remaining, 19)

    def test_hold_engine_refresh_and_switch_guard(self):
        """持续格挡维持中再次选择防守动作, 会刷新倒计时或切换防守姿态。"""
        engine = ActionHoldEngine(hold_ticks=20)
        engine.step(GUARD_HOLD_ACTION)
        for _ in range(5):
            engine.step(0)
        self.assertEqual(engine.hold_ticks_remaining, 15)

        # 刷新同一防守动作
        engine.step(GUARD_HOLD_ACTION)
        self.assertEqual(engine.hold_ticks_remaining, 20)

        for _ in range(3):
            engine.step(0)
        self.assertEqual(engine.hold_ticks_remaining, 17)

        # 切换到下防 (GUARD_HOLD_LOW_ACTION)
        engine.step(GUARD_HOLD_LOW_ACTION)
        self.assertEqual(engine.hold_action_id, GUARD_HOLD_LOW_ACTION)
        self.assertEqual(engine.hold_ticks_remaining, 20)
        eff = engine.step(0)
        self.assertEqual(eff, GUARD_HOLD_LOW_ACTION)

    def test_compute_mask_56(self):
        """动作掩码在 56 动作空间下正确过滤灵力、怪奇槽、符卡与换人白血门槛。"""
        t = _team(0, _actor(), sp=0, op=0, spell_ready=False, tag_ready=False, slave_ban=0, life=5000, regain=5000)
        m = action_space.compute_mask(t)
        self.assertEqual(len(m), 56)
        # B(2), C(3), Occult(5) 应全方向被禁
        for d in range(len(action_space.DIRECTIONS)):
            for b_name in ("B", "C", "Occult"):
                b_idx = action_space.BUTTONS.index(b_name)
                self.assertFalse(m[d * 6 + b_idx])
        # A 与 none 可用
        self.assertTrue(m[0])
        self.assertTrue(m[1])
        # 符卡未就绪被禁
        self.assertFalse(m[action_space.SPELL_ACTION])
        # 换人未就绪被禁
        self.assertFalse(m[action_space.TAG_ACTION])

        # 换人条件消融:
        # 1. tag_ready=True, 但白血差额不足 (regain - life < 1000) -> 屏蔽
        t_gap_low = _team(0, _actor(), sp=1000, tag_ready=True, slave_ban=0, life=5000, regain=5800)
        self.assertFalse(action_space.compute_mask(t_gap_low)[action_space.TAG_ACTION])

        # 2. tag_ready=True, 白血充裕 (gap >= 1000), 但处于换人 CD (slave_ban > 0) -> 屏蔽
        t_banned = _team(0, _actor(), sp=1000, tag_ready=True, slave_ban=120, life=5000, regain=6500)
        self.assertFalse(action_space.compute_mask(t_banned)[action_space.TAG_ACTION])

        # 3. tag_ready=False, 白血充裕且无 CD -> 屏蔽
        t_not_ready = _team(0, _actor(), sp=1000, tag_ready=False, slave_ban=0, life=5000, regain=6500)
        self.assertFalse(action_space.compute_mask(t_not_ready)[action_space.TAG_ACTION])

        # 4. 全部就绪: tag_ready=True, slave_ban=0, regain - life >= 1000 -> 放行
        t_ready = _team(0, _actor(), sp=1000, op=2000, occult_max=1000, spell_ready=True,
                        tag_ready=True, slave_ban=0, life=5000, regain=6000)
        m_ready = action_space.compute_mask(t_ready)
        self.assertTrue(m_ready[action_space.SPELL_ACTION])
        self.assertTrue(m_ready[action_space.TAG_ACTION])
        self.assertTrue(m_ready[action_space.BUTTONS.index("Occult")])

        # 5. action_enabled=False 时整掩码全 False
        m_disabled = action_space.compute_mask(t_ready, action_enabled=False)
        self.assertEqual(m_disabled, [False] * 56)

    def test_hold_engine_zero_or_negative_ticks_disabled(self):
        """持续格挡状态机在 hold_ticks <= 0 或 enabled=False 时直接透传动作, 不触发维持。"""
        # hold_ticks = 0
        eng0 = ActionHoldEngine(hold_ticks=0)
        self.assertFalse(eng0.is_holding())
        eff0 = eng0.step(GUARD_HOLD_ACTION)
        self.assertEqual(eff0, GUARD_HOLD_ACTION)
        self.assertFalse(eng0.is_holding())
        self.assertEqual(eng0.hold_ticks_remaining, 0)
        self.assertIsNone(eng0.hold_action_id)

        # hold_ticks < 0
        eng_neg = ActionHoldEngine(hold_ticks=-5)
        eff_neg = eng_neg.step(GUARD_HOLD_ACTION)
        self.assertEqual(eff_neg, GUARD_HOLD_ACTION)
        self.assertFalse(eng_neg.is_holding())

        # enabled = False
        eng_dis = ActionHoldEngine(hold_ticks=20, enabled=False)
        eff_dis = eng_dis.step(GUARD_HOLD_ACTION)
        self.assertEqual(eff_dis, GUARD_HOLD_ACTION)
        self.assertFalse(eng_dis.is_holding())


class ActionRepeat60HzSeam(unittest.TestCase):
    """60Hz (action_repeat=1) 逐帧决策运行、虚拟输入维持与按键脉冲测试。"""

    def setUp(self):
        self.st = _state(x0=400.0, x1=880.0, frame=100)
        self.actions = []

    def _state_fn(self) -> Dict:
        return self.st

    def _action_fn(self, act: tuple) -> None:
        self.actions.append(act)

    def _make_env(self, action_repeat: int = 1, **kwargs) -> Th155Env:
        def _rf(timeout: float):
            return True

        def _act(act: tuple) -> None:
            self.actions.append(act)
            self.st["frame"] += action_repeat

        def _get_state():
            return copy.deepcopy(self.st)

        return Th155Env(
            action_repeat=action_repeat,
            state_fn=_get_state,
            action_fn=_act,
            reset_fn=_rf,
            **kwargs,
        )

    def test_env_default_action_repeat_is_1(self):
        """环境默认配置支持 action_repeat=1 (60Hz 逐帧决策)。"""
        env = Th155Env(state_fn=self._state_fn, action_fn=self._action_fn)
        self.assertEqual(env.action_repeat, 1)

    def test_env_step_60hz_maintains_hold_guard_input(self):
        """在 60Hz 下触发持续格挡, 底层连续多步严格维持向后输入 (x=-1)。"""
        env = self._make_env(action_repeat=1)
        env.reset()
        self.actions.clear()

        # Step 0: 触发持续格挡
        obs, rew, done, trunc, info = env.step(GUARD_HOLD_ACTION)
        self.assertEqual(info["effective_action"], GUARD_HOLD_ACTION)
        self.assertTrue(info["hold_active"])
        self.assertEqual(env.hold_ticks_remaining, 20)
        self.assertEqual(self.actions[-1][0], -1)  # 对手在右(+1), 后退 = -1
        self.assertEqual(self.actions[-1][2:], (0, 0, 0, 0, 0))  # 无出招按键

        # Step 1 ~ 5: 每步传入中立动作 0 (5 none)
        # 底层输入必须保持 x=-1, 且按键维持 0!
        for i in range(1, 6):
            obs, rew, done, trunc, info = env.step(0)  # 5 none
            self.assertEqual(info["effective_action"], GUARD_HOLD_ACTION)
            self.assertEqual(self.actions[-1][0], -1)
            self.assertEqual(self.actions[-1][2:], (0, 0, 0, 0, 0))
            self.assertEqual(env.hold_ticks_remaining, 20 - i)

        # Step 6: 传入攻击动作 6A (1 * 6 + 1)
        # Ticket 08: 攻击键立即打断持续格挡, 毫无延迟地推前出拳打出确反!
        obs, rew, done, trunc, info = env.step(1 * 6 + 1)  # 6A
        self.assertEqual(info["effective_action"], 1 * 6 + 1)
        self.assertFalse(info["hold_active"])
        self.assertEqual(env.hold_ticks_remaining, 0)
        self.assertEqual(self.actions[-1][0], 1)  # 推前
        self.assertEqual(self.actions[-1][2], 1)  # 按下 A 键

    def test_facing_flip_dynamically_maintains_back_direction(self):
        """格挡维持期间若对手发生换位 (Cross-up), 底层输入自动适应为新的背向。"""
        env = self._make_env(action_repeat=1)
        env.reset()
        self.actions.clear()

        # 对手在右侧 (x0=400, x1=880) -> 后退为 -1
        env.step(GUARD_HOLD_ACTION)
        self.assertEqual(self.actions[-1][0], -1)

        # 对手换位穿越到左侧 (x1 变为 200 < x0 400)
        self.st["teams"][1]["master"]["x"] = 200.0
        # 下一步让环境观测到新坐标
        env.step(0)
        # 再下一步持续格挡自动反转为 +1 (远离左侧对手)!
        env.step(0)
        self.assertEqual(self.actions[-1][0], 1)
        self.assertEqual(self.actions[-1][2:], (0, 0, 0, 0, 0))

    def test_key_pulse_attack_buttons_in_60hz(self):
        """在 action_repeat=1 下, 连续相同攻击键 (AAAA) 产生 30Hz 按下/释放脉冲上升沿。"""
        env = self._make_env(action_repeat=1)
        env.reset()
        self.actions.clear()

        act_a = 0 * 6 + 1  # 5A (中立 + A)
        # Frame 0: 首次按下 A
        env.step(act_a)
        self.assertEqual(self.actions[-1][2], 1, "第 0 步应按下 A")

        # Frame 1: 连续第 2 次输入 A -> 释放 1 帧上升沿
        env.step(act_a)
        self.assertEqual(self.actions[-1][2], 0, "第 1 步应释放 A 以产生上升沿")

        # Frame 2: 连续第 3 次输入 A -> 再次按下 A (连段第 2 击)
        env.step(act_a)
        self.assertEqual(self.actions[-1][2], 1, "第 2 步应再次按下 A")

        # Frame 3: 连续第 4 次输入 A -> 再次释放
        env.step(act_a)
        self.assertEqual(self.actions[-1][2], 0, "第 3 步应再次释放 A")

    def test_key_pulse_does_not_interrupt_dash_d(self):
        """在 action_repeat=1 下, 冲刺/擦弹键 D (按键 4) 保持长按维持, 绝不脉冲打断。"""
        env = self._make_env(action_repeat=1)
        env.reset()
        self.actions.clear()

        act_d = 1 * 6 + 4  # 6D (前冲刺)
        for step_idx in range(4):
            env.step(act_d)
            # b4 是 D 按键位 (索引 6)
            self.assertEqual(self.actions[-1][6], 1, f"第 {step_idx} 步冲刺 D 不应被脉冲释放")

    def test_policy_net_forward_56_action_dimension(self):
        """PolicyNet.actor 输出头维度完全匹配 56, 前向推理正常。"""
        net = PolicyNet()
        self.assertEqual(net.actor.out_features, 56)

        dummy_obs = {
            "self": np.zeros(obs_builder.SELF_DIM, dtype=np.float32),
            "opp": np.zeros(obs_builder.OPP_DIM, dtype=np.float32),
            "global": np.zeros(obs_builder.GLOBAL_DIM, dtype=np.float32),
            "proj": np.zeros((obs_builder.MAX_PROJ, obs_builder.PROJ_DIMS), dtype=np.float32),
            "proj_mask": np.zeros(obs_builder.MAX_PROJ, dtype=np.float32),
            "motion_self": 0,
            "motion_opp": 0,
            "action_mask": np.ones(56, dtype=bool),
        }
        h = net.init_hidden(1)
        batch = {k: torch.as_tensor(v).unsqueeze(0) for k, v in to_batch(dummy_obs).items()}
        with torch.no_grad():
            logits, val, h2 = net.forward(batch, h, seq=False)
        self.assertEqual(logits.shape, (1, 56))
        self.assertEqual(val.shape, (1,))

    def test_env_reset_clears_hold_engine_state_and_pulse(self):
        """回合重置时必须完全清零持续格挡状态机与按键脉冲状态, 杜绝上一局防守状态跨局泄漏。"""
        env = self._make_env(action_repeat=1)
        env.reset()

        # 触发按键脉冲 (连续两步输入 A)
        env.step(1)  # 5A (首次按下)
        self.assertFalse(env._pulse_released)
        env.step(1)  # 5A (触发释放脉冲)
        self.assertTrue(env._pulse_released)

        # 触发持续格挡
        env.step(GUARD_HOLD_ACTION)
        self.assertTrue(env.hold_engine.is_holding())
        self.assertEqual(env.hold_ticks_remaining, 20)
        self.assertEqual(env.hold_action_id, GUARD_HOLD_ACTION)

        # 重置环境
        obs, info = env.reset()
        self.assertFalse(env.hold_engine.is_holding())
        self.assertEqual(env.hold_ticks_remaining, 0)
        self.assertIsNone(env.hold_action_id)
        self.assertFalse(env._pulse_released)
        self.assertEqual(env._prev_action, 0)

    def test_key_pulse_disabled_holds_attack_button(self):
        """当 key_pulse_repeat=False 时, 连续相同攻击键长按不脉冲, 物理按键位持续维持按下。"""
        env = self._make_env(action_repeat=1)
        env.key_pulse_repeat = False
        env.reset()
        self.actions.clear()

        act_a = 0 * 6 + 1  # 5A
        for step_idx in range(4):
            env.step(act_a)
            self.assertEqual(self.actions[-1][2], 1, f"第 {step_idx} 步在 key_pulse 禁用时不应被释放")

    def test_warm_start_55_to_56_action_mapping(self):
        """PolicyNet.warm_start 正确将旧 55 动作权重映射到 56 动作结构中并置 TAG_ACTION 为 0。"""
        net = PolicyNet()
        old_sd = dict(net.state_dict())
        old_w = torch.randn(55, net.lstm_hidden)
        old_b = torch.randn(55)
        old_sd["actor.weight"] = old_w
        old_sd["actor.bias"] = old_b

        skipped = net.warm_start(old_sd, verbose=False)
        self.assertNotIn("actor.weight", skipped)
        self.assertNotIn("actor.bias", skipped)
        self.assertEqual(net.actor.weight.shape, (56, net.lstm_hidden))
        self.assertEqual(net.actor.bias.shape, (56,))

        # 前 55 项完全保持一致
        self.assertTrue(torch.allclose(net.actor.weight[:55], old_w[:55]))
        self.assertTrue(torch.allclose(net.actor.bias[:55], old_b[:55]))

        # 第 55 项 (TAG_ACTION) 置 0
        self.assertTrue(torch.allclose(net.actor.weight[55], torch.zeros(net.lstm_hidden)))
        self.assertTrue(torch.allclose(net.actor.bias[55], torch.tensor(0.0)))

    def test_warm_start_64_to_56_action_mapping(self):
        """PolicyNet.warm_start 正确将旧 64 动作权重映射到 56 动作结构中(含中立 Tag 与 Spell)。"""
        net = PolicyNet()
        old_sd = dict(net.state_dict())
        old_w = torch.randn(64, net.lstm_hidden)
        old_b = torch.randn(64)
        old_sd["actor.weight"] = old_w
        old_sd["actor.bias"] = old_b

        skipped = net.warm_start(old_sd, verbose=False)
        self.assertNotIn("actor.weight", skipped)
        self.assertNotIn("actor.bias", skipped)
        self.assertEqual(net.actor.weight.shape, (56, net.lstm_hidden))
        self.assertEqual(net.actor.bias.shape, (56,))

        # Spell 63 -> 54
        self.assertTrue(torch.allclose(net.actor.weight[54], old_w[63]))
        self.assertTrue(torch.allclose(net.actor.bias[54], old_b[63]))

        # 中立 Tag: old 5 (dir=0, btn=5) -> new 55 (TAG_ACTION)
        self.assertTrue(torch.allclose(net.actor.weight[55], old_w[5]))
        self.assertTrue(torch.allclose(net.actor.bias[55], old_b[5]))


if __name__ == "__main__":
    unittest.main()
