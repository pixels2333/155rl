"""课程接线集成测试:用假环境跑真实的 `train()` 主循环,验证晋级链路。

为什么需要它:`curriculum.py` 的纯逻辑测试覆盖不到"晋级 -> 切难度 -> 退火 ->
存 checkpoint"这条接线。这里用 `_OfflineEnv` 的扩展版(带实时难度/奖励表)驱动
真实训练循环,不碰游戏,也不会调用 `restart_game()`。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from env.reward import RewardCalculator                    # noqa: E402
import train.train_ppo as tp                              # noqa: E402
from train.curriculum import Curriculum                   # noqa: E402
from train.train_ppo import _OfflineEnv, train            # noqa: E402


class _LiveLikeEnv(_OfflineEnv):
    """离线观测 + 实机训练器需要的那几个接口。"""

    action_repeat = 2
    team = 0

    def __init__(self, max_episode_steps: int = 8, com_level: int = 1):
        super().__init__(max_episode_steps=max_episode_steps)
        self.reward_calc = RewardCalculator({"w_approach": 0.05})
        self.com_level = com_level
        self.difficulty_calls: list = []
        self.closed = False

    # -- 实机专属接口 ------------------------------------------------------
    def _state(self):
        return {"teams": [{"com_difficulty": 0}, {"com_difficulty": self.com_level}]}

    def set_opponent_difficulty(self, level: int) -> bool:
        self.difficulty_calls.append(int(level))
        self.com_level = int(level)
        return True

    def close(self):
        self.closed = True


def _cfg(tmp: str, *, apply_mode: str = "live") -> dict:
    return {
        "env": {"team": 0, "action_repeat": 2, "difficulty": 1,
                "max_episode_steps": 8, "frame_timeout": 1.0},
        "network": {"motion_vocab": 4096, "motion_embed": 16, "team_hidden": 32,
                    "global_hidden": 16, "proj_hidden": 16, "fuse_hidden": 32,
                    "lstm_hidden": 32, "lstm_layers": 1},
        "train": {"total_updates": 6, "n_steps": 16, "seq_len": 4, "epochs": 1,
                  "minibatch_seqs": 2, "gamma": 0.99, "gae_lambda": 0.95,
                  "clip_range": 0.2, "value_coef": 0.5, "entropy_coef": 0.01,
                  "max_grad_norm": 0.5, "lr": 3e-4, "target_kl": 0.05, "seed": 0},
        "checkpoint": {"dir": tmp, "save_every": 2, "keep": 2},
        "logging": {"tensorboard": str(Path(tmp) / "tb")},
        "curriculum": {"enabled": True, "apply_mode": apply_mode,
                       "window": 50, "min_decided": 2,
                       "stages": [{"name": "s1", "com_levels": [1],
                                   "promote_win_rate": 0.5, "entropy_scale": 1.0},
                                  {"name": "s2", "com_levels": [2],
                                   "promote_win_rate": None, "entropy_scale": 0.5,
                                   "reward_overrides": {"w_approach": 0.0}}]},
    }


class TestCareerWiring(unittest.TestCase):
    def test_training_promotes_and_anneals(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _LiveLikeEnv()
            cfg = _cfg(tmp)
            stats = train(cfg, env=env, verbose=False)

            self.assertGreaterEqual(len(stats.episodes), 2, "没有跑出 episode")
            # 晋级已触发:阶段 1 -> 阶段 2
            self.assertEqual(cfg["train"]["entropy_coef"], 0.01 * 0.5,
                             "末阶段熵系数未退火")
            self.assertEqual(env.reward_calc.C["w_approach"], 0.0,
                             "末阶段微奖励未退火")
            self.assertEqual(env.difficulty_calls, [2],
                             f"晋级后应把难度切到 COM3(内部 2),实际 {env.difficulty_calls}")
            self.assertTrue(env.closed)

    def test_checkpoint_carries_curriculum_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _LiveLikeEnv()
            cfg = _cfg(tmp)
            train(cfg, env=env, verbose=False)
            ckpt = Path(tmp) / "ppo_000004.pt"
            self.assertTrue(ckpt.exists(), f"未生成 {ckpt}")
            saved = torch.load(ckpt, map_location="cpu")
            cur = saved.get("curriculum")
            self.assertIsNotNone(cur, "checkpoint 未带课程状态")
            self.assertEqual(cur["stage_index"], 1)
            self.assertEqual(cur["current_level"], 2)

    def test_curriculum_disabled_keeps_single_difficulty(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _LiveLikeEnv()
            cfg = _cfg(tmp)
            cfg["curriculum"]["enabled"] = False
            train(cfg, env=env, verbose=False)
            self.assertEqual(env.difficulty_calls, [], "禁用后不应切难度")
            self.assertEqual(cfg["train"]["entropy_coef"], 0.01)


class TestLrAnneal(unittest.TestCase):
    """`lr_anneal: true` 曾经是纯装饰(配置里写了、代码里没人读)。"""

    def test_linear_decay_over_this_run(self):
        base = 3e-4
        self.assertAlmostEqual(tp.lr_at(1, 0, 200, base), base * 0.9955, places=12)
        self.assertAlmostEqual(tp.lr_at(100, 0, 200, base), base * 0.55, places=12)
        self.assertAlmostEqual(tp.lr_at(200, 0, 200, base), base * 0.1, places=12)

    def test_resume_uses_this_run_progress(self):
        # 续训:绝对编号 401 已越过 total=200,退火必须按本轮进度算
        self.assertAlmostEqual(tp.lr_at(400, 400, 200, 1e-3), 1e-3)
        self.assertAlmostEqual(tp.lr_at(401, 400, 200, 1e-3), 1e-3 * 0.9955, places=9)

    def test_clamped_outside_range(self):
        self.assertAlmostEqual(tp.lr_at(999, 0, 10, 1e-3), 1e-4)
        self.assertAlmostEqual(tp.lr_at(-5, 0, 10, 1e-3), 1e-3)

    def test_loop_applies_decay_every_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp)
            cfg["train"]["lr_anneal"] = True
            cfg["train"]["lr_final_scale"] = 0.0
            calls = []
            orig = tp.lr_at

            def spy(*a):
                calls.append((a[0], a[1], a[2]))
                return orig(*a)

            tp.lr_at = spy
            try:
                train(cfg, env=_LiveLikeEnv(), verbose=False)
            finally:
                tp.lr_at = orig
        self.assertEqual([c[0] for c in calls], [1, 2, 3, 4, 5, 6],
                         "每个 update 都必须更新学习率")
        self.assertTrue(all(c[1] == 0 for c in calls), "本轮起点 it_base 应为 0")


class TestShippedConfigs(unittest.TestCase):
    """仓库里发出去的两份配置必须自洽,否则训练会打到错误的对手。"""

    def test_com3_config_matches_its_first_stage(self):
        cfg = tp.load_config(str(ROOT / "configs" / "train_ppo_com3.yaml"))
        cur = Curriculum.from_config(cfg)
        first = cur.stages[0]
        self.assertEqual(cfg["env"]["difficulty"], 2, "env.difficulty 不是 HARD")
        self.assertEqual(first.name, "com3")
        self.assertEqual(first.com_levels, (2,), "首阶段难度与 env.difficulty 不一致")
        self.assertEqual(tp._target_level(cur, "restart"), 2)
        self.assertTrue(cfg["curriculum"]["enabled"], "COM3 轮次应开启课程")
        self.assertNotEqual(cfg["checkpoint"]["dir"], "checkpoints",
                            "COM3 轮次的 checkpoint 必须与 COM2 隔离")

    def test_default_config_matches_its_first_stage(self):
        cfg = tp.load_config(str(ROOT / "configs" / "train_ppo.yaml"))
        cur = Curriculum.from_config(cfg)
        self.assertEqual(cur.stages[0].com_levels, (cfg["env"]["difficulty"],))

    def test_com3_60hz_coldstart_config_matches_its_first_stage(self):
        cfg = tp.load_config(str(ROOT / "configs" / "train_ppo_com3_60hz_coldstart.yaml"))
        cur = Curriculum.from_config(cfg)
        first = cur.stages[0]
        self.assertEqual(cfg["env"]["action_repeat"], 1, "60Hz 配置 action_repeat 必须为 1")
        self.assertEqual(cfg["env"]["difficulty"], 2, "env.difficulty 不是 HARD")
        self.assertEqual(first.name, "com3")
        self.assertEqual(first.com_levels, (2,), "首阶段难度与 env.difficulty 不一致")
        self.assertEqual(tp._target_level(cur, "restart"), 2)
        self.assertTrue(cfg["curriculum"]["enabled"], "COM3 60Hz 轮次应开启课程")
        self.assertIn("60hz", cfg["checkpoint"]["dir"], "Checkpoint 目录必须标识 60hz")
        self.assertTrue(cfg["network"]["orthogonal_init"], "60Hz 冷启动必须配置正交初始化")
        self.assertEqual(cfg["eval"]["interval"], 25)
        self.assertEqual(cfg["eval"]["episodes"], 50)


class _StubEnv:
    def __init__(self, obs=None):
        self.obs = obs or _OfflineEnv()._obs()
        self.closed = False
        self.resets = 0

    def reset(self, **kw):
        self.resets += 1
        return self.obs, {}

    def close(self):
        self.closed = True


class TestDifficultyGuard(unittest.TestCase):
    """难度不一致必须重启重设,且重启后要再验一次(不能只验一次就放行)。"""

    def _run(self, levels, cfg_difficulty=2):
        cfg = {"env": {"difficulty": cfg_difficulty}}
        calls = {"restart": 0, "read": 0}
        seq = list(levels)

        def fake_read(_env):
            calls["read"] += 1
            return seq[min(calls["read"] - 1, len(seq) - 1)]

        def fake_restart(slot=None, period_us=None):
            calls["restart"] += 1
            return True

        with mock.patch.object(tp, "_live_com_difficulty", fake_read), \
             mock.patch.object(tp, "restart_game", fake_restart), \
             mock.patch.object(tp, "build_env", lambda c, ar, pid=None: _StubEnv()):
            env, _ = tp.reset_with_restart(cfg, _StubEnv(), 2, "test")
        return env, calls

    def test_matching_difficulty_no_restart(self):
        _env, calls = self._run([2])
        self.assertEqual(calls["restart"], 0)

    def test_mismatch_restarts_once_then_accepts(self):
        env, calls = self._run([1, 2])
        self.assertEqual(calls["restart"], 1)
        self.assertEqual(calls["read"], 2, "重启后必须再验一次难度")

    def test_persistent_mismatch_stops_after_one_retry(self):
        env, calls = self._run([1, 1, 1, 1])
        self.assertEqual(calls["restart"], 1, "不能无限重启")
        self.assertTrue(isinstance(env, _StubEnv))

    def test_offline_env_skips_check(self):
        self.assertIsNone(tp._live_com_difficulty(_OfflineEnv()))


class TestResumeNumbering(unittest.TestCase):
    """续训必须接着原 update 编号,否则会覆盖旧 checkpoint(实测会丢 190 次更新的存档)。"""

    def test_checkpoints_continue_from_loaded_update(self):
        from agents.ppo_agent import PolicyNet
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _cfg(tmp, apply_mode="live")
            cfg["train"]["total_updates"] = 3
            net = PolicyNet(cfg["network"])
            init = {"model": net.state_dict(), "update": 100}
            train(cfg, env=_LiveLikeEnv(), verbose=False, init_state=init)
            saved = sorted(Path(tmp).glob("ppo_*.pt"))
            names = [p.name for p in saved]
            self.assertIn("ppo_000102.pt", names, f"续训编号未接续:{names}")
            self.assertFalse(any(n.startswith("ppo_000002") for n in names),
                             f"出现了从 1 重新计数的 checkpoint:{names}")
            self.assertEqual(int(torch.load(Path(tmp) / "ppo_000102.pt",
                                            map_location="cpu")["update"]), 102)


class TestApplyDifficulty(unittest.TestCase):
    """晋级时难度落地的三条路:live -> 回标题重进 -> 重启进程。"""

    def _run(self, cmode: str, dswitch: str, title_ok: bool):
        env = _LiveLikeEnv()
        calls = []
        cfg = _cfg(tempfile.mkdtemp())

        def fake_title(level, pid=None):
            calls.append(("title", level))
            return title_ok

        def fake_restart(slot=None, period_us=None):
            calls.append(("restart", 0))
            return True

        def fake_build(cfg_, ar, pid=None):
            calls.append(("build", ar))
            return env

        def fake_reset(cfg_, env_, ar, why, **kw):
            calls.append(("reset", why))
            return env_, (_OfflineEnv()._obs(), {})

        with mock.patch.object(tp, "switch_difficulty", fake_title), \
             mock.patch.object(tp, "restart_game", fake_restart), \
             mock.patch.object(tp, "build_env", fake_build), \
             mock.patch.object(tp, "reset_with_restart", fake_reset):
            got_env, obs = tp._apply_difficulty(cfg, env, 2, cmode, dswitch, 2)
        return got_env, obs, calls, cfg

    def test_title_path_is_preferred(self):
        _env, obs, calls, cfg = self._run("restart", "title", title_ok=True)
        self.assertEqual(calls[0], ("title", 2))
        self.assertNotIn(("restart", 0), calls)
        self.assertIsNotNone(obs)
        self.assertEqual(cfg["env"]["difficulty"], 2)

    def test_falls_back_to_process_restart(self):
        _env, _obs, calls, _cfg = self._run("restart", "title", title_ok=False)
        self.assertEqual(calls[0], ("title", 2))
        self.assertIn(("restart", 0), calls)

    def test_restart_mode_skips_title(self):
        _env, _obs, calls, _cfg = self._run("restart", "restart", title_ok=True)
        self.assertNotIn("title", [c[0] for c in calls])
        self.assertIn(("restart", 0), calls)

    def test_live_mode_writes_field_without_restart(self):
        env, obs, calls, _cfg = self._run("live", "title", title_ok=True)
        self.assertEqual(calls, [])
        self.assertEqual(env.difficulty_calls, [2])
        self.assertIsNone(obs)


if __name__ == "__main__":
    unittest.main()
