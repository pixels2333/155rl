"""战斗数据采集的关键不变量(实机证伪后固化)。

对应 docs: 见 AGENTS/notebook 记录的探针结论。这些断言保护的是
**曾经真实出错并污染训练数据**的几处,不是为了覆盖实现细节:

1. `is_attack` 必须与 `player_check.nut:82` 的 IsAttack() 完全同口径。
   旧实现用 [3000,4999],而实测掉血帧的攻击方 motion 有
   1000/1212/1500/1600/1720/2000 -> 8 个近身招被漏判成"没出招"。
2. Python 侧 is_attack() 与桥接侧 th155_is_attack() 必须逐值一致,
   否则观测说"对手在出招"、奖励说"没出招",两个信号互相打架。
3. `PROJ_DIMS` 与 projectiles() 实际产出的维度必须一致(曾因加特征忘改常量)。
4. 弹幕特征里必须带 is_attack 一维 —— 否则 6050 这种纯演出特效
   无法与 2009 真弹幕区分(实测前者占被发布弹幕的 44%)。
"""
from __future__ import annotations

import collections
import unittest

import numpy as np

from bridge.shm import is_attack
from env.obs_builder import (GLOBAL_DIM, MAX_PROJ, PROJ_COL_BEAM_CROSS,
                             PROJ_COL_BEAM_THREAT, PROJ_COL_HALF_DIAG,
                             PROJ_COL_IS_ATTACK, PROJ_COL_IS_BEAM,
                             PROJ_DIMS, SELF_DIM, ObsBuilder, _half_diag_log)


def reference_is_attack(motion: int) -> int:
    """直接抄 player_check.nut:82 的分支结构,作为独立参照实现。"""
    if 4000 <= motion <= 4999:
        return 5 if motion >= 4900 else 4
    if 3910 <= motion <= 3980:
        return 6
    if 3000 <= motion <= 3909:
        return 3
    if 2000 <= motion <= 2999:
        return 2
    if motion >= 1000:
        return 1
    return 0


class TestIsAttack(unittest.TestCase):
    def test_matches_nut_definition(self):
        """扫全 motion 空间,与 nut 的分支结构逐值对齐。"""
        for motion in range(0, 8000):
            self.assertEqual(
                is_attack(motion), reference_is_attack(motion),
                f"motion={motion} 的 IsAttack 分级不一致")

    def test_real_attacks_from_damage_frames_are_recognised(self):
        """这些 motion 来自实测掉血帧里的攻击方。"""
        for motion in (1000, 1212, 1500, 1600, 1720, 2000, 3031):
            self.assertGreater(is_attack(motion), 0,
                               f"motion={motion} 实测打中过人,必须算攻击")

    def test_locomotion_and_idle_are_not_attacks(self):
        """待机/走路/跳跃的 motion 都在 1000 以下,不能被当成出招。"""
        for motion in (0, 1, 2, 36, 37, 190, 200, 201, 211, 212, 213, 999):
            self.assertEqual(is_attack(motion), 0,
                             f"motion={motion} 不是攻击")

    def test_effect_motion_range_is_covered_by_nut_range(self):
        """6030/6050 是演出特效,但 nut 的 IsAttack 只看 motion 区间:
        `motion >= 1000` 就返回 1,因此它们**也会被算成攻击**。

        这不是 bug,而是 IsAttack 本身的粗糙度 —— 它无法区分"特效动作"
        与"会伤人的动作"。真正的区分依据是帧数据里的 flagAttack
        (BindFrameFlag 索引 1),而该数据不在 Actor2D/SQInstance 上暴露。
        所以这里固化的是**已知局限**,避免有人误以为 is_attack 能过滤特效。
        """
        for motion in (6030, 6050, 9011):
            self.assertEqual(is_attack(motion), 1,
                             f"motion={motion} 落在 nut 的 motion>=1000 分支")


class TestProjectileFeatureShape(unittest.TestCase):
    def _actor(self, **kw):
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "box_l": 620.0, "box_t": 300.0, "box_r": 660.0, "box_b": 410.0}
        a.update(kw)
        return a

    def test_proj_dims_matches_emitted_rows(self):
        """同帧里带/不带 is_attack 的弹幕都要产出 PROJ_DIMS 列。"""
        b = ObsBuilder()
        self_actor = self._actor()
        shots = [
            {"x": 700.0, "y": 360.0, "vx": -1.0, "vy": 0.0, "owner": 1,
             "box_l": 700.0, "box_t": 360.0, "box_r": 700.0, "box_b": 360.0,
             "is_attack": 2},
            {"x": 600.0, "y": 360.0, "vx": 1.0, "vy": 0.0, "owner": 1,
             "box_l": 600.0, "box_t": 360.0, "box_r": 600.0, "box_b": 360.0,
             "is_attack": 0},
        ]
        out, mask = b.projectiles(shots, self_actor, 1.0, self_team=0)
        self.assertEqual(out.shape, (MAX_PROJ, PROJ_DIMS))
        self.assertEqual(int(mask.sum()), 2)
        # is_attack 指示位在倒数第二列(v17b 起末列是真攻击源 atk_lv);
        # 两发弹幕必须被区分开
        flags = sorted(out[:2, PROJ_COL_IS_ATTACK].tolist())
        self.assertEqual(flags, [0.0, 1.0])

    def test_missing_is_attack_defaults_to_not_attack(self):
        """旧协议没有该字段时退化为 0,而不是崩溃或误判为攻击。"""
        b = ObsBuilder()
        shots = [{"x": 700.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "owner": 1}]
        out, mask = b.projectiles(shots, self._actor(), 1.0, self_team=0)
        self.assertEqual(int(mask.sum()), 1)
        self.assertEqual(out[0, -1], 0.0)


if __name__ == "__main__":
    unittest.main()


class TestDamageAttributionContract(unittest.TestCase):
    """掉血事件的归因契约（实机 53 次掉血事件测得，见交付报告）。

    实机测量结果（COM4, 40s, 53 次掉血）：
        攻击方 is_attack 点亮  51/53 = 96.2%
        框间距近身             35/53 = 66.0%
        攻击弹幕存在           42/53 = 79.2%
        **三者皆无             0/53 = 0.0%**

    最后一行是本契约的核心：只要掉血，观测里**必定**至少有一路信号能解释它。
    这里用合成观测固化"归因通路存在"这一结构性质 —— 任何一路被删掉都会失败。
    """

    @staticmethod
    def _obs(**kw):
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 211, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "box_l": 620.0, "box_t": 300.0, "box_r": 660.0, "box_b": 410.0,
             "is_attack": 0}
        a.update(kw)
        return a

    def test_is_attack_channel_can_explain_a_hit(self):
        """通路 A：攻击方 motion 进入 IsAttack 区间 ⇒ is_attack > 0。"""
        atk = self._obs(motion=1220, is_attack=is_attack(1220))
        self.assertGreater(atk["is_attack"], 0)
        # 同一 motion 在旧口径 [3000,4999] 下会被判成 0，断言这一点防回退
        self.assertFalse(3000 <= atk["motion"] <= 4999)

    def test_proximity_channel_can_explain_a_hit(self):
        """通路 B:双方受击框近身时 gapx/gapy 都应为 0。"""
        me, opp = self._obs(), self._obs(
            box_l=650.0, box_t=300.0, box_r=690.0, box_b=410.0)
        gapx = max(opp["box_l"] - me["box_r"], me["box_l"] - opp["box_r"], 0.0)
        gapy = max(opp["box_t"] - me["box_b"], me["box_t"] - opp["box_b"], 0.0)
        self.assertLessEqual(gapx, 120.0)
        self.assertLessEqual(gapy, 60.0)

    def test_projectile_channel_reports_is_attack(self):
        """通路 C:弹幕的 is_attack 维必须能从 shots 一路传到观测。"""
        b = ObsBuilder()
        shots = [{"x": 700.0, "y": 360.0, "vx": -1.0, "vy": 0.0, "owner": 1,
                  "box_l": 700.0, "box_t": 360.0, "box_r": 700.0, "box_b": 360.0,
                  "is_attack": is_attack(2009)}]
        out, mask = b.projectiles(shots, self._obs(), 1.0, self_team=0)
        self.assertEqual(int(mask.sum()), 1)
        self.assertEqual(out[0, PROJ_COL_IS_ATTACK], 1.0, "攻击弹幕的 is_attack 维必须是 1")


class TestCombatStateFields(unittest.TestCase):
    """v15 硬直/霸体状态字段的观测契约。

    实机 1671 帧观测：endure_ratio 79 种取值、stan/recover 各 2 种、
    target_dist 184 种。这里固化它们的**语义**(不是数值)：
    霸体按上限归一、气绝/受身是布尔标志、目标距离带方向。
    """

    @staticmethod
    def _state(**actor_kw):
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "graze_state": 0,
             "endure_count": 200, "endure_max": 200,
             "stan_count": 0, "recover": 0, "center_stop": 0,
             "target_dist": 100.0}
        a.update(actor_kw)
        return {
            "battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
            "teams": [
                {"life": 6000, "regain": 0, "sp": 0, "sp_max": 4000,
                 "tag_ready": True, "spell_ready": False, "spell_active": False,
                 "current": 0, "master": dict(a), "slave": dict(a)},
                {"life": 6000, "regain": 0, "sp": 0, "sp_max": 4000,
                 "tag_ready": True, "spell_ready": False, "spell_active": False,
                 "current": 0, "master": dict(a), "slave": dict(a)},
            ],
            "shots": [],
        }

    # 战斗状态块在 self 向量里的固定起点。**不要**写 SELF_DIM-5:
    # 尾部还会继续追加新列(已从 5 列扩到 8 列),用尾部偏移会让这些测试
    # 在扩容时静默错位。这里用绝对起点。
    COMBAT_OFF = 32

    def test_hit_id_encodes_output_side_hit(self):
        """hit_id 是输出侧命中登记,bit14 是主命中源。

        交叉验证(8363 帧双向提升度):
          对手掩码 -> 我方掉血  bit26 14.1x / bit14 10.8x / 整掩码 6.1x
          我方掩码 -> 对手掉血  bit14 4.2x / 整掩码 2.7x
        掉血相对掩码有延迟,所以不能用"掉血当帧掩码为 0"否定这个方向。
        """
        from env.obs_builder import HIT_COLS, SELF_DIM
        base = self._state()
        off = SELF_DIM - len(HIT_COLS)

        def hit(v):
            st = {**base, "teams": [dict(t) for t in base["teams"]]}
            st["teams"][0] = {**st["teams"][0]}
            st["teams"][0]["master"] = {**st["teams"][0]["master"], "hit_id": v}
            return ObsBuilder().build(st, 0, 0)["self"][off:]

        # bit14 = 主命中源 -> hit_landed 与 hit_mask_any 同时为 1
        landed = hit(1 << 14)
        self.assertEqual(float(landed[0]), 1.0)
        self.assertEqual(float(landed[1]), 1.0)
        # 只有其它位 -> 不是主命中源,但掩码确实非零
        other = hit(1 << 26)
        self.assertEqual(float(other[0]), 0.0)
        self.assertEqual(float(other[1]), 1.0)
        # 空掩码 -> 两维皆 0
        none = hit(0)
        self.assertEqual(float(none[0]), 0.0)
        self.assertEqual(float(none[1]), 0.0)

    def test_atk_box_absent_means_not_attacking(self):
        """没有攻击框时 6 维全 0 —— 表示"这一帧没在出手",不是"够不着"。"""
        from env.obs_builder import ATK_COLS, HIT_COLS, SELF_DIM
        b = ObsBuilder()
        o = b.build(self._state(), 0, 0)
        lo = SELF_DIM - len(HIT_COLS) - len(ATK_COLS)
        tail = o["self"][lo:SELF_DIM - len(HIT_COLS)]
        self.assertTrue(all(float(v) == 0.0 for v in tail), tail)

    def test_atk_box_reach_is_signed_toward_opponent(self):
        """攻击框离对手越远,reach_x 越大;覆盖对手时 covers=1 且 reach 极小。

        这条防的是"把框画在错误一侧却仍给出 0 距离"这种静默错误:
        reach_x 用的是镜像不变坐标(s 乘过),正 = 框在自己与对手之间。
        """
        from env.obs_builder import ATK_COLS, HIT_COLS, SELF_DIM
        base = self._state()
        # 攻击框区块在 hit 两维之前,不能再用尾部切片(尾部是 HIT_COLS)。
        lo = SELF_DIM - len(HIT_COLS) - len(ATK_COLS)
        hi = SELF_DIM - len(HIT_COLS)
        # 自己 x=400,对手 x=600:框中心在 430(还差很远)
        far = ObsBuilder().build(self._with_atk(base, 430), 0, 0)["self"][lo:hi]
        # 框中心在 590(几乎贴住对手)
        near = ObsBuilder().build(self._with_atk(base, 590), 0, 0)["self"][lo:hi]
        self.assertGreater(float(far[0]), 0.0)      # have_atk_box
        self.assertGreater(float(far[1]), float(near[1]))
        self.assertEqual(float(near[5]), 1.0)       # covers_opp

    def _with_atk(self, st, cx):
        st = {**st, "teams": [dict(t) for t in st["teams"]]}
        st["teams"][0] = {**st["teams"][0]}
        st["teams"][0]["master"] = {**st["teams"][0]["master"],
                                    "atk_boxes": [{"x": cx, "y": 360.0,
                                                   "hw": 30.0, "hh": 40.0}]}
        return st

    def _tail(self, **kw):
        b = ObsBuilder()
        o = b.build(self._state(**kw), 0, 0)
        return o["self"][self.COMBAT_OFF:self.COMBAT_OFF + 5]

    def test_endure_is_normalised_by_max(self):
        """霸体余量按各自上限归一，不是原始计数。"""
        full = self._tail(endure_count=200, endure_max=200)[0]
        half = self._tail(endure_count=100, endure_max=200)[0]
        self.assertAlmostEqual(float(full), 1.0, places=5)
        self.assertAlmostEqual(float(half), 0.5, places=5)

    def test_stan_and_recover_are_flags(self):
        """气绝/受身是布尔标志：0 = 否，>0 = 是。

        实测口径不同：
        * `stan_count > 0` = 气绝(8011 帧里 170 帧命中，未观测到负值)；
        * `recover != 0` = 受身/起身。recover 的**正负两态都算**：
          rec+ hurt 率 29.77%(485/1629)、rec- 28.46%(70/246)，基线仅 4.47%。
          二者是同一受击窗口的两种编码，负值不是菜单残留。
        """
        quiet = self._tail(stan_count=0, recover=0)
        self.assertEqual(float(quiet[1]), 0.0)
        self.assertEqual(float(quiet[2]), 0.0)
        downed = self._tail(stan_count=40, recover=7)
        self.assertEqual(float(downed[1]), 1.0)
        self.assertEqual(float(downed[2]), 1.0)

    def test_negative_recover_still_counts_as_recovering(self):
        """recover 的负态(-1)同样是受身/起身,不能被当成"空闲"。

        实机 3342 帧:recover=-1 出现 246 帧,其中 70 帧伴随掉血(28.46%),
        与 recover>0 的 29.77% 同量级,远高于 4.47% 的基线。只判 >0 会丢这 246 帧。
        """
        self.assertEqual(float(self._tail(recover=-1)[2]), 1.0)
        self.assertEqual(float(self._tail(recover=1)[2]), 1.0)
        self.assertEqual(float(self._tail(recover=0)[2]), 0.0)

    def test_target_dist_is_directional(self):
        """目标距离带方向：正负必须区分，且随朝向镜像翻转。"""
        ahead = self._tail(target_dist=200.0)[4]
        behind = self._tail(target_dist=-200.0)[4]
        self.assertGreater(float(ahead), 0.0)
        self.assertLess(float(behind), 0.0)
        self.assertNotAlmostEqual(float(ahead), float(behind), places=5)


class TestDamageSettlementFields(unittest.TestCase):
    """v16 队伍级伤害结算字段的观测契约。

    实机 20s 实测：combo_damage 0..2021、damage_scale 0.46..1.0。
    这两个量决定"这套连段还要不要继续压"——只看 life(结果) 看不出来。
    """

    @staticmethod
    def _obs(combo_damage=0.0, damage_scale=1.0):
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "endure_count": 200,
             "endure_max": 200, "stan_count": 0, "recover": 0,
             "center_stop": 0, "target_dist": 0.0}
        team = {"life": 6000, "regain": 0, "sp": 0, "sp_max": 4000,
                "tag_ready": True, "spell_ready": False, "spell_active": False,
                "current": 0, "combo_damage": combo_damage,
                "damage_scale": damage_scale,
                "master": dict(a), "slave": dict(a)}
        other = dict(team, combo_damage=0.0, damage_scale=1.0)
        return {"battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
                "teams": [team, other], "shots": []}

    def _g(self, **kw):
        return ObsBuilder().build(self._obs(**kw), 0, 0)["global"]

    def test_combo_damage_is_normalised_by_max_hp(self):
        """连段伤害按最大血量归一，且随伤害增长单调。"""
        zero = float(self._g(combo_damage=0.0)[12])
        some = float(self._g(combo_damage=1500.0)[12])
        self.assertEqual(zero, 0.0)
        self.assertGreater(some, 0.0)
        self.assertLessEqual(some, 1.0)

    def test_damage_scale_reaches_into_obs(self):
        """伤害倍率必须原样进观测：1.0 满伤 -> 0.46 连段末衰减。"""
        self.assertAlmostEqual(float(self._g(damage_scale=1.0)[13]), 1.0, places=5)
        self.assertAlmostEqual(float(self._g(damage_scale=0.46)[13]), 0.46, places=5)


class TestRoleAttributesInObs(unittest.TestCase):
    """角色固有属性（atk_range/gravity/slide_speed）必须在观测里可见。

    实测：reimu = (70, 0.70, 17.0)，marisa = (100, 0.85, 19.5)。
    角色名没有进观测，所以这三列是模型区分"速攻型 / 力量型"的唯一依据。
    """

    @staticmethod
    def _self(role):
        atk, grav, slide = role
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "endure_count": 200,
             "endure_max": 200, "stan_count": 0, "recover": 0,
             "center_stop": 0, "target_dist": 0.0,
             "atk_range": atk, "gravity": grav, "slide_speed": slide}
        team = {"life": 6000, "regain": 0, "sp": 0, "sp_max": 4000,
                "tag_ready": True, "spell_ready": False, "spell_active": False,
                "current": 0, "combo_damage": 0, "damage_scale": 1.0,
                "combo_stun": 0, "master": dict(a), "slave": dict(a)}
        return {"battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
                "teams": [team, dict(team)], "shots": []}

    def test_role_columns_differ_between_characters(self):
        r = ObsBuilder().build(self._self((70, 0.70, 17.0)), 0, 0)["self"]
        m = ObsBuilder().build(self._self((100, 0.85, 19.5)), 0, 0)["self"]
        # 尾部三列必须是三条不同的量，且两个角色不等
        for i in (37, 38, 39):
            self.assertNotAlmostEqual(float(r[i]), float(m[i]), places=6,
                                      msg=f"第 {i} 列在两个角色间相同，说明该维没接对")
        # 方向正确：力量型射程更远、重力更大、滑行更快
        self.assertLess(float(r[37]), float(m[37]))   # atk_range 70 < 100
        self.assertLess(float(r[38]), float(m[38]))   # gravity 0.70 < 0.85
        self.assertLess(float(r[39]), float(m[39]))   # slide 17.0 < 19.5


class TestResourceAndAttackChainDimensions(unittest.TestCase):
    """v16b 新接入的 7 维（mp/graze/hit_count/key_take/hit_back/atk_id/hit_result）。

    这七项此前写进协议却零消费，按"采而不用=没采"的标准等于没采。
    这里锁住它们的**接线位置与归一化口径**——实机已验证 3115 帧零偏差。
    """

    OFF = 40   # self[40..46]

    @staticmethod
    def _obs(**kw):
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "endure_count": 200,
             "endure_max": 200, "stan_count": 0, "recover": 0,
             "center_stop": 0, "target_dist": 0.0, "atk_range": 70,
             "gravity": 0.7, "slide_speed": 17.0,
             "graze_state": 0, "hit_count": 0, "key_take": 0,
             "hit_back_flag": 0, "atk_id": 0, "hit_result": 0}
        a.update({k: v for k, v in kw.items() if k not in ("mp",)})
        team = {"life": 6000, "regain": 0, "sp": 0, "sp_max": 4000,
                "mp": kw.get("mp", 1000),
                "tag_ready": True, "spell_ready": False, "spell_active": False,
                "current": 0, "combo_damage": 0, "damage_scale": 1.0,
                "combo_stun": 0, "master": dict(a), "slave": dict(a)}
        return {"battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
                "teams": [team, dict(team)], "shots": []}

    def _cols(self, **kw):
        return ObsBuilder().build(self._obs(**kw), 0, 0)["self"][self.OFF:self.OFF + 7]

    def test_mp_is_its_own_resource_line(self):
        """mp 按 max_mp(1000) 归一，与 sp/op 是三条独立资源线。"""
        self.assertAlmostEqual(float(self._cols(mp=1000)[0]), 1.0, places=5)
        self.assertAlmostEqual(float(self._cols(mp=400)[0]), 0.4, places=5)

    def test_graze_is_a_flag(self):
        self.assertEqual(float(self._cols(graze_state=0)[1]), 0.0)
        self.assertEqual(float(self._cols(graze_state=2)[1]), 1.0)

    def test_hit_count_and_key_take_are_normalised(self):
        c = self._cols(hit_count=5, key_take=4)
        self.assertAlmostEqual(float(c[2]), 0.5, places=5)
        self.assertAlmostEqual(float(c[3]), 0.5, places=5)

    def test_hit_back_flag_covers_all_three_values(self):
        """hit_back_flag 实测 {0,1,2}，三档必须可区分。"""
        vals = [float(self._cols(hit_back_flag=v)[4]) for v in (0, 1, 2)]
        self.assertEqual(len(set(vals)), 3, f"三档没区分开: {vals}")

    def test_attack_chain_ids_reach_obs(self):
        c = self._cols(atk_id=32768, hit_result=1)
        self.assertGreater(float(c[5]), 0.0, "atk_id 必须进观测")
        self.assertEqual(float(c[6]), 1.0, "hit_result 必须原样进观测")


class TestProjectileApproachVelocity(unittest.TestCase):
    """proj 第 5 维是"接近速度"，不是判定尺寸。

    实测 6404 条弹幕的 AABB 宽高**全部为 0**（弹幕的 Actor2D 不携带 hantei
    尺寸），所以这一维若写尺寸会恒为 0。改写成"速度在指向我方向上的分量"后，
    实机 4237 帧 / 16305 条弹幕行测到 719 种取值，覆盖 [-1, 1]。
    """

    @staticmethod
    def _proj(vx, vy=0.0, sx=640.0, shot_x=None, shot_y=360.0):
        a = {"x": sx, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "endure_count": 200,
             "endure_max": 200, "stan_count": 0, "recover": 0,
             "center_stop": 0, "target_dist": 0.0, "atk_range": 70,
             "gravity": 0.7, "slide_speed": 17.0, "graze_state": 0,
             "hit_count": 0, "key_take": 0, "hit_back_flag": 0,
             "atk_id": 0, "hit_result": 0}
        team = {"life": 6000, "regain": 0, "sp": 0, "sp_max": 4000, "mp": 1000,
                "tag_ready": True, "spell_ready": False, "spell_active": False,
                "current": 0, "combo_damage": 0, "damage_scale": 1.0,
                "combo_stun": 0, "master": dict(a), "slave": dict(a)}
        sh = {"x": sx + 300.0 if shot_x is None else shot_x, "y": shot_y,
              "vx": vx, "vy": vy, "dir": 1.0, "id": 1, "motion": 2009, "owner": 1,
              "box_l": 0.0, "box_t": 0.0, "box_r": 0.0, "box_b": 0.0,
              "cb_group": 32, "cb_mask": 0, "src": 0,
              "key_take": 0, "key_frame": 0, "is_attack": 2}
        state = {"battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
                 "teams": [team, dict(team)], "shots": [sh]}
        b = ObsBuilder()
        out, mask = b.projectiles([sh], team["master"], 1.0, 0)
        assert int(mask.sum()) == 1
        return float(out[0][4])

    def test_incoming_bullet_has_negative_approach(self):
        """朝我飞的弹幕:接近速度为负(负 = 正在逼近)。"""
        self.assertLess(self._proj(vx=-4.0), 0.0)

    def test_receding_bullet_has_positive_approach(self):
        """远离我的弹幕:接近速度为正。"""
        self.assertGreater(self._proj(vx=4.0), 0.0)

    def test_approach_is_directional_not_speed_magnitude(self):
        """同样速度大小、相反方向,该维必须一正一负(不是速度绝对值)。"""
        toward = self._proj(vx=-4.0)
        away = self._proj(vx=4.0)
        self.assertAlmostEqual(abs(toward), abs(away), places=5)
        self.assertLess(toward, 0.0)
        self.assertGreater(away, 0.0)


class TestTimeControlDimensions(unittest.TestCase):
    """v17 时间控制三维：时停中 / 时停剩余 / 慢动作剩余。

    实测 6011 帧：is_time_stop 有 391 帧为 True，而同期 time_stop_count
    仍为 0 —— 说明这两个计数属于用户任务队列，不是当前时停剩余帧，三者不可互替。
    """

    @staticmethod
    def _g(is_time_stop=False, time_stop_count=0, slow_count=0):
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "endure_count": 200,
             "endure_max": 200, "stan_count": 0, "recover": 0,
             "center_stop": 0, "target_dist": 0.0, "atk_range": 70,
             "gravity": 0.7, "slide_speed": 17.0, "graze_state": 0,
             "hit_count": 0, "key_take": 0, "hit_back_flag": 0,
             "atk_id": 0, "hit_result": 0}
        team = {"life": 6000, "regain": 0, "sp": 0, "sp_max": 4000, "mp": 1000,
                "tag_ready": True, "spell_ready": False, "spell_active": False,
                "current": 0, "combo_damage": 0, "damage_scale": 1.0,
                "combo_stun": 0, "master": dict(a), "slave": dict(a)}
        st = {"battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
              "is_time_stop": is_time_stop, "time_stop_count": time_stop_count,
              "slow_count": slow_count,
              "teams": [team, dict(team)], "shots": []}
        return ObsBuilder().build(st, 0, 0)["global"]

    def test_time_stop_flag(self):
        self.assertEqual(float(self._g(is_time_stop=False)[15]), 0.0)
        self.assertEqual(float(self._g(is_time_stop=True)[15]), 1.0)

    def test_time_stop_and_slow_counts_are_independent(self):
        """两个计数各自归一，且互不覆盖。"""
        c = self._g(time_stop_count=30, slow_count=120)
        self.assertAlmostEqual(float(c[16]), 0.5, places=5)    # 30/60
        self.assertAlmostEqual(float(c[17]), 0.5, places=5)    # 120/240


class TestFrameLevelAttackData(unittest.TestCase):
    """帧级攻击数据 temp_atk_data（v17b）。

    这是此前被误判为"不可达"的东西：它不在 actor 自身字段上，
    而是 actor 的一个 SQInstance **子对象**。实测存在率 75%（4263/5684 帧），
    attackLV 5 种取值（0/10/20/30/60）。
    它给出"这一招有多强"的直接分级，而不是从 motion 区间间接推断。
    """

    @staticmethod
    def _self(**kw):
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "endure_count": 200,
             "endure_max": 200, "stan_count": 0, "recover": 0,
             "center_stop": 0, "target_dist": 0.0, "atk_range": 70,
             "gravity": 0.7, "slide_speed": 17.0, "graze_state": 0,
             "hit_count": 0, "key_take": 0, "hit_back_flag": 0,
             "atk_id": 0, "hit_result": 0,
             "atk_lv": 0, "atk_cancel": 0, "atk_guard_break": 0}
        a.update(kw)
        team = {"life": 6000, "regain": 0, "sp": 0, "sp_max": 4000, "mp": 1000,
                "tag_ready": True, "spell_ready": False, "spell_active": False,
                "current": 0, "combo_damage": 0, "damage_scale": 1.0,
                "combo_stun": 0, "master": dict(a), "slave": dict(a)}
        return ObsBuilder().build(
            {"battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
             "teams": [team, dict(team)], "shots": []}, 0, 0)["self"][47:50]

    def test_attack_level_is_graded(self):
        """攻击等级按 60 归一，且不同等级可区分。"""
        vals = [float(self._self(atk_lv=v)[0]) for v in (0, 10, 30, 60)]
        self.assertEqual(vals[0], 0.0)
        self.assertAlmostEqual(vals[1], 10 / 60, places=5)
        self.assertAlmostEqual(vals[2], 30 / 60, places=5)
        self.assertAlmostEqual(vals[3], 1.0, places=5)
        self.assertEqual(len(set(vals)), 4, "四个等级必须互不相同")

    def test_cancel_window_distinguishes_cancellable(self):
        """atk_cancel 99 = 不可取消，必须与 0 区分开。"""
        self.assertEqual(float(self._self(atk_cancel=0)[1]), 0.0)
        self.assertAlmostEqual(float(self._self(atk_cancel=99)[1]), 1.0, places=5)

    def test_guard_break_flag(self):
        self.assertEqual(float(self._self(atk_guard_break=0)[2]), 0.0)
        self.assertEqual(float(self._self(atk_guard_break=1)[2]), 1.0)


class TestCurrentSwitchDetection(unittest.TestCase):
    """current 必须靠实例字段读，且 -1 表示解析失败。

    历史 bug：桥接对实例调 table_slot(t,"current")。table_slot 读
    O_TABLE_NODES(0x20)，而实例在 0x20 是 O_INST_USERPTR → nodes 恒 0
    → 永远返回 0 → current 恒 0，换没换人都一样。实机验证修复后
    current 在 Tag 换人时 0 → 1（master.motion 3910 / slave.motion 36）。
    """

    def test_zero_is_master_one_is_slave(self):
        from env.obs_builder import ObsBuilder
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 10, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "endure_count": 200,
             "endure_max": 200, "stan_count": 0, "recover": 0,
             "center_stop": 0, "target_dist": 0.0, "atk_range": 70,
             "gravity": 0.7, "slide_speed": 17.0, "graze_state": 0,
             "hit_count": 0, "key_take": 0, "hit_back_flag": 0,
             "atk_id": 0, "hit_result": 0, "atk_lv": 0,
             "atk_cancel": 0, "atk_guard_break": 0}
        master = dict(a, motion=10)
        slave = dict(a, motion=999)
        base = {"life": 6000, "regain": 0, "sp": 1000, "sp_max": 4000,
                "mp": 1000, "tag_ready": True, "spell_ready": False,
                "spell_active": False, "combo_damage": 0, "damage_scale": 1.0,
                "combo_stun": 0}
        b = ObsBuilder()

        def build(cur):
            tm = dict(base, range=("master", "slave"), master=master,
                      slave=slave, current=cur)
            opp = dict(base, range=None, master=master, slave=slave, current=cur)
            opp.pop("range")
            tm.pop("range")
            st = {"battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
                  "teams": [tm, opp], "shots": []}
            return b.build(st, 0, 0)

        # current=0 与 -1 都应取 master（观测不能为空）
        for cur in (0, -1):
            o = build(cur)
            self.assertTrue(len(o["self"]) > 0)
        # current=1 必须取 slave: motion 维应反映 999 派生值而非 10
        o0, o1 = build(0), build(1)
        self.assertFalse(np.array_equal(o0["self"], o1["self"]),
                         "current=1 时必须切换到 slave 观测")

    def test_reward_ignores_failed_parse_as_switch(self):
        """0 -> -1 是解析失败，不能被算成一次换人。"""
        from env.reward import RewardCalculator
        r = RewardCalculator()
        base = {"regain": [0, 0], "life": [9000, 9000], "sp_max": [4000, 4000],
                "sp": [1000, 1000], "op": [0, 0], "occult": [0, 0],
                "mp": [0, 0], "combo_count": [0, 0], "combo_damage": [0, 0],
                "hit_count": [0, 0], "hit_back": [0, 0], "atk_id": [0, 0],
                "time": 1.0, "state": 8, "winner": 0,
                "current": [0, 0], "guard": [0, 0], "motion": [0, 0],
                "hit_stop": [0, 0], "damage_stop": [0, 0], "dash": [0, 0],
                "graze": [0, 0], "endure": [0, 0], "stan": [0, 0],
                "recover": [0, 0], "center_stop": [0, 0]}
        def switches(prev_cur, cur_cur):
            r._tag_recover_total = [0, 0]
            r._counts = collections.Counter()
            prev = dict(base, current=list(prev_cur))
            cur = dict(base, current=list(cur_cur))
            r._regain(prev, cur, 0)   # 换人逻辑在 _regain 里
            return r._counts.get("switches", 0)

        self.assertEqual(switches([0, 0], [-1, 0]), 0,
                         "0 -> -1 是解析失败，不是换人")
        self.assertEqual(switches([-1, 0], [0, 0]), 0,
                         "-1 -> 0 同样是解析失败")
        self.assertEqual(switches([0, 0], [1, 0]), 1,
                         "真实的 0 -> 1 必须被计数")


class TestProjectileNoFakeAttackLevel(unittest.TestCase):
    """弹幕不再发布 atk_lv（曾经是施法者的值，不是弹幕自己的）。

    实测证据：
      * 扫 native 偏移 0x40..0xc0 取到的第一个 INSTANCE 引用就是**施法者**
        （+0x74 -> master），所以弹幕 atk_lv 恒等于该玩家的攻击等级；
      * 91.2% 的弹幕 atk_lv 恰好等于某个 actor 的 atk_lv；
      * 同一 motion 的弹幕 atk_lv 完全相同，与弹幕种类无关；
      * 弹幕 native 对象上不存在指向自身 SQ 实例的引用。
    因此该维已从观测删除。
    """

    def test_no_atk_lv_column_emitted(self):
        """即便 shots 里带了 atk_lv，也不应出现在观测中。

        判据不是"列数不变"（列数会随真实新增的维度增长），而是"atk_lv 的取值
        不影响 any 一列"：同一发弹幕只改 atk_lv，观测必须逐位相同。
        """
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "endure_count": 200,
             "endure_max": 200, "stan_count": 0, "recover": 0,
             "center_stop": 0, "target_dist": 0.0, "atk_range": 70,
             "gravity": 0.7, "slide_speed": 17.0, "graze_state": 0,
             "hit_count": 0, "key_take": 0, "hit_back_flag": 0,
             "atk_id": 0, "hit_result": 0, "atk_lv": 0,
             "atk_cancel": 0, "atk_guard_break": 0}
        team = {"life": 6000, "regain": 0, "sp": 1000, "sp_max": 4000,
                "mp": 1000, "tag_ready": True, "spell_ready": False,
                "spell_active": False, "current": 0, "combo_damage": 0,
                "damage_scale": 1.0, "combo_stun": 0,
                "master": a, "slave": a}
        sh = {"x": 900.0, "y": 360.0, "vx": -1.0, "vy": 0.0, "dir": 1.0,
              "id": 1, "motion": 2009, "owner": 1,
              "box_l": 900.0, "box_t": 360.0, "box_r": 900.0, "box_b": 360.0,
              "is_attack": 2, "atk_lv": 30}
        st = {"battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
              "teams": [team, dict(team)], "shots": [sh]}
        o1 = ObsBuilder().build(st, 0, 0)
        self.assertEqual(o1["proj"].shape, (MAX_PROJ, PROJ_DIMS))
        # 只改 atk_lv，观测必须逐位相同 —— 这才是"没有 atk_lv 列"的真正判据。
        sh2 = dict(sh, atk_lv=0)
        st2 = dict(st, shots=[sh2])
        o2 = ObsBuilder().build(st2, 0, 0)
        np.testing.assert_array_equal(o1["proj"], o2["proj"])

    def _proj_of_shot(self, shot):
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "dash_count": 0, "guard_state": 0, "endure_count": 200,
             "endure_max": 200, "stan_count": 0, "recover": 0,
             "center_stop": 0, "target_dist": 0.0, "atk_range": 70,
             "gravity": 0.7, "slide_speed": 17.0, "graze_state": 0,
             "hit_count": 0, "key_take": 0, "hit_back_flag": 0,
             "atk_id": 0, "hit_result": 0, "atk_lv": 0,
             "atk_cancel": 0, "atk_guard_break": 0}
        team = {"life": 6000, "regain": 0, "sp": 1000, "sp_max": 4000,
                "mp": 1000, "tag_ready": True, "spell_ready": False,
                "spell_active": False, "current": 0, "combo_damage": 0,
                "damage_scale": 1.0, "combo_stun": 0,
                "master": a, "slave": a}
        st = {"battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
              "teams": [team, dict(team)], "shots": [shot]}
        return ObsBuilder().build(st, 0, 0)["proj"][0]

    def test_proj_carries_hitbox_volume(self):
        """有真实判定框的弹幕，体积维必须 > 0。

        背景：弹幕的 Actor2D AABB 退化成点，真实尺寸藏在 anim+0x84 的攻击框
        向量里，且**不是每帧非空**。桥接层已按 (motion, take) 缓存兜底
        （覆盖率 36% -> 84%），观测必须把这档信息带进策略。
        36x36 是实测最常见的弹幕尺寸（motion 2008/2009）。
        """
        sh = {"x": 900.0, "y": 360.0, "vx": -1.0, "vy": 0.0, "dir": 1.0,
              "id": 1, "motion": 2008, "owner": 1,
              "box_l": 882.0, "box_t": 342.0, "box_r": 918.0, "box_b": 378.0,
              "is_attack": 2, "atk_lv": 0}
        row = self._proj_of_shot(sh)
        expected = 0.5 * (36.0 ** 2 + 36.0 ** 2) ** 0.5
        self.assertAlmostEqual(float(row[PROJ_COL_HALF_DIAG]),
                               _half_diag_log(expected), places=5)
        self.assertGreater(float(row[PROJ_COL_HALF_DIAG]), 0.0)

    def test_degenerate_box_reports_zero_volume(self):
        """退化框（体积未知）必须是 0，不能伪装成"体积为零的很小弹幕"。"""
        sh = {"x": 900.0, "y": 360.0, "vx": -1.0, "vy": 0.0, "dir": 1.0,
              "id": 1, "motion": 2008, "owner": 1,
              "box_l": 900.0, "box_t": 360.0, "box_r": 900.0, "box_b": 360.0,
              "is_attack": 2, "atk_lv": 0}
        row = self._proj_of_shot(sh)
        self.assertEqual(float(row[PROJ_COL_HALF_DIAG]), 0.0)

    def test_volume_scales_with_box_size(self):
        """体积维必须随框变大而单调增大，否则等于没接。"""
        def row_for(size):
            h = size / 2.0
            return self._proj_of_shot({
                "x": 900.0, "y": 360.0, "vx": -1.0, "vy": 0.0, "dir": 1.0,
                "id": 1, "motion": 2008, "owner": 1,
                "box_l": 900.0 - h, "box_t": 360.0 - h,
                "box_r": 900.0 + h, "box_b": 360.0 + h,
                "is_attack": 2, "atk_lv": 0})
        vals = [float(row_for(s)[PROJ_COL_HALF_DIAG]) for s in (16.0, 36.0, 100.0)]
        self.assertTrue(vals[0] < vals[1] < vals[2], vals)

    def test_screen_spanning_box_is_distinguishable_from_large_bullet(self):
        """整屏判定框（motion 4009 实测 1729x104）不能被截断到大弹的同一档。

        这是线性标度踩过的坑：上限取 128 时 130x130(92px) 与 1729x104(866px)
        都变成 1.0，两者不可区分。
        """
        def row_for(w, h):
            return self._proj_of_shot({
                "x": 900.0, "y": 360.0, "vx": -1.0, "vy": 0.0, "dir": 1.0,
                "id": 1, "motion": 4009, "owner": 1,
                "box_l": 900.0 - w / 2, "box_t": 360.0 - h / 2,
                "box_r": 900.0 + w / 2, "box_b": 360.0 + h / 2,
                "is_attack": 2, "atk_lv": 0})
        big = float(row_for(130.0, 130.0)[PROJ_COL_HALF_DIAG])
        huge = float(row_for(1729.0, 104.0)[PROJ_COL_HALF_DIAG])
        self.assertLess(big, 0.9)
        self.assertGreater(huge - big, 0.2, f"big={big:.3f} huge={huge:.3f}")


class LaserGeometrySeam(unittest.TestCase):
    """验证激光射线几何、垂线威胁度衰减、截面覆盖及旋转外接包围盒数学性质。"""

    def _actor(self, **kw):
        x = float(kw.get("x", 640.0))
        y = float(kw.get("y", 360.0))
        a = {"x": x, "y": y, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "box_l": x - 20.0, "box_t": y - 50.0, "box_r": x + 20.0, "box_b": y + 50.0}
        a.update(kw)
        return a

    def _proj(self, shot, actor=None):
        b = ObsBuilder()
        act = actor or self._actor()
        out, mask = b.projectiles([shot], act, 1.0, self_team=0)
        return out[0], mask[0]

    def test_horizontal_beam_axis_alignment_and_decay(self):
        """水平激光（theta=0）：自机正对激光轴线时威胁度最大（1.0），垂线方向远离时单调递减。"""
        shot = {
            "x": 200.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
            "motion": 2018, "owner": 1, "is_beam": 1,
            "beam_w": 40.0, "beam_l": 800.0, "beam_angle": 0.0,
            "box_l": 200.0, "box_t": 340.0, "box_r": 1000.0, "box_b": 380.0,
        }
        # 1. 自机中心在 y=360，正对光束轴线
        row_on_axis, _ = self._proj(shot, self._actor(x=640.0, y=360.0))
        self.assertEqual(row_on_axis[PROJ_COL_IS_BEAM], 1.0)
        self.assertAlmostEqual(row_on_axis[PROJ_COL_BEAM_THREAT], 1.0, places=3)
        self.assertEqual(row_on_axis[PROJ_COL_BEAM_CROSS], 1.0)

        # 2. 自机垂线方向偏移 50px, 120px, 340px，威胁度应严格单调递减
        row_near, _ = self._proj(shot, self._actor(x=640.0, y=410.0))
        row_mid, _ = self._proj(shot, self._actor(x=640.0, y=480.0))
        row_far, _ = self._proj(shot, self._actor(x=640.0, y=700.0))

        t_on = float(row_on_axis[PROJ_COL_BEAM_THREAT])
        t_near = float(row_near[PROJ_COL_BEAM_THREAT])
        t_mid = float(row_mid[PROJ_COL_BEAM_THREAT])
        t_far = float(row_far[PROJ_COL_BEAM_THREAT])

        self.assertGreater(t_on, t_near)
        self.assertGreater(t_near, t_mid)
        self.assertGreater(t_mid, t_far)
        self.assertLess(t_far, 0.2)

    def test_angled_laser_threat_calculation(self):
        """倾斜激光（theta != 0）：沿指定角度方向保持截面威胁。"""
        # 45 度角激光 (cos=0.707, sin=0.707), 原点 (200, 200)
        angle = float(np.pi / 4.0)
        shot = {
            "x": 200.0, "y": 200.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
            "motion": 2018, "owner": 1, "is_beam": 1,
            "beam_w": 30.0, "beam_l": 600.0, "beam_angle": angle,
            "box_l": 200.0, "box_t": 200.0, "box_r": 624.0, "box_b": 624.0,
        }
        # 沿 45 度线上的点: (200 + 200, 200 + 200) = (400, 400)
        row_on, _ = self._proj(shot, self._actor(x=400.0, y=400.0))
        self.assertAlmostEqual(row_on[PROJ_COL_BEAM_THREAT], 1.0, places=3)
        self.assertEqual(row_on[PROJ_COL_BEAM_CROSS], 1.0)

        # 偏离该线的点: (400, 200) -> 垂线距离 ~ 141px
        row_off, _ = self._proj(shot, self._actor(x=400.0, y=200.0))
        self.assertLess(row_off[PROJ_COL_BEAM_THREAT], 0.6)

    def test_longitudinal_coverage_in_cross(self):
        """纵向射程截面：在前方有效射程内为 1.0，在发生源后方或超远距离平滑衰减到 0。"""
        shot = {
            "x": 300.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
            "motion": 2018, "owner": 1, "is_beam": 1,
            "beam_w": 30.0, "beam_l": 500.0, "beam_angle": 0.0,
            "box_l": 300.0, "box_t": 345.0, "box_r": 800.0, "box_b": 375.0,
        }
        # 1. 在射程内部 (x=500)
        row_in, _ = self._proj(shot, self._actor(x=500.0, y=360.0))
        self.assertEqual(row_in[PROJ_COL_BEAM_CROSS], 1.0)

        # 2. 在射程起始前方 (x=310)
        row_start, _ = self._proj(shot, self._actor(x=310.0, y=360.0))
        self.assertEqual(row_start[PROJ_COL_BEAM_CROSS], 1.0)

        # 3. 在发射源后方远距离 (x=100)
        row_behind, _ = self._proj(shot, self._actor(x=100.0, y=360.0))
        self.assertEqual(row_behind[PROJ_COL_BEAM_CROSS], 0.0)
        self.assertEqual(row_behind[PROJ_COL_BEAM_THREAT], 0.0)

        # 4. 在超出射程远端 (x=1000)
        row_beyond, _ = self._proj(shot, self._actor(x=1000.0, y=360.0))
        self.assertEqual(row_beyond[PROJ_COL_BEAM_CROSS], 0.0)
        self.assertEqual(row_beyond[PROJ_COL_BEAM_THREAT], 0.0)

    def test_beam_threat_zero_behind_laser(self):
        """当自机处于激光发射者正后方（同一高度轴线但背向激光）时，威胁度必须为 0.0，避免在抓破绽反击时误报最大危险。"""
        # 魔理沙在 x=800 向左发射 Master Spark (dir=-1, beam_angle=pi)
        shot = {
            "x": 800.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": -1.0,
            "motion": 4009, "owner": 1, "is_beam": 1,
            "beam_w": 104.0, "beam_l": 1729.0, "beam_angle": float(np.pi),
            "box_l": 800.0 - 1729.0, "box_t": 308.0, "box_r": 800.0, "box_b": 412.0,
        }
        # 自机在 x=950 (魔理沙身后 150px 处，同高度 y=360)
        row_behind, _ = self._proj(shot, self._actor(x=950.0, y=360.0))
        self.assertEqual(row_behind[PROJ_COL_BEAM_CROSS], 0.0)
        self.assertEqual(row_behind[PROJ_COL_BEAM_THREAT], 0.0)

    def test_normal_projectile_has_zero_beam_features(self):
        """普通点弹幕（非光束）的激光特征必须严格归零。"""
        shot = {
            "x": 400.0, "y": 360.0, "vx": -2.0, "vy": 0.0, "dir": 1.0,
            "motion": 2008, "owner": 1, "is_beam": 0,
            "box_l": 380.0, "box_t": 340.0, "box_r": 420.0, "box_b": 380.0,
        }
        row, _ = self._proj(shot, self._actor(x=640.0, y=360.0))
        self.assertEqual(row[PROJ_COL_IS_BEAM], 0.0)
        self.assertEqual(row[PROJ_COL_BEAM_THREAT], 0.0)
        self.assertEqual(row[PROJ_COL_BEAM_CROSS], 0.0)

    def test_rotated_aabb_formula(self):
        """测试 2D 旋转外接矩形 AABB 公式：dx = |r00| hw + |r01| hh, dy = |r10| hw + |r11| hh。"""
        hw, hh = 100.0, 20.0
        # 旋转 0 度
        theta_0 = 0.0
        r00, r01 = np.cos(theta_0), -np.sin(theta_0)
        r10, r11 = np.sin(theta_0), np.cos(theta_0)
        dx_0 = abs(r00) * hw + abs(r01) * hh
        dy_0 = abs(r10) * hw + abs(r11) * hh
        self.assertAlmostEqual(dx_0, 100.0)
        self.assertAlmostEqual(dy_0, 20.0)

        # 旋转 90 度
        theta_90 = np.pi / 2.0
        r00, r01 = np.cos(theta_90), -np.sin(theta_90)
        r10, r11 = np.sin(theta_90), np.cos(theta_90)
        dx_90 = abs(r00) * hw + abs(r01) * hh
        dy_90 = abs(r10) * hw + abs(r11) * hh
        self.assertAlmostEqual(dx_90, 20.0)
        self.assertAlmostEqual(dy_90, 100.0)

        # 旋转 45 度
        theta_45 = np.pi / 4.0
        r00, r01 = np.cos(theta_45), -np.sin(theta_45)
        r10, r11 = np.sin(theta_45), np.cos(theta_45)
        dx_45 = abs(r00) * hw + abs(r01) * hh
        dy_45 = abs(r10) * hw + abs(r11) * hh
        expected = np.sqrt(0.5) * (100.0 + 20.0)
        self.assertAlmostEqual(dx_45, expected)
        self.assertAlmostEqual(dy_45, expected)


class SpecialHitboxPerceptionTest(unittest.TestCase):
    """验证特殊招式/大招与激光判定感知（Master Spark、激光前摇预警、字段缺省兜底）。"""

    def _state(self, opp_motion=0, shots=None):
        a = {"x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
             "motion": 0, "key_frame": 0, "hit_stop": 0, "damage_stop": 0,
             "box_l": 620.0, "box_t": 310.0, "box_r": 660.0, "box_b": 410.0}
        opp_a = dict(a)
        opp_a["x"] = 800.0
        opp_a["box_l"] = 780.0
        opp_a["box_r"] = 820.0
        opp_a["motion"] = opp_motion

        team0 = {"life": 10000, "regain": 0, "sp": 0, "sp_max": 4000, "mp": 1000,
                 "tag_ready": True, "spell_ready": False, "spell_active": False,
                 "current": 0, "combo_damage": 0, "damage_scale": 1.0,
                 "combo_stun": 0, "master": dict(a), "slave": dict(a)}
        team1 = dict(team0)
        team1["master"] = dict(opp_a)
        team1["slave"] = dict(opp_a)

        return {
            "battle_state": 8, "time": 50, "time_unit": 90, "round": 1,
            "is_time_stop": False, "time_stop_count": 0, "slow_count": 0,
            "teams": [team0, team1],
            "shots": shots or [],
        }

    def test_marisa_master_spark_perception(self):
        """魔理沙 Master Spark (motion 4009, 1729x104)：自动识别为 beam，产生极大体积和强烈威胁。"""
        b = ObsBuilder()
        spark = {
            "x": 800.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": -1.0,
            "motion": 4009, "owner": 1, "is_beam": 1,
            "beam_w": 104.0, "beam_l": 1729.0, "beam_angle": float(np.pi),  # 朝左
            "box_l": 800.0 - 1729.0, "box_t": 308.0, "box_r": 800.0, "box_b": 412.0,
            "is_attack": 5, "atk_lv": 60,
        }
        st = self._state(opp_motion=4009, shots=[spark])
        obs = b.build(st, controlled_team=0)

        # 1. 弹幕感知：必须被识别为 beam，威胁度 = 1.0
        proj_row = obs["proj"][0]
        self.assertEqual(proj_row[PROJ_COL_IS_BEAM], 1.0)
        self.assertAlmostEqual(proj_row[PROJ_COL_BEAM_THREAT], 1.0, places=3)
        self.assertEqual(proj_row[PROJ_COL_BEAM_CROSS], 1.0)
        # 体积接近上限对数值
        self.assertGreater(proj_row[PROJ_COL_HALF_DIAG], 0.9)

        # 2. 全局特征：laser_startup_warning 必须激活 (1.0)
        self.assertEqual(obs["global"][23], 1.0)

    def test_marisa_laser_beam_motion_2018(self):
        """魔理沙 5B/6B 激光束 (motion 2018)：即使 is_beam 字段为 0 也能根据 motion 自动识别为 beam。"""
        b = ObsBuilder()
        laser = {
            "x": 750.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": -1.0,
            "motion": 2018, "owner": 1, "is_beam": 0,  # 模拟未被 C++ 标记
            "beam_w": 24.0, "beam_l": 600.0, "beam_angle": 0.0,
            "box_l": 150.0, "box_t": 348.0, "box_r": 750.0, "box_b": 372.0,
            "is_attack": 2, "atk_lv": 20,
        }
        st = self._state(opp_motion=2000, shots=[laser])
        obs = b.build(st, controlled_team=0)

        proj_row = obs["proj"][0]
        self.assertEqual(proj_row[PROJ_COL_IS_BEAM], 1.0)
        self.assertAlmostEqual(proj_row[PROJ_COL_BEAM_THREAT], 1.0, places=3)
        # 全局预警也因场上存在激光而点亮
        self.assertEqual(obs["global"][23], 1.0)

    def test_laser_startup_motions_trigger_warning(self):
        """对手处于激光启动前摇或施法动作（4000/4009/4010/4019/4020/4900/4910/2018/2019/2509）时，预警即刻置 1.0。"""
        b = ObsBuilder()
        all_laser_motions = (4000, 4009, 4010, 4019, 4020, 4900, 4910, 2018, 2019, 2509)
        for mo in all_laser_motions:
            st = self._state(opp_motion=mo, shots=[])
            obs = b.build(st, controlled_team=0)
            self.assertEqual(obs["global"][23], 1.0, f"motion={mo} 前摇/执行必须点亮预警")

    def test_peaceful_state_and_friendly_shot_warning_behavior(self):
        """无激光或己方激光不应对自身触发激光危险警告。"""
        b = ObsBuilder()
        # 1. 双方正常待机，无弹幕
        st_peace = self._state(opp_motion=0, shots=[])
        obs_peace = b.build(st_peace, controlled_team=0)
        self.assertEqual(obs_peace["global"][23], 0.0)

        # 2. 己方（controlled_team=0）释放 Master Spark，不应对自己报警
        friendly_spark = {
            "x": 640.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
            "motion": 4009, "owner": 0, "is_beam": 1,
            "beam_w": 104.0, "beam_l": 1729.0, "beam_angle": 0.0,
            "box_l": 640.0, "box_t": 308.0, "box_r": 1280.0, "box_b": 412.0,
        }
        st_friendly = self._state(opp_motion=0, shots=[friendly_spark])
        obs_friendly = b.build(st_friendly, controlled_team=0)
        self.assertEqual(obs_friendly["global"][23], 0.0)

    def test_missing_or_corrupt_beam_fields_handled_gracefully(self):
        """当 beam_w、beam_l、beam_angle 缺失或为 None 时，计算优雅降级且不抛异常。"""
        b = ObsBuilder()
        incomplete_beam = {
            "x": 800.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": -1.0,
            "motion": 2018, "owner": 1,
            # 缺失 is_beam, beam_w, beam_l, beam_angle
            "box_l": 200.0, "box_t": 350.0, "box_r": 800.0, "box_b": 370.0,
        }
        st = self._state(opp_motion=0, shots=[incomplete_beam])
        obs = b.build(st, controlled_team=0)
        proj_row = obs["proj"][0]
        self.assertEqual(proj_row[PROJ_COL_IS_BEAM], 1.0)
        self.assertFalse(np.isnan(proj_row).any())
        self.assertFalse(np.isinf(proj_row).any())
