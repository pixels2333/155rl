"""Ticket 06 验收测试: 60Hz 全感知全新冷启动训练与基线贯通验证。

涵盖:
1. 配置文件 configs/train_ppo_com3_60hz_coldstart.yaml 参数格式与课程阶段自洽性校验
2. PolicyNet 全新网络架构、55 维动作空间与正交初始化 (gain 校验与前向形状)
3. 60Hz 环境步进、Hold 格挡状态机倒计时与激光/特殊弹幕判定感知全链路
4. 从 update=0 启动全新冷启动训练、确定性评估回路与最佳/周期 Checkpoint 完整元数据固化
5. 守护脚本 watchdog_train.py 在无已有 Checkpoint 时的全新冷启动支持与基线自动锚定
6. 60Hz 全感知确定性基线报告生成 (Markdown / JSON 报告与 Wilson 区间)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import torch
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".scratch"))

from agents.ppo_agent import N_ACTIONS, PolicyNet, to_batch
from env import action_space, obs_builder
from env.th155_env import Th155Env
from train.circuit_breaker import (
    CheckpointMetrics,
    WatchdogCircuitBreaker,
    find_best_checkpoint,
    find_latest_checkpoint,
    load_checkpoint_metrics,
)
from train.curriculum import Curriculum
from train.eval import to_markdown, write_reports
import train.train_ppo as tp
from train.train_ppo import Evaluator, _OfflineEnv, train
import watchdog_train
from scripts.run_60hz_coldstart_baseline import run_baseline


class Test60HzColdStartConfig(unittest.TestCase):
    """验收标准 1: 配置文件编写完毕且参数格式通过校验。"""

    def setUp(self):
        self.cfg_path = ROOT / "configs" / "train_ppo_com3_60hz_coldstart.yaml"

    def test_config_file_exists_and_parses(self):
        self.assertTrue(self.cfg_path.exists(), f"配置文件不存在: {self.cfg_path}")
        with open(self.cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self.assertIsInstance(cfg, dict)

    def test_env_parameters_for_60hz(self):
        cfg = tp.load_config(str(self.cfg_path))
        ecfg = cfg["env"]
        self.assertEqual(ecfg["action_repeat"], 1, "60Hz 决策频率 action_repeat 必须为 1")
        self.assertEqual(ecfg["difficulty"], 2, "COM3 对应游戏内难度索引 2 (HARD)")
        self.assertEqual(ecfg["team"], 0)
        self.assertEqual(ecfg["round_seconds"], 99)
        self.assertGreaterEqual(ecfg["max_episode_steps"], 9000, "60Hz 下 max_episode_steps 应等比放大 (>=9000)")

    def test_network_dimensions_and_orthogonal_init(self):
        cfg = tp.load_config(str(self.cfg_path))
        ncfg = cfg["network"]
        self.assertTrue(ncfg.get("orthogonal_init", False), "冷启动网络必须配置 orthogonal_init: true")
        self.assertEqual(ncfg["motion_vocab"], 10240, "词表必须覆盖符卡 4000~4999 与 9xxx 演出动作(docs §36)")
        self.assertEqual(ncfg["motion_embed"], 16)
        self.assertEqual(ncfg["team_hidden"], 128)
        self.assertEqual(ncfg["global_hidden"], 64)
        self.assertEqual(ncfg["proj_hidden"], 64)
        self.assertEqual(ncfg["fuse_hidden"], 256)
        self.assertEqual(ncfg["lstm_hidden"], 256)

    def test_train_parameters_for_60hz(self):
        cfg = tp.load_config(str(self.cfg_path))
        tcfg = cfg["train"]
        self.assertEqual(tcfg["n_steps"], 4096, "60Hz 下单批次推荐 4096 步 (约 1~2 局完整对局)")
        self.assertEqual(tcfg["seq_len"], 64, "60Hz 下 BPTT 推荐 64 步 (覆盖约 1.07s)")
        # Ticket 11 起改为 constant 微调, 不再线性退火
        self.assertEqual(tcfg.get("lr_mode"), "constant")
        self.assertFalse(tcfg.get("lr_anneal", False))
        self.assertGreater(tcfg["entropy_coef"], 0.0)

    def test_eval_parameters(self):
        cfg = tp.load_config(str(self.cfg_path))
        ecfg = cfg.get("eval", {})
        self.assertEqual(ecfg.get("interval"), 25, "eval.interval 应为 25")
        self.assertEqual(ecfg.get("episodes"), 50, "eval.episodes 应为 50")

    def test_curriculum_matches_difficulty_and_isolation(self):
        cfg = tp.load_config(str(self.cfg_path))
        cur = Curriculum.from_config(cfg)
        self.assertTrue(cfg["curriculum"]["enabled"])
        self.assertEqual(cur.stages[0].com_levels, (2,))
        self.assertEqual(cur.stages[0].name, "com3")
        self.assertIn("60hz", cfg["checkpoint"]["dir"])


class TestPolicyNetOrthogonalInitialization(unittest.TestCase):
    """技术要点 1: PolicyNet 全新网络架构、55 动作空间与正交初始化。"""

    def test_action_space_is_56(self):
        self.assertEqual(N_ACTIONS, 56, "扩展 Tag 后动作空间为 56")

    def test_orthogonal_initialization_properties(self):
        net = PolicyNet({"orthogonal_init": True})

        # 1. Actor head: gain = 0.01 (Gram 矩阵 W W^T 严格等于 gain^2 * I_55, 包括对角线与非对角正交性)
        actor_w = net.actor.weight.detach()
        gram_actor = torch.matmul(actor_w, actor_w.t())
        expected_gram = torch.eye(56) * (0.01 ** 2)
        self.assertTrue(torch.allclose(gram_actor, expected_gram, atol=1e-6),
                        "Actor 权重 Gram 矩阵必须严格等于 gain^2 * I_56 (行向量互相正交且范数等于 0.01)")
        self.assertTrue(torch.allclose(net.actor.bias, torch.zeros_like(net.actor.bias)))

        # 2. Critic head: gain = 1.0 (向量范数严格等于 1.0)
        critic_w = net.critic.weight.detach()
        critic_norm = torch.norm(critic_w)
        self.assertAlmostEqual(float(critic_norm), 1.0, places=5)
        self.assertTrue(torch.allclose(net.critic.bias, torch.zeros_like(net.critic.bias)))

        # 3. 隐藏层 Linear 偏置全部归零
        for name, module in net.named_modules():
            if isinstance(module, torch.nn.Linear) and module.bias is not None:
                self.assertTrue(
                    torch.allclose(module.bias, torch.zeros_like(module.bias)),
                    f"层 {name} 的 bias 未被归零",
                )

    def test_orthogonal_init_disabled_has_default_variance(self):
        net = PolicyNet({"orthogonal_init": False})
        actor_w = net.actor.weight.detach()
        gram_actor = torch.matmul(actor_w, actor_w.t())
        expected_gram = torch.eye(56) * (0.01 ** 2)
        self.assertFalse(torch.allclose(gram_actor, expected_gram, atol=1e-4),
                         "未配置 orthogonal_init 时不应具有 gain=0.01 的正交收缩特性")

    def test_forward_pass_with_full_perception_dims(self):
        net = PolicyNet({"orthogonal_init": True})
        batch_size = 2
        dummy_batch = {
            "self": torch.randn(batch_size, obs_builder.SELF_DIM),
            "opp": torch.randn(batch_size, obs_builder.OPP_DIM),
            "global": torch.randn(batch_size, obs_builder.GLOBAL_DIM),
            "proj": torch.randn(batch_size, obs_builder.MAX_PROJ, obs_builder.PROJ_DIMS),
            "proj_mask": torch.ones(batch_size, obs_builder.MAX_PROJ),
            "motion_self": torch.zeros(batch_size, dtype=torch.long),
            "motion_opp": torch.zeros(batch_size, dtype=torch.long),
            "action_mask": torch.ones(batch_size, 56, dtype=torch.bool),
        }
        hidden = net.init_hidden(batch_size)
        logits, value, h_out = net.forward(dummy_batch, hidden, seq=False)
        self.assertEqual(logits.shape, (batch_size, 56))
        self.assertEqual(value.shape, (batch_size,))


class Test60HzHeadlessHoldGuardAndLaserFeatures(unittest.TestCase):
    """技术要点 2 & 3: 60Hz 环境步进、Hold 格挡状态机与激光射线感知。"""

    def _mock_state(self, is_beam: bool = False):
        st = {
            "frame": 100,
            "battle_state": 8,
            "time": 8000,
            "time_unit": 90,
            "round": 1,
            "winner": 0,
            "teams": [
                {
                    "index": 0, "life": 10000, "life_max": 10000, "regain": 0,
                    "sp": 1000, "sp_max": 1000, "mp": 1000, "op": 0,
                    "occult": 0, "occult_max": 2000, "spell_active": False,
                    "spell_use_count": 0, "combo_count": 0, "current": 0,
                    "com_difficulty": 1, "master_name": "reimu", "slave_name": "marisa",
                    "master": {"x": 300.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
                               "motion": 0, "key_take": 0, "key_frame": 0, "hit_stop": 0,
                               "damage_stop": 0, "invin": 0, "baria": 0, "ground": 1,
                               "hit_state": 0, "guard_state": 0},
                    "slave": {"x": 300.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": 1.0,
                              "motion": 0, "key_take": 0, "key_frame": 0, "hit_stop": 0,
                              "damage_stop": 0, "invin": 0, "baria": 0, "ground": 1,
                              "hit_state": 0, "guard_state": 0},
                },
                {
                    "index": 1, "life": 10000, "life_max": 10000, "regain": 0,
                    "sp": 1000, "sp_max": 1000, "mp": 1000, "op": 0,
                    "occult": 0, "occult_max": 2000, "spell_active": False,
                    "spell_use_count": 0, "combo_count": 0, "current": 0,
                    "com_difficulty": 2, "master_name": "marisa", "slave_name": "reimu",
                    "master": {"x": 900.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": -1.0,
                               "motion": 4000 if is_beam else 0, "key_take": 0, "key_frame": 10,
                               "hit_stop": 0, "damage_stop": 0, "invin": 0, "baria": 0,
                               "ground": 1, "hit_state": 0, "guard_state": 0},
                    "slave": {"x": 900.0, "y": 360.0, "vx": 0.0, "vy": 0.0, "dir": -1.0,
                              "motion": 0, "key_take": 0, "key_frame": 0, "hit_stop": 0,
                              "damage_stop": 0, "invin": 0, "baria": 0, "ground": 1,
                              "hit_state": 0, "guard_state": 0},
                },
            ],
            "shots": [],
        }
        if is_beam:
            st["shots"].append({
                "owner": 1,
                "x": 900.0, "y": 360.0, "vx": -15.0, "vy": 0.0, "dir": -1.0,
                "motion": 4009,
                "is_beam": 1,
                "angle": 3.14159,
                "width": 80.0,
                "length": 800.0,
                "box_l": 100.0, "box_t": 320.0, "box_r": 900.0, "box_b": 400.0,
            })
        return st

    def test_60hz_env_laser_perception_and_hold_engine(self):
        state_container = [self._mock_state(is_beam=True)]
        injected_actions = []

        def fake_state():
            st = dict(state_container[0])
            st["frame"] += 1
            state_container[0] = st
            return st

        def fake_action(act_tuple):
            injected_actions.append(act_tuple)

        env = Th155Env(
            action_repeat=1,
            max_episode_steps=9400,
            state_fn=fake_state,
            action_fn=fake_action,
            reset_fn=lambda *a, **kw: True,
        )
        obs, info = env.reset()

        # 1. 验证 60Hz 激光感知特征生效
        self.assertEqual(env.action_repeat, 1)
        proj = obs["proj"]
        self.assertEqual(proj.shape, (obs_builder.MAX_PROJ, obs_builder.PROJ_DIMS))
        self.assertEqual(proj[0, obs_builder.PROJ_COL_IS_BEAM], 1.0, "激光射线 is_beam 必须被识别为 1.0")
        self.assertGreater(proj[0, obs_builder.PROJ_COL_BEAM_THREAT], 0.5, "轴线附近激光威胁度必须显著高于 0")
        # 全局预警特征
        self.assertEqual(obs["global"][-1], 1.0, "对手释放激光前摇时 laser_startup_warning 必须激活")

        # 2. 验证持续格挡状态机 (Hold Guard Engine)
        guard_act = action_space.GUARD_HOLD_ACTION  # 站立后退防御 (4 + none)
        env.step(guard_act)
        self.assertTrue(env.hold_engine.is_holding())
        self.assertEqual(env.hold_engine.hold_ticks_remaining, 20)

        # 持续 10 步输入非防御动作 (如 0: 5 none), 验证底层按键仍然被锁定为背向对手后退
        for step_i in range(10):
            env.step(0)
            self.assertTrue(env.hold_engine.is_holding())
            last_act = injected_actions[-1]
            last_x = last_act[0]
            # 对手在右方 (x=900 > x=300), 我方背向对手为向左 (x=-1)
            self.assertEqual(last_x, -1, f"第 {step_i} 步防守锁定按键 x 应为 -1 (后退)")


class TestColdStartTrainingLoopAndMetadataCheckpoints(unittest.TestCase):
    """验收标准 2 & 3: 从 update=0 启动冷启动训练并保存包含完整元数据字典的 Checkpoint 与 best.pt。"""

    def test_coldstart_training_saves_metadata(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg = {
                "env": {
                    "team": 0,
                    "action_repeat": 1,
                    "difficulty": 2,
                    "max_episode_steps": 16,
                    "frame_timeout": 1.0,
                },
                "network": {
                    "motion_vocab": 4096,
                    "motion_embed": 16,
                    "team_hidden": 32,
                    "global_hidden": 16,
                    "proj_hidden": 16,
                    "fuse_hidden": 32,
                    "lstm_hidden": 32,
                    "lstm_layers": 1,
                    "orthogonal_init": True,
                },
                "train": {
                    "total_updates": 4,
                    "n_steps": 16,
                    "seq_len": 4,
                    "epochs": 1,
                    "minibatch_seqs": 2,
                    "gamma": 0.99,
                    "gae_lambda": 0.95,
                    "clip_range": 0.2,
                    "value_coef": 0.5,
                    "entropy_coef": 0.02,
                    "max_grad_norm": 0.5,
                    "lr": 2.0e-4,
                    "lr_anneal": True,
                    "lr_final_scale": 0.33,
                    "target_kl": 0.05,
                    "seed": 42,
                    "orthogonal_init": True,
                },
                "checkpoint": {
                    "dir": str(tmp_path / "checkpoints"),
                    "save_every": 2,
                    "keep": 5,
                },
                "logging": {
                    "tensorboard": str(tmp_path / "tb"),
                },
                "curriculum": {
                    "enabled": False,
                },
                "eval": {
                    "interval": 2,
                    "episodes": 4,
                },
            }

            env = _OfflineEnv(max_episode_steps=8, action_repeat=1)
            eval_env = _OfflineEnv(max_episode_steps=8, action_repeat=1)

            stats = train(
                cfg,
                env=env,
                eval_env=eval_env,
                verbose=False,
                init_state=None,  # 显式从零冷启动
            )

            self.assertEqual(len(stats.learning_rates), 4)

            ckpt_dir = tmp_path / "checkpoints"
            self.assertTrue(ckpt_dir.exists())

            # 1. 验证周期性 checkpoint 生成与元数据
            p2 = ckpt_dir / "ppo_000002.pt"
            p4 = ckpt_dir / "ppo_000004.pt"
            self.assertTrue(p2.exists(), "未生成周期 checkpoint ppo_000002.pt")
            self.assertTrue(p4.exists(), "未生成周期 checkpoint ppo_000004.pt")

            meta2 = torch.load(p2, map_location="cpu")
            self.assertEqual(meta2["update"], 2)
            self.assertIn("best_eval_win_rate", meta2)
            self.assertIn("model_state_dict", meta2)
            self.assertIn("optimizer_state_dict", meta2)
            self.assertIn("lr_scheduler_state_dict", meta2)

            # 2. 验证 best.pt 固化完整元数据字典 (Ticket 01 规范)
            best_pt = ckpt_dir / "best.pt"
            self.assertTrue(best_pt.exists(), "未生成 best.pt")
            best_meta = torch.load(best_pt, map_location="cpu")
            required_keys = (
                "update",
                "global_step",
                "best_eval_win_rate",
                "eval_episodes",
                "eval_ci_low",
                "eval_ci_high",
                "model_state_dict",
                "optimizer_state_dict",
                "lr_scheduler_state_dict",
            )
            for k in required_keys:
                self.assertIn(k, best_meta, f"best.pt 缺失元数据字段: {k}")
                self.assertIsNotNone(best_meta[k], f"best.pt 元数据字段为 None: {k}")

    def test_cold_start_flag_discards_init_state(self):
        """验证 cold_start=True 时能够显式丢弃旧权重与污染状态，强制从 update=0 重新冷启动。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg = {
                "env": {"team": 0, "action_repeat": 1, "difficulty": 2, "max_episode_steps": 8, "frame_timeout": 1.0},
                "network": {
                    "motion_vocab": 4096, "motion_embed": 16, "team_hidden": 32, "global_hidden": 16,
                    "proj_hidden": 16, "fuse_hidden": 32, "lstm_hidden": 32, "lstm_layers": 1,
                    "orthogonal_init": True,
                },
                "train": {
                    "total_updates": 2, "n_steps": 16, "seq_len": 4, "epochs": 1, "minibatch_seqs": 2,
                    "gamma": 0.99, "gae_lambda": 0.95, "clip_range": 0.2, "value_coef": 0.5,
                    "entropy_coef": 0.02, "max_grad_norm": 0.5, "lr": 2.0e-4, "lr_anneal": True,
                    "lr_final_scale": 0.33, "target_kl": 0.05, "seed": 42, "orthogonal_init": True,
                },
                "checkpoint": {"dir": str(tmp_path / "checkpoints"), "save_every": 1, "keep": 5},
                "logging": {"tensorboard": str(tmp_path / "tb")},
                "curriculum": {"enabled": False},
                "eval": {"interval": 1, "episodes": 2},
            }
            # 制造包含非零历史步数与异构损坏参数的 fake init_state
            fake_state = {
                "update": 999,
                "global_step": 999999,
                "best_eval_win_rate": 0.99,
                "model_state_dict": {},
            }
            stats = train(
                cfg,
                env=_OfflineEnv(max_episode_steps=8, action_repeat=1),
                eval_env=_OfflineEnv(max_episode_steps=8, action_repeat=1),
                verbose=False,
                init_state=fake_state,
                cold_start=True,  # 强制丢弃 fake_state
            )
            # 验证新产出的 checkpoint 是从 update=1, 2 编号，而非在 999 基础上继续
            p1 = tmp_path / "checkpoints" / "ppo_000001.pt"
            p2 = tmp_path / "checkpoints" / "ppo_000002.pt"
            self.assertTrue(p1.exists(), "冷启动应从 ppo_000001.pt 开始保存")
            self.assertTrue(p2.exists(), "冷启动应从 ppo_000002.pt 保存")
            p2_meta = torch.load(p2, map_location="cpu")
            self.assertEqual(p2_meta["update"], 2)

    def test_cold_start_with_existing_best_uses_baseline(self):
        """冷启动目录里已有 best.pt 时, 必须读取其 CI 基线, 不能 NameError 或首轮覆盖。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ckpt_dir = tmp_path / "checkpoints"
            ckpt_dir.mkdir(parents=True, exist_ok=True)
            torch.save(
                {
                    "update": 765,
                    "best_eval_win_rate": 0.7917,
                    "eval_win_rate": 0.7917,
                    "eval_ci_low": 0.6574,
                    "eval_ci_high": 0.8827,
                    "eval_episodes": 48,
                },
                ckpt_dir / "best.pt",
            )
            cfg = {
                "env": {"team": 0, "action_repeat": 1, "difficulty": 2, "max_episode_steps": 8, "frame_timeout": 1.0},
                "network": {
                    "motion_vocab": 4096, "motion_embed": 16, "team_hidden": 32, "global_hidden": 16,
                    "proj_hidden": 16, "fuse_hidden": 32, "lstm_hidden": 32, "lstm_layers": 1,
                    "orthogonal_init": True,
                },
                "train": {
                    "total_updates": 2, "n_steps": 16, "seq_len": 4, "epochs": 1, "minibatch_seqs": 2,
                    "gamma": 0.99, "gae_lambda": 0.95, "clip_range": 0.2, "value_coef": 0.5,
                    "entropy_coef": 0.02, "max_grad_norm": 0.5, "lr": 2.0e-4, "lr_anneal": True,
                    "lr_final_scale": 0.33, "target_kl": 0.05, "seed": 42, "orthogonal_init": True,
                },
                "checkpoint": {"dir": str(ckpt_dir), "save_every": 1, "keep": 5},
                "logging": {"tensorboard": str(tmp_path / "tb")},
                "curriculum": {"enabled": False},
                "eval": {"interval": 1, "episodes": 2},
            }
            train(
                cfg,
                env=_OfflineEnv(max_episode_steps=8, action_repeat=1),
                eval_env=_OfflineEnv(max_episode_steps=8, action_repeat=1),
                verbose=False,
                cold_start=True,
            )
            best = torch.load(ckpt_dir / "best.pt", map_location="cpu")
            self.assertAlmostEqual(best["best_eval_win_rate"], 0.7917)
            self.assertAlmostEqual(best["eval_ci_low"], 0.6574)
            self.assertEqual(best["eval_episodes"], 48)

    def test_freeze_trunk_only_actor_changes(self):
        """freeze_trunk=True 时只有 actor head 变化, LSTM/trunk/critic 必须 bit-identical。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            ckpt_dir = tmp_path / "checkpoints"
            ckpt_dir.mkdir(parents=True, exist_ok=True)
            cfg = {
                "env": {"team": 0, "action_repeat": 1, "difficulty": 2, "max_episode_steps": 8, "frame_timeout": 1.0},
                "network": {
                    "motion_vocab": 4096, "motion_embed": 16, "team_hidden": 32, "global_hidden": 16,
                    "proj_hidden": 16, "fuse_hidden": 32, "lstm_hidden": 32, "lstm_layers": 1,
                    "orthogonal_init": True,
                },
                "train": {
                    "total_updates": 1, "n_steps": 16, "seq_len": 4, "epochs": 2, "minibatch_seqs": 2,
                    "gamma": 0.99, "gae_lambda": 0.95, "clip_range": 0.2, "value_coef": 0.5,
                    "entropy_coef": 0.02, "max_grad_norm": 0.5, "lr": 1.0e-3, "lr_anneal": False,
                    "lr_final_scale": 1.0, "target_kl": 0.05, "seed": 42, "orthogonal_init": True,
                },
                "checkpoint": {"dir": str(ckpt_dir), "save_every": 1, "keep": 5},
                "logging": {"tensorboard": str(tmp_path / "tb")},
                "curriculum": {"enabled": False},
                "eval": {"interval": 99, "episodes": 1},
            }
            init_net = PolicyNet(cfg["network"])
            init_sd = {k: v.detach().clone() for k, v in init_net.state_dict().items()}
            torch.save({"update": 0, "model_state_dict": init_net.state_dict()},
                       ckpt_dir / "init.pt")
            train(
                cfg,
                env=_OfflineEnv(max_episode_steps=8, action_repeat=1),
                eval_env=_OfflineEnv(max_episode_steps=8, action_repeat=1),
                verbose=False,
                init_state=torch.load(ckpt_dir / "init.pt", map_location="cpu"),
                freeze_trunk=True,
            )
            new_sd = torch.load(ckpt_dir / "ppo_000001.pt", map_location="cpu")["model_state_dict"]
            actor_changed = False
            for k, v in init_sd.items():
                same = torch.equal(v, new_sd[k])
                if k.startswith("actor."):
                    actor_changed = actor_changed or not same
                else:
                    self.assertTrue(same, f"冻结参数被更新: {k}")
            self.assertTrue(actor_changed, "actor head 未被更新")

    def test_final_update_saves_checkpoint_even_if_not_save_every(self):
        """验证即使总轮次不是 save_every 的整倍数，终局更新依然会落盘并生成 best.pt。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg = {
                "env": {"team": 0, "action_repeat": 1, "difficulty": 2, "max_episode_steps": 8, "frame_timeout": 1.0},
                "network": {
                    "motion_vocab": 4096, "motion_embed": 16, "team_hidden": 32, "global_hidden": 16,
                    "proj_hidden": 16, "fuse_hidden": 32, "lstm_hidden": 32, "lstm_layers": 1,
                    "orthogonal_init": True,
                },
                "train": {
                    "total_updates": 3, "n_steps": 16, "seq_len": 4, "epochs": 1, "minibatch_seqs": 2,
                    "gamma": 0.99, "gae_lambda": 0.95, "clip_range": 0.2, "value_coef": 0.5,
                    "entropy_coef": 0.02, "max_grad_norm": 0.5, "lr": 2.0e-4, "lr_anneal": True,
                    "lr_final_scale": 0.33, "target_kl": 0.05, "seed": 42, "orthogonal_init": True,
                },
                "checkpoint": {"dir": str(tmp_path / "checkpoints"), "save_every": 10, "keep": 5},
                "logging": {"tensorboard": str(tmp_path / "tb")},
                "curriculum": {"enabled": False},
                "eval": {"interval": 15, "episodes": 2},  # 评估周期 15 大于总步数 3
            }
            stats = train(
                cfg,
                env=_OfflineEnv(max_episode_steps=8, action_repeat=1),
                eval_env=_OfflineEnv(max_episode_steps=8, action_repeat=1),
                verbose=False,
                cold_start=True,
            )
            # 步数 3 并非 10 的倍数，但终局保存机制必须确保 ppo_000003.pt 与首发 best.pt 固化
            p3 = tmp_path / "checkpoints" / "ppo_000003.pt"
            best_pt = tmp_path / "checkpoints" / "best.pt"
            self.assertTrue(p3.exists(), "终局 update=3 必须强制落盘")
            self.assertTrue(best_pt.exists(), "首轮冷启动结束时即使未达 eval_interval 必须生成初始基准 best.pt")


class TestWatchdogColdStartSupport(unittest.TestCase):
    """技术要点 3: 守护脚本对零 Checkpoint 冷启动与基线锚定的无缝支持。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.stop_file = self.dir_path / "STOP_TRAINING"
        self.log_file = self.dir_path / "watchdog.log"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_watchdog_step_handles_cold_start_without_prior_checkpoints(self):
        breaker = WatchdogCircuitBreaker(
            stop_file=self.stop_file,
            log_file=self.log_file,
        )

        def mock_runner(cmd):
            # 模拟冷启动运行: 确保命令行不带 --resume, 而是带 --cold-start
            self.assertIn("--cold-start", cmd)
            self.assertNotIn("--resume", cmd)
            # 模拟生成首批 checkpoints
            time.sleep(0.01)
            ckpt_data = {
                "update": 10,
                "global_step": 40960,
                "best_eval_win_rate": 0.45,
                "eval_win_rate": 0.45,
                "eval_ci_low": 0.30,
                "eval_ci_high": 0.61,
                "eval_episodes": 30,
                "model_state_dict": {},
                "optimizer_state_dict": {},
                "lr_scheduler_state_dict": {},
            }
            torch.save(ckpt_data, self.dir_path / "ppo_000010.pt")
            torch.save(ckpt_data, self.dir_path / "best.pt")
            return 0

        rc, decision = watchdog_train.step_watchdog(
            breaker=breaker,
            ckpt_dir=self.dir_path,
            config="fake_60hz_coldstart.yaml",
            train_log=self.dir_path / "train.log",
            runner=mock_runner,
            allow_cold_start=True,
        )

        self.assertEqual(rc, 0)
        self.assertEqual(decision.action, "CONTINUE")
        self.assertAlmostEqual(breaker.baseline_win_rate, 0.45)
        self.assertAlmostEqual(breaker.best_ci_low, 0.30)
        self.assertFalse(self.stop_file.exists())


class TestBaselineEvaluationReportGeneration(unittest.TestCase):
    """验收标准 4: 产出首份 60Hz 全感知基线报告。"""

    def test_report_generation_structure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir) / "reports"
            net = PolicyNet({"orthogonal_init": True})
            env = _OfflineEnv(max_episode_steps=6, action_repeat=1)

            evaluator = Evaluator(env=env, default_episodes=6)
            summary = evaluator.run(net, deterministic=True, episodes=6, com_level=2)

            self.assertEqual(summary["matches"], 6)
            self.assertIn("win_rate", summary)
            self.assertIn("win_rate_ci", summary)
            self.assertIn("mean_hp_diff", summary)

            md_path, js_path = write_reports(summary, out_dir=str(out_dir), ckpt="checkpoints/com3_60hz_coldstart/best.pt")
            self.assertTrue(Path(md_path).exists())
            self.assertTrue(Path(js_path).exists())

            md_content = Path(md_path).read_text(encoding="utf-8")
            self.assertIn("th155 评估报告", md_content)
            self.assertIn("COM3", md_content)
            self.assertIn("95% CI", md_content)

    def test_run_baseline_pipeline_offline_e2e(self):
        """完整端到端测试: 运行 run_baseline 离线流水线并校验 checkpoint 与报告产出。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            out_reports = tmp_path / "runs" / "eval"
            ckpt_custom_dir = tmp_path / "checkpoints" / "com3_60hz_coldstart"

            # 准备临时测试配置覆盖 checkpoint 目录
            cfg_path = tmp_path / "test_config.yaml"
            with open(ROOT / "configs" / "train_ppo_com3_60hz_coldstart.yaml", encoding="utf-8") as f:
                base_cfg = yaml.safe_load(f)
            base_cfg["checkpoint"]["dir"] = str(ckpt_custom_dir)
            base_cfg["checkpoint"]["save_every"] = 2
            base_cfg["train"]["n_steps"] = 16
            base_cfg["train"]["seq_len"] = 4
            base_cfg["train"]["epochs"] = 1
            base_cfg["train"]["minibatch_seqs"] = 2
            base_cfg["logging"]["tensorboard"] = str(tmp_path / "tb")
            with open(cfg_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(base_cfg, f)

            res = run_baseline(
                config_path=str(cfg_path),
                updates=2,
                eval_episodes=2,
                eval_interval=1,
                offline=True,
                out_dir=str(out_reports),
                verbose=False,
            )

            # 1. 验证必须实际保存了 best.pt 与 ppo_000002.pt
            self.assertTrue(ckpt_custom_dir.exists())
            best_pt = ckpt_custom_dir / "best.pt"
            p2_pt = ckpt_custom_dir / "ppo_000002.pt"
            self.assertTrue(best_pt.exists(), "run_baseline 必须落盘真实 best.pt 权重")
            self.assertTrue(p2_pt.exists(), "run_baseline 必须落盘周期 checkpoint")

            # 2. 验证 best.pt 载入有效且元数据完整
            best_sd = torch.load(best_pt, map_location="cpu")
            self.assertIn("model_state_dict", best_sd)
            self.assertIn("best_eval_win_rate", best_sd)

            # 3. 验证产出的报告文件真实存在且内容自洽
            md_file = out_reports / "eval_com3_60hz_coldstart.md"
            js_file = out_reports / "eval_com3_60hz_coldstart.json"
            self.assertTrue(md_file.exists(), "必须产出 Markdown 评估报告")
            self.assertTrue(js_file.exists(), "必须产出 JSON 评估报告")

            js_data = json.loads(js_file.read_text(encoding="utf-8"))
            self.assertEqual(js_data["architecture"]["hz"], 60)
            self.assertEqual(js_data["architecture"]["actions"], 56)
            self.assertEqual(js_data["updates"], 2)
            self.assertEqual(js_data["summary"]["matches"], 2)
            self.assertIn("best.pt", js_data["checkpoint"])

    def test_run_baseline_missing_checkpoint_raises(self):
        """验证当训练未生成任何 Checkpoint 时，run_baseline 不得伪造报告，必须抛出 FileNotFoundError。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg_path = tmp_path / "empty_cfg.yaml"
            with open(ROOT / "configs" / "train_ppo_com3_60hz_coldstart.yaml", encoding="utf-8") as f:
                base_cfg = yaml.safe_load(f)
            base_cfg["checkpoint"]["dir"] = str(tmp_path / "empty_checkpoints")
            with open(cfg_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(base_cfg, f)

            with unittest.mock.patch("scripts.run_60hz_coldstart_baseline.train") as mock_train:
                # 模拟 train 结束但目录未生成任何权重
                mock_train.return_value = tp.Stats()
                with self.assertRaises(FileNotFoundError):
                    run_baseline(
                        config_path=str(cfg_path),
                        updates=2,
                        eval_episodes=2,
                        offline=True,
                        out_dir=str(tmp_path / "reports"),
                        verbose=False,
                    )


class TestBestSelectionGate(unittest.TestCase):
    """best.pt 只接受"点估计和 CI 下限都提升"的进步, 防止上尾噪声被固化。"""

    def test_higher_point_with_worse_ci_low_is_rejected(self):
        # 小样本 70% (CI 下限 0.40) 不能覆盖 59% 的稳定基线 (CI 下限 0.49)
        self.assertFalse(tp.is_significant_best(0.70, 0.40, 0.59, 0.49))

    def test_higher_point_and_ci_low_is_accepted(self):
        self.assertTrue(tp.is_significant_best(0.66, 0.52, 0.59, 0.49))

    def test_lower_point_is_rejected(self):
        self.assertFalse(tp.is_significant_best(0.58, 0.50, 0.59, 0.49))


class TestEvalModeMetadata(unittest.TestCase):
    """评估口径必须随 best.pt 落盘, 跨口径比较要告警, 否则门控拿 argmax 去打采样基线。"""

    def test_mode_names(self):
        self.assertEqual(tp.eval_mode_name(True), "argmax")
        self.assertEqual(tp.eval_mode_name(False), "sample")

    def test_unrecorded_baseline_does_not_warn(self):
        # 历史 checkpoint 没有该字段, 视为"未记录", 不阻断续训
        self.assertIsNone(tp.baseline_mode_warning(None, "sample"))
        self.assertIsNone(tp.baseline_mode_warning(None, "argmax"))

    def test_same_mode_does_not_warn(self):
        self.assertIsNone(tp.baseline_mode_warning("sample", "sample"))
        self.assertIsNone(tp.baseline_mode_warning("argmax", "argmax"))

    def test_cross_mode_warns_and_names_both_sides(self):
        msg = tp.baseline_mode_warning("sample", "argmax")
        self.assertIn("sample", msg)
        self.assertIn("argmax", msg)


if __name__ == "__main__":
    unittest.main()
