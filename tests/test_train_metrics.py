"""训练统计口径测试:回合结束方式(KO / 打满计时)与奖励分项聚合。

这两个口径直接决定"胜率"的含义 —— 99s 回合下时间到按血量判胜会稀释胜率,
因此必须能稳定区分,不允许出现"把打满计时算成 KO"的回归。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from train.eval import episode_kind                                   # noqa: E402
from train.train_ppo import _comp_means, _comp_str                     # noqa: E402


class TestEpisodeKind(unittest.TestCase):
    def test_ko_keeps_positive_time(self):
        # Round_KO() 只关 enableTimeCount,不把 time 归零
        self.assertEqual(episode_kind({"time": 4310}), "ko")

    def test_timeover_has_zero_time(self):
        self.assertEqual(episode_kind({"time": 0}), "time")

    def test_negative_time_counts_as_timeover(self):
        self.assertEqual(episode_kind({"time": -3}), "time")

    def test_missing_time_defaults_to_time(self):
        self.assertEqual(episode_kind({}), "time")


class TestCompAggregation(unittest.TestCase):
    def test_mean_over_episodes_missing_keys_count_as_zero(self):
        eps = [{"comp": {"hp": 1.0, "regain": 0.5}},
               {"comp": {"hp": -1.0}},
               {"comp": {}}]
        got = _comp_means(eps)
        self.assertAlmostEqual(got["hp"], 0.0)
        self.assertAlmostEqual(got["regain"], 0.5 / 3)
        self.assertNotIn("corner", got)

    def test_empty_input(self):
        self.assertEqual(_comp_means([]), {})

    def test_comp_str_sorts_by_magnitude_and_drops_zeros(self):
        s = _comp_str({"hp": 0.5, "corner": 0.0, "terminal": 1.0, "regain": -0.25})
        self.assertEqual(s, "terminal+1.000,hp+0.500,regain-0.250")

    def test_comp_str_all_zero(self):
        self.assertEqual(_comp_str({"hp": 0.0}), "-")
        self.assertEqual(_comp_str({}), "-")


class TestEpisodeCapScalesWithActionRepeat(unittest.TestCase):
    """`--action-repeat` 覆盖时,单局**墙钟**时长必须保持不变。

    `max_episode_steps` 是决策步数上限,一步的墙钟长度与 action_repeat 成正比。
    配置里的步数是按配置自带的 ar 标定的,所以覆盖 ar 时必须等比缩放,否则
    改变的是"一局能打多久"这个与策略无关的变量。

    实证(2026-10-25):用 `action_repeat: 4` 的配置(mps=2400)跑 ar=2 评估,
    2400 步只有 80s,有 4 局恰好 frames=2400 被截成平局;
    而同配置按 ar=4 跑的评估帧数上限只有 1397、零平局 —— 两组胜率因此不可比。
    """

    CFG = {"env": {"action_repeat": 4, "max_episode_steps": 2400}}

    def _wall_seconds(self, env):
        return env.max_episode_steps * env.action_repeat / 60.0

    def test_no_override_keeps_config_values(self):
        from train.train_ppo import build_env
        env = build_env(self.CFG)
        try:
            self.assertEqual(env.action_repeat, 4)
            self.assertEqual(env.max_episode_steps, 2400)
        finally:
            env.close()

    def test_override_halving_repeat_doubles_steps(self):
        from train.train_ppo import build_env
        env = build_env(self.CFG, 2)
        try:
            self.assertEqual(env.action_repeat, 2)
            self.assertEqual(env.max_episode_steps, 4800)
            self.assertAlmostEqual(self._wall_seconds(env), 160.0)
        finally:
            env.close()

    def test_override_doubling_repeat_halves_steps(self):
        from train.train_ppo import build_env
        env = build_env(self.CFG, 8)
        try:
            self.assertEqual(env.max_episode_steps, 1200)
            self.assertAlmostEqual(self._wall_seconds(env), 160.0)
        finally:
            env.close()

    def test_wall_seconds_invariant_across_repeats(self):
        from train.train_ppo import build_env
        for ar in (1, 2, 4, 6, 8):
            env = build_env(self.CFG, ar)
            try:
                self.assertAlmostEqual(self._wall_seconds(env), 160.0,
                                       msg=f"action_repeat={ar} 改变了单局墙钟长度")
            finally:
                env.close()

    def test_override_equal_to_config_is_noop(self):
        from train.train_ppo import build_env
        env = build_env(self.CFG, 4)
        try:
            self.assertEqual(env.max_episode_steps, 2400)
        finally:
            env.close()


class CheckpointAuditSeam(unittest.TestCase):
    """Ticket 01: Checkpoint 完整元数据审计与确定性评估回路切入点。

    验收标准验证:
    1. 周期保存与 best.pt 必须包含所有元数据字典字段:
       update, global_step, best_eval_win_rate, eval_episodes, eval_ci_low, eval_ci_high,
       model_state_dict, optimizer_state_dict, lr_scheduler_state_dict;
    2. best.pt 中 best_eval_win_rate 恒为有效 float,严禁为 None;
    3. 仅当确定性评估胜率超过历史最高值时才更新保存 best.pt,采样方差绝不触发 best.pt 更新;
    4. 训练主循环支持通过 eval-interval 与 eval-episodes 配置周期触发评估回路;
    5. lr_scheduler_state_dict 完整持久化并在续训时成功还原状态。
    """

    REQUIRED_KEYS = {
        "update",
        "global_step",
        "best_eval_win_rate",
        "best_win_rate",
        "eval_episodes",
        "eval_ci_low",
        "eval_ci_high",
        "model_state_dict",
        "optimizer_state_dict",
        "lr_scheduler_state_dict",
        "com_level",
    }

    def _make_cfg(self, tmp_dir: str, eval_interval: int = 1, eval_episodes: int = 2) -> dict:
        return {
            "env": {"team": 0, "action_repeat": 2, "difficulty": 1,
                    "max_episode_steps": 4, "frame_timeout": 1.0},
            "network": {
                "motion_vocab": 4096, "motion_embed": 16, "team_hidden": 32,
                "global_hidden": 16, "proj_hidden": 16, "fuse_hidden": 32,
                "lstm_hidden": 32, "lstm_layers": 1,
            },
            "train": {
                "total_updates": 2, "n_steps": 8, "seq_len": 2, "epochs": 1,
                "minibatch_seqs": 2, "gamma": 0.99, "gae_lambda": 0.95,
                "clip_range": 0.2, "value_coef": 0.5, "entropy_coef": 0.01,
                "max_grad_norm": 0.5, "lr": 1e-3, "target_kl": 0.05, "seed": 0,
                "eval_interval": eval_interval,
                "eval_episodes": eval_episodes,
            },
            "checkpoint": {"dir": tmp_dir, "save_every": 1, "keep": 2},
            "logging": {"tensorboard": str(Path(tmp_dir) / "tb")},
            "curriculum": {"enabled": False},
        }

    def test_checkpoint_contains_all_required_metadata(self):
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_cfg(tmp, eval_interval=1, eval_episodes=2)
            train(cfg, env=_OfflineEnv(max_episode_steps=4), verbose=False)

            periodic_ckpt = Path(tmp) / "ppo_000001.pt"
            self.assertTrue(periodic_ckpt.exists(), f"周期保存文件不存在: {periodic_ckpt}")
            periodic_meta = torch.load(periodic_ckpt, map_location="cpu")

            for k in self.REQUIRED_KEYS:
                self.assertIn(k, periodic_meta, f"周期 checkpoint 缺少必需字段: {k}")

            self.assertIsNotNone(periodic_meta["best_eval_win_rate"])
            self.assertIsInstance(periodic_meta["best_eval_win_rate"], float)
            self.assertIsNotNone(periodic_meta["best_win_rate"])
            self.assertIsInstance(periodic_meta["best_win_rate"], float)
            self.assertIsInstance(periodic_meta["global_step"], int)
            self.assertGreater(periodic_meta["global_step"], 0)
            self.assertIsInstance(periodic_meta["eval_ci_low"], float)
            self.assertIsInstance(periodic_meta["eval_ci_high"], float)
            self.assertIn("base_lrs", periodic_meta["lr_scheduler_state_dict"])

            best_ckpt = Path(tmp) / "best.pt"
            self.assertTrue(best_ckpt.exists(), "触发评估后必须保存 best.pt")
            best_meta = torch.load(best_ckpt, map_location="cpu")
            for k in self.REQUIRED_KEYS:
                self.assertIn(k, best_meta, f"best.pt 缺少必需字段: {k}")
            self.assertIsNotNone(best_meta["best_eval_win_rate"])
            self.assertIsInstance(best_meta["best_eval_win_rate"], float)
            self.assertIsNotNone(best_meta["best_win_rate"])
            self.assertIsInstance(best_meta["best_win_rate"], float)

    def test_best_pt_only_updates_on_eval_improvement(self):
        import tempfile
        import torch
        from unittest.mock import patch
        from train.train_ppo import _OfflineEnv, train, Evaluator

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_cfg(tmp, eval_interval=1, eval_episodes=2)
            cfg["train"]["total_updates"] = 3

            eval_results = [
                {"matches": 2, "decided": 2, "wins": 1, "losses": 1, "draws": 0,
                 "win_rate": 0.50, "win_rate_ci": [0.20, 0.80], "mean_hp_diff": 500.0},
                {"matches": 2, "decided": 2, "wins": 0, "losses": 2, "draws": 0,
                 "win_rate": 0.00, "win_rate_ci": [0.00, 0.40], "mean_hp_diff": -2000.0},
                {"matches": 2, "decided": 2, "wins": 2, "losses": 0, "draws": 0,
                 "win_rate": 1.00, "win_rate_ci": [0.60, 1.00], "mean_hp_diff": 4000.0},
            ]
            call_idx = [0]

            def fake_eval(self_eval, net, env=None, episodes=None, com_level=0, deterministic=True, on_record=None):
                res = eval_results[call_idx[0]]
                call_idx[0] += 1
                return res

            with patch.object(Evaluator, "run", fake_eval):
                train(cfg, env=_OfflineEnv(max_episode_steps=4), verbose=False)

            best_ckpt = Path(tmp) / "best.pt"
            self.assertTrue(best_ckpt.exists(), "未保存 best.pt")
            best_meta = torch.load(best_ckpt, map_location="cpu")

            # 最终的 best_eval_win_rate 必须是第 3 次的 1.0, 且在第 2 次(0.0)时绝对没有被覆盖
            self.assertAlmostEqual(best_meta["best_eval_win_rate"], 1.00)
            self.assertAlmostEqual(best_meta["best_win_rate"], 1.00)
            self.assertEqual(best_meta["update"], 3)
            self.assertAlmostEqual(best_meta["eval_ci_low"], 0.60)
            self.assertAlmostEqual(best_meta["eval_ci_high"], 1.00)

    def test_checkpoint_metadata_when_eval_interval_zero(self):
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_cfg(tmp, eval_interval=0, eval_episodes=2)
            train(cfg, env=_OfflineEnv(max_episode_steps=4), verbose=False)

            periodic_ckpt = Path(tmp) / "ppo_000001.pt"
            self.assertTrue(periodic_ckpt.exists())
            data = torch.load(periodic_ckpt, map_location="cpu")
            for k in self.REQUIRED_KEYS:
                self.assertIn(k, data, f"eval_interval=0 时 checkpoint 仍需包含字段: {k}")
            self.assertIsInstance(data["best_eval_win_rate"], float)
            self.assertIsInstance(data["best_win_rate"], float)
            # 评估未开启时无 best.pt 生成
            self.assertFalse((Path(tmp) / "best.pt").exists())

    def test_lr_scheduler_state_dict_persistence_and_resume(self):
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_cfg(tmp, eval_interval=1, eval_episodes=2)
            cfg["train"]["lr_anneal"] = True
            train(cfg, env=_OfflineEnv(max_episode_steps=4), verbose=False)

            ckpt_path = Path(tmp) / "ppo_000002.pt"
            self.assertTrue(ckpt_path.exists())
            ckpt_data = torch.load(ckpt_path, map_location="cpu")
            sched_sd = ckpt_data["lr_scheduler_state_dict"]
            self.assertIn("last_epoch", sched_sd)
            self.assertEqual(sched_sd["last_epoch"], 2)

            # 续训 resume 测试
            cfg2 = self._make_cfg(tmp, eval_interval=1, eval_episodes=2)
            cfg2["train"]["total_updates"] = 2
            train(cfg2, env=_OfflineEnv(max_episode_steps=4), verbose=False, init_state=ckpt_data)

            resumed_ckpt = Path(tmp) / "ppo_000004.pt"
            self.assertTrue(resumed_ckpt.exists())
            resumed_data = torch.load(resumed_ckpt, map_location="cpu")
            self.assertEqual(resumed_data["update"], 4)
            self.assertEqual(resumed_data["lr_scheduler_state_dict"]["last_epoch"], 4)


class LrSchedulerResumeTest(unittest.TestCase):
    """Ticket 04: 学习率调度一致性协议与消除 LR Bounce 回弹测试。

    验收标准验证:
    1. 训练脚本支持从 checkpoint 中读取并完整加载 lr_scheduler_state_dict;
    2. 续训测试中断点第 1 个 update 的实际学习率与保存时严格一致,无 2.0e-4 阶跃回弹;
    3. 命令行 / 参数支持 --no-lr-decay 与 --lr-mode {anneal, constant},开启后全程维持指定常数学习率;
    4. 模拟保存、恢复并断言后续 5 步学习率变化趋势严格平滑;
    5. 异构权重(迁移/扩容)续训时隔离优化器与调度器状态,同构续训时完整恢复状态。
    """

    def _make_cfg(self, tmp_dir: str, total_updates: int = 20, lr: float = 2.0e-4,
                   lr_mode: str = "anneal", lr_final_scale: float = 0.1) -> dict:
        return {
            "env": {"team": 0, "action_repeat": 2, "difficulty": 1,
                    "max_episode_steps": 4, "frame_timeout": 1.0},
            "network": {
                "motion_vocab": 4096, "motion_embed": 16, "team_hidden": 32,
                "global_hidden": 16, "proj_hidden": 16, "fuse_hidden": 32,
                "lstm_hidden": 32, "lstm_layers": 1,
            },
            "train": {
                "total_updates": total_updates, "n_steps": 8, "seq_len": 2, "epochs": 1,
                "minibatch_seqs": 2, "gamma": 0.99, "gae_lambda": 0.95,
                "clip_range": 0.2, "value_coef": 0.5, "entropy_coef": 0.01,
                "max_grad_norm": 0.5, "lr": lr, "target_kl": 0.05, "seed": 0,
                "eval_interval": 0, "eval_episodes": 0,
                "lr_mode": lr_mode, "lr_anneal": (lr_mode == "anneal"),
                "lr_final_scale": lr_final_scale,
            },
            "checkpoint": {"dir": tmp_dir, "save_every": 5, "keep": 5},
            "logging": {"tensorboard": str(Path(tmp_dir) / "tb")},
            "curriculum": {"enabled": False},
        }

    def test_anneal_resume_5_steps_strictly_smooth_no_bounce(self):
        """模拟保存、恢复并断言后续 5 步学习率变化趋势严格平滑,无 2.0e-4 阶跃回弹。"""
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg1 = self._make_cfg(tmp, total_updates=20, lr=2.0e-4, lr_mode="anneal", lr_final_scale=0.1)
            stats1 = train(cfg1, updates=5, env=_OfflineEnv(max_episode_steps=4), verbose=False)

            self.assertEqual(len(stats1.learning_rates), 5)
            lr_list_1 = stats1.learning_rates
            lr5 = lr_list_1[-1]

            ckpt_path = Path(tmp) / "ppo_000005.pt"
            self.assertTrue(ckpt_path.exists())
            ckpt_data = torch.load(ckpt_path, map_location="cpu")
            self.assertIn("lr_scheduler_state_dict", ckpt_data)
            self.assertEqual(ckpt_data["lr_scheduler_state_dict"]["last_epoch"], 5)

            # 续训:继续跑 5 步 (updates 6..10)
            cfg2 = self._make_cfg(tmp, total_updates=20, lr=2.0e-4, lr_mode="anneal", lr_final_scale=0.1)
            stats2 = train(cfg2, updates=5, env=_OfflineEnv(max_episode_steps=4),
                           verbose=False, init_state=ckpt_data)

            self.assertEqual(len(stats2.learning_rates), 5)
            lr_list_2 = stats2.learning_rates
            lr6 = lr_list_2[0]

            # 断言 1: 第 6 步学习率绝对没有回弹到起点 2.0e-4
            self.assertLess(lr6, lr5)
            self.assertLess(lr6, 2.0e-4 * 0.85)

            # 断言 2: 断点跨步差值与断点前单步差值严格一致 (严格平滑)
            delta_before = lr_list_1[-2] - lr_list_1[-1]  # lr4 - lr5
            delta_resume = lr5 - lr6                       # lr5 - lr6
            self.assertAlmostEqual(delta_resume, delta_before, places=9,
                                   msg=f"断点跨步差值 {delta_resume} 与前一步差值 {delta_before} 不一致")

            # 断言 3: 续训后 5 步的斜率严格一致且单调递减
            for i in range(len(lr_list_2) - 1):
                step_delta = lr_list_2[i] - lr_list_2[i + 1]
                self.assertAlmostEqual(step_delta, delta_before, places=9)

            # 断言 4: 完整 10 步学习率曲线严格单调递减
            full_lrs = lr_list_1 + lr_list_2
            self.assertEqual(len(full_lrs), 10)
            self.assertTrue(all(a > b for a, b in zip(full_lrs[:-1], full_lrs[1:])))

    def test_resume_with_no_lr_decay_maintains_saved_lr(self):
        """命令行 / 参数支持 --no-lr-decay,续训断点第 1 个 update 实际学习率与保存时严格一致。"""
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg1 = self._make_cfg(tmp, total_updates=20, lr=2.0e-4, lr_mode="anneal", lr_final_scale=0.1)
            stats1 = train(cfg1, updates=5, env=_OfflineEnv(max_episode_steps=4), verbose=False)
            lr5 = stats1.learning_rates[-1]

            ckpt_path = Path(tmp) / "ppo_000005.pt"
            ckpt_data = torch.load(ckpt_path, map_location="cpu")

            # 续训开启 no_lr_decay
            cfg2 = self._make_cfg(tmp, total_updates=20, lr=2.0e-4, lr_mode="anneal")
            stats2 = train(cfg2, updates=5, env=_OfflineEnv(max_episode_steps=4),
                           verbose=False, init_state=ckpt_data, no_lr_decay=True)

            self.assertEqual(len(stats2.learning_rates), 5)
            # 断点第 1 个 update (第 6 步) 学习率与保存时严格一致,无 2.0e-4 阶跃回弹
            self.assertAlmostEqual(stats2.learning_rates[0], lr5, places=9)
            # 全程维持常数微调学习率
            for lr_step in stats2.learning_rates:
                self.assertAlmostEqual(lr_step, lr5, places=9)

    def test_resume_with_explicit_lr_and_no_decay(self):
        """支持微调时指定恒定学习率:如 --lr 5e-5 --no-lr-decay。"""
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg1 = self._make_cfg(tmp, total_updates=20, lr=2.0e-4, lr_mode="anneal")
            train(cfg1, updates=5, env=_OfflineEnv(max_episode_steps=4), verbose=False)

            ckpt_path = Path(tmp) / "ppo_000005.pt"
            ckpt_data = torch.load(ckpt_path, map_location="cpu")

            cfg2 = self._make_cfg(tmp, total_updates=20)
            stats2 = train(cfg2, updates=5, env=_OfflineEnv(max_episode_steps=4),
                           verbose=False, init_state=ckpt_data, lr=5e-5, no_lr_decay=True)

            self.assertEqual(len(stats2.learning_rates), 5)
            for lr_step in stats2.learning_rates:
                self.assertAlmostEqual(lr_step, 5e-5, places=9)

    def test_lr_mode_constant_and_anneal(self):
        """验证 lr_mode 配置与参数优先覆盖行为。"""
        import tempfile
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            # 模式 1: constant 模式即使 lr_anneal=True 也不衰减
            cfg_const = self._make_cfg(tmp, total_updates=5, lr=1e-3, lr_mode="constant")
            cfg_const["train"]["lr_anneal"] = True
            stats_const = train(cfg_const, updates=3, env=_OfflineEnv(max_episode_steps=4), verbose=False)
            self.assertTrue(all(abs(lr - 1e-3) < 1e-9 for lr in stats_const.learning_rates))

            # 模式 2: anneal 模式发生退火
            cfg_ann = self._make_cfg(tmp, total_updates=5, lr=1e-3, lr_mode="anneal")
            stats_ann = train(cfg_ann, updates=3, env=_OfflineEnv(max_episode_steps=4), verbose=False)
            self.assertTrue(stats_ann.learning_rates[0] > stats_ann.learning_rates[1] > stats_ann.learning_rates[2])

    def test_optimizer_and_scheduler_isolation_on_weight_migration(self):
        """当网络结构发生变动(迁移)时,显式隔离优化器与调度器状态;同构续训时完整恢复。"""
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_cfg(tmp, total_updates=2, lr=1e-3, lr_mode="constant")
            cfg["checkpoint"]["save_every"] = 1
            train(cfg, updates=1, env=_OfflineEnv(max_episode_steps=4), verbose=False)
            ckpt_path = Path(tmp) / "ppo_000001.pt"
            ckpt_data = torch.load(ckpt_path, map_location="cpu")

            # 同构续训: Adam 与调度器状态成功恢复
            stats_same = train(cfg, updates=1, env=_OfflineEnv(max_episode_steps=4),
                               verbose=False, init_state=ckpt_data)
            self.assertEqual(len(stats_same.learning_rates), 1)

            # 异构网络(修改 motion_vocab 词表大小触发 warm_start 迁移)
            cfg_diff = self._make_cfg(tmp, total_updates=2, lr=1e-3, lr_mode="constant")
            cfg_diff["network"]["motion_vocab"] = 8192  # 原来是 4096
            # 续训不会崩溃,会安全隔离丢弃旧优化器状态与调度器状态
            stats_diff = train(cfg_diff, updates=1, env=_OfflineEnv(max_episode_steps=4),
                               verbose=False, init_state=ckpt_data)
            self.assertEqual(len(stats_diff.learning_rates), 1)

    def test_lr_at_edge_cases_and_calling_conventions(self):
        """测试 lr_at 的边界值、负值、越界截断及多重参数调用形态。"""
        from train.train_ppo import lr_at

        # 1. total <= 0
        self.assertAlmostEqual(lr_at(1, 0, 0, 1e-3), 1e-3)
        self.assertAlmostEqual(lr_at(1, total=-5, base=2e-4), 2e-4)

        # 2. it < 0 截断为起点 base
        self.assertAlmostEqual(lr_at(-10, 0, 100, 1e-3), 1e-3)

        # 3. it > total 截断为终点 base * final_scale
        self.assertAlmostEqual(lr_at(500, 0, 100, 1e-3, final_scale=0.2), 2e-4)

        # 4. 4 参数新规范调用: lr_at(it, total, base, final_scale) -> 修复前曾错误返回 0.2
        self.assertAlmostEqual(lr_at(10, 100, 2e-4, 0.2), 2e-4 + (4e-5 - 2e-4) * 0.1, places=9)

        # 5. 4 参数旧接口兼容: lr_at(it, it_base, total, base)
        self.assertAlmostEqual(lr_at(401, 400, 200, 1e-3), 1e-3 + (1e-4 - 1e-3) * (1 / 200), places=9)

        # 6. 5 参数旧接口: lr_at(it, it_base, total, base, final_scale)
        self.assertAlmostEqual(lr_at(10, 0, 100, 2e-4, 0.2), 0.000184, places=9)

        # 7. 3 参数调用模式: lr_at(it, total, base)
        self.assertAlmostEqual(lr_at(50, 100, 1e-3), 1e-3 + (1e-4 - 1e-3) * 0.5)

        # 8. 2 参数调用模式: lr_at(it, total)
        self.assertAlmostEqual(lr_at(50, 100), 1e-3 + (1e-4 - 1e-3) * 0.5)

        # 9. 关键字参数调用
        self.assertAlmostEqual(lr_at(it=10, total=100, base=2e-4, final_scale=0.5),
                               2e-4 + (1e-4 - 2e-4) * 0.1)

    def test_resume_with_updates_exceeding_schedule_total_strictly_monotone(self):
        """续训 updates 步数即使超过原始 schedule_total, 也不得因分母扩大而产生向上回弹。"""
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg1 = self._make_cfg(tmp, total_updates=20, lr=2.0e-4, lr_mode="anneal", lr_final_scale=0.1)
            stats1 = train(cfg1, updates=5, env=_OfflineEnv(max_episode_steps=4), verbose=False)
            lr5 = stats1.learning_rates[-1]

            ckpt_path = Path(tmp) / "ppo_000005.pt"
            ckpt_data = torch.load(ckpt_path, map_location="cpu")

            # 续训 20 步, end_it = 25 > total_updates 20
            cfg2 = self._make_cfg(tmp, total_updates=20, lr=2.0e-4, lr_mode="anneal", lr_final_scale=0.1)
            stats2 = train(cfg2, updates=20, env=_OfflineEnv(max_episode_steps=4),
                           verbose=False, init_state=ckpt_data)

            lr6 = stats2.learning_rates[0]
            # 断言: 第 6 步严格小于第 5 步, 斜率严格一致, 杜绝向上的 LR Bounce
            self.assertLess(lr6, lr5)
            delta_before = stats1.learning_rates[-2] - stats1.learning_rates[-1]
            delta_resume = lr5 - lr6
            self.assertAlmostEqual(delta_resume, delta_before, places=9)

            # 超过 schedule_total=20 之后 (第 21..25 步), 学习率维持在 minimum final_scale, 不得下溢或反弹
            min_lr = 2.0e-4 * 0.1
            for lr_step in stats2.learning_rates[15:]:  # it 21..25
                self.assertAlmostEqual(lr_step, min_lr, places=9)

    def test_resume_anneal_inherits_checkpoint_base_lr_without_bounce(self):
        """续训在 anneal 模式下即使配置文件默认 lr 不同, 只要未显式传 --lr 就自动继承 checkpoint 的 base_lr。"""
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg1 = self._make_cfg(tmp, total_updates=20, lr=2.0e-4, lr_mode="anneal", lr_final_scale=0.1)
            stats1 = train(cfg1, updates=5, env=_OfflineEnv(max_episode_steps=4), verbose=False)
            lr5 = stats1.learning_rates[-1]

            ckpt_path = Path(tmp) / "ppo_000005.pt"
            ckpt_data = torch.load(ckpt_path, map_location="cpu")

            # 新配置默认 lr 为 3.0e-4 (如 train_ppo.yaml 的默认值)
            cfg2 = self._make_cfg(tmp, total_updates=20, lr=3.0e-4, lr_mode="anneal", lr_final_scale=0.1)
            stats2 = train(cfg2, updates=5, env=_OfflineEnv(max_episode_steps=4),
                           verbose=False, init_state=ckpt_data)

            lr6 = stats2.learning_rates[0]
            # 必须继承 2.0e-4, 绝不能跃迁至 3.0e-4 的曲线上 (例如 2.19e-4)
            self.assertLess(lr6, lr5)
            delta_before = stats1.learning_rates[-2] - stats1.learning_rates[-1]
            delta_resume = lr5 - lr6
            self.assertAlmostEqual(delta_resume, delta_before, places=9)

    def test_checkpoint_contains_lr_metadata_fields(self):
        """验证保存的 checkpoint 元数据完整包含 lr, lr_base, lr_mode, lr_final_scale 等字段。"""
        import tempfile
        import torch
        from train.train_ppo import _OfflineEnv, train

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._make_cfg(tmp, total_updates=10, lr=2.5e-4, lr_mode="anneal", lr_final_scale=0.2)
            cfg["checkpoint"]["save_every"] = 2
            train(cfg, updates=2, env=_OfflineEnv(max_episode_steps=4), verbose=False)

            ckpt_path = Path(tmp) / "ppo_000002.pt"
            self.assertTrue(ckpt_path.exists())
            data = torch.load(ckpt_path, map_location="cpu")

            self.assertIn("lr", data)
            self.assertIn("lr_base", data)
            self.assertIn("lr_mode", data)
            self.assertIn("lr_final_scale", data)
            self.assertIn("schedule_total_updates", data)

            self.assertAlmostEqual(data["lr_base"], 2.5e-4)
            self.assertEqual(data["lr_mode"], "anneal")
            self.assertAlmostEqual(data["lr_final_scale"], 0.2)
            self.assertEqual(data["schedule_total_updates"], 10)

    def test_main_cli_lr_flags_end_to_end(self):
        """通过 main() 测试 --lr, --lr-mode, --no-lr-decay 与 --schedule-total-updates 的端到端集成。"""
        import sys
        import tempfile
        from unittest.mock import patch
        from train.train_ppo import main

        with tempfile.TemporaryDirectory() as tmp:
            test_args = [
                "train_ppo.py",
                "--offline",
                "--updates", "1",
                "--lr", "5e-5",
                "--no-lr-decay",
                "--schedule-total-updates", "100",
                "--quiet",
            ]
            with patch.object(sys, "argv", test_args):
                exit_code = main()
                self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()


