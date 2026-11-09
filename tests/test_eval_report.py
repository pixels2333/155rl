"""issue 06 验收测试:标准化评估报告的统计口径与输出格式。

* 纯函数部分用合成 `MatchRecord` 验统计口径与 Markdown/JSON 输出;
* `play_match` 用脚本化环境 + 钉死策略验记录口径(实血差方向、破防计数、
  KO/计时判定、换人计数、时长折算)。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from env import action_space                                   # noqa: E402
from train.eval import (MatchRecord, compare_summaries, matches_needed,  # noqa: E402
                        mean_interval, play_match, proportions_diff_interval,
                        summarize, to_markdown, wilson_interval, write_reports)
from train.train_ppo import _OfflineEnv                        # noqa: E402

N_ACTIONS = int(action_space.N_ACTIONS)


def tag_action() -> int:
    for i in range(N_ACTIONS):
        _d, btn, _x, _y = action_space.decode(i)
        if btn >= 0 and btn < len(action_space.BUTTONS) and action_space.BUTTONS[btn] == "Tag":
            return i
    return 0


def rec(result="win", kind="ko", frames=1800, hp_diff=3000, tags=4,
        guard_breaks=1, com_level=0):
    return MatchRecord(result=result, kind=kind, frames=frames, secs=frames / 30.0,
                       hp_diff=hp_diff, tags=tags, guard_breaks=guard_breaks,
                       com_level=com_level)


class TestSummarize(unittest.TestCase):
    def test_rates_and_means(self):
        records = [rec("win", "ko", 1800, 4000, 5, 2),
                   rec("win", "time", 4455, 800, 3, 0),
                   rec("lose", "ko", 2400, -5000, 1, 0),
                   rec("draw", "time", 4455, 0, 2, 1)]
        s = summarize(records, com_level=0)
        self.assertEqual(s["matches"], 4)
        self.assertEqual(s["com_level"], 1)                 # 对外 1..5
        self.assertEqual(s["wins"], 2)
        self.assertEqual(s["losses"], 1)
        self.assertEqual(s["draws"], 1)
        self.assertAlmostEqual(s["win_rate"], 2 / 3)        # 只按分出胜负的算
        self.assertAlmostEqual(s["draw_rate"], 1 / 4)       # 超时/平局按总局数算
        self.assertEqual(s["ko_wins"], 1)
        self.assertEqual(s["time_wins"], 1)
        self.assertAlmostEqual(s["ko_rate"], 1 / 2)
        self.assertAlmostEqual(s["mean_hp_diff"], -50.0)    # (4000+800-5000+0)/4
        self.assertAlmostEqual(s["mean_frames"], (1800 + 4455 + 2400 + 4455) / 4)
        self.assertAlmostEqual(s["tags_per_match"], 11 / 4)
        self.assertAlmostEqual(s["guard_breaks_per_match"], 3 / 4)

    def test_empty(self):
        s = summarize([], com_level=2)
        self.assertEqual(s["matches"], 0)
        self.assertEqual(s["win_rate"], 0.0)
        self.assertEqual(s["mean_frames"], 0.0)

    def test_all_draws_have_zero_decided_rate(self):
        s = summarize([rec("draw", "time"), rec("draw", "time")], com_level=4)
        self.assertEqual(s["decided"], 0)
        self.assertEqual(s["win_rate"], 0.0)
        self.assertAlmostEqual(s["draw_rate"], 1.0)


    def test_counts_means_default_zero(self):
        s = summarize([rec("win"), rec("lose")], com_level=0)
        self.assertAlmostEqual(s["combo_per_match"], 0.0)
        self.assertAlmostEqual(s["opp_combo_per_match"], 0.0)
        self.assertAlmostEqual(s["combo_diff_per_match"], 0.0)

    def test_counts_means_from_records(self):
        a = rec("win")
        a.counts = {"combo": 20, "opp_combo": 8}
        b = rec("lose")
        b.counts = {"combo": 10, "opp_combo": 14}
        s = summarize([a, b], com_level=0)
        self.assertAlmostEqual(s["combo_per_match"], 15.0)
        self.assertAlmostEqual(s["opp_combo_per_match"], 11.0)
        self.assertAlmostEqual(s["combo_diff_per_match"], 4.0)
        self.assertAlmostEqual(s["combo_diff_win"], 12.0)
        self.assertAlmostEqual(s["combo_diff_lose"], -4.0)
        self.assertEqual(len(s["combo_diff_win_ci"]), 2)
        self.assertEqual(len(s["combo_diff_lose_ci"]), 2)

    def test_markdown_contains_required_rows(self):
        s = summarize([rec("win"), rec("lose")], com_level=3)
        md = to_markdown(s, ckpt="checkpoints/ppo_000400.pt")
        for key in ("胜率", "平局", "实血差", "回合帧数", "换人", "破防", "checkpoint"):
            self.assertIn(key, md, f"报告缺少 {key}")
        self.assertIn("| COM4 |", md)                 # com_level=3 -> 人类口径 COM4
        self.assertIn("50.0%", md)

    def test_write_reports_emits_md_and_json(self):
        s = summarize([rec("win"), rec("win")], com_level=0)
        with tempfile.TemporaryDirectory() as td:
            md_path, js_path = write_reports(s, td, ckpt="x.pt")
            body = Path(md_path).read_text(encoding="utf-8")
            data = json.loads(Path(js_path).read_text(encoding="utf-8"))
        self.assertIn("胜率", body)
        self.assertEqual(data["matches"], 2)
        self.assertAlmostEqual(data["win_rate"], 1.0)


class _StubDist:
    def __init__(self, logits):
        self.logits = logits
        self.probs = torch.softmax(logits, dim=-1)


class _StubNet:
    """把策略钉死到指定动作,用于验证 eval 的统计口径而不是策略本身。"""

    def __init__(self, action: int):
        self.action = int(action)

    def init_hidden(self, batch_size=1):
        return None

    def forward(self, batch, hidden=None, seq=False):
        logits = torch.zeros(1, N_ACTIONS)
        logits[0, self.action] = 100.0
        return logits, torch.zeros(1, 1), hidden

    def masked_dist(self, logits, mask):
        return _StubDist(logits)


class _ScriptedEnv:
    """两步一局:第 1 步对手被破防(sp_max 下降),第 2 步终局。"""

    team = 0
    action_repeat = 2

    def __init__(self):
        self.calls = 0
        self.obs = _OfflineEnv()._obs()      # step 必须回传合法观测

    def _info(self, teams, time_value):
        return {"team": 0, "battle_state": 64, "time": time_value,
                "state_snapshot": {"winner": 1,
                                   "teams": [{"life": t[0], "sp_max": t[1]} for t in teams]}}

    def step(self, action):
        self.calls += 1
        if self.calls == 1:
            info = self._info([(9000, 5000), (7000, 5000)], 8800)
            return self.obs, 0.1, False, False, info
        info = self._info([(3000, 5000), (1000, 4000)], 8700)
        return self.obs, 0.4, True, False, info


class TestPlayMatch(unittest.TestCase):
    def test_records_orientation_guard_break_and_kind(self):
        env = _ScriptedEnv()
        rec = play_match(_StubNet(tag_action()), env, _OfflineEnv()._obs(), {},
                         com_level=2)
        self.assertEqual(rec.result, "win")                 # winner=1 且 team=0
        self.assertEqual(rec.kind, "ko")                    # time>0
        self.assertEqual(rec.frames, 2)
        self.assertAlmostEqual(rec.secs, 2 * 2 / 60.0)      # action_repeat=2, 60fps
        self.assertEqual(rec.hp_diff, 3000 - 1000)          # 我方实血 - 对方实血
        self.assertEqual(rec.guard_breaks, 1)               # 对手 sp_max 5000->4000(第 1->2 步)
        self.assertEqual(rec.tags, 0)                       # Tag 动作已移除, 换人按键计数为 0
        self.assertEqual(rec.com_level, 2)
        self.assertAlmostEqual(rec.ret, 0.5)

    def test_reports_lose_when_winner_is_opponent(self):
        class _Lose(_ScriptedEnv):
            def _info(self, teams, time_value):
                info = super()._info(teams, time_value)
                info["state_snapshot"]["winner"] = 2
                info["time"] = 0
                return info

        rec = play_match(_StubNet(0), _Lose(), _OfflineEnv()._obs(), {}, com_level=0)
        self.assertEqual(rec.result, "lose")
        self.assertEqual(rec.kind, "time")
        self.assertEqual(rec.guard_breaks, 1, "只看对手 sp_max,不看自己的")

    def test_play_match_carries_combat_counts(self):
        class _Counted(_ScriptedEnv):
            def _info(self, teams, time_value):
                info = super()._info(teams, time_value)
                info["combat_counts"] = {"combo": 17, "opp_combo": 9}
                return info

        rec = play_match(_StubNet(0), _Counted(), _OfflineEnv()._obs(), {}, com_level=0)
        self.assertEqual(rec.counts.get("combo"), 17)
        self.assertEqual(rec.counts.get("opp_combo"), 9)
        s = summarize([rec], com_level=0)
        self.assertAlmostEqual(s["combo_diff_per_match"], 8.0)


if __name__ == "__main__":
    unittest.main()


class TestConfidenceIntervals(unittest.TestCase):
    """评估噪声的量化工具:没有区间,20 局的口径会把噪声当结论。"""

    def test_wilson_contains_point_estimate(self):
        for wins, n in [(0, 20), (8, 20), (19, 50), (10, 30), (20, 20)]:
            lo, hi = wilson_interval(wins, n)
            self.assertLessEqual(lo, wins / n + 1e-9)
            self.assertGreaterEqual(hi, wins / n - 1e-9)
            self.assertGreaterEqual(lo, 0.0)
            self.assertLessEqual(hi, 1.0)

    def test_wilson_narrows_with_more_games(self):
        """同样的胜率,局数越多区间越窄——这正是 20 局不可用的原因。"""
        small = wilson_interval(8, 20)
        large = wilson_interval(80, 200)
        self.assertLess(large[1] - large[0], small[1] - small[0])

    def test_wilson_halfwidth_at_40pct_20_games_is_about_22pp(self):
        lo, hi = wilson_interval(8, 20)
        half = (hi - lo) / 2.0
        self.assertGreater(half, 0.18)
        self.assertLess(half, 0.26)

    def test_wilson_degenerate_inputs(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 0.0))
        self.assertEqual(wilson_interval(0, 10)[0], 0.0)

    def test_mean_interval_requires_two_samples(self):
        self.assertEqual(mean_interval([]), (0.0, 0.0))
        self.assertEqual(mean_interval([5.0]), (0.0, 0.0))
        lo, hi = mean_interval([1.0, 2.0, 3.0])
        self.assertLess(lo, 2.0)
        self.assertGreater(hi, 2.0)
        self.assertAlmostEqual((lo + hi) / 2.0, 2.0, places=6)

    def test_diff_interval_flags_clear_separation(self):
        stat = proportions_diff_interval(18, 20, 2, 20)
        self.assertTrue(stat["significant"])
        self.assertGreater(stat["diff"], 0)

    def test_diff_interval_refuses_noise(self):
        """本仓库真实踩过的坑:38% vs 30%(50/40 局)不能算差异。"""
        stat = proportions_diff_interval(12, 40, 19, 50)
        self.assertFalse(stat["significant"])
        self.assertLess(stat["lo"], 0.0)
        self.assertGreater(stat["hi"], 0.0)

    def test_diff_interval_needs_both_samples(self):
        self.assertIsNone(proportions_diff_interval(1, 0, 1, 5))

    def test_matches_needed_scales_with_precision(self):
        coarse = matches_needed(0.4, 0.10)
        fine = matches_needed(0.4, 0.05)
        self.assertGreater(fine, coarse)
        self.assertGreater(coarse, 50)          # ±10pp 也需要上百局
        self.assertGreaterEqual(matches_needed(0.0, 0.05), 0)


class TestCompareSummaries(unittest.TestCase):
    def test_noise_is_reported_as_not_significant(self):
        old = {"wins": 19, "decided": 50, "mean_hp_diff": -1491}
        new = {"wins": 12, "decided": 40, "mean_hp_diff": -2075}
        out = compare_summaries(old, new)
        self.assertIn("无显著差异", out["verdict"])
        self.assertAlmostEqual(out["hp_diff_delta"], -584.0, places=6)

    def test_clear_gain_is_reported_as_significant(self):
        old = {"wins": 2, "decided": 20, "mean_hp_diff": -3000}
        new = {"wins": 18, "decided": 20, "mean_hp_diff": 500}
        out = compare_summaries(old, new)
        self.assertIn("显著变强", out["verdict"])

    def test_empty_samples_are_rejected(self):
        out = compare_summaries({"wins": 0}, {"wins": 0})
        self.assertIn("样本不足", out["verdict"])


class TestSummaryCarriesIntervals(unittest.TestCase):
    def test_summarize_emits_both_intervals(self):
        records = [rec("win") for _ in range(8)] + [rec("lose") for _ in range(12)]
        s = summarize(records)
        self.assertEqual(len(s["win_rate_ci"]), 2)
        self.assertEqual(len(s["mean_hp_diff_ci"]), 2)
        lo, hi = s["win_rate_ci"]
        self.assertLessEqual(lo, 0.4)
        self.assertGreaterEqual(hi, 0.4)

    def test_markdown_shows_intervals(self):
        s = summarize([rec("win") for _ in range(8)] + [rec("lose") for _ in range(12)])
        md = to_markdown(s, "ckpt.pt")
        self.assertIn("95% CI", md)
        self.assertEqual(md.count("95% CI"), 2)


class DeterministicPolicySeam(unittest.TestCase):
    """Ticket 01: 确定性策略评估切入点。

    断言:
    1. 在相同观测输入下,deterministic=True 始终返回唯一确定的最高概率动作 (argmax);
    2. 确定性决策不调用随机数采样 (关闭 Categorical 随机探索采样);
    3. 确定性决策严格遵循 action_mask (即使被遮罩的动作原始 logit 更大也不被选取);
    4. 评估循环中多步执行在相同环境转换下具有严格的确定性复现能力。
    """

    NET_CFG = {
        "motion_vocab": 4096,
        "motion_embed": 16,
        "team_hidden": 32,
        "global_hidden": 16,
        "proj_hidden": 16,
        "fuse_hidden": 32,
        "lstm_hidden": 32,
        "lstm_layers": 1,
    }

    def setUp(self):
        from agents.ppo_agent import PolicyNet
        torch.manual_seed(42)
        self.net = PolicyNet(self.NET_CFG).eval()
        self.obs = _OfflineEnv()._obs()

    def test_deterministic_action_equals_masked_argmax(self):
        from agents.ppo_agent import to_batch
        h = self.net.init_hidden(1)
        batch = {k: v.unsqueeze(0) for k, v in to_batch(self.obs).items()}
        with torch.no_grad():
            logits, _, _ = self.net.forward(batch, h, seq=False)
            dist = self.net.masked_dist(logits, batch["action_mask"])
            expected = int(dist.logits.argmax(dim=-1).item())
            action, _, _, _ = self.net.act(self.obs, h, deterministic=True)
        self.assertEqual(int(action), expected)

    def test_deterministic_bypasses_sampling_rng(self):
        from unittest.mock import patch
        from torch.distributions import Categorical
        h = self.net.init_hidden(1)
        orig_sample = Categorical.sample
        calls = []

        def spy_sample(dist_self, *args, **kwargs):
            calls.append(1)
            return orig_sample(dist_self, *args, **kwargs)

        with patch.object(Categorical, "sample", spy_sample):
            # deterministic=True 不应该触发 Categorical.sample
            self.net.act(self.obs, h, deterministic=True)
            self.assertEqual(len(calls), 0, "确定性决策不应调用 Categorical.sample")

            # deterministic=False 必须触发 Categorical.sample
            self.net.act(self.obs, h, deterministic=False)
            self.assertEqual(len(calls), 1, "随机采样必须调用 Categorical.sample")

    def test_deterministic_respects_action_mask(self):
        from agents.ppo_agent import to_batch
        h = self.net.init_hidden(1)
        batch = {k: v.unsqueeze(0) for k, v in to_batch(self.obs).items()}
        with torch.no_grad():
            logits, _, _ = self.net.forward(batch, h, seq=False)
            top_raw_action = int(logits.argmax(dim=-1).item())

        # 构造把 top_raw_action 屏蔽掉的掩码
        mask = np.ones(N_ACTIONS, dtype=bool)
        mask[top_raw_action] = False
        masked_obs = dict(self.obs)
        masked_obs["action_mask"] = mask

        with torch.no_grad():
            action, _, _, _ = self.net.act(masked_obs, h, deterministic=True)
        self.assertNotEqual(int(action), top_raw_action, "确定性决策选用了被 mask 的非法动作")
        self.assertTrue(mask[int(action)], "选中的动作必须在合法掩码内")

    def test_deterministic_repeated_invariance(self):
        h = self.net.init_hidden(1)
        results = []
        for _ in range(20):
            torch.manual_seed(np.random.randint(0, 100000))
            act, _, _, _ = self.net.act(self.obs, h, deterministic=True)
            results.append(int(act))
        self.assertTrue(all(a == results[0] for a in results), "不同种子下确定性动作必须完全恒定")

    def test_play_match_deterministic_replay(self):
        env1 = _ScriptedEnv()
        rec1 = play_match(self.net, env1, self.obs, {}, deterministic=True)
        env2 = _ScriptedEnv()
        rec2 = play_match(self.net, env2, self.obs, {}, deterministic=True)
        self.assertEqual(rec1.result, rec2.result)
        self.assertEqual(rec1.frames, rec2.frames)
        self.assertEqual(rec1.hp_diff, rec2.hp_diff)
        self.assertEqual(rec1.tags, rec2.tags)

    def test_collect_deterministic_respects_action_mask(self):
        from train.train_ppo import Stats, collect
        h = self.net.init_hidden(1)
        stats = Stats()
        # 强制将 actor 的最高 raw logit 偏置到 action 0
        with torch.no_grad():
            self.net.actor.bias.fill_(-100.0)
            self.net.actor.bias[0] = 100.0

        mask = np.ones(N_ACTIONS, dtype=bool)
        mask[0] = False  # 屏蔽 action 0
        masked_obs = dict(self.obs)
        masked_obs["action_mask"] = mask

        roll, _, _, _ = collect(self.net, _OfflineEnv(), n_steps=1, hidden=h,
                                stats=stats, obs=masked_obs, deterministic=True)
        chosen = int(roll.actions[0])
        self.assertNotEqual(chosen, 0, "collect(deterministic=True) 选用了被 mask 屏蔽的非法动作 0")
        self.assertTrue(mask[chosen], f"collect 选中的动作 {chosen} 必须在 action_mask 合法范围内")

    def test_evaluator_eval_mode_and_summary(self):
        from train.train_ppo import Evaluator
        self.net.train()
        self.assertTrue(self.net.training)
        evaluator = Evaluator(env=_OfflineEnv(max_episode_steps=4), default_episodes=2)
        summary = evaluator.run(self.net, episodes=2, deterministic=True)
        # 评估后必须恢复原训练状态
        self.assertTrue(self.net.training)
        self.assertEqual(summary["matches"], 2)
        self.assertIn("win_rate", summary)
        self.assertIn("win_rate_ci", summary)
        self.assertIn("mean_hp_diff", summary)
        lo, hi = summary["win_rate_ci"]
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 1.0)
        self.assertLessEqual(lo, hi)

    def test_evaluator_requires_env(self):
        from train.train_ppo import Evaluator
        evaluator = Evaluator(env=None)
        with self.assertRaises(ValueError):
            evaluator.run(self.net, env=None)


