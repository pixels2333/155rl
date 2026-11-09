"""观测扩容后旧权重的迁移(warm start)测试。

背景:opp_enc 的输入是 cat([opp, motion_emb]),v13 在 opp 末尾插入 3 列
(gapx/gapy/contact)后 motion 那一整块整体后移。若按"拷前 N 列"实现,
形状刚好对得上、**不会报错**,但 motion 分量会被塞进 contact —— 静默错位。
这里锁住三件事:
  1. 按列名迁移后,新列置 0 时新旧网络输出完全一致;
  2. 每列的落点正确(不靠"看起来能跑");
  3. 形状不兼容的加载走 warm_start、未知布局不静默瞎猜。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents.ppo_agent import PolicyNet, _mlp, load_weights  # noqa: E402
from env import obs_builder                                  # noqa: E402

NET_CFG = {"motion_vocab": 4096, "motion_embed": 16, "team_hidden": 128,
           "global_hidden": 64, "proj_hidden": 64, "fuse_hidden": 256,
           "lstm_hidden": 256, "lstm_layers": 1}


def _old_net_and_state() -> tuple[PolicyNet, dict]:
    """构造一个 v12 布局的网络(opp 尾部只有 rel_dx/rel_dy)并取它的 state_dict。"""
    torch.manual_seed(0)
    old = PolicyNet(NET_CFG)
    old_dim = obs_builder.SELF_DIM + len(obs_builder.OPP_TAIL_V12)
    # 用与当前代码相同的形状,只把 opp 输入宽度换成旧的
    old.opp_enc = _mlp(old_dim + old.motion_embed, 128)
    sd = {k: v.clone() for k, v in old.state_dict().items()}
    return old, sd


def _inputs(b: int = 8, seed: int = 1):
    g = torch.Generator().manual_seed(seed)
    team = torch.randn(b, obs_builder.SELF_DIM, generator=g)
    dx = torch.rand(b, 1, generator=g)
    dy = torch.randn(b, 1, generator=g) * 0.3
    emb = torch.randn(b, 16, generator=g)
    old_in = torch.cat([team, dx, dy, emb], dim=-1)
    new_in = torch.cat([team, torch.zeros(b, 3), dx, dy, emb], dim=-1)
    return old_in, new_in


class TestWarmStartColumns(unittest.TestCase):
    def test_new_columns_zeroed_reproduces_old_encoder_output(self):
        old, sd = _old_net_and_state()
        net = PolicyNet(NET_CFG)
        skipped = net.warm_start(sd, verbose=False)
        self.assertEqual(skipped, [])
        net.eval()
        old.eval()
        old_in, new_in = _inputs()
        with torch.no_grad():
            a = net.opp_enc(new_in)
            b = old.opp_enc(old_in)
        self.assertTrue(torch.allclose(a, b, atol=1e-5),
                        f"最大差 {(a - b).abs().max().item():.3e}")

    def test_motion_block_moves_with_tail(self):
        """motion 第 0 维必须落在新列 37(不是 34),否则就是静默错位。"""
        _, sd = _old_net_and_state()
        net = PolicyNet(NET_CFG)
        net.warm_start(sd, verbose=False)
        w_old = sd["opp_enc.0.weight"]
        w_new = net.opp_enc[0].weight
        old_tail = len(obs_builder.OPP_TAIL_V12)
        for i in range(obs_builder.SELF_DIM):
            self.assertTrue(torch.equal(w_new[:, i], w_old[:, i]),
                            f"self 第 {i} 列未对齐")
        # 旧的 rel_dx/rel_dy 落在新 dx/dy 的位置
        for old_off, name in enumerate(obs_builder.OPP_TAIL_V12):
            new_idx = obs_builder.SELF_DIM + obs_builder.OPP_TAIL.index(
                obs_builder.OPP_TAIL_ALIAS[name])
            self.assertTrue(torch.equal(w_new[:, new_idx],
                                        w_old[:, obs_builder.SELF_DIM + old_off]),
                            f"{name} 未映射到对应新列")
        # 新增三列权重为 0
        for name in ("gapx", "gapy", "contact"):
            idx = obs_builder.SELF_DIM + obs_builder.OPP_TAIL.index(name)
            self.assertEqual(float(w_new[:, idx].detach().abs().max()), 0.0,
                             f"{name} 未置 0")
        # motion 块整体后移 len(new)-len(old) 列
        shift = len(obs_builder.OPP_TAIL) - old_tail
        base_old = obs_builder.SELF_DIM + old_tail
        base_new = obs_builder.OPP_DIM
        for i in range(net.motion_embed):
            self.assertTrue(torch.equal(w_new[:, base_new + i], w_old[:, base_old + i]),
                            f"motion 第 {i} 维错位")
        self.assertEqual(base_new - base_old, shift)

    def test_naive_prefix_copy_would_be_wrong(self):
        """反证:朴素的"拷前 N 列"会改变 encoder 输出,证明测试确实能抓到错位。"""
        old, sd = _old_net_and_state()
        net = PolicyNet(NET_CFG)
        net.warm_start(sd, verbose=False)
        naive = _mlp(obs_builder.OPP_DIM + net.motion_embed, 128)
        w = torch.zeros_like(naive[0].weight)
        w[:, :sd["opp_enc.0.weight"].shape[1]] = sd["opp_enc.0.weight"]
        naive[0].weight.data = w
        naive[0].bias.data = sd["opp_enc.0.bias"].clone()
        for i in (1, 2, 3, 4, 5):
            naive[i].load_state_dict(
                {k[len(f"opp_enc.{i}."):]: v for k, v in sd.items()
                 if k.startswith(f"opp_enc.{i}.")})
        _, new_in = _inputs()
        with torch.no_grad():
            self.assertFalse(torch.allclose(net.opp_enc(new_in), naive(new_in), atol=1e-4))


class TestLoadWeightsFallback(unittest.TestCase):
    def test_same_shape_loads_without_skip(self):
        net = PolicyNet(NET_CFG)
        other = PolicyNet(NET_CFG)
        torch.nn.init.constant_(other.actor.weight, 0.5)
        sd2 = {k: v.clone() for k, v in other.state_dict().items()}
        res = load_weights(net, sd2, verbose=False)
        self.assertEqual(res.skipped, [])
        self.assertFalse(res.migrated, "同形状加载不应标记为迁移")
        self.assertTrue(torch.equal(net.actor.weight, sd2["actor.weight"]))

    def test_shape_mismatch_falls_back_to_warm_start(self):
        _, sd = _old_net_and_state()
        net = PolicyNet(NET_CFG)
        res = load_weights(net, sd, verbose=False)
        self.assertEqual(res.skipped, [])
        self.assertTrue(res.migrated)
        self.assertEqual(int(net.opp_enc[0].weight.shape[1]),
                         obs_builder.OPP_DIM + net.motion_embed)

    def test_unknown_layout_keeps_random_opp_encoder(self):
        net = PolicyNet(NET_CFG)
        before = net.opp_enc[0].weight.clone()
        sd = {k: v.clone() for k, v in net.state_dict().items()}
        # 构造一个无法识别的旧宽度:尾部既不是 5 也不是 2
        sd["opp_enc.0.weight"] = torch.zeros(128, obs_builder.SELF_DIM + 7 + net.motion_embed)
        sd["opp_enc.0.bias"] = torch.zeros(128)
        res = load_weights(net, sd, verbose=False)
        self.assertIn("opp_enc.0.weight", res.skipped)
        self.assertTrue(torch.equal(net.opp_enc[0].weight, before),
                        "未知布局必须保持原初始化,不能瞎拷")


class TestResumeAfterMigration(unittest.TestCase):
    """迁移后必须丢弃旧 Adam 状态,否则 step() 时才炸形状不匹配。"""

    def test_migrated_weights_drop_optimizer_state(self):
        import train.train_ppo as tp
        from train.train_ppo import _OfflineEnv, train

        old_net, sd = _old_net_and_state()
        opt = torch.optim.Adam(old_net.parameters(), lr=1e-3)
        # 造一份带 Adam 状态的旧 checkpoint(形状 = 旧 opp_enc)
        loss = sum(p.sum() for p in old_net.parameters())
        loss.backward()
        opt.step()
        state = {"model": sd, "optimizer": opt.state_dict(), "update": 0}

        cfg = {
            "env": {"team": 0, "action_repeat": 2, "difficulty": 1,
                    "max_episode_steps": 4, "frame_timeout": 1.0},
            "network": NET_CFG,
            "train": {"total_updates": 2, "n_steps": 8, "seq_len": 2, "epochs": 1,
                      "minibatch_seqs": 2, "gamma": 0.99, "gae_lambda": 0.95,
                      "clip_range": 0.2, "value_coef": 0.5, "entropy_coef": 0.01,
                      "max_grad_norm": 0.5, "lr": 1e-3, "target_kl": 0.05, "seed": 0},
            "checkpoint": {"dir": "", "save_every": 100, "keep": 1},
            "logging": {"tensorboard": ""},
            "curriculum": {"enabled": False},
        }
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cfg["checkpoint"]["dir"] = tmp
            cfg["logging"]["tensorboard"] = str(Path(tmp) / "tb")
            # 不抛异常即为通过:旧实现会在 opt.step() 抛
            # "size of tensor a (50) must match the size of tensor b (53)"
            stats = train(cfg, env=_OfflineEnv(max_episode_steps=4),
                          init_state=state, verbose=False)
        self.assertGreaterEqual(len(stats.episodes), 1)


if __name__ == "__main__":
    unittest.main()


class TestSelfColumnExpansion(unittest.TestCase):
    """v16 把 SELF_DIM 从 37 扩到 40（尾部追加 atk_range/gravity/slide_speed）。

    旧的 self 前 37 列与新的前 37 列**严格同义**（新列只加在尾部），
    所以迁移必须保留旧权重、把 motion_emb 整体右移 3，而不是整体作废。
    """

    def test_motion_block_moves_right_when_self_grows(self):
        cfg = dict(NET_CFG)
        torch.manual_seed(0)
        net = PolicyNet(cfg)
        delta = 3
        old_self = obs_builder.SELF_DIM - delta

        # 造一个"旧版" self_enc：输入窄 delta 列
        sd = {k: v.clone() for k, v in net.state_dict().items()}
        w = sd["self_enc.0.weight"]
        b = sd["self_enc.0.bias"]
        new_w = torch.randn(w.shape[0], w.shape[1] - delta) * 0.05
        new_w[:, :old_self] = w[:, :old_self]
        new_w[:, old_self:] = w[:, obs_builder.SELF_DIM:]
        sd["self_enc.0.weight"] = new_w
        sd["self_enc.0.bias"] = b.clone()

        net.warm_start(sd, verbose=False)
        got = net.self_enc[0].weight.detach()

        # 旧的前 old_self 列 -> 新的前 old_self 列,逐元素相同
        self.assertTrue(torch.allclose(got[:, :old_self], w[:, :old_self]),
                        "旧状态列必须原样保留")
        # 新的中间 3 列(本 tests 的净新增维)允许任意值,但不得覆盖旧列
        # motion 块：旧列 old_self.. -> 新列 SELF_DIM..
        self.assertTrue(torch.allclose(got[:, obs_builder.SELF_DIM:],
                                       w[:, obs_builder.SELF_DIM:]),
                        "motion_emb 必须整体右移到 SELF_DIM 之后")


class TestOppMapUsesOldSelfDim(unittest.TestCase):
    """回归：opp 的旧布局必须靠旧 self_enc 宽度反推，不能用当前 SELF_DIM。

    反例（修复前会静默错位）：
        旧 ckpt: self=47, tail=5, embed=16  =>  opp_enc 输入宽 = 68
        当前:    self=50, tail=5, embed=16  =>  opp_enc 输入宽 = 71
    若用当前 SELF_DIM 反推：old_tail = 68 - 50 - 16 = 2，恰好等于 V12 的 2 列，
    于是走 V12 分支、形状对得上、不报错 —— 但新 self 的 47..49 三列会拷入
    旧 opp 的 tail/emb 值。这正是 warm_start 本身要防的静默错位。
    """

    def _old_state(self, old_self: int, old_tail: int):
        cfg = dict(NET_CFG)
        torch.manual_seed(0)
        net = PolicyNet(cfg)
        sd = {k: v.clone() for k, v in net.state_dict().items()}
        emb = net.motion_embed
        # self_enc: 输入 = old_self + emb
        sd["self_enc.0.weight"] = torch.randn(
            sd["self_enc.0.weight"].shape[0], old_self + emb) * 0.05
        # opp_enc: 输入 = old_self + old_tail + emb
        sd["opp_enc.0.weight"] = torch.randn(
            sd["opp_enc.0.weight"].shape[0], old_self + old_tail + emb) * 0.05
        return net, sd

    def test_tail_inferred_from_old_self_not_current(self):
        net, sd = self._old_state(old_self=47, old_tail=5)
        # 前提：这个组合在当前 SELF_DIM 下会算出错误的 2
        self.assertEqual(47 + 5 + 16, 68)
        old_self = net._infer_old_self(sd)
        self.assertEqual(old_self, 47, "必须从 self_enc 反推出 47，而不是当前 SELF_DIM")

        m = net._opp_column_map(sd, old_self, verbose=False)
        self.assertIsNotNone(m, "已知布局不应被拒绝")

        # 状态列 0..46 必须按同列号映射
        pairs = dict(m)
        for i in range(47):
            self.assertEqual(pairs.get(i), i, f"状态列 {i} 映射错误")

        # 关键：旧 tail 的 5 列起点是 47（不是错误的 50）
        old_tail_start = 47
        new_tail_cols = [pairs.get(obs_builder.SELF_DIM + k) for k in range(5)]
        self.assertNotIn(None, new_tail_cols)
        self.assertEqual(min(c for c in new_tail_cols if c is not None),
                         old_tail_start, "旧 tail 必须从 47 开始读，而不是 50")

    def test_rejects_when_old_self_unavailable(self):
        """推不出旧 SELF_DIM 时必须拒绝，而不是猜一个。"""
        cfg = dict(NET_CFG)
        torch.manual_seed(0)
        net = PolicyNet(cfg)
        sd = {k: v.clone() for k, v in net.state_dict().items()}
        del sd["self_enc.0.weight"]
        self.assertIsNone(net._infer_old_self(sd))
        # 没有 old_self 时 opp 映射必须拒绝
        self.assertIsNone(net._opp_column_map(sd, None, verbose=False))


class TestGlobalPrefixMigration(unittest.TestCase):
    """GLOBAL_DIM 12 -> 23 是纯尾部追加，必须按前缀迁移而不是丢弃。

    丢弃会让 `global_enc` 整层随机初始化 —— 而它编码计时器/血量差/时间控制，
    是老策略里已经被训练过的信号。v17 前 `warm_start` 只处理 self/opp，
    `global_enc` 与 `proj_enc` 都会掉进 "跳过并随机初始化" 分支（静默降级）。
    """

    def _old_global_state(self, old_global: int):
        cfg = dict(NET_CFG)
        torch.manual_seed(0)
        net = PolicyNet(cfg)
        sd = {k: v.clone() for k, v in net.state_dict().items()}
        sd["global_enc.0.weight"] = torch.randn(
            sd["global_enc.0.weight"].shape[0], old_global) * 0.05
        return net, sd

    def test_prefix_columns_kept_and_new_zeroed(self):
        old_global = 12
        net, sd = self._old_global_state(old_global)
        net.warm_start(sd, verbose=False)
        w_old = sd["global_enc.0.weight"]
        w_new = net.global_enc[0].weight.detach()
        self.assertEqual(int(w_new.shape[1]), obs_builder.GLOBAL_DIM)
        self.assertTrue(torch.allclose(w_new[:, :old_global], w_old),
                        "旧 global 列必须原样保留")
        self.assertEqual(float(w_new[:, old_global:].abs().max()), 0.0,
                         "新增 global 列必须置 0")

    def test_new_columns_zero_reproduces_old_global_output(self):
        """新增列为 0 时，新旧 encoder 在旧 12 维输入上的输出必须逐位一致。"""
        old_global = 12
        net, sd = self._old_global_state(old_global)
        old_enc = _mlp(old_global, 64)
        old_enc.load_state_dict({
            k[len("global_enc."):]: v for k, v in sd.items()
            if k.startswith("global_enc.")
        })
        net.warm_start(sd, verbose=False)
        net.eval(); old_enc.eval()
        g = torch.Generator().manual_seed(3)
        old_in = torch.randn(8, old_global, generator=g)
        new_in = torch.cat([old_in, torch.zeros(8, obs_builder.GLOBAL_DIM - old_global)], -1)
        with torch.no_grad():
            self.assertTrue(torch.allclose(net.global_enc(new_in), old_enc(old_in),
                                           atol=1e-5))

    def test_does_not_migrate_proj_encoder(self):
        """proj 第一层旧输入 **7 维**在 v17 前恒为零，必须重置而非搬运。

        注意宽度要写死 7，不能用 `PROJ_DIMS - 1`：后者会随当前维度漂移，
        等 PROJ_DIMS 涨到 9 时它变成 8，把"v17 之后的真实训练期"误当成
        "v17 之前的死输入期"，测试就会在错误的前提下通过。
        """
        cfg = dict(NET_CFG)
        torch.manual_seed(0)
        net = PolicyNet(cfg)
        sd = {k: v.clone() for k, v in net.state_dict().items()}
        sd["proj_enc.point.0.weight"] = torch.randn(64, 7) * 0.05
        sd["global_enc.0.weight"] = torch.randn(64, 12) * 0.05
        before = net.proj_enc.point[0].weight.clone()
        net.warm_start(sd, verbose=False)
        self.assertTrue(torch.equal(net.proj_enc.point[0].weight, before),
                        "proj_enc 首层必须保持新初始化")

    def test_migrates_proj_encoder_by_prefix_when_v17_trained(self):
        """旧输入 **8 维**（v17 起已用真实弹幕训练）必须按前缀迁移，不能重置。

        与上一个测试的分界:`proj_mask` 在 v17 之前恒为 0,那层权重从未收到
        梯度,重置才对;v17 修好数据链路后 M3 已用 8 维真实弹幕训练过,
        这些权重有语义,重置等于丢掉一整轮训练成果。
        """
        cfg = dict(NET_CFG)
        torch.manual_seed(0)
        net = PolicyNet(cfg)
        sd = {k: v.clone() for k, v in net.state_dict().items()}
        old_w = torch.randn(64, 8) * 0.05
        sd["proj_enc.point.0.weight"] = old_w
        sd["global_enc.0.weight"] = torch.randn(64, 12) * 0.05
        skipped = net.warm_start(sd, verbose=False)
        self.assertNotIn("proj_enc.point.0.weight", skipped)
        new_w = net.proj_enc.point[0].weight
        self.assertEqual(new_w.shape[1], obs_builder.PROJ_DIMS)
        self.assertTrue(torch.equal(new_w[:, :8], old_w),
                        "旧 8 列必须原样保留")
        self.assertTrue(bool((new_w[:, 8:] == 0).all()),
                        "新增列必须为 0,保证旧行为不变")

    def test_v17_trained_proj_branch_keeps_fuse_weights(self):
        """8 维已训练过的场景**不能**清空 fuse 的弹幕列。

        `_silence_proj_branch` 只适用于"弹幕通道从未收到梯度"的情况。
        若旧宽度 >= 8,说明这些列已被真实训练,清零会把学到的弹幕表示
        一并抹掉 —— 那是纯粹的退步。
        """
        cfg = dict(NET_CFG)
        torch.manual_seed(0)
        net = PolicyNet(cfg)
        sd = {k: v.clone() for k, v in net.state_dict().items()}
        sd["proj_enc.point.0.weight"] = torch.randn(64, 8) * 0.05
        sd["global_enc.0.weight"] = torch.randn(64, 12) * 0.05
        before_fuse = net.fuse[0].weight.clone()
        net.warm_start(sd, verbose=False)
        self.assertTrue(torch.equal(net.fuse[0].weight, before_fuse),
                        "fuse 的弹幕列必须保留,不能被静音")

    def test_proj_branch_contributes_zero_after_migration(self):
        """弹幕通道迁移后必须从"零贡献"起步。

        旧模型里 proj_mask 全程为 0 ⇒ ProjectilePointNet 输出恒为 0 ⇒
        fuse 对应的 128 列权重从未收到梯度。若迁移后直接让真实弹幕输入进来，
        这 128 列会把随机权重乘上真实特征，向已训好的 fuse 注入噪声。
        判据:喂入任意弹幕(mask 也为 1)与喂入零弹幕,encode 输出必须逐位相同。
        """
        cfg = dict(NET_CFG)
        torch.manual_seed(0)
        net = PolicyNet(cfg)
        sd = {k: v.clone() for k, v in net.state_dict().items()}
        # 写死 7 = v17 之前的死输入宽度(不能用 PROJ_DIMS - 1,见上一个测试)
        sd["proj_enc.point.0.weight"] = torch.randn(64, 7) * 0.05
        sd["global_enc.0.weight"] = torch.randn(64, 12) * 0.05
        net.warm_start(sd, verbose=False)
        net.eval()

        b, k = 4, obs_builder.MAX_PROJ
        g = torch.Generator().manual_seed(7)
        base = {
            "self": torch.randn(b, obs_builder.SELF_DIM, generator=g),
            "opp": torch.randn(b, obs_builder.OPP_DIM, generator=g),
            "global": torch.randn(b, obs_builder.GLOBAL_DIM, generator=g),
            "motion_self": torch.randint(0, 100, (b,), generator=g),
            "motion_opp": torch.randint(0, 100, (b,), generator=g),
        }
        live = dict(base,
                    proj=torch.randn(b, k, obs_builder.PROJ_DIMS, generator=g),
                    proj_mask=torch.ones(b, k))
        dead = dict(base,
                    proj=torch.zeros(b, k, obs_builder.PROJ_DIMS),
                    proj_mask=torch.zeros(b, k))
        with torch.no_grad():
            a = net.encode(live)
            c = net.encode(dead)
        self.assertTrue(torch.equal(a, c),
                        f"弹幕通道初始贡献必须为 0,实际差 {(a - c).abs().max().item():.3e}")
