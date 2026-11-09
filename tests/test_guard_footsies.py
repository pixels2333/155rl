"""Ticket 08 专项验收测试: 防御反击立回 (Guard Footsies)、解除反击自锁与 60Hz 确反窗口。

测试内容:
1. 解除反击自锁: ActionHoldEngine 收到后退防御后, 任何攻击键 (A/B/C/Occult/Spell/Tag) 或跳跃
   立即打断防御维持 (self.reset()), 智能体防住对手后可零延迟推前出拳打出确反;
   中立 5(无攻击键) 或后退相关动作继续按 hold_ticks 维持后退防守。
2. 60Hz 确反窗口与判定放宽:
   - 确反窗口扩大至 25 步 (60Hz 下约 416ms);
   - 抓对手出招结束后的【收招硬直】(Recovery, motion < 1000) 仍正确判定防后确反 (+0.15);
   - 首次命中后 _guard_t 立即归零, 避免后续连击重复刷分;
   - 对手出招中途命中同时获得打康 (+0.08) 与确反 (+0.15);
   - 我方出招被打康受到加大惩罚 (-0.08), 且正确计入 countered 诊断指标。
3. 中近距离架招稠密引导 (w_guard_read = 0.03, guard_read_range = 240.0)。
4. 配置文件与训练加载同步。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml
from env import (
    GUARD_ACTIONS,
    GUARD_HOLD_ACTION,
    GUARD_HOLD_LOW_ACTION,
    ActionHoldEngine,
    Th155Env,
    action_space,
    is_guard_action,
    is_interrupt_action,
    load_env_config,
)
from env.reward import RewardCalculator, snapshot
from train.train_ppo import build_env, load_config


def _actor(x: float = 400.0, y: float = 300.0, dir_: float = 1.0, motion: int = 0) -> dict:
    return {
        "x": x, "y": y, "vx": 0.0, "vy": 0.0, "dir": dir_,
        "motion": motion, "key_take": 0, "key_frame": 0,
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


def _team(team_id: int, actor_dict: dict, life: int = 10000, **kwargs) -> dict:
    t = {
        "id": team_id, "current": 0, "life": life, "life_max": 10000,
        "regain": life, "sp": 1000, "sp_max": 1000, "mp": 0, "op": 0,
        "combo_count": 0, "combo_damage": 0, "damage_scale": 1.0,
        "combo_stun": 0, "master": actor_dict, "slave": _actor(640.0),
        "tag_ready": False, "spell_ready": False,
    }
    t.update(kwargs)
    return t


def _state(x0: float = 400.0, x1: float = 600.0, life0: int = 10000, life1: int = 10000,
           motion0: int = 0, motion1: int = 0, guard0: int = 0, guard1: int = 0) -> dict:
    a0 = _actor(x0, motion=motion0)
    a0["guard_state"] = guard0
    a1 = _actor(x1, motion=motion1)
    a1["guard_state"] = guard1
    return {
        "frame": 100, "battle_state": 8, "time": 8000, "time_unit": 90,
        "round": 1, "win": [0, 0], "winner": 0, "match_num": 1,
        "attached": 1, "shots": [],
        "teams": [_team(0, a0, life=life0), _team(1, a1, life=life1)],
    }


class TestUnlockCounterPunish(unittest.TestCase):
    """1. 解除反击自锁测试。"""

    def test_all_attack_actions_interrupt_hold_engine(self):
        """所有有效攻击键 (A/B/C/Occult/Spell/Tag) 与跳跃动作均判定为打断指令。"""
        # A 键: 5A, 6A, 2A, 4A, 1A, 3A
        for d in range(len(action_space.DIRECTIONS)):
            act_a = d * len(action_space.BUTTONS) + 1  # b=1 (A)
            self.assertTrue(is_interrupt_action(act_a), f"direction {d} + A 必须打断防守维持")

        # B 键 (b=2), C 键 (b=3), Occult 键 (b=5)
        for d in range(len(action_space.DIRECTIONS)):
            self.assertTrue(is_interrupt_action(d * len(action_space.BUTTONS) + 2))
            self.assertTrue(is_interrupt_action(d * len(action_space.BUTTONS) + 3))
            self.assertTrue(is_interrupt_action(d * len(action_space.BUTTONS) + 5))

        # 特殊动作: 符卡 (54) 与 换人 (55)
        self.assertTrue(is_interrupt_action(action_space.SPELL_ACTION))
        self.assertTrue(is_interrupt_action(action_space.TAG_ACTION))

        # 向上跳跃: 8 (d=3), 9 (d=5)
        self.assertTrue(is_interrupt_action(3 * len(action_space.BUTTONS) + 0))
        self.assertTrue(is_interrupt_action(5 * len(action_space.BUTTONS) + 0))

    def test_non_attack_neutral_and_back_do_not_interrupt(self):
        """中立 5(无按键) 与后退相关动作不判定为打断指令。"""
        self.assertFalse(is_interrupt_action(0))  # 5 none
        self.assertFalse(is_interrupt_action(GUARD_HOLD_ACTION))  # 4 none
        self.assertFalse(is_interrupt_action(GUARD_HOLD_LOW_ACTION))  # 1 none
        self.assertFalse(is_interrupt_action(6 * len(action_space.BUTTONS) + 0))  # 7 none
        self.assertFalse(is_interrupt_action(1 * len(action_space.BUTTONS) + 0))  # 6 none (前走)
        self.assertFalse(is_interrupt_action(4 * len(action_space.BUTTONS) + 0))  # 2 none (下蹲)

    def test_engine_instant_reset_on_counter_attack(self):
        """状态机在维持防守期间, 收到 6A 攻击立即 reset 并直出 6A, 不吞招。"""
        engine = ActionHoldEngine(hold_ticks=20)
        eff = engine.step(GUARD_HOLD_ACTION)
        self.assertEqual(eff, GUARD_HOLD_ACTION)
        self.assertTrue(engine.is_holding())

        # 维持 3 帧
        for _ in range(3):
            eff = engine.step(0)  # 中立
            self.assertEqual(eff, GUARD_HOLD_ACTION)
        self.assertEqual(engine.hold_ticks_remaining, 17)

        # 智能体决定推前出拳打确反: 6A
        act_6a = 1 * len(action_space.BUTTONS) + 1  # 6A
        eff_attack = engine.step(act_6a)
        self.assertEqual(eff_attack, act_6a, "出拳动作必须立即被返回, 不能被底层吞掉覆写为防守")
        self.assertFalse(engine.is_holding(), "攻击后状态机必须立即释放维持状态")
        self.assertEqual(engine.hold_ticks_remaining, 0)
        self.assertIsNone(engine.hold_action_id)

        # 紧接着下一步中立动作, 不再被覆写为防守
        eff_next = engine.step(0)
        self.assertEqual(eff_next, 0)


class TestReward60HzGuardFootsies(unittest.TestCase):
    """2 & 3. 60Hz 确反窗口放宽、打康与架招奖励测试。"""

    def setUp(self):
        self.env_cfg = load_env_config()
        self.rc = RewardCalculator(dict(self.env_cfg["reward"], combat_scale=1.0))
        self.rc.reset_combat()

    def test_guard_window_default_is_25(self):
        """确反窗口在 60Hz 下默认为 25 步 (约 416ms)。"""
        self.assertEqual(self.rc.C["guard_window"], 25)
        self.assertEqual(self.rc.C["w_guard_punish"], 0.15)
        self.assertEqual(self.rc.C["w_counter"], 0.08)
        self.assertEqual(self.rc.C["w_guard_read"], 0.03)
        self.assertEqual(self.rc.C["guard_read_range"], 240.0)

    def test_guard_punish_on_recovery_motion(self):
        """确反判定放宽: 格挡成功后, 即使对手已处于收招硬直 (motion < 1000) 仍正确计入确反。"""
        # 1. 我方持续防御 min_run 次, 成功开启确反窗口
        g1 = _state(guard0=1, motion1=3200)
        for _ in range(int(self.rc.C["guard_min_run"])):
            self.rc.compute(snapshot(g1), g1, team=0)
        self.assertEqual(self.rc._guard_t[0], int(self.rc.C["guard_window"]) - 1,
                         "格挡成功当步扣减 1 帧后确反倒计时为 24")

        # 2. 模拟对手出招动作结束, 进入收招硬直或原地站立 (motion=200, < 1000)
        prev = snapshot(_state(life1=10000, motion1=200))
        # 3. 我方出拳命中对手造成实血下降
        cur = _state(life1=9500, motion1=50)
        _, comp, _ = self.rc.compute(prev, cur, team=0)

        # 验证: 获得防后确反 (+0.15), 对手未在攻击动作中故无打康 (+0.0)
        self.assertAlmostEqual(comp["combat"], 0.15, places=6)
        self.assertEqual(self.rc.counts()["guard_punish"], 1)
        self.assertEqual(self.rc.counts()["counter"], 0)
        self.assertEqual(self.rc._guard_t[0], 0, "确反命中后计时器必须立即清零")

        # 4. 连段后续攻击命中 (life1 继续下降), 不重复计算确反基数
        prev2 = snapshot(cur)
        cur2 = _state(life1=9000, motion1=50)
        _, comp2, _ = self.rc.compute(prev2, cur2, team=0)
        # 第 2 击只获得连段奖励 (+w_combo = 0.01), 不重复获得确反
        self.assertAlmostEqual(comp2["combat"], 0.01, places=6)
        self.assertEqual(self.rc.counts()["guard_punish"], 1)

    def test_guard_punish_during_opponent_attack_startup(self):
        """若对手仍在出招中 (motion in [1000, 4999]), 命中同时获得打康 (+0.08) 与确反 (+0.15)。"""
        g1 = _state(guard0=1, motion1=3200)
        for _ in range(int(self.rc.C["guard_min_run"])):
            self.rc.compute(snapshot(g1), g1, team=0)

        prev = snapshot(_state(life1=10000, motion1=3200))  # 攻击动作中
        cur = _state(life1=9500, motion1=50)
        _, comp, _ = self.rc.compute(prev, cur, team=0)

        self.assertAlmostEqual(comp["combat"], 0.08 + 0.15, places=6)
        self.assertEqual(self.rc.counts()["guard_punish"], 1)
        self.assertEqual(self.rc.counts()["counter"], 1)
        self.assertEqual(self.rc._guard_t[0], 0)

    def test_countered_penalty_and_tracking(self):
        """我方盲目出招被打康: 零和项 -0.08 + 非对称被康惩罚 -0.16 = -0.24, 且累加 countered 计数。"""
        prev = snapshot(_state(life0=10000, motion0=3200))  # 我方在攻击动作中
        cur = _state(life0=9500, motion0=50)               # 我方被打掉血
        _, comp, _ = self.rc.compute(prev, cur, team=0)

        self.assertAlmostEqual(comp["combat"], -(0.08 + 0.16), places=6)
        self.assertEqual(self.rc.counts()["countered"], 1)
        self.assertEqual(self.rc.counts()["counter"], 0)

    def test_countered_punish_is_asymmetric(self):
        """被康惩罚只扣我方:对手被我打康时仍只 +0.08, 不额外获赏。"""
        prev = snapshot(_state(life1=10000, motion1=3200))
        cur = _state(life1=9500, motion1=50)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["combat"], 0.08, places=6)
        # 同一事件从对手视角看:它被康,惩罚落在它身上,我方不额外得利
        self.rc.reset_combat()
        prev = snapshot(_state(life1=10000, motion1=3200))
        self.rc.compute(prev, cur, team=1)
        self.assertEqual(self.rc.counts()["countered"], 1)

    def test_guard_read_at_240px_range(self):
        """中近距离立回架招引导: 在 240px 范围内对手出招且我方后退架招给 +0.03。"""
        hold_back = 2 * len(action_space.BUTTONS) + 0  # 4 none
        # 1. 距离 200px (在 240px 范围内) -> 获得 +0.03
        st_close = _state(x0=400.0, x1=600.0, motion1=3200)  # dist = 200px
        _, comp_close, _ = self.rc.compute(snapshot(st_close), st_close, team=0, action=hold_back)
        self.assertAlmostEqual(comp_close["guard"], 0.03, places=6)
        self.assertEqual(self.rc.counts()["guard_read"], 1)

        # 2. 距离 300px (超出 240px 且无弹幕) -> 0.0
        self.rc.reset_combat()
        st_far = _state(x0=300.0, x1=600.0, motion1=3200)  # dist = 300px
        _, comp_far, _ = self.rc.compute(snapshot(st_far), st_far, team=0, action=hold_back)
        self.assertAlmostEqual(comp_far["guard"], 0.0, places=6)
        self.assertEqual(self.rc.counts()["guard_read"], 0)


class TestConfigSynchronization(unittest.TestCase):
    """4. 配置文件同步测试。"""

    def test_env_yaml_matches_ticket08_spec(self):
        """configs/env.yaml 严格符合 Ticket 08 规范。"""
        env_cfg = load_env_config()
        rw = env_cfg["reward"]
        self.assertEqual(rw["guard_window"], 25)
        self.assertEqual(rw["w_guard_punish"], 0.15)
        self.assertEqual(rw["w_counter"], 0.08)
        self.assertEqual(rw["w_countered_punish"], 0.16)
        self.assertEqual(rw["w_guard_read"], 0.03)
        self.assertEqual(rw["guard_read_range"], 240.0)

    def test_coldstart_yaml_matches_ticket08_spec(self):
        """configs/train_ppo_com3_60hz_coldstart.yaml 同步包含相应 reward 字段。"""
        cfg_path = ROOT / "configs" / "train_ppo_com3_60hz_coldstart.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.assertIn("reward", cfg)
        rw = cfg["reward"]
        self.assertEqual(rw["guard_window"], 25)
        self.assertEqual(rw["w_guard_punish"], 0.15)
        self.assertEqual(rw["w_counter"], 0.08)
        self.assertEqual(rw["w_guard_read"], 0.03)
        self.assertEqual(rw["guard_read_range"], 240.0)

    def test_build_env_applies_reward_overrides(self):
        """build_env 会将训练配置中的 reward 覆盖注入到 env.reward_calc.C 中。"""
        cfg = {
            "env": {
                "team": 0,
                "action_repeat": 1,
                "max_episode_steps": 100,
                "difficulty": 2,
            },
            "reward": {
                "w_guard_punish": 0.20,
                "guard_window": 30,
            }
        }
        env = build_env(cfg)
        self.assertEqual(env.reward_calc.C["w_guard_punish"], 0.20)
        self.assertEqual(env.reward_calc.C["guard_window"], 30)


if __name__ == "__main__":
    unittest.main()
