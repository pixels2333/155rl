"""单元测试: 远程弹幕/激光防御威胁判定与擦弹奖励。

验证目标:
1. check_proj_threat 几何算法(敌方弹幕接近判定、己方弹幕过滤、光束/激光垂线投影)。
2. 即使敌方本体在 800px 远处, 当敌方弹幕/激光临近时, 按住后防御能正确获得 w_guard_read。
3. 实际格挡成功(is_guard_block_motion)在任何距离(如 1000px)都能获得有效格挡奖励(w_guard_block)。
4. graze_state == 2 时能正确获得擦弹闪避奖励 w_graze, 并受 graze_cap 限制。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from env import action_space
from env.reward import (
    RewardCalculator,
    check_proj_threat,
    is_guard_block_motion,
    snapshot,
)


def make_actor(x=300.0, y=360.0, motion=0, graze_state=0, box_l=None, box_t=None, box_r=None, box_b=None):
    return {
        "x": x, "y": y, "vx": 0.0, "vy": 0.0, "dir": 1.0,
        "motion": motion, "key_take": 0, "key_frame": 0,
        "hit_stop": 0, "damage_stop": 0, "invin": 0, "baria": 0, "ground": 1,
        "hit_state": 0, "guard_state": 0, "graze_state": graze_state,
        "dash_count": 0,
        "box_l": box_l if box_l is not None else x - 20.0,
        "box_t": box_t if box_t is not None else y - 60.0,
        "box_r": box_r if box_r is not None else x + 20.0,
        "box_b": box_b if box_b is not None else y + 50.0,
    }


def make_team(index, actor_obj, life=10000, regain=0, sp_max=800):
    return {
        "index": index, "life": life, "life_max": 10000, "regain": regain,
        "sp": sp_max, "sp_max": sp_max, "mp": 1000, "op": 0,
        "current": 0, "master": actor_obj, "slave": actor_obj,
    }


def make_state(team0, team1, shots=None):
    return {
        "frame": 100, "battle_state": 8, "time": 8000,
        "winner": 0, "teams": [team0, team1], "shots": shots or [],
    }


class TestRewardThreatAndGraze(unittest.TestCase):
    def setUp(self):
        self.rc = RewardCalculator({"w_guard_read": 0.060})
        self.rc.reset_combat()

    def test_check_proj_threat_aabb_distance(self):
        """测试弹幕 AABB 欧几里得距离判定。"""
        self_actor = make_actor(x=300.0, y=360.0)  # box: [280, 300, 320, 410]
        opp_team = 1

        # 1. 敌方弹幕在距离 100px 处 (在 180px 威胁阈值内)
        sh_close = [{"owner": 1, "x": 420.0, "y": 360.0, "box_l": 410.0, "box_t": 350.0, "box_r": 430.0, "box_b": 370.0}]
        self.assertTrue(check_proj_threat(sh_close, self_actor, opp_team, threat_range=180.0))

        # 2. 敌方弹幕在距离 300px 处 (超出 180px 威胁阈值)
        sh_far = [{"owner": 1, "x": 700.0, "y": 360.0, "box_l": 690.0, "box_t": 350.0, "box_r": 710.0, "box_b": 370.0}]
        self.assertFalse(check_proj_threat(sh_far, self_actor, opp_team, threat_range=180.0))

        # 3. 己方弹幕即使贴脸也不应被判定为威胁
        sh_own = [{"owner": 0, "x": 325.0, "y": 360.0, "box_l": 320.0, "box_t": 350.0, "box_r": 330.0, "box_b": 370.0}]
        self.assertFalse(check_proj_threat(sh_own, self_actor, opp_team, threat_range=180.0))

    def test_check_proj_threat_beam_laser(self):
        """测试持续光束/激光的垂线威胁度判定。"""
        self_actor = make_actor(x=500.0, y=380.0)  # y 中心约 355
        opp_team = 1

        # 敌方在 x=100 发射水平贯穿激光 (从 x=100 到 x=1000, 穿过 y=360)
        laser = [{
            "owner": 1,
            "is_beam": 1,
            "x": 100.0, "y": 360.0,
            "box_l": 100.0, "box_t": 350.0, "box_r": 1000.0, "box_b": 370.0,
            "beam_w": 20.0,
            "beam_l": 900.0,
            "beam_angle": 0.0,  # 沿正 x 方向
        }]
        # 我方在 (500, 380), 垂线距离约为 20px, 远小于 180px
        self.assertTrue(check_proj_threat(laser, self_actor, opp_team, threat_range=180.0))

        # 若激光发射在 y=800, 垂直距离 440px 超过 180px
        laser_away = [{
            "owner": 1,
            "is_beam": 1,
            "x": 100.0, "y": 800.0,
            "box_l": 100.0, "box_t": 790.0, "box_r": 1000.0, "box_b": 810.0,
            "beam_w": 20.0,
            "beam_l": 900.0,
            "beam_angle": 0.0,
        }]
        self.assertFalse(check_proj_threat(laser_away, self_actor, opp_team, threat_range=180.0))

    def test_guard_read_triggered_by_distant_projectile(self):
        """验证: 敌方本体在 800px 远处, 但弹幕逼近时, 按后防御能触发 w_guard_read。"""
        hold_back = 2 * len(action_space.BUTTONS) + 0  # 方向 2(后) + 空按

        # 对手本体在 x=1000 (距离我方 800px, 远大于近身阈值 160px)
        a0 = make_actor(x=200.0, y=360.0)
        a1 = make_actor(x=1000.0, y=360.0, motion=211)  # 对手没有近身出招
        t0 = make_team(0, a0)
        t1 = make_team(1, a1)

        # 1. 没有弹幕时, 距离过远不给分
        st_no_proj = make_state(t0, t1, shots=[])
        _, comp1, _ = self.rc.compute(snapshot(st_no_proj), st_no_proj, team=0, action=hold_back)
        self.assertAlmostEqual(comp1["guard"], 0.0, places=6)

        # 2. 敌方弹幕飞临我方身前 100px (x=330)
        incoming_shot = [{"owner": 1, "x": 330.0, "y": 360.0, "box_l": 320.0, "box_t": 350.0, "box_r": 340.0, "box_b": 370.0}]
        st_with_proj = make_state(t0, t1, shots=incoming_shot)

        self.rc.reset_combat()
        _, comp2, _ = self.rc.compute(snapshot(st_with_proj), st_with_proj, team=0, action=hold_back)
        self.assertAlmostEqual(comp2["guard"], self.rc.C["w_guard_read"], places=6)
        self.assertEqual(self.rc.counts()["guard_read"], 1)

    def test_guard_block_has_no_distance_limit(self):
        """验证: 真正格挡成功(进入格挡动作)没有任何像素限制, 1000px 外同样给分。"""
        prev_a0 = make_actor(x=100.0, motion=0)
        cur_a0 = make_actor(x=100.0, motion=120)  # 通常防御动作 Guard_Init
        a1 = make_actor(x=1100.0, motion=0)       # 对手在 1000px 外

        prev_st = make_state(make_team(0, prev_a0), make_team(1, a1))
        cur_st = make_state(make_team(0, cur_a0), make_team(1, a1))

        self.rc.reset_combat()
        _, comp, _ = self.rc.compute(snapshot(prev_st), cur_st, team=0)
        self.assertAlmostEqual(comp["guard"], self.rc.C["w_guard_block"], places=6)
        self.assertEqual(self.rc.counts()["guard_block"], 1)

    def test_graze_reward_and_cap(self):
        """验证: graze_state == 2 时奖励 w_graze, 达到上限后封顶。"""
        self.rc.reset_combat()
        a0 = make_actor(x=300.0, graze_state=2)
        a1 = make_actor(x=800.0)
        st = make_state(make_team(0, a0), make_team(1, a1))

        # 连续擦弹
        w_graze = float(self.rc.C["w_graze"])
        cap = int(self.rc.C["graze_cap"])
        for i in range(cap):
            _, comp, _ = self.rc.compute(snapshot(st), st, team=0)
            self.assertAlmostEqual(comp["graze"], w_graze, places=6)

        self.assertEqual(self.rc.counts()["graze"], cap)

        # 超过上限后不再奖励
        _, comp_capped, _ = self.rc.compute(snapshot(st), st, team=0)
        self.assertAlmostEqual(comp_capped["graze"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main()
