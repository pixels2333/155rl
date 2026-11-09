"""issue 06 验收测试:课程晋级调度器的窗口统计、晋级阈值与退火逻辑。

只测纯逻辑(不碰游戏):窗口统计、晋级条件、混合难度轮转、奖励/熵退火、
断点序列化。实机写 `com_difficulty` 的那一层由 `BattleOps.set_com_difficulty`
负责,另有集成脚本验证。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from train.curriculum import DEFAULT_STAGES, Curriculum, Stage  # noqa: E402
from train.train_ppo import (_anneal, _curriculum_mode,  # noqa: E402
                             _live_com_difficulty, _target_level)


def play(cur: Curriculum, n: int, result: str) -> None:
    """在**当前**难度上打 n 局。"""
    for _ in range(n):
        cur.observe(result, cur.pick_com_level())


class TestStageDefaults(unittest.TestCase):
    def test_default_stages_cover_com1_to_com5_without_gaps(self):
        levels = [lv for s in DEFAULT_STAGES for lv in s.com_levels]
        self.assertEqual(sorted(levels), [0, 1, 2, 3, 4])

    def test_default_thresholds_match_ticket(self):
        self.assertAlmostEqual(DEFAULT_STAGES[0].promote_win_rate, 0.80)
        self.assertAlmostEqual(DEFAULT_STAGES[1].promote_win_rate, 0.75)
        self.assertIsNone(DEFAULT_STAGES[2].promote_win_rate)   # 最终阶段不再晋级


class TestWindowStats(unittest.TestCase):
    def test_win_rate_uses_decided_only(self):
        cur = Curriculum(min_decided=1)
        play(cur, 3, "win")
        play(cur, 1, "draw")
        play(cur, 1, "lose")
        st = cur.stats()
        self.assertEqual(st["matches"], 5)
        self.assertEqual(st["decided"], 4)
        self.assertAlmostEqual(st["win_rate"], 3 / 4)

    def test_window_bounds_history(self):
        cur = Curriculum(window=10, min_decided=1)
        play(cur, 8, "lose")
        play(cur, 5, "win")
        st = cur.stats()
        self.assertEqual(st["matches"], 10)           # 窗口截断
        self.assertAlmostEqual(st["win_rate"], 5 / 10)

    def test_no_episodes_means_zero(self):
        cur = Curriculum()
        st = cur.stats()
        self.assertEqual(st["matches"], 0)
        self.assertEqual(st["win_rate"], 0.0)
        self.assertFalse(cur.should_promote())


class TestPromotion(unittest.TestCase):
    def test_promotes_at_threshold(self):
        cur = Curriculum(min_decided=10)
        play(cur, 9, "win")
        self.assertFalse(cur.should_promote(), "样本不足不应晋级")
        play(cur, 2, "win")                            # 11 局 · 100%
        self.assertTrue(cur.should_promote())
        self.assertTrue(cur.promote())
        self.assertEqual(cur.stage_index, 1)
        self.assertEqual(cur.com_levels, tuple(DEFAULT_STAGES[1].com_levels))

    def test_no_promotion_below_threshold(self):
        cur = Curriculum(min_decided=10)
        play(cur, 6, "win")
        play(cur, 6, "lose")                           # 50% < 80%
        self.assertFalse(cur.should_promote())

    def test_promotion_uses_only_current_stage_episodes(self):
        """晋级后窗口里还留着旧阶段的 100% 胜率,不能被它带着继续晋级。"""
        cur = Curriculum(min_decided=10)
        play(cur, 12, "win")
        cur.promote()
        play(cur, 1, "lose")
        self.assertFalse(cur.should_promote())

    def test_promote_stops_at_last_stage(self):
        cur = Curriculum(min_decided=1)
        for _ in range(len(DEFAULT_STAGES) - 1):
            play(cur, 2, "win")
            self.assertTrue(cur.promote())
        play(cur, 2, "win")
        self.assertFalse(cur.should_promote())
        self.assertFalse(cur.promote())
        self.assertEqual(cur.stage_index, len(DEFAULT_STAGES) - 1)

    def test_draws_do_not_count_as_decisions(self):
        cur = Curriculum(min_decided=10)
        play(cur, 20, "draw")
        self.assertEqual(cur.stats()["decided"], 0)
        self.assertFalse(cur.should_promote())


class TestDifficultyRotation(unittest.TestCase):
    def test_mixed_stage_rotates_deterministically(self):
        cur = Curriculum(min_decided=1)
        play(cur, 12, "win")
        cur.promote()                                  # COM2/3 混合
        seq = [cur.pick_com_level() for _ in range(6)]
        self.assertEqual(seq, [1, 2, 1, 2, 1, 2])

    def test_single_level_stage_is_constant(self):
        cur = Curriculum(min_decided=1)
        self.assertEqual({cur.pick_com_level() for _ in range(5)}, {0})


class TestAnnealing(unittest.TestCase):
    def test_last_stage_turns_off_approach_shaping(self):
        cur = Curriculum(min_decided=1)
        self.assertEqual(cur.reward_overrides(), {})
        for _ in range(len(DEFAULT_STAGES) - 1):
            play(cur, 2, "win")
            cur.promote()
        self.assertAlmostEqual(cur.reward_overrides().get("w_approach", -1.0), 0.0)

    def test_entropy_scale_decays_per_stage(self):
        cur = Curriculum(min_decided=1)
        scales = [cur.entropy_scale()]
        for _ in range(len(DEFAULT_STAGES) - 1):
            play(cur, 2, "win")
            cur.promote()
            scales.append(cur.entropy_scale())
        self.assertEqual(scales, sorted(scales, reverse=True))
        self.assertLess(scales[-1], scales[0])


class TestSerialization(unittest.TestCase):
    def test_roundtrip_keeps_stage_and_history(self):
        cur = Curriculum(min_decided=1)
        play(cur, 5, "win")
        cur.promote()
        play(cur, 3, "lose")
        d = cur.to_dict()
        back = Curriculum.from_dict(d)
        self.assertEqual(back.stage_index, cur.stage_index)
        self.assertEqual(back.stats(), cur.stats())

    def test_custom_stages_are_honoured(self):
        stages = [Stage(name="only", com_levels=(2,), promote_win_rate=None)]
        cur = Curriculum(stages=stages, min_decided=1)
        self.assertEqual(cur.com_levels, (2,))
        play(cur, 3, "win")
        self.assertFalse(cur.should_promote())


class TestStageBest(unittest.TestCase):
    def test_keeps_best_per_stage(self):
        cur = Curriculum(min_decided=1)
        self.assertTrue(cur.update_best("a.pt", 0.5))
        self.assertFalse(cur.update_best("b.pt", 0.4))
        self.assertTrue(cur.update_best("c.pt", 0.6))
        self.assertEqual(cur.best_checkpoint(), "c.pt")
        play(cur, 3, "win")
        cur.promote()
        self.assertIsNone(cur.best_checkpoint(), "新阶段从头计最优")
        self.assertTrue(cur.update_best("d.pt", 0.1))


class _FakeReward:
    def __init__(self):
        self.C = {"w_approach": 0.05}


class _FakeEnv:
    def __init__(self, com_level=0, team=0):
        self.reward_calc = _FakeReward()
        self.team = team
        self._com_level = com_level

    def _state(self):
        return {"teams": [{"com_difficulty": 0}, {"com_difficulty": self._com_level}]}


class _BrokenEnv(_FakeEnv):
    def _state(self):
        raise RuntimeError("无头模式")


class TestTrainerIntegration(unittest.TestCase):
    def test_mode_defaults_to_restart(self):
        self.assertEqual(_curriculum_mode({}), "restart")
        self.assertEqual(_curriculum_mode({"curriculum": {}}), "restart")
        self.assertEqual(_curriculum_mode({"curriculum": {"apply_mode": "live"}}), "live")
        self.assertEqual(_curriculum_mode({"curriculum": {"apply_mode": "??"}}), "restart")

    def test_target_level_restart_takes_first_live_rotates(self):
        stages = [Stage("mix", (1, 2), promote_win_rate=0.5)]
        cur = Curriculum(stages=stages, min_decided=1)
        self.assertEqual(_target_level(cur, "restart"), 1)
        # live 用 peek:纯函数,不推进轮转(逐局轮转由调用方 pick_com_level 负责)
        self.assertEqual([_target_level(cur, "live") for _ in range(3)], [1, 1, 1])
        self.assertEqual([cur.pick_com_level() for _ in range(3)], [1, 2, 1])

    def test_anneal_applies_reward_overrides_and_entropy(self):
        cur = Curriculum(min_decided=1)
        for _ in range(len(DEFAULT_STAGES) - 1):
            play(cur, 2, "win")
            cur.promote()
        env, tcfg = _FakeEnv(), {"entropy_coef": 0.01}
        _anneal(env, cur, 0.01, tcfg)
        self.assertEqual(env.reward_calc.C["w_approach"], 0.0)      # 末阶退火到 0
        self.assertAlmostEqual(tcfg["entropy_coef"], 0.01 * 0.4)

    def test_live_difficulty_reads_opponent_slot(self):
        self.assertEqual(_live_com_difficulty(_FakeEnv(com_level=3)), 3)
        self.assertEqual(_live_com_difficulty(_FakeEnv(com_level=1, team=1)), 0)
        self.assertIsNone(_live_com_difficulty(_BrokenEnv()))


if __name__ == "__main__":
    unittest.main()


class TestResumeAcrossConfigs(unittest.TestCase):
    """把一份 checkpoint 的课程状态拿到另一份配置(换难度阶段)时不能误判晋级。"""

    COM2_STAGES = [Stage("com2", (1,), promote_win_rate=0.80),
                   Stage("com3", (2,), promote_win_rate=0.75)]
    COM3_STAGES = [Stage("com3", (2,), promote_win_rate=0.75),
                   Stage("com4_5", (3, 4), promote_win_rate=None)]

    def _com2_state(self):
        cur = Curriculum(stages=self.COM2_STAGES, window=100, min_decided=30)
        for _ in range(40):
            cur.observe("win", com_level=1)
        return cur.to_dict()

    def test_history_from_other_difficulty_is_dropped(self):
        saved = self._com2_state()
        self.assertEqual(len(saved["history"]), 40)
        cur = Curriculum.from_dict(saved, stages=self.COM3_STAGES)
        self.assertEqual(cur._recent(), [],
                         "COM2 的胜率不能算进 COM3 阶段")
        self.assertFalse(cur.should_promote(), "带入旧战绩会立刻误判晋级")
        self.assertEqual(cur.current_level, 2, "当前难度必须重置到 COM3")
        self.assertEqual(cur.stage.com_levels, (2,))

    def test_same_config_keeps_history(self):
        saved = self._com2_state()
        cur = Curriculum.from_dict(saved, stages=self.COM2_STAGES)
        self.assertEqual(len(cur._recent()), 40)
        self.assertEqual(cur.current_level, 1)
        self.assertTrue(cur.should_promote(), "同一配置续训必须保留晋级进度")

    def test_stage_index_clamped_to_new_stages(self):
        saved = self._com2_state()
        saved["stage_index"] = 5
        cur = Curriculum.from_dict(saved, stages=self.COM3_STAGES)
        self.assertLess(cur.stage_index, len(self.COM3_STAGES))
        # 夹到最后一阶(com4_5)是允许的,但当前难度必须落在该阶段的难度里
        self.assertIn(cur.current_level, cur.stage.com_levels)
