"""无头单元测试:观测镜像不变性 / 动作掩码 / 奖励计算 / 环境步进。

不需要游戏进程,也不需要共享内存(用 mock 状态字典)。

    python -m unittest discover -s tests -v
    (装了 pytest 也可以: pytest tests/ -v)
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from env import action_space                                   # noqa: E402
from env.obs_builder import (GLOBAL_DIM, MAX_PROJ, OPP_DIM,    # noqa: E402
                            PROJ_DIMS, SELF_DIM, ObsBuilder, flatten)
from env.reward import RewardCalculator, snapshot              # noqa: E402
from env.th155_env import Th155Env, load_env_config            # noqa: E402


# ---------------------------------------------------------------------------
# mock 状态
# ---------------------------------------------------------------------------
def actor(x, y=360.0, vx=0.0, vy=0.0, direction=1.0, motion=0, key_frame=0,
          hit_stop=0, damage_stop=0, invin=0, baria=0, ground=1, hit_state=0,
          guard_state=0):
    return {
        "x": x, "y": y, "vx": vx, "vy": vy, "dir": direction,
        "motion": motion, "key_take": 0, "key_frame": key_frame,
        "hit_stop": hit_stop, "damage_stop": damage_stop,
        "invin": invin, "baria": baria, "ground": ground, "hit_state": hit_state,
        "guard_state": guard_state,
    }


def team(index, master, slave=None, life=10000, regain=0, sp=800, sp_max=800,
         mp=1000, op=0, current=0, combo_count=0, tag_ready=True, spell_ready=False,
         spell_active=False, name="reimu", slave_ban=None, **kwargs):
    t = {
        "index": index,
        "life": life, "life_max": 10000, "regain": regain,
        "sp": sp, "sp_max": sp_max, "mp": mp, "op": op,
        "occult": op, "occult_max": 2000,
        "spell_active": spell_active, "spell_use_count": 1,
        "combo_count": combo_count, "slave_ban": (0 if tag_ready else 60) if slave_ban is None else slave_ban,
        "com_difficulty": 1, "com_level": 33,
        "current": current, "tag_ready": tag_ready, "spell_ready": spell_ready,
        "master_name": name, "slave_name": "marisa",
        "master": master, "slave": slave or actor(640.0),
    }
    t.update(kwargs)
    return t


def state(t0, t1, shots=None, time=8910, battle_state=8, winner=0, round_=1, frame=0):
    return {
        "frame": frame, "battle_state": battle_state, "time": time, "time_unit": 90,
        "round": round_, "winner": winner, "match_num": 2, "is_practice": False,
        "teams": [t0, t1], "shots": shots or [],
    }


def mirror_state(st):
    """把整帧状态沿场地中心左右镜像(x -> 1280 - x, vx/dir 取反)。"""
    out = copy.deepcopy(st)
    for t in out["teams"]:
        for key in ("master", "slave"):
            a = t[key]
            a["x"] = 1280.0 - a["x"]
            a["vx"] = -a["vx"]
            a["dir"] = -a["dir"]
    for sh in out["shots"]:
        sh["x"] = 1280.0 - sh["x"]
        sh["vx"] = -sh["vx"]
        sh["dir"] = -sh["dir"]
    return out


# ---------------------------------------------------------------------------
class TestMirrorInvariance(unittest.TestCase):
    def setUp(self):
        self.b = ObsBuilder(load_env_config()["limits"])

    def _state(self):
        t0 = team(0, actor(400.0, 360.0, vx=3.0, direction=1.0, motion=211, key_frame=10),
                  life=8000, regain=1200, sp=400, op=500)
        t1 = team(1, actor(880.0, 300.0, vx=-2.0, direction=-1.0, motion=100, key_frame=4),
                  life=6000, regain=800, sp=200, op=1500, tag_ready=False)
        shots = [
            {"x": 600.0, "y": 340.0, "vx": -4.0, "vy": 0.0, "dir": -1.0,
             "id": 1, "motion": 5, "owner": 1},
            {"x": 500.0, "y": 380.0, "vx": 6.0, "vy": 1.0, "dir": 1.0,
             "id": 2, "motion": 6, "owner": 0},
        ]
        return state(t0, t1, shots)

    def test_mirror_observation_identical(self):
        st = self._state()
        a = self.b.build(st, controlled_team=0, prev_action=action_space.N_ACTIONS - 2)
        b = self.b.build(mirror_state(st), controlled_team=0,
                         prev_action=action_space.N_ACTIONS - 2)
        np.testing.assert_allclose(a["self"], b["self"], atol=1e-5,
                                   err_msg="自身特征在镜像后应完全一致")
        np.testing.assert_allclose(a["opp"], b["opp"], atol=1e-5,
                                   err_msg="对手特征在镜像后应完全一致")
        np.testing.assert_allclose(a["global"], b["global"], atol=1e-5)
        np.testing.assert_allclose(a["proj"], b["proj"], atol=1e-5,
                                   err_msg="弹幕相对特征在镜像后应完全一致")
        np.testing.assert_array_equal(a["proj_mask"], b["proj_mask"])
        self.assertEqual(a["motion_self"], b["motion_self"])

    def test_forward_is_towards_opponent(self):
        # 越过场地中心、朝对手一侧 -> pos_x_center 为正
        st = state(team(0, actor(700.0)), team(1, actor(900.0)))
        obs = self.b.build(st, controlled_team=0)
        self.assertGreater(obs["self"][2], 0.0)
        # 在自己半场(场地中心左侧,对手在右) -> 为负
        st2 = state(team(0, actor(400.0)), team(1, actor(880.0)))
        obs2 = self.b.build(st2, controlled_team=0)
        self.assertLess(obs2["self"][2], 0.0)
        # 弹幕 rel_x 以"朝对手为正"(对手在右、弹幕在角色右侧 -> 正)
        st3 = state(team(0, actor(400.0)), team(1, actor(880.0)),
                    shots=[{"x": 600.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
                             "id": 1, "motion": 1, "owner": 1}])
        obs3 = self.b.build(st3, controlled_team=0)
        self.assertGreater(obs3["proj"][0][0], 0.0)

    def test_active_slave_actor_used_when_current_is_1(self):
        # 当 current=1(Slave 在场)时,观测应取 slave 的坐标与 motion_id
        t0 = team(0, master=actor(300.0, motion=10), slave=actor(750.0, motion=305), current=1)
        t1 = team(1, master=actor(900.0, motion=20))
        obs = self.b.build(state(t0, t1), controlled_team=0)
        self.assertEqual(obs["motion_self"], 305)
        self.assertGreater(obs["self"][2], 0.0)   # 750 > 640(中心),朝向 900 -> 正

    def test_shapes_and_mask(self):
        obs = self.b.build(self._state(), controlled_team=0)
        self.assertEqual(obs["self"].shape, (SELF_DIM,))
        self.assertEqual(obs["opp"].shape, (OPP_DIM,))
        self.assertEqual(obs["global"].shape, (GLOBAL_DIM,))
        self.assertEqual(obs["proj"].shape, (MAX_PROJ, PROJ_DIMS))
        self.assertEqual(len(obs["action_mask"]), 56)
        self.assertEqual(flatten(obs).dtype, np.float32)


class TestActionMask(unittest.TestCase):
    def _mask(self, **kw):
        return action_space.compute_mask(team(0, actor(400.0), **kw))

    def test_no_sp_blocks_shots(self):
        m = self._mask(sp=0)
        for name in ("B", "C", "Occult"):
            b = action_space.BUTTONS.index(name)
            for d in range(len(action_space.DIRECTIONS)):
                self.assertFalse(m[d * len(action_space.BUTTONS) + b], f"{name} 在灵力为 0 时应被屏蔽")
        # A / D / 中立仍然可用
        self.assertTrue(m[0])
        self.assertTrue(m[action_space.BUTTONS.index("A")])

    def test_tag_removed_from_action_space(self):
        self.assertNotIn("Tag", action_space.BUTTONS)

    def test_occult_needs_gauge(self):
        self.assertFalse(self._mask(op=1999)[action_space.BUTTONS.index("Occult")])
        self.assertTrue(self._mask(op=2000)[action_space.BUTTONS.index("Occult")])

    def test_spell_needs_ready(self):
        self.assertFalse(self._mask(spell_ready=False)[action_space.SPELL_ACTION])
        self.assertTrue(self._mask(spell_ready=True, sp=800)[action_space.SPELL_ACTION])

    def test_tag_needs_ready_and_regain_gap(self):
        self.assertFalse(self._mask(tag_ready=False)[action_space.TAG_ACTION])
        self.assertFalse(self._mask(tag_ready=True, slave_ban=10)[action_space.TAG_ACTION])
        self.assertFalse(self._mask(tag_ready=True, slave_ban=0, life=5000, regain=5500)[action_space.TAG_ACTION])
        self.assertTrue(self._mask(tag_ready=True, slave_ban=0, life=5000, regain=6500)[action_space.TAG_ACTION])

    def test_action_count(self):
        self.assertEqual(action_space.N_ACTIONS, 56)
        self.assertEqual(action_space.SPELL_ACTION, 54)
        self.assertEqual(action_space.TAG_ACTION, 55)


class TestActionDecode(unittest.TestCase):
    def test_forward_maps_to_opponent_side(self):
        act = 1 * len(action_space.BUTTONS) + 0                       # 方向"6 前",无按键
        self.assertEqual(action_space.to_input(act, +1)[0], 1)
        self.assertEqual(action_space.to_input(act, -1)[0], -1)

    def test_back_maps_away(self):
        act = 2 * len(action_space.BUTTONS) + 0                       # 方向"4 后"
        self.assertEqual(action_space.to_input(act, +1)[0], -1)
        self.assertEqual(action_space.to_input(act, -1)[0], 1)

    def test_button_bits(self):
        a = action_space.BUTTONS.index("A")
        self.assertEqual(action_space.to_input(a, +1)[2:], (1, 0, 0, 0, 0))
        o = action_space.BUTTONS.index("Occult")
        self.assertEqual(action_space.to_input(o, +1)[2:], (1, 1, 0, 0, 0))
        self.assertEqual(action_space.to_input(action_space.SPELL_ACTION, +1)[2:], (0, 1, 1, 0, 0))

    def test_vertical_sign(self):
        """实测:input.y=+1 使屏幕 y 减小(角色向上),见 docs §17.10。

        曾经这里断言"8 上 -> y=-1",把颠倒的映射固化成了"正确行为";
        实机测量(静止按 0.11s)证明 input.y=+1 才是向上。
        """
        up = 3 * len(action_space.BUTTONS)                          # "8 上"
        self.assertEqual(action_space.to_input(up, +1)[1], 1)
        down = 4 * len(action_space.BUTTONS)                        # "2 下"
        self.assertEqual(action_space.to_input(down, +1)[1], -1)


class TestReward(unittest.TestCase):
    def setUp(self):
        rw = dict(load_env_config()["reward"])
        rw["combat_scale"] = 1.0            # 立回项单测直接看单事件权重,不受整体缩放影响
        self.rc = RewardCalculator(rw)

    def _st(self, life0=10000, life1=10000, regain0=0, regain1=0, sp_max0=800, sp_max1=800,
            x0=400.0, x1=880.0, current0=0, current1=0, motion0=0, motion1=0,
            hit_state0=0, hit_state1=0, baria0=0, baria1=0, combo0=0, combo1=0,
            guard0=0, guard1=0, time=8910, battle_state=8, winner=0):
        a0 = actor(x0, motion=motion0, hit_state=hit_state0, baria=baria0, guard_state=guard0)
        a1 = actor(x1, motion=motion1, hit_state=hit_state1, baria=baria1, guard_state=guard1)
        return state(team(0, a0, life=life0, regain=regain0, sp_max=sp_max0,
                          current=current0, combo_count=combo0),
                     team(1, a1, life=life1, regain=regain1, sp_max=sp_max1,
                          current=current1, combo_count=combo1),
                     time=time, battle_state=battle_state, winner=winner)

    def test_damage_dealt(self):
        prev = snapshot(self._st())
        cur = self._st(life1=8000)
        total, comp, done = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["hp"], 2.0 * 2000 / 10000, places=6)
        self.assertFalse(done)

    def test_damage_taken_is_negative(self):
        prev = snapshot(self._st())
        cur = self._st(life0=9000)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["hp"], -2.0 * 1000 / 10000, places=6)

    def test_regain_recovery(self):
        prev = snapshot(self._st(regain0=1000))
        cur = self._st(regain0=3000)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["regain"], 0.5 * 2000 / 10000, places=6)

    def test_tag_recover_bonus_when_white_hp_high(self):
        # 积累 2000 白血差(life=6000, regain=8000)时触发换人(current 0->1),给予一次性换人回血奖励
        prev = snapshot(self._st(life0=6000, regain0=8000, current0=0))
        cur = self._st(life0=6000, regain0=8000, current0=1)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["regain"], 0.05 * (2000.0 / 10000.0), places=6)

    def test_guard_crush(self):
        prev = snapshot(self._st())
        cur = self._st(sp_max1=600)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["resource"], 0.15, places=6)
        prev2 = snapshot(self._st())
        cur2 = self._st(sp_max0=600)
        _, comp2, _ = self.rc.compute(prev2, cur2, team=0)
        self.assertAlmostEqual(comp2["resource"], -0.15, places=6)

    def test_counter_hit_and_whiff_punish_symmetric(self):
        # 打康判据:命中(实血下降)且对手上一帧 motion 落在 IsAttack 区间 [1000, 4999] (w_counter=0.08)
        self.rc.reset_combat()
        prev = snapshot(self._st(motion1=3200))
        cur = self._st(life1=9500, motion1=50)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["combat"], 0.08, places=6)

        # 对称部分 -0.08;另加非对称被康惩罚 w_countered_punish(0.16) -> -0.24
        self.rc.reset_combat()
        prev = snapshot(self._st(motion0=3100))
        cur = self._st(life0=9500, motion0=50)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["combat"], -0.08 - self.rc.C["w_countered_punish"], places=6)

        # 非攻击 motion(走路/跳跃)不算打康
        self.rc.reset_combat()
        prev = snapshot(self._st(motion1=200))
        cur = self._st(life1=9500, motion1=50)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["combat"], 0.0, places=6)

    def test_guard_punish_window(self):
        # 真格挡确反:连续 guard_min_run 次采样处于防御(真格挡),随后命中"出招中的对手" -> +0.08(打康)+0.15(确反)=0.23
        self.rc.reset_combat()
        g1 = self._st(guard0=1, motion1=3200)
        for _ in range(self.rc.C["guard_min_run"] - 1):          # 连续防御采样数不足 -> 不开窗
            self.rc.compute(snapshot(g1), g1, team=0)
        self.assertEqual(self.rc._guard_t[0], 0)
        prev = snapshot(g1)
        cur = self._st(life1=9400)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["combat"], 0.08 + 0.15, places=6)

        # 单帧假格挡(COM 每帧 rand() 重掷)不给确反
        self.rc.reset_combat()
        prev = snapshot(self._st(guard0=1, motion1=3200))
        cur = self._st(life1=9400)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["combat"], 0.08, places=6)

        # Ticket 08: 收招硬直确反 —— 对手出招结束后进入收招硬直/中立(motion=200), 仍判定确反(+0.15, 无打康)
        self.rc.reset_combat()
        g1 = self._st(guard0=1, motion1=3200)
        for _ in range(self.rc.C["guard_min_run"]):
            self.rc.compute(snapshot(g1), g1, team=0)
        self.assertGreater(self.rc._guard_t[0], 0)
        prev_recovery = snapshot(self._st(motion1=200))  # 对手处于收招硬直, motion 不在攻击区间
        cur_hit = self._st(life1=9200, motion1=50)
        _, comp_rec, _ = self.rc.compute(prev_recovery, cur_hit, team=0)
        self.assertAlmostEqual(comp_rec["combat"], 0.15, places=6)
        self.assertEqual(self.rc._guard_t[0], 0, "确反命中后计时器必须立即归零")

        # 窗口超时后不再给确反 (guard_window=25 步)
        self.rc.reset_combat()
        g1 = self._st(guard0=1, motion1=3200)
        for _ in range(self.rc.C["guard_min_run"]):
            self.rc.compute(snapshot(g1), g1, team=0)
        prev = snapshot(g1)
        cur = self._st(motion1=3200)             # 保持对手出招,只让防御窗口自然过期
        for _ in range(int(self.rc.C["guard_window"]) + 2):
            self.rc.compute(prev, cur, team=0)
            prev = snapshot(cur)
        cur = self._st(life1=9400, motion1=3200)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["combat"], 0.08, places=6)

    def test_guard_block_reward_and_anti_turtling_quota(self):
        # 格挡已移出零和的 combat 分量:这里是独立的 guard 分量,权重即实际生效值(不乘 combat_scale)。
        # 配额锁测试把单回合总上限调到 1.0,隔离"配额锁"与"总上限"两套机制(总上限另有专门用例)。
        from env.reward import RewardCalculator
        rc = RewardCalculator(dict(self.rc.C, guard_round_cap=1.0))

        # 1) 真正挡住攻击(motion 0 -> 120 上升沿)给 +0.05,且维持在 120 内不重复给分
        rc.reset_combat()
        s_free = self._st(motion0=0)
        s_block = self._st(motion0=120)
        _, comp1, _ = rc.compute(snapshot(s_free), s_block, team=0)
        self.assertAlmostEqual(comp1["guard"], 0.05, places=6)
        _, comp_hold, _ = rc.compute(snapshot(s_block), s_block, team=0)
        self.assertAlmostEqual(comp_hold["guard"], 0.0, places=6)

        # 2) 空开结界姿态(motion=118)或破防(129/130)或仅按后退(guard0=1)不给格挡分
        rc.reset_combat()
        _, comp_stance, _ = rc.compute(snapshot(s_free), self._st(motion0=118), team=0)
        self.assertAlmostEqual(comp_stance["guard"], 0.0, places=6)
        _, comp_break, _ = rc.compute(snapshot(s_free), self._st(motion0=129), team=0)
        self.assertAlmostEqual(comp_break["guard"], 0.0, places=6)

        # 3) 连续纯防配额锁(guard_block_quota=7):不进攻只死防最多领 quota 次,超额后严格为 0.0
        rc.reset_combat()
        quota = int(rc.C["guard_block_quota"])
        for i in range(quota):
            _, c, _ = rc.compute(snapshot(s_free), s_block, team=0)
            self.assertAlmostEqual(c["guard"], 0.05, places=6, msg=f"block #{i + 1}")
        for _ in range(10):
            _, c_locked, _ = rc.compute(snapshot(s_free), s_block, team=0)
            self.assertAlmostEqual(c_locked["guard"], 0.0, places=6,
                                   msg="超出连续纯防配额后必须为 0,防止只靠防御刷分")

        # 4) 一旦反击对敌方造成实血伤害,配额立即解锁;同时仍触发防后确反(+0.15)与打康(+0.08)
        _, c_counter, _ = rc.compute(
            snapshot(self._st(motion0=120, motion1=3200)),
            self._st(life1=9200, motion0=3100, motion1=200),
            team=0,
        )
        self.assertAlmostEqual(c_counter["combat"], 0.08 + 0.15, places=6)
        # 命中后配额已重置,再次格挡重新获得 +0.05
        _, c_unlocked, _ = rc.compute(snapshot(s_free), s_block, team=0)
        self.assertAlmostEqual(c_unlocked["guard"], 0.05, places=6)

        # 5) 全局防纯防获胜校验(用默认总上限):全场只死防 50 次拖到超时平局,整局总回报必须为负
        self.rc.reset_combat()
        total_return = 0.0
        for _ in range(50):
            r, _, _ = self.rc.compute(snapshot(s_free), s_block, team=0)
            total_return += r
        r_end, _, done = self.rc.compute(
            snapshot(s_block),
            self._st(battle_state=32, winner=0, time=0),
            team=0,
        )
        total_return += r_end
        self.assertTrue(done)
        self.assertLess(total_return, 0.0,
                        "死防到平局的总回报必须为负(防御总收益上限 < 平局惩罚)")

    def test_guard_block_weights_by_defense_type(self):
        # 三种防御各自权重:通常(120-123)=0.02 / 结界(110-113,119)=0.03 / 精准(100-103)=0.04
        from env.reward import guard_block_kind, is_guard_block_motion
        self.assertEqual(guard_block_kind(120), "normal")
        self.assertEqual(guard_block_kind(123), "normal")
        self.assertEqual(guard_block_kind(110), "baria")
        self.assertEqual(guard_block_kind(119), "baria")
        self.assertEqual(guard_block_kind(100), "just")
        self.assertEqual(guard_block_kind(103), "just")
        self.assertEqual(guard_block_kind(118), "")
        self.assertFalse(is_guard_block_motion(118))

        free = self._st(motion0=0)
        for mo, exp in ((120, 0.05), (121, 0.05), (110, 0.08), (113, 0.08),
                        (100, 0.12), (103, 0.12)):
            self.rc.reset_combat()
            _, comp, _ = self.rc.compute(snapshot(free), self._st(motion0=mo), team=0)
            self.assertAlmostEqual(comp["guard"], exp, places=6, msg=f"motion={mo}")

        # 三种防御共用同一配额锁:连续纯防仍只能领 3 次
        # (这里把单回合总上限调到 1.0,隔离"配额锁"与"总上限"两套机制各自的行为)
        from env.reward import RewardCalculator
        quota_rc = RewardCalculator(dict(self.rc.C, guard_round_cap=1.0, guard_block_quota=3))
        quota_rc.reset_combat()
        got = []
        for mo in (100, 110, 120, 110, 100):
            _, comp, _ = quota_rc.compute(snapshot(free), self._st(motion0=mo), team=0)
            got.append(round(comp["guard"], 6))
        self.assertEqual(got, [0.12, 0.08, 0.05, 0.0, 0.0])

    def test_combo_chain_rewards_each_extra_hit(self):
        # 连续命中链:第 2、3 击各 +0.01,封顶 combo_cap
        self.rc.reset_combat()
        life = 10000
        prev = snapshot(self._st())
        expected = [0.0, 0.01, 0.01]
        for i, exp in enumerate(expected):
            life -= 300
            cur = self._st(life1=life)
            _, comp, _ = self.rc.compute(prev, cur, team=0)
            self.assertAlmostEqual(comp["combat"], exp, places=6, msg=f"hit {i + 1}")
            prev = snapshot(cur)

        # 断连(超过 combo_gap 步无命中)后重新从 1 数起,不再给连段分
        self.rc.reset_combat()
        prev = snapshot(self._st(life1=9000))
        for _ in range(10):
            _, _, _ = self.rc.compute(prev, self._st(life1=9000), team=0)
        prev = snapshot(self._st(life1=9000))
        cur = self._st(life1=8700)
        _, comp, _ = self.rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["combat"], 0.0, places=6)

    def test_opp_combo_counts_opponent_chains(self):
        # 对手连续命中我方:第 2、3 击计 opp_combo,不计 combo;奖励仍只给对手侧(零和对称)
        self.rc.reset_combat()
        life = 10000
        prev = snapshot(self._st())
        for _ in range(3):
            life -= 300
            cur = self._st(life0=life)
            self.rc.compute(prev, cur, team=0)
            prev = snapshot(cur)
        counts = self.rc.counts()
        self.assertEqual(counts["opp_combo"], 2)
        self.assertEqual(counts["combo"], 0)

    def test_combat_scale_multiplies_component(self):
        rc = RewardCalculator({**load_env_config()["reward"], "combat_scale": 0.25})
        rc.reset_combat()
        prev = snapshot(self._st(motion1=3200))
        cur = self._st(life1=9500, motion1=50)
        _, comp, _ = rc.compute(prev, cur, team=0)
        self.assertAlmostEqual(comp["combat"], 0.08 * 0.25, places=6)

    def test_terminal_ko_win_scales_with_remaining_hp(self):
        prev = snapshot(self._st())
        # 满血(life0=10000)快胜 KO -> 2.5 + 0.5(时间) + 0.5(满血加成) = 3.5
        cur_full = self._st(life0=10000, life1=0, battle_state=32, winner=1)
        _, comp_full, done = self.rc.compute(prev, cur_full, team=0)
        self.assertTrue(done)
        self.assertAlmostEqual(comp_full["terminal"], 3.5, places=6)
        # 残血(life0=2000)险胜 KO -> 2.5 + 0.5 + 0.5 * 0.2 = 3.1 (显著低于满血胜,抑制收尾阶段瞎浪换血)
        cur_low = self._st(life0=2000, life1=0, battle_state=32, winner=1)
        _, comp_low, _ = self.rc.compute(prev, cur_low, team=0)
        self.assertAlmostEqual(comp_low["terminal"], 3.1, places=6)
        self.assertGreater(comp_full["terminal"], comp_low["terminal"])

    def test_terminal_ko_lose_scales_with_opponent_remaining_hp(self):
        prev = snapshot(self._st())
        # 被满血剃光头(life1=10000) -> -2.5 - 0.5 * 1.0 = -3.0
        cur_crushed = self._st(life0=0, life1=10000, battle_state=32, winner=2)
        _, comp_crushed, done = self.rc.compute(prev, cur_crushed, team=0)
        self.assertTrue(done)
        self.assertAlmostEqual(comp_crushed["terminal"], -3.0, places=6)
        # 惜败(死前把对手打到剩 1000 血) -> -2.5 - 0.5 * 0.1 = -2.55 (鼓励逆风拼死刮血)
        cur_close = self._st(life0=0, life1=1000, battle_state=32, winner=2)
        _, comp_close, _ = self.rc.compute(prev, cur_close, team=0)
        self.assertAlmostEqual(comp_close["terminal"], -2.55, places=6)
        self.assertGreater(comp_close["terminal"], comp_crushed["terminal"])

    def test_terminal_timeover_with_hp_margin(self):
        prev = snapshot(self._st())
        # 超时胜(双方同血或特殊判定):基础 +1.0 + 0.5 * (hp_self - hp_opp)
        cur_win = self._st(life0=8000, life1=4000, battle_state=32, winner=1, time=0)
        _, comp_win, _ = self.rc.compute(prev, cur_win, team=0)
        self.assertAlmostEqual(comp_win["terminal"], 1.0 + 0.5 * (0.8 - 0.4), places=6)
        # 超时负:基础 -2.5 + 0.5 * (hp_self - hp_opp) = -2.7
        cur_lose = self._st(life0=4000, life1=8000, battle_state=32, winner=2, time=0)
        _, comp_lose, _ = self.rc.compute(prev, cur_lose, team=0)
        self.assertAlmostEqual(comp_lose["terminal"], -2.5 + 0.5 * (0.4 - 0.8), places=6)
        # 平局惩罚(-1.5):必须明显重于"什么也没发生",否则防御塑形会诱导死防拖平局
        cur_draw = self._st(battle_state=32, winner=0, time=0)
        _, comp_draw, _ = self.rc.compute(prev, cur_draw, team=0)
        self.assertAlmostEqual(comp_draw["terminal"], -1.5, places=6)

    def test_kill_instinct_hp_threshold_boost(self):
        prev = snapshot(self._st(life1=2500))
        # 正常掉血(对手 HP > 2000): 2500 -> 2100 (400点伤害)
        cur_normal = self._st(life1=2100)
        _, comp_norm, _ = self.rc.compute(prev, cur_normal, team=0)
        # 2.0 * 400 / 10000 = 0.08
        self.assertAlmostEqual(comp_norm["hp"], 0.08, places=6)

        # 残局斩杀掉血(对手 HP <= 2000): 2000 -> 1600 (400点伤害)
        prev_kill = snapshot(self._st(life1=2000))
        cur_kill = self._st(life1=1600)
        _, comp_kill, _ = self.rc.compute(prev_kill, cur_kill, team=0)
        # 1.5x 加成: 2.0 * (400 * 1.5) / 10000 = 0.12
        self.assertAlmostEqual(comp_kill["hp"], 0.12, places=6)
        self.assertGreater(comp_kill["hp"], comp_norm["hp"])

    def test_approach_bonus_when_far_and_zero_sum_on_oscillation(self):
        s0 = self._st(x0=200.0, x1=1100.0)
        s1 = self._st(x0=260.0, x1=1100.0)
        _, comp_fwd, _ = self.rc.compute(snapshot(s0), s1, team=0)
        self.assertGreater(comp_fwd["approach"], 0.0)
        # 1 大步后退 + 5 小碎步前进回到原点:势函数总和必须精确为 0.0(杜绝碎步刷分)
        _, comp_back, _ = self.rc.compute(snapshot(s1), s0, team=0)
        total_cycle = comp_back["approach"]
        xs = np.linspace(200.0, 260.0, 6)
        for a, b in zip(xs[:-1], xs[1:]):
            _, c, _ = self.rc.compute(snapshot(self._st(x0=float(a), x1=1100.0)),
                                      self._st(x0=float(b), x1=1100.0), team=0)
            total_cycle += c["approach"]
        self.assertAlmostEqual(total_cycle, 0.0, places=7)

    def test_corner_pressure_symmetric_and_self_corner_penalty(self):
        """版边势函数对称(进 +0.002 / 出 -0.002,进出循环和为 0)且自身进版边受罚(-0.002)。"""
        far = self._st(x0=700.0, x1=700.0)          # 双方都在中间
        near = self._st(x0=700.0, x1=40.0)          # 对手贴左墙
        _, comp_in, _ = self.rc.compute(snapshot(far), near, team=0)
        self.assertAlmostEqual(comp_in["corner"], 0.002, places=6)
        # 连续两帧都在版边 -> 第二帧不再给分
        _, comp_stay, _ = self.rc.compute(snapshot(near), near, team=0)
        self.assertEqual(comp_stay["corner"], 0.0)
        # 离开版边 -> 对称扣回 -0.002,一进一出总和严格为 0.0(彻底堵死跨线刷分)
        _, comp_out, _ = self.rc.compute(snapshot(near), far, team=0)
        self.assertAlmostEqual(comp_out["corner"], -0.002, places=6)
        self.assertAlmostEqual(comp_in["corner"] + comp_out["corner"], 0.0, places=7)
        # 自身退入版边 -> 惩罚 -0.002
        self_corner = self._st(x0=40.0, x1=700.0)
        _, comp_self_in, _ = self.rc.compute(snapshot(far), self_corner, team=0)
        self.assertAlmostEqual(comp_self_in["corner"], -0.002, places=6)

    def test_guard_round_cap_bounds_total_income(self):
        """单回合防御姿态引导收益 <= guard_round_cap,且必须低于平局惩罚(否则死防拖平局变成正收益)。"""
        from env.reward import RewardCalculator
        from env import action_space
        hold_back = 2 * len(action_space.BUTTONS) + 0
        close = dict(x0=800.0, x1=880.0, motion1=3200)
        quota_rc = RewardCalculator(dict(self.rc.C, w_guard_read=0.03, guard_read_cap=100))
        quota_rc.reset_combat()
        total = 0.0
        for _ in range(40):
            _, c, _ = quota_rc.compute(snapshot(self._st(**close)), self._st(**close), team=0, action=hold_back)
            total += c["guard"]
        self.assertAlmostEqual(total, quota_rc.C["guard_round_cap"], places=6)
        self.assertLess(quota_rc.C["guard_round_cap"], -quota_rc.C["terminal_draw"],
                        "防御总收益上限必须严格小于平局惩罚的绝对值")

    def test_guard_read_dense_reward(self):
        """对手出招 + 距离足够近 + 按住后方向不出招 -> 每步给 w_guard_read。"""
        from env import action_space
        hold_back = 2 * len(action_space.BUTTONS) + 0          # 方向 2(后) + 按键 none
        attack_forward = 1 * len(action_space.BUTTONS) + 1     # 方向 6(前) + A
        close = dict(x0=800.0, x1=880.0, motion1=3200)         # 距离 80 < 320,对手出招

        self.rc.C["w_guard_read"] = 0.060
        self.rc.reset_combat()
        _, c, _ = self.rc.compute(snapshot(self._st(**close)), self._st(**close),
                                  team=0, action=hold_back)
        self.assertAlmostEqual(c["guard"], self.rc.C["w_guard_read"], places=6)

        # 出招不算防御姿态
        self.rc.reset_combat()
        _, c_atk, _ = self.rc.compute(snapshot(self._st(**close)), self._st(**close),
                                      team=0, action=attack_forward)
        self.assertAlmostEqual(c_atk["guard"], 0.0, places=6)

        # 距离过远不算(对手打不到我)
        self.rc.reset_combat()
        far = dict(x0=200.0, x1=880.0, motion1=3200)
        _, c_far, _ = self.rc.compute(snapshot(self._st(**far)), self._st(**far),
                                      team=0, action=hold_back)
        self.assertAlmostEqual(c_far["guard"], 0.0, places=6)

        # 对手没在出招不算
        self.rc.reset_combat()
        idle = dict(x0=800.0, x1=880.0, motion1=211)
        _, c_idle, _ = self.rc.compute(snapshot(self._st(**idle)), self._st(**idle),
                                       team=0, action=hold_back)
        self.assertAlmostEqual(c_idle["guard"], 0.0, places=6)

    def test_guard_read_is_capped_per_round(self):
        """单回合防御姿态引导计分步数受 guard_read_cap 封顶。"""
        from env import action_space
        from env.reward import RewardCalculator
        # 隔离回合总分上限(设大 guard_round_cap), 专项验证步数上限 guard_read_cap 封顶
        cap_rc = RewardCalculator(dict(self.rc.C, w_guard_read=0.060, guard_round_cap=10.0))
        hold_back = 2 * len(action_space.BUTTONS)
        close = dict(x0=800.0, x1=880.0, motion1=3200)
        cap_rc.reset_combat()
        n = 0
        for _ in range(cap_rc.C["guard_read_cap"] + 20):
            _, c, _ = cap_rc.compute(snapshot(self._st(**close)), self._st(**close),
                                      team=0, action=hold_back)
            if c["guard"] > 0:
                n += 1
        self.assertLessEqual(n, cap_rc.C["guard_read_cap"])
        self.assertEqual(cap_rc.counts()["guard_read"], n)


class TestEnvLoop(unittest.TestCase):
    """用 mock I/O 跑完整的 reset/step 循环。"""

    def setUp(self):
        self.frames = {"n": 0}
        self.actions = []
        self.st = state(team(0, actor(400.0)), team(1, actor(880.0)))
        self.cfg = load_env_config()

    def _state_fn(self):
        self.st["frame"] = self.frames["n"]
        return self.st

    def _action_fn(self, v):
        self.actions.append(v)
        self.frames["n"] += self.cfg["action"]["action_repeat"]

    def _reset_fn(self, timeout):
        self.st["battle_state"] = 8
        self.st["time"] = 8910
        return True

    def _env(self):
        return Th155Env(config=self.cfg, state_fn=self._state_fn,
                        action_fn=self._action_fn, reset_fn=self._reset_fn)

    def test_reset_returns_obs_and_mask(self):
        env = self._env()
        obs, info = env.reset()
        self.assertIn("action_mask", info)
        self.assertEqual(info["action_mask"].shape, (56,))
        self.assertEqual(obs["self"].shape, (SELF_DIM,))

    def test_step_advances_frames_and_writes_input(self):
        env = self._env()
        env.reset()
        f0 = self.st["frame"]
        obs, rew, done, trunc, info = env.step(1 * len(action_space.BUTTONS))     # "6 前"
        self.assertEqual(self.st["frame"] - f0, self.cfg["action"]["action_repeat"])
        self.assertEqual(len(self.actions), 1)
        self.assertEqual(self.actions[0][0], 1)           # 对手在右 -> x=+1
        self.assertFalse(done)
        self.assertIn("reward_components", info)

    def test_step_terminates_on_round_end(self):
        env = self._env()
        env.reset()
        self.st["battle_state"] = 32
        self.st["winner"] = 1
        self.st["teams"][1]["life"] = 0
        _, rew, done, trunc, info = env.step(0)
        self.assertTrue(done)
        self.assertFalse(trunc)
        self.assertGreater(info["reward_components"]["terminal"], 0.0)

    def test_mask_flags_illegal_action(self):
        env = self._env()
        env.reset()
        self.st["teams"][0]["sp"] = 0
        self.st["teams"][0]["occult"] = 0
        illegal_act = action_space.BUTTONS.index("B")     # 灵力为 0 时的射撃
        _, _, _, _, info = env.step(illegal_act)
        self.assertTrue(info["illegal_action"])


# ---------------------------------------------------------------------------
# 未挂接(attached)语义
# ---------------------------------------------------------------------------
class TestUnattachedIsNotAStall(unittest.TestCase):
    """桥接未挂接时必须安静,不能当作"画面停住"。

    背景:DLL 以前挂接共享内存时只重置 magic/version 等少数字段,
    frame/battle_state 继承上一局的残留值。游戏退回标题菜单后 Python
    仍读到 "frame=61822 state=8",于是判定画面停住并重启游戏,
    形成"重启 -> 回菜单 -> 帧不动 -> 再重启"的死循环。
    现在 DLL 在未解析到战斗对象时发布 attached=0 + frame=0,
    环境侧必须据此视为"不在战斗中"。
    """

    def _env(self):
        env = Th155Env.__new__(Th155Env)      # 只测 _wait_frames,不跑 __init__
        env.action_repeat = 4
        env.frame_timeout = 0.02
        env._shm = None
        env.stall_steps = 3
        env._stall_steps = 0
        return env

    def test_unattached_never_counts_as_stall(self):
        env = self._env()
        off = {"attached": 0, "frame": 0, "battle_state": 0, "time": 0}
        env._state = lambda: dict(off)
        for _ in range(10):
            st = env._wait_frames(dict(off))
            self.assertEqual(st["attached"], 0)
        self.assertEqual(env._stall_steps, 0)

    def test_attached_but_frozen_still_raises(self):
        """反向保障:真挂上了却不推进,必须照旧报错,否则崩溃会被漏检。"""
        env = self._env()
        stuck = {"attached": 1, "frame": 500, "battle_state": 8, "time": 100}
        env._state = lambda: dict(stuck)
        with self.assertRaises(RuntimeError) as cm:
            for _ in range(env.stall_steps):
                env._wait_frames(dict(stuck))
        self.assertIn("画面停住", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
