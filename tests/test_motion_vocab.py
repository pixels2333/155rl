"""motion embedding 词表扩容(docs/train_report_com3_full.md §36)的回归测试。

旧词表 4096 会把符卡 4000~4999 中 >= 4095 的 motion 与 9xxx 演出动作全部 clamp
到同一行。扩容迁移必须满足:
  1. 旧权重加载进新词表模型后,对任何 motion 的输出与旧模型逐元素一致;
  2. 新增行是独立参数,训练后 4500 与 4999 可以分开学习;
  3. 续训时形状变化会丢弃旧 Adam 状态,而不是报错或带着错位的动量继续。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents.ppo_agent import N_ACTIONS, PolicyNet, load_weights  # noqa: E402
from env import obs_builder                                       # noqa: E402

BASE = {"motion_embed": 16, "team_hidden": 32, "global_hidden": 16,
        "proj_hidden": 16, "fuse_hidden": 32, "lstm_hidden": 32, "lstm_layers": 1}
OLD_CFG = dict(BASE, motion_vocab=4096)
NEW_CFG = dict(BASE, motion_vocab=10240)
MOTIONS = (0, 3000, 4095, 4500, 4999, 9020)


def _batch(motions, seed: int = 7):
    g = torch.Generator().manual_seed(seed)
    b = len(motions)
    m = torch.tensor(motions, dtype=torch.long)
    return {
        "self": torch.randn(b, obs_builder.SELF_DIM, generator=g),
        "opp": torch.randn(b, obs_builder.OPP_DIM, generator=g),
        "global": torch.randn(b, obs_builder.GLOBAL_DIM, generator=g),
        "proj": torch.randn(b, obs_builder.MAX_PROJ, obs_builder.PROJ_DIMS, generator=g),
        "proj_mask": (torch.rand(b, obs_builder.MAX_PROJ, generator=g) > 0.5).float(),
        "motion_self": m,
        "motion_opp": m.flip(0),
        "action_mask": torch.ones(b, N_ACTIONS),
    }


def _old_state():
    torch.manual_seed(0)
    old = PolicyNet(OLD_CFG)
    old.init_orthogonal()
    return old, {k: v.clone() for k, v in old.state_dict().items()}


class TestMotionVocabMigration(unittest.TestCase):
    def test_migrated_outputs_match_old_model(self):
        old, sd = _old_state()
        net = PolicyNet(NEW_CFG)
        res = load_weights(net, sd, verbose=False)
        self.assertTrue(res.migrated)
        self.assertEqual(res.skipped, [])
        self.assertEqual(tuple(net.motion.weight.shape), (10240, 16))
        old.eval(); net.eval()
        batch = _batch(MOTIONS)
        with torch.no_grad():
            lo, vo, _ = old(batch)
            ln, vn, _ = net(batch)
        self.assertTrue(torch.equal(lo, ln), f"logits 最大差 {(lo - ln).abs().max().item():.3e}")
        self.assertTrue(torch.equal(vo, vn), f"value 最大差 {(vo - vn).abs().max().item():.3e}")

    def test_rows_copied_and_new_rows_seeded_from_last_old_row(self):
        _, sd = _old_state()
        net = PolicyNet(NEW_CFG)
        net.warm_start(sd, verbose=False)
        w_old, w_new = sd["motion.weight"], net.motion.weight.detach()
        self.assertTrue(torch.equal(w_new[:4096], w_old))
        self.assertTrue(torch.equal(w_new[4096:], w_old[4095].expand(10240 - 4096, -1)))

    def test_new_rows_train_independently(self):
        """4500 与 4999 是不同的参数行:只对 4500 求梯度,4999 那行不动。"""
        _, sd = _old_state()
        net = PolicyNet(NEW_CFG)
        net.warm_start(sd, verbose=False)
        opt = torch.optim.SGD([net.motion.weight], lr=0.1)
        before = net.motion.weight.detach().clone()
        net.motion(torch.tensor([4500])).sum().backward()
        opt.step()
        after = net.motion.weight.detach()
        self.assertFalse(torch.equal(after[4500], before[4500]))
        self.assertTrue(torch.equal(after[4999], before[4999]))
        self.assertFalse(torch.equal(net.motion(torch.tensor([4500])),
                                     net.motion(torch.tensor([4999]))))

    def test_encode_no_longer_clamps_spell_motions(self):
        net = PolicyNet(NEW_CFG)
        with torch.no_grad():
            net.motion.weight.copy_(torch.arange(10240, dtype=torch.float32)
                                    .unsqueeze(1).expand(-1, 16))
        captured = []
        h = net.self_enc.register_forward_hook(lambda m, i, o: captured.append(i[0]))
        try:
            with torch.no_grad():
                net.encode(_batch(MOTIONS))
        finally:
            h.remove()
        emb_col = captured[0][:, obs_builder.SELF_DIM]
        self.assertEqual(emb_col.tolist(), [float(m) for m in MOTIONS])

    def test_shrinking_vocab_is_not_silently_migrated(self):
        """10240 -> 4096 不是扩容,语义未知,必须报告为跳过。"""
        net_big = PolicyNet(NEW_CFG)
        net = PolicyNet(OLD_CFG)
        skipped = net.warm_start(net_big.state_dict(), verbose=False)
        self.assertIn("motion.weight", skipped)


class TestMotionVocabResume(unittest.TestCase):
    def test_resume_from_old_vocab_drops_optimizer_state(self):
        import io
        from contextlib import redirect_stdout
        import tempfile

        from train.train_ppo import _OfflineEnv, train

        old, sd = _old_state()
        opt = torch.optim.Adam(old.parameters(), lr=1e-3)
        logits, value, _ = old(_batch(MOTIONS))
        (logits.sum() + value.sum()).backward()
        opt.step()
        init_state = {"model_state_dict": sd, "optimizer_state_dict": opt.state_dict(),
                      "lr_base": 1e-3}
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "env": {"team": 0, "action_repeat": 2, "difficulty": 1,
                        "max_episode_steps": 4, "frame_timeout": 1.0},
                "network": dict(NEW_CFG),
                "train": {
                    "total_updates": 1, "n_steps": 8, "seq_len": 2, "epochs": 1,
                    "minibatch_seqs": 2, "gamma": 0.99, "gae_lambda": 0.95,
                    "clip_range": 0.2, "value_coef": 0.5, "entropy_coef": 0.01,
                    "max_grad_norm": 0.5, "lr": 1e-3, "target_kl": 0.05, "seed": 0,
                    "eval_interval": 0, "eval_episodes": 0, "lr_mode": "constant",
                },
                "checkpoint": {"dir": tmp, "save_every": 5, "keep": 5},
                "logging": {"tensorboard": str(Path(tmp) / "tb")},
                "curriculum": {"enabled": False},
            }
            buf = io.StringIO()
            with redirect_stdout(buf):
                train(cfg, updates=1, env=_OfflineEnv(max_episode_steps=4),
                      verbose=True, init_state=init_state)
        out = buf.getvalue()
        self.assertIn("motion.weight 词表 4096 -> 10240", out)
        self.assertIn("丢弃旧优化器状态", out)
        self.assertNotIn("成功恢复优化器状态", out)


if __name__ == "__main__":
    unittest.main()
