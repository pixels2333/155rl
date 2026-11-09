"""单元测试:验证 P0 级修复(GAE 终局无泄漏、BPTT 局边界不跨越、Tag 刷分封顶、Motion 归一化)。"""
from __future__ import annotations

import unittest
import numpy as np
import torch

from env.obs_builder import ObsBuilder, SELF_DIM
from env.reward import RewardCalculator, snapshot
from train.train_ppo import Rollout, compute_gae, _seq_batches


class TestP0Fixes(unittest.TestCase):
    def test_gae_no_leak_at_episode_boundary(self):
        """验证:当 step t 是终局(dones[t]=True)时,不向后 bootstrap 下一局的初始价值。"""
        # 构造两局各 2 步的 rollout: t=0,1 (第一局, t=1 终结赢+2.0); t=2,3 (第二局, t=3 终结输-1.0)
        rewards = np.array([0.0, 2.0, 0.0, -1.0], dtype=np.float32)
        values = np.array([0.5, 1.5, -0.2, -0.8], dtype=np.float32)
        dones = np.array([False, True, False, True], dtype=bool)
        
        roll = Rollout(
            rewards=rewards,
            values=values,
            dones=dones,
            last_value=0.0,
            last_done=True,
        )
        gamma = 0.99
        lam = 0.95
        adv, ret = compute_gae(roll, gamma, lam)
        
        # 对于 t=1(第一局终结步):
        # nonterminal 应严格为 0.0, delta_1 = r_1 - v_1 = 2.0 - 1.5 = 0.5
        # 且不应累加 t=2(第二局)的任何优势!
        expected_adv_1 = rewards[1] - values[1]
        self.assertAlmostEqual(adv[1], expected_adv_1, places=5)
        self.assertAlmostEqual(ret[1], rewards[1], places=5)
        
        # 对于 t=0(第一局第 0 步):
        # nonterminal 应为 1.0, bootstrap 第一局自身 t=1 的价值与优势
        expected_delta_0 = rewards[0] + gamma * values[1] - values[0]
        expected_adv_0 = expected_delta_0 + gamma * lam * adv[1]
        self.assertAlmostEqual(adv[0], expected_adv_0, places=5)

    def test_seq_batches_respects_dones(self):
        """验证:_seq_batches 在遇见 done=True 时截断当前 chunk,下一 chunk 从新一局开始。"""
        dones = np.array([False, False, True, False, False, True, False], dtype=bool)
        chunks = _seq_batches(len(dones), seq_len=4, dones=dones)
        
        # 步 0,1,2 属于第一局(dones[2]=True) -> chunk 0 必须是 (0, 3)
        self.assertEqual(chunks[0], (0, 3))
        # 步 3,4,5 属于第二局(dones[5]=True) -> chunk 1 必须是 (3, 6)
        self.assertEqual(chunks[1], (3, 6))
        # 步 6 属于第三局 -> chunk 2 必须是 (6, 7)
        self.assertEqual(chunks[2], (6, 7))

    def test_tag_recover_reward_capped(self):
        """验证:即使频繁换人,单回合 tag_recover 次数严格封顶(默认 4 次),无法无限刷分。"""
        rc = RewardCalculator({"tag_recover_cap": 2, "w_tag_recover": 0.1, "tag_recover_min_gap": 500.0})
        
        # 构造白血缺口 2000
        prev_st = {"teams": [{"life": 5000, "regain": 8000, "current": 0}, {"life": 8000, "regain": 8000, "current": 0}],
                   "battle_state": 8, "time": 5000}
        
        # 第 1 次切人 (0 -> 1)
        cur1 = {"teams": [{"life": 5000, "regain": 8000, "current": 1}, {"life": 8000, "regain": 8000, "current": 0}],
                "battle_state": 8, "time": 4900}
        _, comp1, _ = rc.compute(snapshot(prev_st), cur1, team=0)
        self.assertGreater(comp1["regain"], 0.0)
        
        # 第 2 次切人 (1 -> 0)
        cur2 = {"teams": [{"life": 5000, "regain": 8000, "current": 0}, {"life": 8000, "regain": 8000, "current": 0}],
                "battle_state": 8, "time": 4800}
        _, comp2, _ = rc.compute(snapshot(cur1), cur2, team=0)
        self.assertGreater(comp2["regain"], 0.0)
        
        # 第 3 次切人 (0 -> 1), 超出 tag_recover_cap=2, 不得再给换人分!
        cur3 = {"teams": [{"life": 5000, "regain": 8000, "current": 1}, {"life": 8000, "regain": 8000, "current": 0}],
                "battle_state": 8, "time": 4700}
        _, comp3, _ = rc.compute(snapshot(cur2), cur3, team=0)
        self.assertAlmostEqual(comp3["regain"], 0.0)

    def test_motion_norm_bounded(self):
        """验证:obs_builder 中 motion 经过归一化后落在 [0.0, 1.0],不再出现 3000+ 离群数值。"""
        ob = ObsBuilder({"motion_vocab": 4096})
        st = {
            "teams": [
                {"life": 10000, "regain": 10000, "master": {"x": 300, "y": 360, "motion": 3200, "dir": 1}},
                {"life": 10000, "regain": 10000, "master": {"x": 900, "y": 360, "motion": 4500, "dir": -1}},
            ],
            "battle_state": 8,
            "time": 8910,
        }
        res = ob.build(st, controlled_team=0)
        self.assertEqual(res["self"].shape[0], SELF_DIM)
        # 所有 self 特征必须在 [-2.0, 2.0] 范围内
        self.assertTrue((res["self"] >= -2.0).all())
        self.assertTrue((res["self"] <= 2.0).all())



    def test_dash_count_drives_invincible_not_invin(self):
        """dash 才是 th155 的真无敌(`invin` 恒 0,见 docs §17.8)。

        回归点:如果哪天有人把 `invincible` 维度改回读 `invin`,
        这两个断言会立刻失败 —— 因为 `invin` 在实际游戏里永远是 0。
        """
        ob = ObsBuilder()
        base = {
            "teams": [
                {"life": 10000, "regain": 10000,
                 "master": {"x": 300, "y": 360, "motion": 0, "dir": 1}},
                {"life": 10000, "regain": 10000,
                 "master": {"x": 900, "y": 360, "motion": 0, "dir": -1}},
            ],
            "battle_state": 8, "time": 5000,
        }
        idle = ob.build(base, controlled_team=0)

        # dash_count > 0 -> 观测必须变化
        dash_st = {
            "teams": [
                {"life": 10000, "regain": 10000,
                 "master": {"x": 300, "y": 360, "motion": 0, "dir": 1, "dash_count": 1}},
                {"life": 10000, "regain": 10000,
                 "master": {"x": 900, "y": 360, "motion": 0, "dir": -1}},
            ],
            "battle_state": 8, "time": 5000,
        }
        dashing = ob.build(dash_st, controlled_team=0)
        self.assertFalse(np.array_equal(idle["self"], dashing["self"]),
                         "dash_count 变化没有反映到观测里")

        # invin 变化 -> 观测必须**完全不变**(它不参与特征)
        invin_st = {
            "teams": [
                {"life": 10000, "regain": 10000,
                 "master": {"x": 300, "y": 360, "motion": 0, "dir": 1, "invin": 99}},
                {"life": 10000, "regain": 10000,
                 "master": {"x": 900, "y": 360, "motion": 0, "dir": -1}},
            ],
            "battle_state": 8, "time": 5000,
        }
        with_invin = ob.build(invin_st, controlled_team=0)
        self.assertTrue(np.array_equal(idle["self"], with_invin["self"]),
                        "invin 是死字段,不应对观测产生任何影响")



    def test_status_flags_use_verified_signals(self):
        """状态标志必须用实测验证过的信号,不能用被证伪的 `hit_state`。

        实测(docs §17.10):
          * `hit_state` 只有 {0,3,7},bit0&bit1 恒同时置位 => 实际 2 态;
          * 3 个完整掉血事件窗口里它逐帧不变,lift 仅 1.08x —— 不是受击枚举;
          * 真正的受击硬直是 `damageStopTime`(0->10 递减),lift 4.30x;
          * 被打飞(motion 213)的帧 91.3% 离地,用 y 偏离 center_y 判定 down。
        """
        ob = ObsBuilder()

        def build(**actor_kw):
            actor = {"x": 300, "y": 360, "motion": 0, "dir": 1}
            actor.update(actor_kw)
            st = {
                "teams": [
                    {"life": 10000, "regain": 10000, "master": actor},
                    {"life": 10000, "regain": 10000,
                     "master": {"x": 900, "y": 360, "motion": 0, "dir": -1}},
                ],
                "battle_state": 8, "time": 5000,
            }
            return ob.build(st, controlled_team=0)["self"]

        # flags 从 16 开始:free, attacking, guard, hitstun, down, invincible
        FREE, ATK, GUARD, STUN, DOWN, INVIN = 16, 17, 18, 19, 20, 21

        idle = build()
        self.assertEqual(idle[FREE], 1.0)
        self.assertEqual(idle[STUN], 0.0)
        self.assertEqual(idle[DOWN], 0.0)

        # 受击硬直:damage_stop > 0
        stun = build(damage_stop=8)
        self.assertEqual(stun[STUN], 1.0, "damage_stop>0 必须点亮 hitstun")
        self.assertEqual(stun[FREE], 0.0, "受击中不可能是 free")
        self.assertEqual(stun[DOWN], 0.0, "地面上受击不算 down")

        # 被打飞:受击硬直 + 离地
        launched = build(damage_stop=8, y=285)
        self.assertEqual(launched[DOWN], 1.0, "受击且离地必须点亮 down")

        # hit_state 是死字段,不应影响任何标志
        for hs in (0, 3, 7):
            with_hs = build(hit_state=hs)
            self.assertTrue(np.array_equal(idle, with_hs),
                            f"hit_state={hs} 不应影响观测(它是被证伪的字段)")

        # guard_state 才是有效防御标志(baria 恒 False)
        self.assertEqual(build(guard_state=1)[GUARD], 1.0)

        # attacking 必须用 IsAttack() 区间 [1000,4999](player_check.nut:82):
        # 走路(200-213)/待机(0-2)/跳跃的 motion 都 < 1000,绝不能被当成出招。
        for walk_motion in (0, 1, 2, 36, 37, 200, 201, 211, 212, 213):
            self.assertEqual(build(motion=walk_motion)[ATK], 0.0,
                             f"motion={walk_motion} 不是攻击,不应点亮 attacking")
        # 掉血帧实测的攻击方 motion。1220/1500/1600/1720 这些近身招
        # 在旧的 [3000,4999] 口径下被漏判,是"对手没出招却掉血"的成因。
        for atk_motion in (1000, 1220, 1500, 1600, 1720, 2000, 3050, 3910, 4999):
            self.assertEqual(build(motion=atk_motion)[ATK], 1.0,
                             f"motion={atk_motion} 是攻击动作,应点亮 attacking")
        # 受击中即使 motion 在攻击区间也不算"正在出招"
        self.assertEqual(build(motion=3050, damage_stop=8)[ATK], 0.0)



    def test_action_space_y_axis_matches_game_input(self):
        """y 轴符号必须与游戏 input 一致(实测,docs §17.10)。

        实测:input.y=+1 让**屏幕 y 减小**(角色向上),input.y=-1 向下。
        Actor2D.y 是屏幕坐标(向下为正),两者符号相反。
        曾经 DIRECTIONS 把"8 上"映射成 (0,-1),导致所有上下方向反了。
        """
        from env import action_space as a

        # 方向名 -> 期望的 input.y 符号
        expect = {"8": +1, "2": -1, "9": +1, "7": +1, "3": -1, "1": -1,
                  "5": 0, "6": 0, "4": 0}
        for idx, name in enumerate(a.DIR_NAMES):
            dy = a.DIRECTIONS[idx][1]
            self.assertEqual(dy, expect[name],
                             f"方向 {name} 的 input.y 应为 {expect[name]:+d},实际 {dy:+d}")

        # 通过公开 API 复核:中立方向下,各方向的 (x,y)
        for idx, name in enumerate(a.DIR_NAMES):
            act = idx * len(a.BUTTONS)          # 该方向的 none 按键动作
            x, y, *_ = a.to_input(act, facing_sign=1)
            self.assertEqual(y, expect[name], f"to_input('{name}') 的 y 不对")


if __name__ == "__main__":
    unittest.main()
