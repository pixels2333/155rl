"""Ticket 05 验收测试:守护进程确定性评估锚定与早停/回滚安全熔断机制。

涵盖:
1. Checkpoint best_eval_win_rate 与置信区间等元数据解析与容错
2. 较 best.pt 显著衰退(>=15pp 或 Wilson 置信区间不重叠)触发回滚至 best.pt
3. 连续 2 轮无正向收益/衰退触发回滚至 best.pt
4. 回滚后仍无改善触发熔断并生成 STOP_TRAINING 标志文件安全停机
5. 回滚后若指标回升恢复正常续跑
6. 回滚后发散断点隔离 (quarantine) 彻底打破毒药循环 (后续断点编号较小亦能正确识别)
7. 训练进程崩溃 (rc!=0 且无新断点) 重试机制与连续崩溃安全熔断
8. Checkpoint 损坏容错而不伪造 0.0 胜率衰退
9. watchdog_train.py 脚本命令行与断点检索集成
10. 最近 N 轮评估趋势 (get_recent_trend) 跟踪
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".scratch"))

import watchdog_train
from train.circuit_breaker import (  # noqa: E402
    CheckpointMetrics,
    CircuitBreakerDecision,
    WatchdogCircuitBreaker,
    find_best_checkpoint,
    find_latest_checkpoint,
    load_checkpoint_metrics,
    quarantine_diverged_checkpoints,
)


class WatchdogCircuitBreakerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.stop_file = self.dir_path / "STOP_TRAINING"
        self.log_file = self.dir_path / "watchdog.log"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _save_ckpt(self, path: Path, **meta_overrides) -> Path:
        data = {
            "update": 1200,
            "global_step": 4915200,
            "best_eval_win_rate": 0.65,
            "eval_win_rate": 0.65,
            "eval_ci_low": 0.50,
            "eval_ci_high": 0.78,
            "eval_episodes": 40,
            "model_state_dict": {},
            "optimizer_state_dict": {},
            "lr_scheduler_state_dict": {},
        }
        data.update(meta_overrides)
        torch.save(data, path)
        return path

    def test_checkpoint_metadata_parsing(self):
        """验收标准 1: 能够正确解析 checkpoint 的 best_eval_win_rate 与 Wilson 区间元数据。"""
        ckpt_path = self.dir_path / "ppo_001200.pt"
        self._save_ckpt(
            ckpt_path,
            update=1200,
            best_eval_win_rate=0.625,
            eval_win_rate=0.575,
            eval_ci_low=0.42,
            eval_ci_high=0.72,
            eval_episodes=40,
        )

        metrics = load_checkpoint_metrics(ckpt_path)
        self.assertTrue(metrics.is_valid)
        self.assertEqual(metrics.update, 1200)
        self.assertAlmostEqual(metrics.best_eval_win_rate, 0.625)
        self.assertAlmostEqual(metrics.eval_win_rate, 0.575)
        self.assertAlmostEqual(metrics.eval_ci_low, 0.42)
        self.assertAlmostEqual(metrics.eval_ci_high, 0.72)
        self.assertEqual(metrics.eval_episodes, 40)

    def test_checkpoint_metadata_error_handling(self):
        """测试文件不存在、非字典或损坏时的容错处理。"""
        # 不存在的文件
        missing = load_checkpoint_metrics(self.dir_path / "non_existent.pt")
        self.assertFalse(missing.is_valid)
        self.assertIn("not found", missing.error_msg.lower())

        # 损坏文件
        bad_file = self.dir_path / "corrupt.pt"
        bad_file.write_text("corrupted content", encoding="utf-8")
        corrupted = load_checkpoint_metrics(bad_file, retries=1)
        self.assertFalse(corrupted.is_valid)

        # 遗留旧格式 checkpoint(无 best_eval_win_rate, 只有 win_rate)
        legacy_file = self.dir_path / "legacy.pt"
        torch.save({"win_rate": 0.45, "update": 100}, legacy_file)
        legacy = load_checkpoint_metrics(legacy_file)
        self.assertTrue(legacy.is_valid)
        self.assertAlmostEqual(legacy.best_eval_win_rate, 0.45)
        self.assertAlmostEqual(legacy.eval_win_rate, 0.45)

    def test_rollback_on_15pp_significant_decay(self):
        """验收标准 2: 单轮胜率较 best.pt 发生显著衰退(>=15pp)触发回滚。"""
        best_pt = self._save_ckpt(
            self.dir_path / "best.pt",
            best_eval_win_rate=0.60,
            eval_win_rate=0.60,
            eval_ci_low=0.45,
            eval_ci_high=0.74,
            eval_episodes=40,
        )
        curr_pt = self._save_ckpt(
            self.dir_path / "ppo_001220.pt",
            best_eval_win_rate=0.60,
            eval_win_rate=0.40,  # 下降 20pp > 15pp
            eval_ci_low=0.26,
            eval_ci_high=0.56,
            eval_episodes=40,
        )

        breaker = WatchdogCircuitBreaker(
            decay_threshold=0.15,
            consecutive_decay_limit=2,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        best_m = load_checkpoint_metrics(best_pt)
        curr_m = load_checkpoint_metrics(curr_pt)

        decision = breaker.evaluate_round(curr_m, best_m)
        self.assertEqual(decision.action, "ROLLBACK")
        self.assertEqual(decision.target_ckpt, best_pt)
        self.assertTrue(breaker.is_rolled_back)
        self.assertFalse(self.stop_file.exists(), "第一次回滚不应触发安全停机")

    def test_rollback_on_wilson_interval_disjoint(self):
        """验收标准 2: Wilson 置信区间完全不重叠(统计显著衰退)触发回滚。"""
        best_pt = self._save_ckpt(
            self.dir_path / "best.pt",
            best_eval_win_rate=0.65,
            eval_win_rate=0.65,
            eval_ci_low=0.55,  # 下限 0.55
            eval_ci_high=0.75,
            eval_episodes=50,
        )
        curr_pt = self._save_ckpt(
            self.dir_path / "ppo_001220.pt",
            best_eval_win_rate=0.65,
            eval_win_rate=0.52,  # 下降 13pp (<15pp), 但区间上限 0.54 < 0.55 完全不重叠
            eval_ci_low=0.40,
            eval_ci_high=0.54,
            eval_episodes=50,
        )

        breaker = WatchdogCircuitBreaker(
            decay_threshold=0.15,
            consecutive_decay_limit=2,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        best_m = load_checkpoint_metrics(best_pt)
        curr_m = load_checkpoint_metrics(curr_pt)

        decision = breaker.evaluate_round(curr_m, best_m)
        self.assertEqual(decision.action, "ROLLBACK")
        self.assertEqual(decision.target_ckpt, best_pt)
        self.assertTrue(breaker.is_rolled_back)

    def test_rollback_on_consecutive_decays(self):
        """验收标准 2: 连续 2 轮有实际幅度的温和衰退触发回滚 (>= threshold/2)。"""
        best_pt = self._save_ckpt(
            self.dir_path / "best.pt",
            best_eval_win_rate=0.55,
            eval_win_rate=0.55,
            eval_ci_low=0.41,
            eval_ci_high=0.68,
            eval_episodes=50,
        )
        r1_pt = self._save_ckpt(
            self.dir_path / "ppo_001220.pt",
            best_eval_win_rate=0.55,
            eval_win_rate=0.47,  # 衰退 8pp (<15pp, 区间重叠, 但 >= 7.5pp 温和阈值)
            eval_ci_low=0.34,
            eval_ci_high=0.60,
            eval_episodes=50,
        )
        r2_pt = self._save_ckpt(
            self.dir_path / "ppo_001240.pt",
            best_eval_win_rate=0.55,
            eval_win_rate=0.46,  # 第 2 轮依然低于 baseline 0.55
            eval_ci_low=0.33,
            eval_ci_high=0.59,
            eval_episodes=50,
        )

        breaker = WatchdogCircuitBreaker(
            decay_threshold=0.15,
            consecutive_decay_limit=2,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        best_m = load_checkpoint_metrics(best_pt)
        r1_m = load_checkpoint_metrics(r1_pt)
        r2_m = load_checkpoint_metrics(r2_pt)

        # 第 1 轮: 容忍单轮小幅波动
        d1 = breaker.evaluate_round(r1_m, best_m)
        self.assertEqual(d1.action, "CONTINUE")
        self.assertEqual(breaker.consecutive_decays, 1)
        self.assertFalse(breaker.is_rolled_back)

        # 第 2 轮: 达到连续 2 轮限制, 触发回滚
        d2 = breaker.evaluate_round(r2_m, best_m)
        self.assertEqual(d2.action, "ROLLBACK")
        self.assertEqual(d2.target_ckpt, best_pt)
        self.assertTrue(breaker.is_rolled_back)

    def test_circuit_breaker_trips_and_creates_stop_file_after_failed_rollback(self):
        """验收标准 3: 回滚后仍无改善, 生成 STOP_TRAINING 标志文件并安全退出。"""
        best_pt = self._save_ckpt(
            self.dir_path / "best.pt",
            best_eval_win_rate=0.60,
            eval_win_rate=0.60,
            eval_ci_low=0.45,
            eval_ci_high=0.74,
            eval_episodes=40,
        )
        failed_r1 = self._save_ckpt(
            self.dir_path / "ppo_001220.pt",
            best_eval_win_rate=0.60,
            eval_win_rate=0.40,
            eval_ci_low=0.26,
            eval_ci_high=0.56,
            eval_episodes=40,
        )
        still_failed_r2 = self._save_ckpt(
            self.dir_path / "ppo_001240.pt",
            best_eval_win_rate=0.60,
            eval_win_rate=0.42,
            eval_ci_low=0.28,
            eval_ci_high=0.58,
            eval_episodes=40,
        )

        breaker = WatchdogCircuitBreaker(
            decay_threshold=0.15,
            consecutive_decay_limit=2,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        best_m = load_checkpoint_metrics(best_pt)
        m1 = load_checkpoint_metrics(failed_r1)
        m2 = load_checkpoint_metrics(still_failed_r2)

        # 第 1 步: 严重衰退触发回滚
        d1 = breaker.evaluate_round(m1, best_m)
        self.assertEqual(d1.action, "ROLLBACK")
        self.assertTrue(breaker.is_rolled_back)
        self.assertFalse(self.stop_file.exists())

        # 第 2 步: 从 best.pt 续训一轮后, 仍未改善(0.42 < 0.60), 触发熔断停机
        d2 = breaker.evaluate_round(m2, best_m)
        self.assertEqual(d2.action, "STOP")
        self.assertTrue(self.stop_file.exists(), "必须生成 STOP_TRAINING 标志文件")

        # 检查日志文件是否记录了报警
        log_content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("STOP_TRAINING", log_content)
        self.assertIn("熔断", log_content)

    def test_recovery_after_rollback_if_improved(self):
        """测试在回滚到 best.pt 之后, 若指标反弹超越 baseline, 恢复正常续跑。"""
        best_pt = self._save_ckpt(
            self.dir_path / "best.pt",
            best_eval_win_rate=0.55,
            eval_win_rate=0.55,
            eval_ci_low=0.40,
            eval_ci_high=0.70,
            eval_episodes=40,
        )
        bad_pt = self._save_ckpt(
            self.dir_path / "ppo_001220.pt",
            best_eval_win_rate=0.55,
            eval_win_rate=0.35,  # 严重衰退
            eval_ci_low=0.20,
            eval_ci_high=0.50,
            eval_episodes=40,
        )
        good_pt = self._save_ckpt(
            self.dir_path / "ppo_001240.pt",
            best_eval_win_rate=0.58,
            eval_win_rate=0.58,  # 指标恢复并创新高
            eval_ci_low=0.42,
            eval_ci_high=0.72,
            eval_episodes=40,
        )

        breaker = WatchdogCircuitBreaker(
            decay_threshold=0.15,
            consecutive_decay_limit=2,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        # 训练进程在评估后已把新 best 写入 best.pt (CI 门控通过)
        best_pt2 = self._save_ckpt(
            self.dir_path / "best.pt",
            best_eval_win_rate=0.58,
            eval_win_rate=0.58,
            eval_ci_low=0.42,
            eval_ci_high=0.72,
            eval_episodes=40,
        )

        best_m = load_checkpoint_metrics(best_pt)
        bad_m = load_checkpoint_metrics(bad_pt)
        good_m = load_checkpoint_metrics(good_pt)
        best_m2 = load_checkpoint_metrics(best_pt2)

        # 回滚
        d1 = breaker.evaluate_round(bad_m, best_m)
        self.assertEqual(d1.action, "ROLLBACK")
        self.assertTrue(breaker.is_rolled_back)

        # 恢复: best.pt 已更新为 0.58, 基线应抬升到 0.58
        d2 = breaker.evaluate_round(good_m, best_m2)
        self.assertEqual(d2.action, "CONTINUE")
        self.assertFalse(breaker.is_rolled_back)
        self.assertEqual(breaker.consecutive_decays, 0)
        self.assertAlmostEqual(breaker.baseline_win_rate, 0.58)
        self.assertFalse(self.stop_file.exists())

    def test_stale_checkpoint_best_eval_does_not_raise_baseline(self):
        """重基线后, 续训断点里残留的 0.8667 best_eval 不能把 baseline 弹回去。"""
        best_pt = self._save_ckpt(
            self.dir_path / "best.pt",
            best_eval_win_rate=0.59,
            eval_win_rate=0.59,
            eval_ci_low=0.45,
            eval_ci_high=0.72,
            eval_episodes=50,
        )
        stale_pt = self._save_ckpt(
            self.dir_path / "ppo_000840.pt",
            best_eval_win_rate=0.8667,  # 重基线前的陈旧值
            eval_win_rate=0.63,
            eval_ci_low=0.49,
            eval_ci_high=0.75,
            eval_episodes=50,
        )

        breaker = WatchdogCircuitBreaker(
            decay_threshold=0.20,
            consecutive_decay_limit=3,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        best_m = load_checkpoint_metrics(best_pt)
        stale_m = load_checkpoint_metrics(stale_pt)

        decision = breaker.evaluate_round(stale_m, best_m)
        self.assertEqual(decision.action, "CONTINUE")
        self.assertAlmostEqual(breaker.baseline_win_rate, 0.59)
        self.assertFalse(breaker.is_rolled_back)

    def test_find_checkpoints_resolution(self):
        """测试最新与最佳 checkpoint 检索与兜底机制。"""
        # 空目录
        self.assertIsNone(find_latest_checkpoint(self.dir_path))
        self.assertIsNone(find_best_checkpoint(self.dir_path))

        # 存在多个 ppo_*.pt
        p1 = self._save_ckpt(self.dir_path / "ppo_000100.pt", best_eval_win_rate=0.70)
        p2 = self._save_ckpt(self.dir_path / "ppo_000200.pt", best_eval_win_rate=0.50)

        self.assertEqual(find_latest_checkpoint(self.dir_path), p2)
        # 没有 best.pt 时, find_best_checkpoint 兜底挑选最高胜率的 p1
        self.assertEqual(find_best_checkpoint(self.dir_path), p1)

        # 加入经审计的 best.pt
        best_pt = self._save_ckpt(self.dir_path / "best.pt", best_eval_win_rate=0.75)
        self.assertEqual(find_best_checkpoint(self.dir_path), best_pt)

    def test_watchdog_step_with_mock_runner(self):
        """端到端验证 step_watchdog 在指标劣化时的回滚与熔断行为。"""
        best_pt = self._save_ckpt(
            self.dir_path / "best.pt",
            best_eval_win_rate=0.60,
            eval_win_rate=0.60,
            eval_ci_low=0.45,
            eval_ci_high=0.75,
            eval_episodes=40,
        )
        self._save_ckpt(
            self.dir_path / "ppo_000100.pt",
            best_eval_win_rate=0.60,
            eval_win_rate=0.60,
            eval_ci_low=0.45,
            eval_ci_high=0.75,
            eval_episodes=40,
        )

        breaker = watchdog_train.WatchdogCircuitBreaker(
            decay_threshold=0.15,
            consecutive_decay_limit=2,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        recorded_commands = []

        # 模拟第 1 轮训练: 产出衰退 checkpoint ppo_000120.pt (win_rate=0.40)
        def mock_runner_r1(cmd):
            recorded_commands.append(cmd)
            self._save_ckpt(
                self.dir_path / "ppo_000120.pt",
                update=120,
                best_eval_win_rate=0.60,
                eval_win_rate=0.40,  # 严重衰退 20pp
                eval_ci_low=0.25,
                eval_ci_high=0.55,
                eval_episodes=40,
            )
            return 0

        rc1, d1 = watchdog_train.step_watchdog(
            breaker=breaker,
            ckpt_dir=self.dir_path,
            config="fake_config.yaml",
            train_log=self.dir_path / "train.log",
            runner=mock_runner_r1,
        )

        self.assertEqual(rc1, 0)
        self.assertEqual(d1.action, "ROLLBACK")
        self.assertEqual(d1.target_ckpt, best_pt)
        self.assertTrue(breaker.is_rolled_back)
        self.assertFalse(self.stop_file.exists())

        # 模拟第 2 轮训练: 按照熔断指示强制回滚到 best.pt 续跑, 但仍未改善 (win_rate=0.41)
        def mock_runner_r2(cmd):
            recorded_commands.append(cmd)
            self._save_ckpt(
                self.dir_path / "ppo_000140.pt",
                update=140,
                best_eval_win_rate=0.60,
                eval_win_rate=0.41,  # 仍无改善
                eval_ci_low=0.26,
                eval_ci_high=0.56,
                eval_episodes=40,
            )
            return 0

        rc2, d2 = watchdog_train.step_watchdog(
            breaker=breaker,
            ckpt_dir=self.dir_path,
            config="fake_config.yaml",
            train_log=self.dir_path / "train.log",
            runner=mock_runner_r2,
        )

        self.assertEqual(rc2, 0)
        self.assertEqual(d2.action, "STOP")
        self.assertTrue(self.stop_file.exists())

        # 验证命令中 resume 参数确为回滚目标 best.pt
        self.assertIn(str(best_pt), " ".join(recorded_commands[1]))

    def test_rollback_diverged_checkpoints_quarantine(self):
        """核心断言: 回滚后自动隔离高于 best_update 的发散断点, 避免毒药循环。"""
        best_pt = self._save_ckpt(
            self.dir_path / "best.pt",
            update=100,
            best_eval_win_rate=0.60,
            eval_win_rate=0.60,
            eval_ci_low=0.45,
            eval_ci_high=0.75,
            eval_episodes=40,
        )
        self._save_ckpt(
            self.dir_path / "ppo_000120.pt",
            update=120,
            best_eval_win_rate=0.60,
            eval_win_rate=0.40,  # 严重衰退
            eval_ci_low=0.25,
            eval_ci_high=0.55,
            eval_episodes=40,
        )

        breaker = watchdog_train.WatchdogCircuitBreaker(
            decay_threshold=0.15,
            consecutive_decay_limit=2,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        # 第 1 步: 评估劣化, 触发 ROLLBACK, 验证 ppo_000120.pt 被隔离
        rc1, d1 = watchdog_train.step_watchdog(
            breaker=breaker,
            ckpt_dir=self.dir_path,
            config="fake_config.yaml",
            train_log=self.dir_path / "train.log",
            runner=lambda cmd: 0,
        )
        self.assertEqual(d1.action, "ROLLBACK")
        self.assertTrue(breaker.is_rolled_back)

        # 确认 ppo_000120.pt 已被移入 quarantine_diverged
        quarantine_dir = self.dir_path / "quarantine_diverged"
        self.assertTrue((quarantine_dir / "ppo_000120.pt").exists())
        self.assertFalse((self.dir_path / "ppo_000120.pt").exists())

        # 第 2 步: 回滚后从 best.pt(100) 续跑, 产出 update=110 (虽小于 120, 但大于 100 且指标恢复为 0.62)
        def mock_rollback_runner(cmd):
            self._save_ckpt(
                self.dir_path / "ppo_000110.pt",
                update=110,
                best_eval_win_rate=0.62,
                eval_win_rate=0.62,
                eval_ci_low=0.46,
                eval_ci_high=0.76,
                eval_episodes=40,
            )
            return 0

        rc2, d2 = watchdog_train.step_watchdog(
            breaker=breaker,
            ckpt_dir=self.dir_path,
            config="fake_config.yaml",
            train_log=self.dir_path / "train.log",
            runner=mock_rollback_runner,
        )

        # 验证正确识别到 ppo_000110.pt, 并恢复正常续跑(而不是误读 120 触发 STOP)
        self.assertEqual(d2.action, "CONTINUE")
        self.assertFalse(breaker.is_rolled_back)
        self.assertFalse(self.stop_file.exists())
        self.assertAlmostEqual(d2.current_win_rate, 0.62)

    def test_crash_handling_retry_and_eventual_stop(self):
        """测试训练进程非零退出码且未产出新 Checkpoint 时的重试与连续崩溃熔断。"""
        self._save_ckpt(
            self.dir_path / "best.pt",
            update=100,
            best_eval_win_rate=0.60,
            eval_win_rate=0.60,
            eval_ci_low=0.45,
            eval_ci_high=0.75,
            eval_episodes=40,
        )

        breaker = watchdog_train.WatchdogCircuitBreaker(
            max_crashes=3,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        # 模拟前 2 次启动即崩溃 (无新 checkpoint)
        rc1, d1 = watchdog_train.step_watchdog(
            breaker=breaker,
            ckpt_dir=self.dir_path,
            config="fake.yaml",
            train_log=self.dir_path / "train.log",
            runner=lambda cmd: 1,
        )
        self.assertEqual(d1.action, "RETRY")
        self.assertEqual(breaker.consecutive_crashes, 1)
        self.assertFalse(self.stop_file.exists())

        rc2, d2 = watchdog_train.step_watchdog(
            breaker=breaker,
            ckpt_dir=self.dir_path,
            config="fake.yaml",
            train_log=self.dir_path / "train.log",
            runner=lambda cmd: 1,
        )
        self.assertEqual(d2.action, "RETRY")
        self.assertEqual(breaker.consecutive_crashes, 2)
        self.assertFalse(self.stop_file.exists())

        # 第 3 次崩溃达到上限, 触发 STOP 停机
        rc3, d3 = watchdog_train.step_watchdog(
            breaker=breaker,
            ckpt_dir=self.dir_path,
            config="fake.yaml",
            train_log=self.dir_path / "train.log",
            runner=lambda cmd: 1,
        )
        self.assertEqual(d3.action, "STOP")
        self.assertTrue(self.stop_file.exists())
        self.assertIn("连续崩溃", d3.reason)

    def test_corrupt_checkpoint_does_not_distort_decay(self):
        """测试 Checkpoint 损坏时能正确报警回滚或停机, 不会将损坏文件伪造为 0.0 胜率衰退。"""
        best_pt = self._save_ckpt(
            self.dir_path / "best.pt",
            update=100,
            best_eval_win_rate=0.60,
            eval_win_rate=0.60,
        )
        corrupt_file = self.dir_path / "corrupt.pt"
        corrupt_file.write_text("corrupted content", encoding="utf-8")

        breaker = WatchdogCircuitBreaker(
            baseline_win_rate=0.60,
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        corrupt_metrics = load_checkpoint_metrics(corrupt_file, retries=1)
        best_m = load_checkpoint_metrics(best_pt)

        # 损坏文件触发回滚
        dec = breaker.evaluate_round(corrupt_metrics, best_m)
        self.assertEqual(dec.action, "ROLLBACK")
        self.assertEqual(dec.decay_amount, 0.0)  # 不伪造 0.60 的胜率衰退
        self.assertIn("损坏", dec.reason)

        # 若已在回滚状态又损坏, 触发安全停机
        dec2 = breaker.evaluate_round(corrupt_metrics, best_m)
        self.assertEqual(dec2.action, "STOP")
        self.assertTrue(self.stop_file.exists())

    def test_get_recent_trend(self):
        """验收标准 1: 验证 get_recent_trend 能够正确跟踪最近 N 轮评估趋势。"""
        breaker = WatchdogCircuitBreaker()
        best_pt = self._save_ckpt(self.dir_path / "best.pt", best_eval_win_rate=0.60)
        best_m = load_checkpoint_metrics(best_pt)

        for wr in [0.60, 0.62, 0.65]:
            p = self._save_ckpt(self.dir_path / f"p_{wr}.pt", best_eval_win_rate=wr, eval_win_rate=wr)
            m = load_checkpoint_metrics(p)
            breaker.evaluate_round(m, best_m)

        trend = breaker.get_recent_trend(2)
        self.assertEqual(len(trend), 2)
        self.assertAlmostEqual(trend[0]["decision"].current_win_rate, 0.62)
        self.assertAlmostEqual(trend[1]["decision"].current_win_rate, 0.65)

    def test_watchdog_main_cli_once(self):
        """测试 main 命令行入口 --once 与参数解析。"""
        self._save_ckpt(self.dir_path / "best.pt", best_eval_win_rate=0.50)

        # 提前写入 STOP_TRAINING 测试即时退出
        self.stop_file.write_text("stop\n", encoding="utf-8")

        exit_code = watchdog_train.main([
            "--ckpt-dir", str(self.dir_path),
            "--log", str(self.dir_path / "train.log"),
            "--watchdog-log", str(self.log_file),
            "--stop-file", str(self.stop_file),
            "--once",
        ])
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
