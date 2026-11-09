"""多环境并行训练:批量 update 等价性、worker 边界切序列、分布式评估汇总、离线多 worker 冒烟。

worker 用 multiprocessing 'spawn' 启动(与实机训练同一条路径),这里全部用合成环境,不碰游戏。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import train.train_ppo as tp                                   # noqa: E402
from agents.ppo_agent import PolicyNet                         # noqa: E402
from train.eval import MatchRecord, summarize, wilson_interval  # noqa: E402
from train.parallel import (OfflineProvider, WorkerPool,       # noqa: E402
                            merge_episodes, split_episodes)
from train.train_ppo import (Evaluator, Rollout, Stats, _OfflineEnv,  # noqa: E402
                             _seq_batches, collect, compute_gae, merge_rollouts, update)

NET = {"motion_vocab": 4096, "motion_embed": 16, "team_hidden": 32, "global_hidden": 16,
       "proj_hidden": 16, "fuse_hidden": 32, "lstm_hidden": 32, "lstm_layers": 1}


def _train_cfg(**kw) -> dict:
    t = {"total_updates": 2, "n_steps": 32, "seq_len": 4, "epochs": 2, "minibatch_seqs": 3,
         "gamma": 0.99, "gae_lambda": 0.95, "clip_range": 0.2, "value_coef": 0.5,
         "entropy_coef": 0.01, "max_grad_norm": 0.5, "lr": 3e-4, "target_kl": 0.0, "seed": 0}
    t.update(kw)
    return t


def _reference_update(net, opt, roll, adv, ret, cfg, ref_net=None, ref_kl_coef=0.0) -> dict:
    """批量化之前的逐序列实现(原样保留),作为等价性基准。"""
    tcfg = cfg["train"]
    clip = float(tcfg["clip_range"])
    epochs = int(tcfg["epochs"])
    seq_len = int(tcfg["seq_len"])
    mb_seqs = int(tcfg["minibatch_seqs"])
    target_kl = float(tcfg.get("target_kl", 0.0) or 0.0)
    ref_kl_coef = float(ref_kl_coef or 0.0)
    use_ref = ref_net is not None and ref_kl_coef > 0.0

    obs_t = {k: torch.as_tensor(v) for k, v in roll.obs.items()}
    actions = torch.as_tensor(roll.actions)
    old_logp = torch.as_tensor(roll.logp)
    masks = torch.as_tensor(roll.masks)
    adv_t = torch.as_tensor(adv)
    ret_t = torch.as_tensor(ret)
    h0 = torch.as_tensor(roll.h_in[0])
    c0 = torch.as_tensor(roll.h_in[1])
    if use_ref and getattr(roll, "ref_h_in", None) is not None:
        rh0 = torch.as_tensor(roll.ref_h_in[0])
        rc0 = torch.as_tensor(roll.ref_h_in[1])
    else:
        rh0, rc0 = h0, c0

    seqs = _seq_batches(len(roll.actions), seq_len, roll.dones, breaks=roll.seg_ends)
    logs = {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0,
            "approx_kl": 0.0, "clip_frac": 0.0, "ref_kl": 0.0, "n_updates": 0}
    adv_std = adv_t.std()
    if torch.isfinite(adv_std) and float(adv_std) > 1e-6:
        adv_norm = (adv_t - adv_t.mean()) / (adv_std + 1e-8)
    else:
        adv_norm = adv_t - adv_t.mean()

    stop = False
    for _ in range(epochs):
        order = np.random.permutation(len(seqs))
        for i in range(0, len(order), mb_seqs):
            chunk = [seqs[j] for j in order[i:i + mb_seqs]]
            if not chunk:
                continue
            max_len = max(e - s for s, e in chunk)
            if max_len < 2:
                continue
            pol_losses, val_losses, ents, kls, clips, ref_kls = [], [], [], [], [], []
            for (s, e) in chunk:
                sub = {k: v[s:e].unsqueeze(0) for k, v in obs_t.items()}
                hidden = (h0[s].unsqueeze(0), c0[s].unsqueeze(0))
                ref_hidden = (rh0[s].unsqueeze(0), rc0[s].unsqueeze(0))
                logits, values, _ = net.forward(sub, hidden, seq=True)
                dist = net.masked_dist(logits, masks[s:e].unsqueeze(0))
                a = actions[s:e].unsqueeze(0)
                logp = dist.log_prob(a)
                ratio = torch.exp(logp - old_logp[s:e].unsqueeze(0))
                a_adv = adv_norm[s:e].unsqueeze(0)
                surr1 = ratio * a_adv
                surr2 = torch.clamp(ratio, 1.0 - clip, 1.0 + clip) * a_adv
                pol_losses.append(-torch.min(surr1, surr2).mean())
                val_losses.append(nn.functional.mse_loss(values.squeeze(0), ret_t[s:e]))
                ents.append(dist.entropy().mean())
                if use_ref:
                    with torch.no_grad():
                        ref_logits, _, _ = ref_net.forward(sub, ref_hidden, seq=True)
                        ref_dist = ref_net.masked_dist(ref_logits, masks[s:e].unsqueeze(0))
                    ref_kls.append(torch.distributions.kl_divergence(dist, ref_dist).mean())
                with torch.no_grad():
                    kls.append(((old_logp[s:e].unsqueeze(0) - logp) ** 2).mean() * 0.5)
                    clips.append(((ratio - 1.0).abs() > clip).float().mean())
            policy_loss = torch.stack(pol_losses).mean()
            value_loss = torch.stack(val_losses).mean()
            entropy = torch.stack(ents).mean()
            loss = (policy_loss
                    + float(tcfg["value_coef"]) * value_loss
                    - float(tcfg["entropy_coef"]) * entropy)
            ref_kl_value = 0.0
            if ref_kls:
                ref_kl_t = torch.stack(ref_kls).mean()
                loss = loss + ref_kl_coef * ref_kl_t
                ref_kl_value = float(ref_kl_t.detach())
            opt.zero_grad()
            if not torch.isfinite(loss):
                logs["skipped"] = logs.get("skipped", 0) + 1
                continue
            loss.backward()
            nn.utils.clip_grad_norm_(net.parameters(), float(tcfg["max_grad_norm"]))
            if any(p.grad is not None and not torch.isfinite(p.grad).all()
                   for p in net.parameters()):
                logs["skipped"] = logs.get("skipped", 0) + 1
                opt.zero_grad(set_to_none=True)
                continue
            opt.step()
            kl = float(torch.stack(kls).mean())
            logs["policy_loss"] += float(policy_loss.detach())
            logs["value_loss"] += float(value_loss.detach())
            logs["entropy"] += float(entropy.detach())
            logs["approx_kl"] += kl
            logs["ref_kl"] += ref_kl_value
            logs["clip_frac"] += float(torch.stack(clips).mean())
            logs["n_updates"] += 1
            if target_kl > 0 and kl > target_kl * 1.5:
                stop = True
                break
        if stop:
            break
    n = max(logs.pop("n_updates"), 1)
    for k in ("policy_loss", "value_loss", "entropy", "approx_kl", "clip_frac", "ref_kl"):
        logs[k] /= n
    return logs


def _rollout(net, n_steps: int, episode_steps: int, seed: int, ref_net=None) -> Rollout:
    env = _OfflineEnv(max_episode_steps=episode_steps, seed=seed)
    obs, _ = env.reset()
    roll, _, _, _ = collect(net, env, n_steps, net.init_hidden(1), Stats(), obs, ref_net=ref_net)
    return roll


class TestBatchedUpdateEquivalence(unittest.TestCase):
    """批量化 update 与逐序列旧实现:同种子同 rollout,参数更新差 ≤ 1e-5。"""

    def _compare(self, tcfg: dict, use_ref: bool, seg_ends: bool = False) -> None:
        torch.manual_seed(0)
        cfg = {"train": tcfg}
        base = PolicyNet(NET).init_orthogonal()
        ref = None
        if use_ref:
            torch.manual_seed(1)
            ref = PolicyNet(NET)
            ref.eval()
        # 13 步一局 + seq_len=4:序列长度有 4 和 1 两种,覆盖"chunk 里混有长度 1 的序列"。
        roll = _rollout(base, 96, 13, seed=3, ref_net=ref)
        adv, ret = compute_gae(roll, tcfg["gamma"], tcfg["gae_lambda"])
        if seg_ends:
            roll.seg_ends = np.zeros(len(roll.actions), dtype=bool)
            roll.seg_ends[[29, 61]] = True
        nets, outs = [], []
        for fn in (_reference_update, update):
            net = PolicyNet(NET)
            net.load_state_dict(base.state_dict())
            opt = torch.optim.Adam(net.parameters(), lr=3e-4)
            np.random.seed(123)
            outs.append(fn(net, opt, roll, adv, ret, cfg, ref_net=ref,
                           ref_kl_coef=0.5 if use_ref else 0.0))
            nets.append(net)
        diff = max(float((a - b).abs().max())
                   for a, b in zip(nets[0].state_dict().values(), nets[1].state_dict().values()))
        moved = max(float((a - b).abs().max())
                    for a, b in zip(base.state_dict().values(), nets[1].state_dict().values()))
        self.assertGreater(moved, 1e-4, "参数没有被更新,比较无意义")
        self.assertLessEqual(diff, 1e-5, f"批量实现与逐序列实现的参数差 {diff:.3e}")
        for k, v in outs[0].items():
            self.assertAlmostEqual(v, outs[1][k], delta=1e-5, msg=f"日志项 {k} 不一致")

    def test_plain(self):
        self._compare(_train_cfg(), use_ref=False)

    def test_with_ref_kl(self):
        self._compare(_train_cfg(), use_ref=True)

    def test_target_kl_early_stop(self):
        # target_kl 极小:第一个 minibatch 后两边都必须早停,n_updates 相同
        self._compare(_train_cfg(target_kl=1e-9, epochs=3), use_ref=False)

    def test_with_worker_breaks(self):
        self._compare(_train_cfg(), use_ref=False, seg_ends=True)


class TestWorkerBoundary(unittest.TestCase):
    def test_seq_batches_breaks(self):
        dones = np.zeros(12, dtype=np.float32)
        dones[2] = 1
        breaks = np.zeros(12, dtype=bool)
        breaks[6] = True
        self.assertEqual(_seq_batches(12, 4, dones, breaks=breaks),
                         [(0, 3), (3, 7), (7, 11), (11, 12)])
        self.assertEqual(_seq_batches(12, 4, None, breaks=breaks),
                         [(0, 4), (4, 7), (7, 11), (11, 12)])

    def test_merge_rollouts_gae_and_cuts(self):
        torch.manual_seed(0)
        net = PolicyNet(NET)
        r0 = _rollout(net, 30, 50, seed=1)     # 不足一局:末步未结束,需用自己的 last_value 自举
        r1 = _rollout(net, 22, 9, seed=2)
        self.assertFalse(r0.last_done)
        merged, adv, ret = merge_rollouts([r0, r1], 0.99, 0.95)
        a0, t0 = compute_gae(r0, 0.99, 0.95)
        a1, t1 = compute_gae(r1, 0.99, 0.95)
        np.testing.assert_array_equal(adv, np.concatenate([a0, a1]))
        np.testing.assert_array_equal(ret, np.concatenate([t0, t1]))
        # 拼成一条再算 GAE 会把 r1 的首步价值当成 r0 的自举值:必须不同,证明各自 GAE 是必要的
        self.assertFalse(np.allclose(compute_gae(merged, 0.99, 0.95)[0][:30], a0))
        self.assertEqual(np.flatnonzero(merged.seg_ends).tolist(), [29, 51])
        self.assertEqual(len(merged.actions), 52)
        self.assertEqual(merged.h_in[0].shape[0], 52)
        seqs = _seq_batches(52, 8, merged.dones, breaks=merged.seg_ends)
        self.assertFalse(any(s < 30 < e for s, e in seqs), f"序列跨越 worker 边界: {seqs}")
        self.assertTrue(any(s < 30 < e for s, e in _seq_batches(52, 8, merged.dones)),
                        "不传 breaks 时应当会跨界(否则本测试没有覆盖到)")
        cfg = {"train": _train_cfg(seq_len=8)}
        opt = torch.optim.Adam(net.parameters(), lr=1e-3)
        logs = update(net, opt, merged, adv, ret, cfg)
        self.assertTrue(np.isfinite(logs["policy_loss"]))

    def test_merge_episodes_in_slot_order(self):
        stats = Stats()
        results = [{"ok": True, "episodes": [{"result": "win"}]}, None,
                   {"ok": False, "episodes": [{"result": "lose"}, {"result": "draw"}]}]
        merge_episodes(stats, results)
        self.assertEqual([e["result"] for e in stats.episodes], ["win", "lose", "draw"])
        self.assertEqual((stats.wins, stats.losses, stats.draws), (1, 1, 1))


def _rec(result: str, hp: int) -> MatchRecord:
    return MatchRecord(result=result, kind="ko", frames=10, secs=1.0, hp_diff=hp, tags=0,
                       com_level=2)


class _FakePool:
    def __init__(self, per_worker):
        self.per_worker = per_worker
        self.calls = []

    def evaluate(self, net, episodes, com_level=0, deterministic=True, on_record=None):
        self.calls.append((episodes, com_level, deterministic))
        out = [r for recs in self.per_worker for r in recs][:episodes]
        for i, r in enumerate(out):
            if on_record is not None:
                on_record(i, r)
        return out


class TestDistributedEvalSummary(unittest.TestCase):
    def test_split_episodes(self):
        self.assertEqual(split_episodes(7, 3), [3, 2, 2])
        self.assertEqual(split_episodes(2, 3), [1, 1, 0])
        self.assertEqual(sum(split_episodes(100, 8)), 100)

    def test_evaluator_with_pool_uses_wilson_summary(self):
        per_worker = [[_rec("win", 300), _rec("lose", -200)],
                      [_rec("win", 100), _rec("draw", 0)],
                      [_rec("win", 50)]]
        pool = _FakePool(per_worker)
        ev = Evaluator(env=None, default_episodes=5, pool=pool)
        seen = []
        summary = ev.run(PolicyNet(NET), episodes=5, com_level=2, deterministic=False,
                         on_record=lambda i, r: seen.append(i))
        self.assertEqual(pool.calls, [(5, 2, False)])
        self.assertEqual(seen, [0, 1, 2, 3, 4])
        flat = [r for recs in per_worker for r in recs]
        self.assertEqual(summary, summarize(flat, com_level=2))
        self.assertEqual(summary["matches"], 5)
        self.assertEqual(summary["decided"], 4)
        self.assertEqual(summary["com_level"], 3)
        self.assertEqual(summary["win_rate_ci"], list(wilson_interval(3, 4)))
        self.assertAlmostEqual(summary["mean_hp_diff"], 50.0)


# --- 真实 worker 进程(spawn) -------------------------------------------------
class _DyingEnv(_OfflineEnv):
    """第 die_at 步直接结束 worker 进程,模拟游戏/worker 崩溃。"""

    def __init__(self, die_at: int, **kw):
        super().__init__(**kw)
        self._die_at = int(die_at)
        self._n = 0

    def step(self, action):
        self._n += 1
        if self._n >= self._die_at:
            os._exit(3)
        return super().step(action)


class _DyingProvider(OfflineProvider):
    """slot `victim` 第一次构造的 env 会在采样中途杀死 worker;每次 make 记一行到日志文件。"""

    def __init__(self, workdir: str, victim: int = 1, die_at: int = 10, **kw):
        super().__init__(**kw)
        self.workdir = workdir
        self.victim = int(victim)
        self.die_at = int(die_at)

    def make(self, slot: int):
        log = Path(self.workdir) / f"make_{slot}.log"
        first = not log.exists()
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{os.getpid()}\n")
        if slot == self.victim and first:
            env = _DyingEnv(self.die_at, max_episode_steps=self.max_episode_steps,
                            action_repeat=self.action_repeat, seed=self.seed + slot)
            obs, _ = env.reset()
            return env, obs
        return super().make(slot)


class _BrokenProvider(OfflineProvider):
    def make(self, slot: int):
        if slot == 1:
            raise RuntimeError("实例启动失败(测试)")
        return super().make(slot)


class _SlowEnv(_OfflineEnv):
    def step(self, action):
        time.sleep(0.5)
        return super().step(action)


class _SlowProvider(OfflineProvider):
    """每局约 max_episode_steps × 0.5s:单局在超时内,整轮评估远超超时。"""

    def make(self, slot: int):
        env = _SlowEnv(max_episode_steps=self.max_episode_steps,
                       action_repeat=self.action_repeat, seed=self.seed + slot)
        obs, _ = env.reset()
        return env, obs


def _smoke_cfg(tmp: str) -> dict:
    return {
        "env": {"team": 0, "action_repeat": 2, "difficulty": 2, "max_episode_steps": 8,
                "frame_timeout": 1.0},
        "network": dict(NET),
        "train": _train_cfg(worker_timeout=120.0, worker_max_failures=3),
        "checkpoint": {"dir": tmp, "save_every": 1, "keep": 2},
        "logging": {"tensorboard": str(Path(tmp) / "tb")},
        "curriculum": {"enabled": False},
    }


class TestWorkerPool(unittest.TestCase):
    def test_eval_split_and_worker_revive(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _smoke_cfg(tmp)
            net = PolicyNet(NET)
            pool = WorkerPool(cfg, OfflineProvider(max_episode_steps=6), 3, seed=0,
                              timeout=120.0, max_failures=3, verbose=False)
            try:
                pool.start()
                summary = Evaluator(pool=pool).run(net, episodes=7, com_level=2)
                self.assertEqual(summary["matches"], 7)
                self.assertEqual(summary["com_level"], 3)
                self.assertEqual(summary["win_rate_ci"],
                                 list(wilson_interval(summary["wins"], summary["decided"])))

                victim = pool.slots[1].proc
                victim.kill()
                victim.join(30)
                results, _ = pool.collect(net, 16)
                self.assertIsNone(results[1], "被杀的 worker 本轮不应有结果")
                self.assertTrue(results[0]["ok"] and results[2]["ok"])
                self.assertEqual(pool.slots[1].gen, 1, "应按原 slot 重启 worker")
                results, _ = pool.collect(net, 16)
                self.assertTrue(all(r is not None and r["ok"] for r in results))
                self.assertEqual([r["steps"] for r in results], [16, 16, 16])
                procs = [s.proc for s in pool.slots]
            finally:
                pool.close()
            self.assertFalse(any(p.is_alive() for p in procs), "close 后 worker 仍在运行")

    def test_consecutive_failures_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = WorkerPool(_smoke_cfg(tmp), _BrokenProvider(), 2, seed=0, timeout=120.0,
                              max_failures=1, verbose=False)
            try:
                with self.assertRaises(RuntimeError):
                    pool.start()
            finally:
                pool.close()
            self.assertTrue(all(s is None or not s.proc.is_alive() for s in pool.slots))

    def test_eval_longer_than_timeout_keeps_every_record(self):
        # 实机 100 局/4 实例时每个 worker 要打 25 局(~40min),而 worker_timeout 是 900s:
        # 超时必须按"两局之间"算,否则整轮被判失效、已打完的局全部丢掉。
        with tempfile.TemporaryDirectory() as tmp:
            pool = WorkerPool(_smoke_cfg(tmp), _SlowProvider(max_episode_steps=6), 1, seed=0,
                              timeout=10.0, max_failures=0, verbose=False)
            try:
                pool.start()
                t0 = time.monotonic()
                recs = pool.evaluate(PolicyNet(NET), 4, com_level=2)
                elapsed = time.monotonic() - t0
                self.assertGreater(elapsed, 10.0, "评估总时长应超过单次超时,否则没测到")
                self.assertEqual(len(recs), 4)
                self.assertEqual(pool.slots[0].gen, 0, "逐局有进度时不应判 worker 失效")
            finally:
                pool.close()


class TestOfflineMultiEnvTrain(unittest.TestCase):
    def test_three_envs_with_eval_and_killed_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _smoke_cfg(tmp)
            provider = _DyingProvider(tmp, victim=1, die_at=10, max_episode_steps=8)
            stats = tp.train(cfg, updates=2, steps=32, verbose=False, num_envs=3,
                             provider=provider, eval_interval=1, eval_episodes=3)
            self.assertEqual(len(stats.learning_rates), 2)
            # update 1: worker 1 中途死掉,只有 2 个 worker 的对局;update 2: 3 个 worker 各 4 局
            self.assertGreaterEqual(len(stats.episodes), 3 * 32 // 8)
            makes = (Path(tmp) / "make_1.log").read_text(encoding="utf-8").split()
            self.assertEqual(len(makes), 2, f"worker 1 应被重启一次: {makes}")
            self.assertNotEqual(makes[0], makes[1], "重启后应是新的 worker 进程")
            best = torch.load(Path(tmp) / "best.pt", map_location="cpu")
            self.assertEqual(best["eval_episodes"], 3)
            self.assertEqual(best["com_level"], 2)
            ckpt = torch.load(Path(tmp) / "ppo_000002.pt", map_location="cpu")
            # update 1 只有 worker 0/2 的 2×32 步, update 2 三个 worker 共 3×32 步
            self.assertEqual(ckpt["global_step"], 2 * 32 + 3 * 32)

    def test_env_with_num_envs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                tp.train(_smoke_cfg(tmp), updates=1, env=_OfflineEnv(), verbose=False,
                         num_envs=2, provider=OfflineProvider())


if __name__ == "__main__":
    unittest.main()
