"""Unit tests for src/train/posttrain_replay.py (synthetic trajectories only).

No game, no real checkpoints: tiny PolicyNet on CPU, fully seeded.
"""
from __future__ import annotations

import copy
import sys
import unittest

from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents.ppo_agent import N_ACTIONS, PolicyNet  # noqa: E402
from env import obs_builder  # noqa: E402
from train.posttrain_replay import (  # noqa: E402
    action_group,
    apply_logit_temperature,
    apply_trainable_scope,
    compute_step_weights,
    evaluate_dataset,
    gae,
    load_base_checkpoint,
    _match_manifest,
    load_selected_trajectories,
    load_trajectory,
    posttrain_epoch,
    prepare_rollouts,
    read_perspective_id,
    run_posttrain,
    save_trajectory,
)

TINY_CFG = {"motion_vocab": 64, "motion_embed": 4, "team_hidden": 16,
            "global_hidden": 8, "proj_hidden": 8, "fuse_hidden": 32,
            "lstm_hidden": 32, "lstm_layers": 1}


def make_traj(t: int = 40, seed: int = 0, done_at: tuple = (),
              invalid: tuple = ()) -> dict:
    """合成轨迹:随机观测、全 1 掩码、固定随机动作、``invalid`` 步 bc 无效。"""
    rng = np.random.default_rng(seed)
    traj = {
        "self": (rng.standard_normal((t, obs_builder.SELF_DIM)) * 0.3).astype(np.float32),
        "opp": (rng.standard_normal((t, obs_builder.OPP_DIM)) * 0.3).astype(np.float32),
        "global": (rng.standard_normal((t, obs_builder.GLOBAL_DIM)) * 0.3).astype(np.float32),
        "proj": (rng.standard_normal((t, obs_builder.MAX_PROJ, obs_builder.PROJ_DIMS))
                 * 0.3).astype(np.float32),
        "proj_mask": (rng.random((t, obs_builder.MAX_PROJ)) > 0.5),
        "motion_self": rng.integers(0, 64, size=t).astype(np.int64),
        "motion_opp": rng.integers(0, 64, size=t).astype(np.int64),
        "action_mask": np.ones((t, N_ACTIONS), dtype=bool),
        "actions": rng.integers(0, N_ACTIONS, size=t).astype(np.int64),
        "bc_valid": np.ones(t, dtype=bool),
        "rewards": (rng.standard_normal(t) * 0.1).astype(np.float32),
        "dones": np.zeros(t, dtype=bool),
    }
    for d in done_at:
        traj["dones"][d] = True
    for s in invalid:
        traj["bc_valid"][s] = False
    return traj


def make_nets(seed: int = 0):
    torch.manual_seed(seed)
    net = PolicyNet(TINY_CFG)
    ref = copy.deepcopy(net)
    ref.requires_grad_(False)
    ref.eval()
    return net, ref


def prep(trajs, net, ref, **kw):
    return prepare_rollouts(trajs, net, ref, seq_len=64, device="cpu", **kw)


def snap(net):
    return {k: v.detach().cpu().clone() for k, v in net.state_dict().items()}


def same_state(a, b):
    return all(torch.equal(a[k], b[k]) for k in a)


class TestBcMasking(unittest.TestCase):
    def test_all_invalid_epoch_is_noop(self):
        torch.manual_seed(0)
        np.random.seed(0)
        net, ref = make_nets()
        rolls = prep([make_traj(40, 1, invalid=tuple(range(40)))], net, ref)
        before = snap(net)
        opt = torch.optim.Adam(net.parameters(), lr=3e-3)
        np.random.seed(0)
        logs = posttrain_epoch(net, ref, opt, rolls, mode="sft", bc_coef=1.0,
                               kl_coef=0.0, value_coef=0.0, entropy_coef=0.0)
        self.assertEqual(logs["bc_loss"], 0.0)
        self.assertTrue(same_state(before, snap(net)))

    def test_invalid_step_actions_do_not_matter(self):
        torch.manual_seed(0)
        base = make_traj(40, 7, invalid=tuple(range(10, 16)))
        alt = copy.deepcopy(base)
        alt["actions"][10:16] = (base["actions"][10:16] + 13) % N_ACTIONS
        results = []
        metrics = []
        for traj in (base, alt):
            torch.manual_seed(0)
            net, ref = make_nets()
            rolls = prep([traj], net, ref)
            opt = torch.optim.Adam(net.parameters(), lr=3e-3)
            np.random.seed(0)
            posttrain_epoch(net, ref, opt, rolls, mode="sft", bc_coef=1.0,
                            kl_coef=0.0, value_coef=0.0, entropy_coef=0.0)
            results.append(snap(net))
            metrics.append(evaluate_dataset(net, ref, rolls))
        self.assertTrue(same_state(results[0], results[1]))
        for k in ("bc_nll", "top1_acc", "top3_acc"):
            self.assertAlmostEqual(metrics[0][k], metrics[1][k], places=6)


class TestFreezeCritic(unittest.TestCase):
    def test_critic_bit_identical(self):
        torch.manual_seed(0)
        np.random.seed(0)
        net, ref = make_nets()
        rolls = prep([make_traj(40, 3)], net, ref)
        before = snap(net)
        opt = torch.optim.Adam(net.parameters(), lr=3e-3)
        np.random.seed(0)
        logs = posttrain_epoch(net, ref, opt, rolls, mode="sft", bc_coef=1.0,
                               kl_coef=0.05, value_coef=1.0, freeze_critic=True)
        after = snap(net)
        critic_keys = [k for k in before if k.startswith("critic.")]
        self.assertTrue(critic_keys)
        for k in critic_keys:
            self.assertTrue(torch.equal(before[k], after[k]), f"critic param {k} changed")
        self.assertEqual(logs["value_loss"], 0.0)
        actor_keys = [k for k in before if not k.startswith("critic.")]
        self.assertTrue(any(not torch.equal(before[k], after[k]) for k in actor_keys),
                        "actor should have learned something")


class TestOverfitKLBounded(unittest.TestCase):
    def test_overfit_drives_nll_down_kl_bounded(self):
        torch.manual_seed(0)
        np.random.seed(0)
        trajs = [make_traj(33, 11), make_traj(33, 12)]
        inits, finals, kls = {}, {}, {}
        for tag, kl_coef in (("free", 0.0), ("anchored", 0.1)):
            torch.manual_seed(0)
            net, ref = make_nets()
            rolls = prep(trajs, net, ref)
            m0 = evaluate_dataset(net, ref, rolls)
            opt = torch.optim.Adam(net.parameters(), lr=5e-3)
            for _ in range(30):
                np.random.seed(1)
                posttrain_epoch(net, ref, opt, rolls, mode="sft", bc_coef=1.0,
                                kl_coef=kl_coef, value_coef=0.5, critic_mode="anchor",
                                mb_seqs=2)
            m1 = evaluate_dataset(net, ref, rolls)
            inits[tag], finals[tag], kls[tag] = m0, m1, (m1["kl_div"], m0["kl_div"])
        m0, m1 = inits["anchored"], finals["anchored"]
        self.assertLess(m1["bc_nll"], m0["bc_nll"] - 0.3)
        self.assertGreater(m1["top1_acc"], m0["top1_acc"])
        self.assertGreater(m1["top1_acc"], 0.2)
        self.assertLessEqual(finals["anchored"]["kl_div"], finals["free"]["kl_div"] + 1e-6)
        self.assertLess(finals["anchored"]["kl_div"], 5.0)

    def test_awbc_epoch_runs(self):
        torch.manual_seed(0)
        np.random.seed(0)
        net, ref = make_nets()
        rolls = prep([make_traj(40, 15)], net, ref)
        opt = torch.optim.Adam(net.parameters(), lr=3e-3)
        np.random.seed(0)
        logs = posttrain_epoch(net, ref, opt, rolls, mode="awbc", awbc_beta=1.0,
                               bc_coef=1.0, kl_coef=0.0, value_coef=0.0,
                               entropy_coef=0.0)
        self.assertTrue(np.isfinite(logs["bc_loss"]))
        self.assertGreater(logs["n_bc_valid"], 0)


class TestHiddenReset(unittest.TestCase):
    def test_reset_across_dones(self):
        torch.manual_seed(0)
        net, ref = make_nets()
        rolls = prep([make_traj(20, 5, done_at=(9,))], net, ref)
        h_in, ref_h_in = rolls[0]["h_in"], rolls[0]["ref_h_in"]
        for arr in (h_in[0], h_in[1], ref_h_in[0], ref_h_in[1]):
            np.testing.assert_array_equal(arr[10], np.zeros_like(arr[10]))
        self.assertGreater(float(np.abs(h_in[0][5]).sum()), 0.0)
        starts = [s for s, _ in rolls[0]["seqs"]]
        self.assertIn(10, starts)


class TestGae(unittest.TestCase):
    def test_matches_train_ppo_compute_gae(self):
        from train.train_ppo import Rollout, compute_gae
        rng = np.random.default_rng(0)
        rewards = (rng.standard_normal(25) * 0.5).astype(np.float32)
        values = (rng.standard_normal(25) * 0.5).astype(np.float32)
        dones = np.zeros(25, dtype=bool)
        dones[[7, 20]] = True
        adv, ret = gae(rewards, values, dones, 0.995, 0.95, 0.3, False)
        roll = Rollout(rewards=rewards, values=values, dones=dones,
                       last_value=0.3, last_done=False)
        adv2, ret2 = compute_gae(roll, 0.995, 0.95)
        np.testing.assert_allclose(adv, adv2, rtol=1e-5)
        np.testing.assert_allclose(ret, ret2, rtol=1e-5)

    def test_truncation_semantics(self):
        adv, ret = gae(np.array([1.0]), np.array([0.5]), np.array([False]),
                       last_value=99.0, last_done=True)
        self.assertAlmostEqual(float(adv[0]), 0.5)  # last_value 被截断忽略
        self.assertAlmostEqual(float(ret[0]), 1.0)


class TestTrajectoryIO(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile
        traj = make_traj(17, 9, done_at=(16,))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t0.npz"
            save_trajectory(p, traj)
            back = load_trajectory(p)
        from train.posttrain_replay import TRAJ_KEYS
        self.assertEqual(set(back), set(TRAJ_KEYS))
        for k in TRAJ_KEYS:
            np.testing.assert_array_equal(np.asarray(back[k]), np.asarray(traj[k]))

    def test_action_groups_cover_all(self):
        seen = {action_group(a) for a in range(N_ACTIONS)}
        self.assertEqual(seen, {"move", "guard", "attack_ABC", "occult",
                                "dash_D", "spell", "tag"})


class TestRunPosttrain(unittest.TestCase):
    def test_integration_saves_checkpoints(self):
        import tempfile
        torch.manual_seed(0)
        np.random.seed(0)
        net, ref = make_nets()
        train_trajs = [make_traj(33, 21), make_traj(33, 22)]
        val_trajs = [make_traj(33, 23)]
        opt = torch.optim.Adam(net.parameters(), lr=3e-3)
        with tempfile.TemporaryDirectory() as d:
            result = run_posttrain(
                net, ref, opt,
                prepare_rollouts(train_trajs, net, ref),
                prepare_rollouts(val_trajs, net, ref),
                prepare_rollouts(val_trajs, net, ref),
                epochs=2, out_dir=d, base_meta={"win_rate": 0.6},
                posttrain_info={"test_run": True}, device="cpu",
                recompute_hidden_each_epoch=True,
                mb_seqs=2, mode="sft", kl_coef=0.05, value_coef=0.5,
                critic_mode="anchor")
            self.assertEqual(len(result["history"]), 3)  # epoch 0 基线 + 2 轮
            self.assertEqual([r["epoch"] for r in result["history"]], [0, 1, 2])
            self.assertIn("val_bc_nll", result["history"][0])  # 零样本基座基线
            self.assertIn("test_bc_nll", result["history"][0])
            self.assertTrue((Path(d) / "best_val.pt").exists())
            self.assertTrue((Path(d) / "last.pt").exists())
            self.assertIn("test", result)
            best = torch.load(str(Path(d) / "best_val.pt"), map_location="cpu",
                              weights_only=False)
            self.assertIn("posttrain_info", best)
            self.assertEqual(best["win_rate"], 0.6)  # 基座元数据保留


class TestBaseCheckpoint(unittest.TestCase):
    CKPT = ROOT / "checkpoints" / "com4_multi_b24k_cpun_e200" / "best.pt"

    def test_real_checkpoint_loads_strictly(self):
        import os
        if not os.path.exists(self.CKPT):
            self.skipTest("real best.pt not present")
        net, meta = load_base_checkpoint(self.CKPT)  # 缺键/迁移直接抛错
        self.assertEqual(net.motion.num_embeddings, 10240)
        self.assertEqual(tuple(net.actor.weight.shape), (N_ACTIONS, 256))
        self.assertEqual(net.lstm_hidden, 256)
        self.assertIn("model_state_dict", meta)

    def test_explicit_cfg_override_still_strict(self):
        import os
        if not os.path.exists(self.CKPT):
            self.skipTest("real best.pt not present")
        net, _ = load_base_checkpoint(self.CKPT, {"motion_vocab": 10240})
        self.assertEqual(net.motion.num_embeddings, 10240)


class TestManifestMatching(unittest.TestCase):
    SPLITS = ROOT / "configs" / "replay_splits.json"

    @classmethod
    def setUpClass(cls):
        import json
        with open(cls.SPLITS, encoding="utf-8") as f:
            cls.manifest = json.load(f)

    def test_batch_selection_counts(self):
        all_p = _match_manifest(self.manifest, "train", "all")
        self.assertEqual(len(all_p), 88)
        b1 = _match_manifest(self.manifest, "train", "batch_1")
        self.assertEqual(len(b1), 22)
        b13 = _match_manifest(self.manifest, "train", "batch_1,batch_3")
        self.assertEqual(len(b13), 44)
        self.assertEqual(set(b13), set(_match_manifest(self.manifest, "train", "batch_1"))
                         | set(_match_manifest(self.manifest, "train", "batch_3")))
        self.assertEqual(len(_match_manifest(self.manifest, "val")), 20)
        self.assertEqual(len(_match_manifest(self.manifest, "test")), 20)
        with self.assertRaises(ValueError):
            _match_manifest(self.manifest, "train", "batch_9")
        with self.assertRaises(ValueError):
            _match_manifest(self.manifest, "val", "batch_1")

    def test_exact_perspective_selection(self):
        import tempfile
        base = make_traj(17, 31)

        def _with(traj, **kw):
            t = dict(traj)
            t.update(kw)
            return t

        keyed = {
            "a0.npz": _with(base, source_file=np.asarray("250103/replay_212807.rep"),
                            team=np.asarray(0)),
            "a1.npz": _with(base, source_file=np.asarray("250103/replay_212807.rep"),
                            team=np.asarray(1)),
            "b.npz": _with(base, source_file=np.asarray("250104/replay_999999.rep"),
                           team=np.asarray(0)),
        }
        fallback = {
            "replay_212807_t0.npz": base,  # 无键:文件名退化匹配 stem+team
            "other_t1.npz": base,  # stem 不在候选中 -> 排除
            "ambig.npz": base,  # 无 team 后缀且无法定位 -> 排除
        }
        want = [("250103/replay_212807.rep", 0), ("250103/replay_212807.rep", 1)]
        with tempfile.TemporaryDirectory() as d:
            keyed_paths = []
            for name, traj in keyed.items():
                p = Path(d) / name
                save_trajectory(p, traj)
                keyed_paths.append(p)
            fb_paths = []
            for name, traj in fallback.items():
                p = Path(d) / name
                save_trajectory(p, traj)
                fb_paths.append(p)
            # 身份读取:键优先,文件名退化次之。
            self.assertEqual(read_perspective_id(keyed_paths[0]),
                             ("250103/replay_212807.rep", 0, "a0"))
            self.assertEqual(read_perspective_id(fb_paths[0]), (None, 0, "replay_212807"))
            # 键精确匹配:b(异文件)被排除。
            kept = load_selected_trajectories(keyed_paths, want)
            self.assertEqual(sorted(t["_perspective"] for t in kept), sorted(want))
            # 文件名退化:命中 stem+team 唯一候选,排除其他。
            kept_fb = load_selected_trajectories(fb_paths, [want[0]])
            self.assertEqual(len(kept_fb), 1)
            self.assertEqual(kept_fb[0]["_perspective"], want[0])
            # 同一视角出现两次(键文件 + 退化文件)只保留首个,不重复计数。
            both = load_selected_trajectories(keyed_paths[:1] + fb_paths[:1], [want[0]])
            self.assertEqual(len(both), 1)

class TestRefreshVectorized(unittest.TestCase):
    """向量化 refresh 与旧逐步循环的数学等价性(合成轨迹,含 done 边界/拖尾/短段)。"""

    @staticmethod
    def naive_refresh(trajs, net, ref, gamma=0.995, lam=0.95):
        from train.posttrain_replay import _obs_tensors
        layers, hidden = net.lstm_layers, net.lstm_hidden
        out = []
        with torch.no_grad():
            for traj in trajs:
                t = len(traj["actions"])
                obs = _obs_tensors(traj, "cpu")
                h = [torch.zeros(layers, 1, hidden) for _ in (0, 1)]
                rh = [torch.zeros(layers, 1, hidden) for _ in (0, 1)]
                h_in = np.zeros((2, t, layers, hidden), dtype=np.float32)
                rh_in = np.zeros((2, t, layers, hidden), dtype=np.float32)
                rv = np.zeros(t, dtype=np.float32)
                dones = np.asarray(traj["dones"]).astype(bool)
                for i in range(t):
                    if i > 0 and dones[i - 1]:
                        h = [torch.zeros(layers, 1, hidden) for _ in (0, 1)]
                        rh = [torch.zeros(layers, 1, hidden) for _ in (0, 1)]
                    h_in[0, i] = h[0][:, 0, :].numpy()
                    h_in[1, i] = h[1][:, 0, :].numpy()
                    rh_in[0, i] = rh[0][:, 0, :].numpy()
                    rh_in[1, i] = rh[1][:, 0, :].numpy()
                    single = {k: v[i:i + 1] for k, v in obs.items()}
                    _, _, h_new = net.forward(single, tuple(h), seq=False)
                    _, rv1, rh_new = ref.forward(single, tuple(rh), seq=False)
                    h, rh = list(h_new), list(rh_new)
                    rv[i] = float(rv1[0])
                adv, ret = gae(traj["rewards"], rv, dones, gamma, lam, 0.0, True)
                out.append({"h_in": (h_in[0], h_in[1]), "ref_h_in": (rh_in[0], rh_in[1]),
                            "ref_values": rv, "adv": adv, "ret": ret})
        return out

    def check_equivalent(self, trajs, layers):
        cfg = dict(TINY_CFG)
        cfg["lstm_layers"] = layers
        torch.manual_seed(0)
        net = PolicyNet(cfg)
        ref = copy.deepcopy(net)
        ref.requires_grad_(False)
        ref.eval()
        rolls = prepare_rollouts(trajs, net, ref, seq_len=64, device="cpu")
        want = self.naive_refresh(trajs, net, ref)
        self.assertEqual(len(rolls), len(want))
        for got, exp in zip(rolls, want):
            for k in ("h_in", "ref_h_in"):
                for a, b in zip(got[k], exp[k]):
                    np.testing.assert_allclose(np.asarray(a), b, rtol=1e-5, atol=1e-6)
            for k in ("ref_values", "adv", "ret"):
                np.testing.assert_allclose(np.asarray(got[k]), exp[k],
                                           rtol=1e-5, atol=1e-6)

    def test_equivalent_with_dones_and_tail(self):
        torch.manual_seed(0)
        np.random.seed(0)
        trajs = [make_traj(40, 41, done_at=(9, 39)),   # 中段 done + 结尾 done
                 make_traj(25, 42),                    # 全拖尾(无 done)
                 make_traj(17, 43, done_at=(0, 5)),    # 长度 1 首段 + 短段
                 make_traj(33, 44, done_at=(32,))]     # 仅结尾 done
        self.check_equivalent(trajs, layers=1)

    def test_equivalent_multilayer_and_tiny_groups(self):
        import train.posttrain_replay as pr
        torch.manual_seed(0)
        np.random.seed(0)
        trajs = [make_traj(30, 51, done_at=(10, 20)),
                 make_traj(22, 52, done_at=(21,))]
        old = pr.LSTM_GROUP_POSITIONS
        pr.LSTM_GROUP_POSITIONS = 64  # 强制多组 + 单步段,走分组/散射全路径
        try:
            self.check_equivalent(trajs, layers=2)
        finally:
            pr.LSTM_GROUP_POSITIONS = old

class TestSurgicalWeights(unittest.TestCase):
    """transition/组加权 + adv_min 过滤 + pos_adv 评估。"""

    @staticmethod
    def group_ids(group):
        return [a for a in range(N_ACTIONS) if action_group(a) == group]

    def test_step_weights_repeats_roundstarts_attacks(self):
        mv = self.group_ids("move")[0]
        gd = self.group_ids("guard")[0]
        atk = self.group_ids("attack_ABC")[0]
        dash = self.group_ids("dash_D")[0]
        actions = np.array([mv, mv, mv, atk, atk, dash, gd, gd])
        dones = np.zeros(8, dtype=bool)
        w = compute_step_weights(actions, dones, 0.1, 2.0)
        np.testing.assert_allclose(w, [1, 0.1, 0.1, 2, 2, 1, 1, 0.1], rtol=1e-6)
        dones[1] = True  # 第 2 步是回合首步:虽重复仍为决策步
        w2 = compute_step_weights(actions, dones, 0.1, 2.0)
        self.assertEqual(float(w2[2]), 1.0)
        w3 = compute_step_weights(actions, dones)  # 默认恒 1
        np.testing.assert_allclose(w3, np.ones(8), rtol=1e-6)

    def test_adv_min_zeroes_negative_bc(self):
        torch.manual_seed(0)
        np.random.seed(0)
        base = make_traj(24, 61)
        base["rewards"] = np.full(24, -10.0, dtype=np.float32)  # adv 全负
        net, ref = make_nets()
        rolls = prep([base], net, ref)
        self.assertTrue(bool((np.asarray(rolls[0]["adv"]) < 0).all()))
        opt = torch.optim.Adam(net.parameters(), lr=3e-3)
        np.random.seed(0)
        logs = posttrain_epoch(net, ref, opt, rolls, mode="awbc", awbc_beta=0.3,
                               kl_coef=0.0, value_coef=0.0, adv_min=0.0)
        self.assertEqual(logs["n_bc_valid"], 0)
        self.assertEqual(logs["bc_loss"], 0.0)
        net2, ref2 = make_nets()
        rolls2 = prep([base], net2, ref2)
        opt2 = torch.optim.Adam(net2.parameters(), lr=3e-3)
        np.random.seed(0)
        logs2 = posttrain_epoch(net2, ref2, opt2, rolls2, mode="awbc",
                                awbc_beta=0.3, kl_coef=0.0, value_coef=0.0,
                                adv_min=None)
        self.assertGreater(logs2["n_bc_valid"], 0)

    def test_weights_baked_and_epoch_runs(self):
        torch.manual_seed(0)
        np.random.seed(0)
        mv = self.group_ids("move")[0]
        atk = self.group_ids("attack_ABC")[0]
        base = make_traj(16, 62)
        base["actions"] = np.array([mv] * 8 + [atk] * 8)
        net, ref = make_nets()
        rolls = prepare_rollouts([base], net, ref, repeat_move_weight=0.1,
                                 attack_weight=2.0)
        sw = np.asarray(rolls[0]["step_w"])
        np.testing.assert_allclose(sw[:8], [1] + [0.1] * 7, rtol=1e-6)
        np.testing.assert_allclose(sw[8:], [2.0] * 8, rtol=1e-6)
        opt = torch.optim.Adam(net.parameters(), lr=3e-3)
        np.random.seed(0)
        logs = posttrain_epoch(net, ref, opt, rolls, mode="awbc", awbc_beta=0.3,
                               kl_coef=1.0, value_coef=0.0, adv_min=0.02)
        for k in ("bc_loss", "kl", "entropy"):
            self.assertTrue(np.isfinite(logs[k]), k)

    def test_evaluate_pos_adv_metrics(self):
        torch.manual_seed(0)
        np.random.seed(0)
        net, ref = make_nets()
        rolls = prep([make_traj(24, 63)], net, ref)
        m = evaluate_dataset(net, ref, rolls)
        for k in ("pos_adv_bc_nll", "pos_adv_top1", "pos_adv_n"):
            self.assertIn(k, m)
        adv = np.asarray(rolls[0]["adv"])
        bc = np.asarray(rolls[0]["bc_valid"]).astype(bool)
        self.assertEqual(m["pos_adv_n"], int(((adv > 0) & bc).sum()))
        self.assertLessEqual(m["pos_adv_n"], m["n_bc_valid"])
        # 全负 adv:pos_adv_n 必为 0,全量 BC 指标不受影响。
        base = make_traj(24, 64)
        base["rewards"] = np.full(24, -10.0, dtype=np.float32)
        rolls2 = prep([base], net, ref)
        m2 = evaluate_dataset(net, ref, rolls2)
        self.assertEqual(m2["pos_adv_n"], 0)
        self.assertGreater(m2["n_bc_valid"], 0)

    def test_move_weight_zero_kills_move_imitation(self):
        mv = self.group_ids("move")[0]
        gd = self.group_ids("guard")[0]
        atk = self.group_ids("attack_ABC")[0]
        actions = np.array([mv, mv, atk, gd, gd, mv])
        dones = np.zeros(6, dtype=bool)
        dones[4] = True  # index 5 是回合首步
        w = compute_step_weights(actions, dones, 0.1, 2.0, move_weight=0.0)
        # move 步全 0;attack 2;guard 切换 1;guard 长按 0.1
        np.testing.assert_allclose(w, [0, 0, 2, 1, 0.1, 0], rtol=1e-6)
        w2 = compute_step_weights(actions, dones, 0.1, 2.0, move_weight=0.05)
        np.testing.assert_allclose(w2, [0.05, 0.005, 2, 1, 0.1, 0.05], rtol=1e-6)

    def test_freeze_backbone_scope(self):
        torch.manual_seed(0)
        net, _ = make_nets()
        apply_trainable_scope(net, freeze_critic=True, freeze_backbone=True)
        frozen = ("motion", "self_enc", "opp_enc", "global_enc",
                  "proj_enc", "lstm", "critic")
        for name in frozen:
            params = list(getattr(net, name).parameters())
            self.assertTrue(params, name)
            for p in params:
                self.assertFalse(p.requires_grad, name)
        for name in ("fuse", "actor"):
            for p in getattr(net, name).parameters():
                self.assertTrue(p.requires_grad, name)
        # 默认路径不变:只冻 critic。
        net2, _ = make_nets()
        apply_trainable_scope(net2, freeze_critic=True)
        for p in net2.actor.parameters():
            self.assertTrue(p.requires_grad)
        for p in net2.lstm.parameters():
            self.assertTrue(p.requires_grad)
        for p in net2.critic.parameters():
            self.assertFalse(p.requires_grad)

    def test_backbone_bit_identical_after_epoch(self):
        torch.manual_seed(0)
        np.random.seed(0)
        net, ref = make_nets()
        rolls = prep([make_traj(24, 65)], net, ref)
        before = snap(net)
        opt = torch.optim.Adam(net.parameters(), lr=3e-3)
        np.random.seed(0)
        logs = posttrain_epoch(net, ref, opt, rolls, mode="awbc", awbc_beta=0.3,
                               kl_coef=3.0, value_coef=0.5, freeze_critic=True,
                               freeze_backbone=True)
        after = snap(net)
        for k in before:
            if k.split(".")[0] in ("fuse", "actor"):
                continue
            self.assertTrue(torch.equal(before[k], after[k]), f"{k} changed")
        self.assertEqual(logs["value_loss"], 0.0)
        self.assertTrue(np.isfinite(logs["kl"]))


class TestEntropyControl(unittest.TestCase):
    """ent_cap 系数接线 + logit temperature 精确性。"""

    def test_ent_cap_enters_loss(self):
        torch.manual_seed(0)
        np.random.seed(0)
        net, ref = make_nets()
        rolls = prep([make_traj(24, 71)], net, ref)
        opt = torch.optim.Adam(net.parameters(), lr=3e-3)
        np.random.seed(0)
        # 先跑一轮让 net 偏离 ref:cap 度量非零才有梯度可言。
        posttrain_epoch(net, ref, opt, rolls, mode="sft", bc_coef=1.0,
                        kl_coef=0.0, value_coef=0.0)
        branched = []
        for coef in (0.0, 50.0):
            torch.manual_seed(0)
            n2, r2 = make_nets()
            n2.load_state_dict(net.state_dict())
            rolls2 = prep([make_traj(24, 71)], n2, r2)
            opt2 = torch.optim.Adam(n2.parameters(), lr=3e-3)
            np.random.seed(0)
            logs = posttrain_epoch(n2, r2, opt2, rolls2, mode="sft",
                                   bc_coef=1.0, kl_coef=0.0, value_coef=0.0,
                                   ent_cap_coef=coef)
            self.assertIn("ent_cap", logs)
            self.assertTrue(np.isfinite(logs["ent_cap"]))
            self.assertGreaterEqual(logs["ent_cap"], 0.0)
            branched.append((logs, snap(n2)))
        self.assertGreater(branched[0][0]["ent_cap"], 0.0)
        self.assertFalse(same_state(branched[0][1], branched[1][1]),
                         "ent_cap_coef 应改变梯度与更新后参数")

    def test_logit_temperature_exact(self):
        torch.manual_seed(0)
        net, ref = make_nets()
        rolls = prep([make_traj(24, 72)], net, ref)
        before = evaluate_dataset(net, ref, rolls)
        w0 = net.actor.weight.detach().cpu().clone()
        apply_logit_temperature(net, 1.0)
        self.assertTrue(torch.equal(w0, net.actor.weight.cpu()))
        apply_logit_temperature(net, 0.9)
        torch.testing.assert_close(net.actor.weight.cpu(), w0 / 0.9)
        if net.actor.bias is not None:
            torch.testing.assert_close(
                net.actor.bias.cpu(),
                ref.actor.bias.cpu() / 0.9)
        after = evaluate_dataset(net, ref, rolls)
        self.assertLess(after["entropy"], before["entropy"])
        # argmax 不变:top1 只升不降(分布变锐,排序 preserved)。
        self.assertGreaterEqual(after["top1_acc"], before["top1_acc"] - 1e-9)


if __name__ == "__main__":
    unittest.main()
