"""Masked Recurrent PPO 训练主循环。

用法:

    # 实机训练(需要游戏已启动并注入 DLL;脚本会自己进 VS COM)
    python -m src.train.train_ppo --updates 20

    # 无游戏冒烟(合成环境,只验证训练循环不崩)
    python -m src.train.train_ppo --updates 2 --steps 64 --offline

    # 断点续训
    python -m src.train.train_ppo --resume checkpoints/ppo_000020.pt

超参在 `configs/train_ppo.yaml`。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agents.ppo_agent import N_ACTIONS, PolicyNet, load_weights, to_batch  # noqa: E402
from env import action_space, obs_builder                      # noqa: E402
from train.curriculum import Curriculum                        # noqa: E402
from train.eval import (MatchRecord, episode_kind, episode_result, play_match,  # noqa: E402
                        play_matches, summarize)

OBS_KEYS = ("self", "opp", "global", "proj", "proj_mask", "motion_self", "motion_opp")


def eval_mode_name(deterministic: bool) -> str:
    """评估口径的稳定名字, 写进 checkpoint 元数据, 便于跨会话核对。"""
    return "argmax" if deterministic else "sample"


def baseline_mode_warning(baseline_mode: Optional[str], configured_mode: str) -> Optional[str]:
    """best.pt 基线口径与本次训练不一致时返回告警文本, 否则 None。

    历史 checkpoint 不带该字段(None), 视为"未记录"不告警; 只有记录了口径且
    与本次配置不同才提示——跨口径的胜率点估计与 CI 不可直接比较, 门控会误判。
    """
    if baseline_mode is None or baseline_mode == configured_mode:
        return None
    return (f"best.pt 基线口径={baseline_mode}, 本次 in-loop 评估口径={configured_mode}; "
            f"跨口径比较会误提拔/误杀 best, 请先用同口径重测基线")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Rollout:
    obs: Dict[str, np.ndarray] = field(default_factory=dict)
    actions: np.ndarray = None
    logp: np.ndarray = None
    values: np.ndarray = None
    rewards: np.ndarray = None
    dones: np.ndarray = None
    masks: np.ndarray = None
    h_in: Tuple[np.ndarray, np.ndarray] = None
    ref_h_in: Optional[Tuple[np.ndarray, np.ndarray]] = None
    last_value: float = 0.0
    last_done: bool = False
    # 多环境拼接后的 worker 段末标记(bool, 与 actions 等长)。切 BPTT 序列时
    # 必须在这里断开:下一步属于另一个 env,LSTM hidden 不连续。单环境为 None。
    seg_ends: Optional[np.ndarray] = None


@dataclass
class Stats:
    episodes: List[dict] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    draws: int = 0
    learning_rates: List[float] = field(default_factory=list)

    def win_rate(self, window: int = 100) -> float:
        recent = self.episodes[-window:]
        if not recent:
            return 0.0
        decided = [e for e in recent if e["result"] != "draw"]
        if not decided:
            return 0.0
        return sum(1 for e in decided if e["result"] == "win") / len(decided)


class Evaluator:
    """轻量内置确定性评估回路 (Ticket 01)。

    解耦于数据收集循环。在无探索噪声下以 argmax() 决策评估策略,
    计算胜率、95% Wilson 分数置信区间与平均血量差。

    多环境训练时传 `pool`(`train.parallel.WorkerPool`):不给 env 的 run() 会把局数
    分给各 worker 并行打,记录按 worker 顺序合并后用同一个 `summarize` 汇总。
    """

    def __init__(self, env=None, default_episodes: int = 30, pool=None):
        self.env = env
        self.default_episodes = int(default_episodes)
        self.pool = pool

    def run(self, net: PolicyNet, env=None, episodes: Optional[int] = None,
            com_level: int = 0, deterministic: bool = True,
            on_record: Optional[Callable[[int, MatchRecord], None]] = None) -> Dict:
        target_env = env if env is not None else self.env
        n_episodes = int(episodes or self.default_episodes)
        if target_env is None and self.pool is not None:
            records = self.pool.evaluate(net, n_episodes, com_level=com_level,
                                         deterministic=deterministic, on_record=on_record)
            return summarize(records, com_level=com_level)
        if target_env is None:
            raise ValueError("Evaluator 缺少环境实例")
        was_training = net.training
        net.eval()
        try:
            records = play_matches(net, target_env, n_episodes, com_level=com_level,
                                   on_record=on_record, deterministic=deterministic)
        finally:
            if was_training:
                net.train()
        return summarize(records, com_level=com_level)


# ---------------------------------------------------------------------------
# 采样
# ---------------------------------------------------------------------------
def _stack_obs(obs: Dict[str, np.ndarray]) -> Dict[str, np.ndarray]:
    return {k: np.asarray(obs[k], dtype=np.float32 if k not in ("motion_self", "motion_opp")
                          else np.int64) for k in OBS_KEYS}


def collect(net: PolicyNet, env, n_steps: int, hidden, stats: Stats, obs: Dict,
            deterministic: bool = False, ep_state: Optional[Dict] = None,
            stop_on_done: bool = False,
            max_steps: Optional[int] = None,
            ref_net: Optional[PolicyNet] = None, ref_hidden=None
            ) -> Tuple[Rollout, object, float]:
    """采样 n_steps 步;返回 rollout、最新观测、最后一步的推理耗时(ms)。

    **不在开头 reset**:回合必须跨 update 连续(单局 60s ≈ 1800 步远远超过
    n_steps=512)。早期版本每次 update 都 reset 一次,导致 `max_episode_steps`
    永远触发不了、每 17s 白白重开一次回合。

    `stop_on_done=True` 时批次在**本局结束的那一步之后**截断(上限 `max_steps`),
    这样 `last_done=True`,GAE 的最后一步不再依赖 critic 对未完成对局的猜测。
    实测固定 1024 步(不足一局)时,续训会在 20~40 次更新内把胜率从 40~75% 练到
    8~20%;整局批次的作用就是消掉这部分噪声。
    """
    buf = {k: [] for k in OBS_KEYS}
    actions, logps, values, rewards, dones, masks = [], [], [], [], [], []
    h_in = ([], [])
    ref_h_in = ([], [])
    if ref_net is not None and ref_hidden is None:
        ref_hidden = ref_net.init_hidden(1)
    ep = ep_state or {}
    ep_return = float(ep.get("return", 0.0))
    ep_steps = int(ep.get("steps", 0))
    ep_damage = float(ep.get("damage", 0.0))
    ep_tags = int(ep.get("tags", 0))
    ep_comp: Dict[str, float] = dict(ep.get("comp") or {})
    ep_counts: Dict[str, int] = dict(ep.get("counts") or {})
    ep_tag_probe = list(ep.get("tag_probe") or [])
    ep_last_sw_step = int(ep.get("last_sw_step", 0))
    ep_last_sw = int((ep.get("counts") or {}).get("switches", 0))
    infer_ms = 0.0
    t0 = time.perf_counter()
    steps_done = 0

    budget = int(max_steps) if (stop_on_done and max_steps) else int(n_steps)
    for _ in range(budget):
        for k in OBS_KEYS:
            buf[k].append(np.asarray(obs[k]))
        masks.append(np.asarray(obs["action_mask"], dtype=np.float32))
        h_in[0].append(hidden[0][0].detach().numpy().copy())
        h_in[1].append(hidden[1][0].detach().numpy().copy())
        if ref_net is not None:
            ref_h_in[0].append(ref_hidden[0][0].detach().numpy().copy())
            ref_h_in[1].append(ref_hidden[1][0].detach().numpy().copy())

        t_i = time.perf_counter()
        with torch.inference_mode():
            batch = {k: v.unsqueeze(0) for k, v in to_batch(obs).items()}
            logits, value, hidden = net.forward(batch, hidden, seq=False)
            dist = net.masked_dist(logits, batch["action_mask"])
            cand = dist.logits if getattr(dist, "logits", None) is not None else dist.probs
            action = cand.argmax(-1) if deterministic else dist.sample()
        infer_ms += (time.perf_counter() - t_i) * 1000.0
        if ref_net is not None:
            with torch.no_grad():
                _, _, ref_hidden = ref_net.forward(batch, ref_hidden, seq=False)

        obs, reward, term, trunc, info = env.step(int(action))
        done = bool(term or trunc)

        actions.append(int(action))
        logps.append(float(dist.log_prob(action)))
        values.append(float(value.squeeze(-1)))
        rewards.append(float(reward))
        dones.append(done)

        ep_return += float(reward)
        ep_steps += 1
        d, b, _, name = action_space.decode(int(action))
        comp = info.get("reward_components") or {}
        ep_damage += float(comp.get("hp", 0.0))
        for _k, _v in comp.items():
            ep_comp[_k] = ep_comp.get(_k, 0.0) + float(_v)
        _cnt = info.get("combat_counts")
        if _cnt:
            ep_counts = dict(_cnt)
            _sw_now = int(_cnt.get("switches", 0) or 0)
            if _sw_now > ep_last_sw:
                ep_last_sw = _sw_now
                ep_last_sw_step = ep_steps
                # sw 增量在换人后一步的 info 才可见:回填最近 12 步内的 Tag 尝试为成功,
                # 并记录命中延迟 delta(§8.9 探针按住 200ms≈12 帧才成功,换人前摇可能多帧;
                # 窄窗口会把"需保持 N 帧"误判成"条件不对"。若 12 步内无 Tag 尝试,
                # 说明是自动/强制换人,不回填)。
                for _p in reversed(ep_tag_probe):
                    _delta = int(ep_steps - _p.get("step", -999))
                    if _delta < 1 or _delta > 13:
                        break
                    if _p.get("ok", 0) == 0:
                        _p["ok"] = 1
                        _p["delta"] = _delta
                        break
        if name == "Tag":
            ep_tags += 1
            # Tag 尝试分箱(定位换人前置条件):记决策帧前台 motion / slave_ban /
            # 白血差 / 距上次成功换人的步数;成功与否在 episode 结束时按 sw 增量回填。
            try:
                _snap0 = (info.get("state_snapshot") or {}).get("teams") or []
                _tm0 = _snap0[env.team] if len(_snap0) > getattr(env, "team", 0) else {}
                _life0 = float(_tm0.get("life", 0) or 0)
                _reg0 = float(_tm0.get("regain", 0) or 0)
                ep_tag_probe.append({
                    "step": int(ep_steps),
                    "motion": int((obs.get("motion_self", 0) or 0)),
                    "slave_ban": int(_tm0.get("slave_ban", 0) or 0),
                    "white_gap": float(_reg0 - _life0),
                    "since_sw": int(ep_steps - ep_last_sw_step),
                    "ok": 0,
                    "delta": 0,
                })
            except Exception:
                pass

        if done:
            result = episode_result(info)
            _snap = (info.get("state_snapshot") or {})
            _lives = [t.get("life") for t in (_snap.get("teams") or [])]
            stats.episodes.append({"return": ep_return, "steps": ep_steps,
                                   "result": result, "damage": ep_damage,
                                   "tags": ep_tags, "lives": _lives,
                                   "bstate": info.get("battle_state"),
                                   "kind": episode_kind(info),
                                   "comp": dict(ep_comp),
                                   "counts": dict(ep_counts),
                                   "tag_probe": list(ep_tag_probe),
                                   "secs": ep_steps * float(getattr(env, "action_repeat", 2)) / 60.0})
            if result == "win":
                stats.wins += 1
            elif result == "lose":
                stats.losses += 1
            else:
                stats.draws += 1
            ep_return, ep_steps, ep_damage, ep_tags = 0.0, 0, 0.0, 0
            ep_comp = {}
            ep_counts = {}
            ep_tag_probe = []
            ep_last_sw_step = 0
            ep_last_sw = 0
            hidden = net.init_hidden(1)          # episode 边界清零 LSTM
            if ref_net is not None:
                ref_hidden = ref_net.init_hidden(1)  # 参考网自己的 hidden 同步清零
            # episode 结束后必须 reset,否则会继续在回合演出里空转,
            # 把每一帧"仍是终止态"重复算成一局(之前 2048 步出现 184 局的根因)。
            obs, info = env.reset()
            if stop_on_done:
                break

    with torch.inference_mode():
        batch = {k: v.unsqueeze(0) for k, v in to_batch(obs).items()}
        _, last_value, _ = net.forward(batch, hidden, seq=False)

    roll = Rollout(
        obs={k: np.stack(v) for k, v in buf.items()},
        actions=np.asarray(actions, dtype=np.int64),
        logp=np.asarray(logps, dtype=np.float32),
        values=np.asarray(values, dtype=np.float32),
        rewards=np.asarray(rewards, dtype=np.float32),
        dones=np.asarray(dones, dtype=np.float32),
        masks=np.stack(masks),
        # h_in: (T, batch=1, hidden);训练时取 h_in[s].unsqueeze(0) -> (1, 1, hidden)
        h_in=(np.stack(h_in[0]), np.stack(h_in[1])),
        ref_h_in=((np.stack(ref_h_in[0]), np.stack(ref_h_in[1]))
                  if ref_net is not None else None),
        last_value=float(last_value.squeeze(-1)),
        last_done=bool(dones[-1]),
    )
    elapsed = time.perf_counter() - t0
    ep_out = {"return": ep_return, "steps": ep_steps,
              "damage": ep_damage, "tags": ep_tags, "comp": ep_comp,
              "counts": ep_counts, "tag_probe": list(ep_tag_probe),
              "last_sw_step": int(ep_last_sw_step)}
    if ref_net is not None:
        ep_out["ref_hidden"] = ref_hidden
    return roll, (obs, hidden, ep_out), (infer_ms / max(steps_done, 1)), elapsed


def _comp_means(episodes: List[dict]) -> Dict[str, float]:
    """最近若干局的奖励分项均值。

    用来发现"权重配了但从未触发"的奖励项(例如版边/接近项在随机策略下恒为 0)。
    """
    if not episodes:
        return {}
    out: Dict[str, float] = {}
    for e in episodes:
        for k, v in (e.get("comp") or {}).items():
            out[k] = out.get(k, 0.0) + float(v)
    return {k: v / len(episodes) for k, v in out.items()}


def _comp_str(comp: Dict[str, float]) -> str:
    items = [(k, float(v)) for k, v in (comp or {}).items() if abs(float(v)) > 1e-9]
    items.sort(key=lambda kv: -abs(kv[1]))
    return ",".join(f"{k}{v:+.3f}" for k, v in items) or "-"


# ---------------------------------------------------------------------------
# GAE + 更新
# ---------------------------------------------------------------------------
def is_significant_best(
    latest_win_rate: float,
    latest_ci_low: float,
    best_win_rate: float,
    best_ci_low: float,
) -> bool:
    """best.pt 更新门控: 点估计更高且 Wilson 下限也更高才算真正进步。

    单次 30~50 局 sample 评估的 95% CI 宽达 ±12~17pp; 只看点估计会把
    86.7% 这类上尾样本固化成"历史最佳", 之后所有 ~59% 的真实策略都会被判退化。
    """
    return (latest_win_rate > best_win_rate) and (latest_ci_low > best_ci_low)


def compute_gae(roll: Rollout, gamma: float, lam: float) -> Tuple[np.ndarray, np.ndarray]:
    n = len(roll.rewards)
    adv = np.zeros(n, dtype=np.float32)
    last = 0.0
    for t in reversed(range(n)):
        nonterminal = 0.0 if bool(roll.dones[t]) else 1.0
        next_value = roll.last_value if t == n - 1 else roll.values[t + 1]
        delta = roll.rewards[t] + gamma * next_value * nonterminal - roll.values[t]
        last = delta + gamma * lam * nonterminal * last
        adv[t] = last
    return adv, adv + roll.values


def _seq_batches(n: int, seq_len: int, dones: Optional[np.ndarray] = None,
                 breaks: Optional[np.ndarray] = None) -> List[Tuple[int, int]]:
    """把长度 n 的 rollout 切成 BPTT 序列 [s, e)。

    `dones[t]` 或 `breaks[t]` 为真时序列在 t 之后断开:前者是对局边界,
    后者是多环境拼接时的 worker 边界(下一步属于另一个 env,hidden 不连续)。
    """
    if dones is None and breaks is None:
        return [(s, min(s + seq_len, n)) for s in range(0, n, seq_len)]
    cut = np.zeros(n, dtype=bool)
    if dones is not None:
        cut |= np.asarray(dones[:n]).astype(bool)
    if breaks is not None:
        cut |= np.asarray(breaks[:n]).astype(bool)
    chunks: List[Tuple[int, int]] = []
    s = 0
    while s < n:
        e = s
        while e < n and (e - s) < seq_len:
            if cut[e]:
                e += 1
                break
            e += 1
        chunks.append((s, e))
        s = e
    return chunks


def merge_rollouts(rolls: List[Rollout], gamma: float, lam: float
                   ) -> Tuple[Rollout, np.ndarray, np.ndarray]:
    """多环境:每个 worker 的 rollout 先各自做 GAE(用各自的 last_value/last_done),
    再按 worker 顺序拼接;`seg_ends` 标出每段最后一步,供切序列时断开。"""
    advs, rets = [], []
    for r in rolls:
        a, rt = compute_gae(r, gamma, lam)
        advs.append(a)
        rets.append(rt)
    lens = [len(r.actions) for r in rolls]
    seg_ends = np.zeros(sum(lens), dtype=bool)
    seg_ends[np.cumsum(lens) - 1] = True
    have_ref = all(r.ref_h_in is not None for r in rolls)
    merged = Rollout(
        obs={k: np.concatenate([r.obs[k] for r in rolls]) for k in rolls[0].obs},
        actions=np.concatenate([r.actions for r in rolls]),
        logp=np.concatenate([r.logp for r in rolls]),
        values=np.concatenate([r.values for r in rolls]),
        rewards=np.concatenate([r.rewards for r in rolls]),
        dones=np.concatenate([r.dones for r in rolls]),
        masks=np.concatenate([r.masks for r in rolls]),
        h_in=(np.concatenate([r.h_in[0] for r in rolls]),
              np.concatenate([r.h_in[1] for r in rolls])),
        ref_h_in=((np.concatenate([r.ref_h_in[0] for r in rolls]),
                   np.concatenate([r.ref_h_in[1] for r in rolls])) if have_ref else None),
        last_value=float(rolls[-1].last_value),
        last_done=bool(rolls[-1].last_done),
        seg_ends=seg_ends,
    )
    return merged, np.concatenate(advs), np.concatenate(rets)


def update(net: PolicyNet, opt: torch.optim.Optimizer, roll: Rollout,
           adv: np.ndarray, ret: np.ndarray, cfg: dict,
           ref_net: Optional[PolicyNet] = None, ref_kl_coef: float = 0.0) -> dict:
    tcfg = cfg["train"]
    gamma, lam = float(tcfg["gamma"]), float(tcfg["gae_lambda"])
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
        # 兼容旧 rollout / 未存参考 hidden 的调用方:退回旧行为。
        rh0, rc0 = h0, c0

    seqs = _seq_batches(len(roll.actions), seq_len, roll.dones, breaks=roll.seg_ends)
    logs = {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0,
            "approx_kl": 0.0, "clip_frac": 0.0, "ref_kl": 0.0, "n_updates": 0}

    # 全局优势归一化:在整个 rollout 级别归一化(避免 32 步 chunk 局部放大微弱噪声)
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
            # 一次前向整个 minibatch:序列右侧 pad 到 max_len(LSTM 因果, pad 不影响有效步),
            # 每条序列带自己的起始 hidden;损失先按掩码对时间求均值, 再对序列求均值,
            # 与逐序列实现的口径完全一致。pad 位复用该序列最后一步的数据, 避免 NaN 渗入。
            starts = torch.as_tensor([s for s, _ in chunk])
            lens = torch.as_tensor([e - s for s, e in chunk], dtype=torch.float32)
            steps = torch.arange(max_len)
            valid = steps.unsqueeze(0) < lens.unsqueeze(1)                   # (B, T)
            idx = torch.minimum(starts.unsqueeze(1) + steps.unsqueeze(0),
                                (starts + lens.long() - 1).unsqueeze(1))   # (B, T)
            m = valid.float()

            def _seq_mean(x: torch.Tensor) -> torch.Tensor:
                return (x * m).sum(1) / lens                                 # (B,)

            sub = {k: v[idx] for k, v in obs_t.items()}
            hidden = (h0[starts].transpose(0, 1).contiguous(),
                      c0[starts].transpose(0, 1).contiguous())             # (L=1, B, H)
            logits, values, _ = net.forward(sub, hidden, seq=True)        # (B, T, ...)
            mb_masks = masks[idx]
            dist = net.masked_dist(logits, mb_masks)
            logp = dist.log_prob(actions[idx])
            mb_old = old_logp[idx]
            ratio = torch.exp(logp - mb_old)
            a_adv = adv_norm[idx]
            surr1 = ratio * a_adv
            surr2 = torch.clamp(ratio, 1.0 - clip, 1.0 + clip) * a_adv
            policy_loss = _seq_mean(-torch.min(surr1, surr2)).mean()
            value_loss = _seq_mean((values - ret_t[idx]) ** 2).mean()
            entropy = _seq_mean(dist.entropy()).mean()
            loss = (policy_loss
                    + float(tcfg["value_coef"]) * value_loss
                    - float(tcfg["entropy_coef"]) * entropy)
            ref_kl_value = 0.0
            if use_ref:
                with torch.no_grad():
                    ref_hidden = (rh0[starts].transpose(0, 1).contiguous(),
                                  rc0[starts].transpose(0, 1).contiguous())
                    ref_logits, _, _ = ref_net.forward(sub, ref_hidden, seq=True)
                    ref_temp = float(tcfg.get("ref_temperature", 1.0) or 1.0)
                    if ref_temp != 1.0 and ref_temp > 0.0:
                        ref_logits = ref_logits / ref_temp
                    ref_dist = ref_net.masked_dist(ref_logits, mb_masks)
                # KL 必须在 no_grad 之外算:dist 带当前策略梯度, 参考分布已 detach。
                ref_kl_t = _seq_mean(torch.distributions.kl_divergence(dist, ref_dist)).mean()
                loss = loss + ref_kl_coef * ref_kl_t
                ref_kl_value = float(ref_kl_t.detach())
            with torch.no_grad():
                kl_t = _seq_mean((mb_old - logp) ** 2).mean() * 0.5
                clip_t = _seq_mean(((ratio - 1.0).abs() > clip).float()).mean()
            opt.zero_grad()
            if not torch.isfinite(loss):
                logs["skipped"] = logs.get("skipped", 0) + 1
                continue
            loss.backward()
            nn.utils.clip_grad_norm_(net.parameters(), float(tcfg["max_grad_norm"]))
            if any(p.grad is not None and not torch.isfinite(p.grad).all()
                   for p in net.parameters()):
                # 梯度出现 NaN/Inf 时绝不 step:一次脏更新会让后续所有前向输出 NaN
                logs["skipped"] = logs.get("skipped", 0) + 1
                opt.zero_grad(set_to_none=True)
                continue
            opt.step()

            kl = float(kl_t)
            logs["policy_loss"] += float(policy_loss.detach())
            logs["value_loss"] += float(value_loss.detach())
            logs["entropy"] += float(entropy.detach())
            logs["approx_kl"] += kl
            logs["ref_kl"] += ref_kl_value
            logs["clip_frac"] += float(clip_t)
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


# ---------------------------------------------------------------------------
# 游戏进程守护
# ---------------------------------------------------------------------------
def frame_period_us(cfg: dict) -> Optional[int]:
    """`env.frame_period_us`:多实例启动时补丁的帧间隔(微秒);缺省/0 = 原版 16666。"""
    v = (cfg.get("env") or {}).get("frame_period_us")
    return int(v) if v else None


def restart_game(timeout: float = 180.0, slot: Optional[int] = None,
                 period_us: Optional[int] = None):
    """重启游戏并重新注入 DLL(长时间训练时游戏崩溃/卡在胜负对话的自愈路径)。

    slot=None:单实例,走 scripts/game_session.py(结束全部 th155.exe 再启动),返回 bool。
    slot 给定:多实例,只重启该 slot 的实例(`th155.instances`),返回新 pid;失败返回 None。
    `period_us`:仅多实例,见 `frame_period_us`。
    """
    if slot is not None:
        print(f"[!] 重启游戏实例 slot={slot} ...", flush=True)
        try:
            from th155.instances import kill_instance, launch_instance, slot_pid
            old = slot_pid(int(slot))
            if old is not None:
                kill_instance(old)
            pid = launch_instance(int(slot), period_us=period_us)
            print(f"[!] 重启结果: ok slot={slot} pid={pid}", flush=True)
            return pid
        except Exception as exc:
            print(f"[!] 重启 slot={slot} 失败: {exc}", flush=True)
            return None
    py = sys.executable
    script = str(ROOT / "scripts" / "game_session.py")
    print("[!] 重启游戏 ...", flush=True)
    try:
        subprocess.run([py, script, "kill"], timeout=60, capture_output=True)
        time.sleep(3)
        r = subprocess.run([py, script, "start"], timeout=timeout, capture_output=True,
                           text=True)
        ok = r.returncode == 0
        print(f"[!] 重启结果: {'ok' if ok else 'failed'} {(r.stdout or '').strip()[-120:]}", flush=True)
        return ok
    except Exception as exc:
        print(f"[!] 重启失败: {exc}", flush=True)
        return False


def _restart_and_build(cfg: dict, action_repeat, slot: Optional[int] = None):
    """重启游戏(slot=None = 单实例;否则只重启该 slot)并重建绑定新实例的 env。

    重启失败返回 None。
    """
    got = restart_game(slot=slot, period_us=frame_period_us(cfg))
    if not got:
        return None
    return build_env(cfg, action_repeat, pid=None if slot is None else int(got))


def _live_com_difficulty(env) -> Optional[int]:
    """读当前对手(内置 COM)的实际难度;无头/读失败返回 None。"""
    try:
        teams = (env._state() or {}).get("teams") or []
        if len(teams) < 2:
            return None
        value = teams[1 - int(env.team)].get("com_difficulty")
        return None if value is None else int(value)
    except Exception:                       # noqa: BLE001 - 无头环境没有实时状态
        return None


def reset_with_restart(cfg: dict, env, action_repeat, why: str, retries: int = 2,
                        fix_difficulty: bool = True, depth: int = 0,
                        slot: Optional[int] = None):
    """reset 失败(比赛打满 2 胜卡在胜负对话 / 游戏崩溃)时重启游戏再试。

    `fix_difficulty`:确认实机对手难度与 `env.difficulty` 一致。不一致时重启
    游戏重进对局 —— 难度只在"标题 -> VS COM -> 选人"这条路径上由
    `config.difficulty.vs` 写进角色,对局中再改会崩。

    这条检查曾静默失效(配置写 EASY,实机在 COM2 上练了 196 次 update),
    因此现在**重启后还会再验一次**(`depth` 限一次,避免无限重启),再不一致就大声报错。

    `slot`:多实例模式下只重启该 slot 的游戏实例,并把新 env 绑定到新 pid。
    """
    last = None
    want = int((cfg.get("env") or {}).get("difficulty", 1))
    for attempt in range(retries + 1):
        try:
            env, obs_info = env, env.reset()
            if fix_difficulty:
                got = _live_com_difficulty(env)
                if got is not None and got != want:
                    print(f"[w] 对手难度不一致(实机 COM{got + 1} / 配置 COM{want + 1}),"
                          f"重启游戏以走标题菜单重设难度", flush=True)
                    if depth >= 1:
                        print(f"[!] 重启后难度仍是 COM{got + 1},不再重试;"
                              f"训练会打错对手,请检查标题菜单是否卡住", flush=True)
                        return env, obs_info
                    try:
                        env.close()
                    except Exception:
                        pass
                    new_env = _restart_and_build(cfg, action_repeat, slot)
                    if new_env is None:
                        print("[!] 重启游戏失败,沿用当前难度继续", flush=True)
                        return env, obs_info
                    return reset_with_restart(cfg, new_env, action_repeat, why,
                                              retries=retries, fix_difficulty=True,
                                              depth=depth + 1, slot=slot)
            return env, obs_info
        except Exception as exc:
            last = exc
            print(f"[w] {why} reset 失败({type(exc).__name__}: {exc}),"
                  f"重启游戏({attempt + 1}/{retries + 1})", flush=True)
            try:
                env.close()
            except Exception:
                pass
            new_env = _restart_and_build(cfg, action_repeat, slot)
            if new_env is None:
                raise
            env = new_env
    raise last


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
CURRICULUM_MODES = ("restart", "live")


def lr_at(it: int, *args, it_base: Optional[int] = None, total: Optional[int] = None,
          base: Optional[float] = None, final_scale: Optional[float] = None, **kwargs) -> float:
    """线性学习率退火:基于累计步数计算退火进度。

    支持多种调用范式:
    1. 全局进度退火(推荐): lr_at(it, total, base, final_scale=0.1) 或 it_base=0
       严格基于全局累计更新步数 it 计算进度 (it / total),防止断点续训时进度被重置为 0
       导致的高学习率硬回弹 (LR Bounce)。
    2. 兼容相对退火: lr_at(it, it_base, total, base, final_scale=0.1)
       当显式指定非零 it_base 且提供 5 个位置参数时,按 (it - it_base) / total 计算。
    3. 4 参数调用兼容:
       - lr_at(it, total, base, final_scale)  (新规范: args[1] 为小浮点 base)
       - lr_at(it, it_base, total, base)      (旧接口: args[1] 为整数/计数 total)
    4. 3 参数调用: lr_at(it, total, base)
    5. 2 参数调用: lr_at(it, total)
    """
    _it_base = 0
    _total = 0
    _base = 1e-3
    _final_scale = 0.1

    if len(args) >= 4:
        # (it, it_base, total, base, final_scale, ...)
        _it_base, _total, _base, _final_scale = args[0], args[1], args[2], args[3]
    elif len(args) == 3:
        # 两种可能:
        # A. (it, total, base, final_scale): 新规范, args[1] 为学习率 base (如 1e-3, 2e-4 < 1.0)
        # B. (it, it_base, total, base): 旧规范, args[1] 为更新轮数 total (如 200, 10 >= 1)
        if isinstance(args[1], (float, np.floating)) and args[1] < 1.0:
            _it_base = 0
            _total, _base, _final_scale = args[0], args[1], args[2]
        else:
            _it_base, _total, _base = args[0], args[1], args[2]
            _final_scale = 0.1
    elif len(args) == 2:
        if total is not None:
            _it_base, _base = args[0], args[1]
            _total = total
            _final_scale = 0.1
        elif isinstance(args[1], (float, np.floating)) and args[1] < 1.0:
            _it_base = 0
            _total, _base = args[0], args[1]
            _final_scale = 0.1
        else:
            _it_base, _total = args[0], args[1]
            _base = 1e-3
            _final_scale = 0.1
    elif len(args) == 1:
        if total is not None:
            _it_base = args[0]
            _total = total
            _base = 1e-3
            _final_scale = 0.1
        else:
            _it_base = 0
            _total = args[0]
            _base = 1e-3
            _final_scale = 0.1

    # 关键字覆盖
    if it_base is not None:
        _it_base = it_base
    if total is not None:
        _total = total
    if base is not None:
        _base = base
    if final_scale is not None:
        _final_scale = final_scale

    _it_base = int(_it_base or 0)
    _total = int(_total or 0)
    _base = float(_base if _base is not None else 1e-3)
    _final_scale = float(_final_scale if _final_scale is not None else 0.1)

    if _total <= 0:
        return _base
    frac = (it - _it_base) / float(_total)
    frac = min(1.0, max(0.0, frac))
    return _base + (_base * _final_scale - _base) * frac



def _anneal(env, cur: "Curriculum", base_entropy: float, tcfg: dict) -> None:
    """把当前阶段的退火参数落到奖励/熵上(不涉及难度)。"""
    try:
        env.reward_calc.C.update(cur.reward_overrides())
    except Exception:                       # noqa: BLE001 - 无头环境没有 reward_calc
        pass
    tcfg["entropy_coef"] = base_entropy * cur.entropy_scale()


def switch_difficulty(level: int, pid: Optional[int] = None) -> bool:
    """不重启进程切换 VS COM 难度:暂停菜单回标题 -> 重进比赛(选人前写难度)。

    `pid`:多实例时指定操作哪个游戏进程;None = 第一个 th155.exe。
    失败(卡在结算对话/菜单没响应)返回 False,调用方应回退到 `restart_game()`。
    """
    try:
        from th155 import Th155Game
        from th155.menu import MenuDriver
        return bool(MenuDriver(Th155Game(writable=True, pid=pid)).switch_difficulty(int(level)))
    except Exception as exc:                # noqa: BLE001 - 任何异常都退化为重启
        print(f"[w] 回标题切难度失败({type(exc).__name__}: {exc})", flush=True)
        return False


def _difficulty_switch(cfg: dict) -> str:
    mode = str((cfg.get("curriculum") or {}).get("difficulty_switch", "title")).lower()
    return mode if mode in ("title", "restart") else "title"


def _apply_difficulty(cfg: dict, env, level: int, cmode: str, dswitch: str,
                      action_repeat, slot: Optional[int] = None) -> tuple:
    """把新难度落到实机,返回 (env, 新 obs 或 None)。

    优先顺序:live 写字段(未验证) -> 回标题重进(便宜) -> 重启进程(兜底)。
    `slot`:多实例模式下只操作/重启该 slot 的实例。
    """
    cfg["env"]["difficulty"] = int(level)
    if cmode == "live" and env.set_opponent_difficulty(level):
        print(f"[课程] 实时写 com_difficulty -> COM{level + 1}", flush=True)
        return env, None
    pid = getattr(env, "pid", None) if slot is not None else None
    if dswitch == "title" and switch_difficulty(level, pid=pid):
        print(f"[课程] 已通过暂停菜单回标题重进对局(COM{level + 1},未重启进程)", flush=True)
    else:
        print(f"[课程] 重启游戏以按 COM{level + 1} 重进对局(~20-50s)", flush=True)
        got = restart_game(slot=slot, period_us=frame_period_us(cfg))
        if not got:
            return env, None
        if slot is not None:
            pid = int(got)
    try:
        env.close()
    except Exception:                       # noqa: BLE001
        pass
    env = build_env(cfg, action_repeat, pid=pid)
    env, (obs, _info) = reset_with_restart(cfg, env, action_repeat, "课程晋级", slot=slot)
    return env, obs


def _curriculum_mode(cfg: dict) -> str:
    mode = str((cfg.get("curriculum") or {}).get("apply_mode", "restart")).lower()
    return mode if mode in CURRICULUM_MODES else "restart"


def _target_level(cur: "Curriculum", mode: str) -> int:
    """当前阶段该用哪个难度(纯函数,不推进轮转指针)。

    restart 模式下只能取阶段内的第一个难度(换难度必须重进对局);
    live 模式取轮转指针指向的难度。**逐局混合轮转尚未接线** —— 它依赖
    live 写入先被证明安全,见 `_curriculum_mode` 的说明。
    """
    return cur.peek_com_level() if mode == "live" else cur.com_levels[0]


def _recover_game(cfg: dict, env, action_repeat, why: str, slot: Optional[int] = None):
    """采样失效时的统一自愈:重启游戏 -> 重建 env -> 重置到可对战状态。

    两种触发路径共用:
      1) `collect` 抛异常(游戏崩溃/进程消失);
      2) **采样停顿**(fps 连续过低)——游戏状态机卡死时 `step()` 不会抛异常,
         只会每步吃满 `frame_timeout`,表现为 fps 从 25 掉到 ~2。这种情况如果
         不主动重启,训练会在冻结的共享内存上无限空转一整天。
    `slot`:多实例模式下只重启该 slot 的实例。
    """
    print(f"[!] {why}:重启游戏以恢复采样", flush=True)
    try:
        env.close()
    except Exception:                       # noqa: BLE001
        pass
    env = _restart_and_build(cfg, action_repeat, slot)
    if env is None:
        raise RuntimeError("重启游戏失败")
    env, info = reset_with_restart(cfg, env, action_repeat, why, slot=slot)
    return env, info


def build_env(cfg: dict, action_repeat: Optional[int] = None, pid: Optional[int] = None):
    """构造环境。

    `--action-repeat` 覆盖时必须同步缩放 `max_episode_steps`:
    它是**决策步数**上限(见 `Th155Env.step`:`self._steps >= max_episode_steps`),
    而一步的墙钟长度与 action_repeat 成正比。配置里的 `max_episode_steps`
    是按配置自带的 action_repeat 标定的,直接拿它配一个不同的 ar
    就会改变单局墙钟时长 —— 实测踩到过:用 ar=4 的配置(mps=2400)
    跑 ar=2 评估,2400 步只有 80s 就把 4 局提前截成平局,而 ar=4 组一局未截。
    这里按 `配置ar / 实际ar` 等比缩放,保持墙钟时长不变。

    `pid`:多实例时绑定的游戏进程;None = 第一个可用实例。
    `env.lockstep` / `env.lockstep_timeout_ms`:帧锁步开关(见 `Th155Env`)。
    """
    from env import Th155Env
    ecfg = cfg["env"]
    cfg_repeat = int(ecfg.get("action_repeat", 2))
    repeat = int(action_repeat or cfg_repeat)
    steps = int(ecfg.get("max_episode_steps", 900))
    if repeat > 0 and cfg_repeat > 0 and repeat != cfg_repeat:
        # 四舍五入到整步;下限 1 步,避免 ar 极大时缩成 0。
        steps = max(1, int(round(steps * cfg_repeat / repeat)))
    env = Th155Env(team=int(ecfg.get("team", 0)),
                    action_repeat=repeat,
                    max_episode_steps=steps,
                    frame_timeout=float(ecfg.get("frame_timeout", 1.0)),
                    difficulty=int(ecfg.get("difficulty", 1)),
                    round_units=int(ecfg.get("round_units", 0)),
                    round_seconds=int(ecfg.get("round_seconds", 0)),
                    pid=pid,
                    lockstep=bool(ecfg.get("lockstep", False)),
                    lockstep_timeout_ms=int(ecfg.get("lockstep_timeout_ms", 100)))
    if "reward" in cfg and hasattr(env, "reward_calc"):
        env.reward_calc.C.update(cfg["reward"])
    return env


def load_config(path: Optional[str] = None) -> dict:
    p = Path(path) if path else ROOT / "configs" / "train_ppo.yaml"
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def train(cfg: dict, updates: Optional[int] = None, steps: Optional[int] = None,
          env=None, log_dir: Optional[str] = None, verbose: bool = True,
          init_state: Optional[dict] = None,
          action_repeat: Optional[int] = None,
          eval_interval: Optional[int] = None,
          eval_episodes: Optional[int] = None,
          eval_env=None,
          lr: Optional[float] = None,
          lr_mode: Optional[str] = None,
          no_lr_decay: bool = False,
          schedule_total_updates: Optional[int] = None,
          cold_start: bool = False,
          ref_kl_coef: Optional[float] = None,
          ref_checkpoint: Optional[str] = None,
          ent_coef: Optional[float] = None,
          freeze_trunk: bool = False,
          num_envs: Optional[int] = None,
          provider=None) -> Stats:
    """PPO 训练主循环。

    `num_envs`(缺省读 `train.num_envs`,默认 1 = 单环境原路径)>1 时多环境并行采样:
    env 由 `provider`(缺省 `parallel.GameProvider`)在各 worker 进程里构造,此时不能
    传 `env`;`n_steps` 表示每个 env 的步数。训练退出(含异常)时关闭全部 worker 与实例。
    """
    owned: List = []
    try:
        return _train(cfg, updates, steps, env, log_dir, verbose, init_state, action_repeat,
                      eval_interval, eval_episodes, eval_env, lr, lr_mode, no_lr_decay,
                      schedule_total_updates, cold_start, ref_kl_coef, ref_checkpoint,
                      ent_coef, freeze_trunk, num_envs, provider, owned)
    finally:
        for res in reversed(owned):
            res.close()


def _train(cfg: dict, updates: Optional[int], steps: Optional[int], env,
           log_dir: Optional[str], verbose: bool, init_state: Optional[dict],
           action_repeat: Optional[int], eval_interval: Optional[int],
           eval_episodes: Optional[int], eval_env, lr: Optional[float],
           lr_mode: Optional[str], no_lr_decay: bool,
           schedule_total_updates: Optional[int], cold_start: bool,
           ref_kl_coef: Optional[float], ref_checkpoint: Optional[str],
           ent_coef: Optional[float], freeze_trunk: bool, num_envs: Optional[int],
           provider, owned: list) -> Stats:
    is_cold_start = bool(cold_start or cfg.get("train", {}).get("cold_start", False))
    if is_cold_start:
        init_state = None
        if verbose:
            print("[i] 启动全新冷启动训练 (Cold Start, update=0, 杜绝历史旧权重与优化器)")
    tcfg = cfg["train"]
    freeze_trunk = bool(freeze_trunk or tcfg.get("freeze_trunk", False))
    if lr is not None:
        tcfg["lr"] = float(lr)
    if ent_coef is not None:
        tcfg["entropy_coef"] = float(ent_coef)
    if schedule_total_updates is not None:
        tcfg["schedule_total_updates"] = int(schedule_total_updates)

    if no_lr_decay:
        tcfg["lr_mode"] = "constant"
        tcfg["lr_anneal"] = False
    elif lr_mode is not None:
        tcfg["lr_mode"] = str(lr_mode).lower()
        tcfg["lr_anneal"] = (str(lr_mode).lower() == "anneal")
    elif "lr_mode" not in tcfg and "lr_anneal" not in tcfg and init_state is not None and "lr_mode" in init_state and init_state["lr_mode"]:
        tcfg["lr_mode"] = str(init_state["lr_mode"]).lower()
        tcfg["lr_anneal"] = (tcfg["lr_mode"] == "anneal")

    mode = str(tcfg.get("lr_mode") or ("anneal" if tcfg.get("lr_anneal", False) else "constant")).lower()

    total = int(updates or tcfg["total_updates"])
    n_steps = int(steps or tcfg["n_steps"])
    episode_batch = bool(tcfg.get("episode_batch", False))
    seed = int(tcfg.get("seed", 0))
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.set_num_threads(int(tcfg.get("num_threads", 2)))

    eval_interval = int(eval_interval if eval_interval is not None else
                        tcfg.get("eval_interval", cfg.get("eval", {}).get("interval", 15)))
    eval_episodes = int(eval_episodes if eval_episodes is not None else
                        tcfg.get("eval_episodes", cfg.get("eval", {}).get("episodes", 30)))
    n_envs = int(num_envs if num_envs is not None else (tcfg.get("num_envs", 1) or 1))
    multi = n_envs > 1
    if multi:
        if env is not None:
            raise ValueError("num_envs>1 时 env 由各 worker 经 provider 构造,不能再传 env")
        if provider is None:
            from train.parallel import GameProvider
            provider = GameProvider(cfg, action_repeat)
    else:
        env = env if env is not None else build_env(cfg, action_repeat)
    evaluator = Evaluator(env=eval_env or env, default_episodes=eval_episodes)
    eval_det = bool((cfg.get("eval") or {}).get("deterministic", True))
    net = PolicyNet(cfg.get("network") or {})
    if init_state is None and (
        bool((cfg.get("network") or {}).get("orthogonal_init", False)) or
        bool(tcfg.get("orthogonal_init", False))
    ):
        net.init_orthogonal()
    loaded = None
    if init_state is not None:
        model_sd = init_state.get("model_state_dict") or init_state.get("model")
        # 观测扩容后旧权重形状不兼容:load_weights 会按列名迁移(见 PolicyNet.warm_start)
        loaded = load_weights(net, model_sd, verbose=verbose)

    if freeze_trunk:
        # 只训练 actor head: LSTM/trunk/critic 全部冻结, 保住 79% 权重的闭环动力学。
        for _p in net.parameters():
            _p.requires_grad_(False)
        for _p in net.actor.parameters():
            _p.requires_grad_(True)
        if verbose:
            n_train = sum(p.numel() for p in net.parameters() if p.requires_grad)
            n_all = sum(p.numel() for p in net.parameters())
            print(f"[i] 冻结 trunk/LSTM/critic, 仅训练 actor head"
                  f"({n_train}/{n_all} 参数)", flush=True)

    resumed_lr = None
    resumed_base_lr = None
    if init_state is not None:
        if "lr_base" in init_state and init_state["lr_base"] is not None:
            try:
                resumed_base_lr = float(init_state["lr_base"])
            except Exception:
                pass
        opt_sd_raw = init_state.get("optimizer_state_dict") or init_state.get("optimizer")
        if opt_sd_raw and "param_groups" in opt_sd_raw and opt_sd_raw["param_groups"]:
            try:
                resumed_lr = float(opt_sd_raw["param_groups"][0]["lr"])
            except Exception:
                pass
        sched_sd_raw = init_state.get("lr_scheduler_state_dict") or init_state.get("lr_scheduler")
        if sched_sd_raw:
            if resumed_lr is None and "_last_lr" in sched_sd_raw and sched_sd_raw["_last_lr"]:
                try:
                    resumed_lr = float(sched_sd_raw["_last_lr"][0])
                except Exception:
                    pass
            if resumed_base_lr is None and "base_lrs" in sched_sd_raw and sched_sd_raw["base_lrs"]:
                try:
                    resumed_base_lr = float(sched_sd_raw["base_lrs"][0])
                except Exception:
                    pass

    if lr is not None:
        lr_base = float(lr)
    elif mode == "constant":
        # 常数微调模式: 若配置显式指定了 lr, 优先采纳配置中给定的常数学习率 (如 3.0e-5 防漂移)
        if "lr" in tcfg and tcfg["lr"] is not None and (
            init_state is None or float(tcfg["lr"]) != float(init_state.get("lr_base", 0.0) or 0.0)
        ):
            lr_base = float(tcfg["lr"])
        elif resumed_lr is not None:
            lr_base = resumed_lr
        elif resumed_base_lr is not None:
            lr_base = resumed_base_lr
        else:
            lr_base = float(tcfg["lr"])
    elif resumed_base_lr is not None:
        # 续训且未显式传入 --lr 时,优先沿用 checkpoint 中的 base_lr,杜绝配置默认值覆盖导致的 LR 阶跃
        lr_base = resumed_base_lr
    else:
        lr_base = float(tcfg["lr"])

    trainable = [p for p in net.parameters() if p.requires_grad]
    opt = torch.optim.Adam(trainable, lr=lr_base)
    reset_opt = bool(tcfg.get("reset_optimizer", False))
    opt_sd = (init_state.get("optimizer_state_dict") or init_state.get("optimizer")) if (init_state is not None and not reset_opt) else None
    if opt_sd is not None:
        if freeze_trunk:
            if verbose:
                print("[i] 冻结模式: 跳过优化器状态(旧状态含全部参数, 仅 actor 参数组)", flush=True)
        elif loaded is not None and loaded.migrated:
            # 迁移过的层形状变了,旧的 Adam 一阶/二阶矩对不上:load 会成功但 step 时才炸。
            if verbose:
                print(f"[w] 权重已迁移(跳过 {len(loaded.skipped)} 个参数),"
                      f"丢弃旧优化器状态", flush=True)
        else:
            try:
                opt.load_state_dict(opt_sd)
                if verbose:
                    print("[i] 成功恢复优化器状态(Adam 动量与二阶矩)")
            except Exception as exc:
                if verbose:
                    print(f"[w] 无法恢复优化器状态({exc}),使用新优化器")

    # 如果显式指定了 lr 或处于常数微调模式,同步更新 opt 参数组中的 lr
    if lr is not None or (mode == "constant" and (resumed_lr is not None or "lr" in tcfg)):
        for _g in opt.param_groups:
            _g["lr"] = lr_base

    if verbose:
        print(f"[i] 学习率设定: mode={mode}, lr={lr_base:.2e}", flush=True)

    scheduler = torch.optim.lr_scheduler.LambdaLR(opt, lr_lambda=lambda _: 1.0)
    if init_state is not None and not freeze_trunk and not reset_opt:
        sched_sd = init_state.get("lr_scheduler_state_dict") or init_state.get("lr_scheduler")
        if sched_sd is not None:
            if freeze_trunk:
                if verbose:
                    print("[i] 冻结模式: 跳过学习率调度器状态", flush=True)
            elif loaded is not None and loaded.migrated:
                if verbose:
                    print("[w] 权重已迁移,丢弃旧学习率调度器状态", flush=True)
            else:
                try:
                    scheduler.load_state_dict(sched_sd)
                    if verbose:
                        print("[i] 成功恢复学习率调度器状态")
                except Exception as exc:
                    if verbose:
                        print(f"[w] 无法恢复学习率调度器状态({exc})")

    stats = Stats()
    hidden = net.init_hidden(1)

    ckpt_dir = ROOT / str(cfg.get("checkpoint", {}).get("dir", "checkpoints"))
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    # 参考策略(ref-KL): 冻结 best.pt, 惩罚新策略偏离它。用于续训时防止
    # 3e-5 级别的微调在几十个 update 内把 79% 的权重练坏。
    kl_coef = float(ref_kl_coef if ref_kl_coef is not None else (tcfg.get("ref_kl_coef", 0.0) or 0.0))
    ref_net = None
    ref_path = None
    if kl_coef > 0.0:
        ref_path = Path(ref_checkpoint or tcfg.get("ref_checkpoint") or (ckpt_dir / "best.pt"))
        if not ref_path.is_absolute():
            ref_path = ROOT / ref_path
        if ref_path.exists():
            try:
                ref_data = torch.load(ref_path, map_location="cpu", weights_only=False)
                ref_net = PolicyNet(cfg.get("network") or {})
                load_weights(ref_net, ref_data.get("model_state_dict") or ref_data.get("model"),
                             verbose=False)
                ref_net.eval()
                for _p in ref_net.parameters():
                    _p.requires_grad_(False)
                if verbose:
                    print(f"[i] 参考策略: {ref_path} (ref_kl_coef={kl_coef})", flush=True)
            except Exception as exc:
                print(f"[w] 参考策略加载失败({exc}),关闭 ref-KL", flush=True)
                ref_net = None
                kl_coef = 0.0
        else:
            print(f"[w] 参考策略不存在: {ref_path}, 关闭 ref-KL", flush=True)
            kl_coef = 0.0

    ref_hidden = ref_net.init_hidden(1) if ref_net is not None else None

    writer = None
    try:
        from torch.utils.tensorboard import SummaryWriter
        writer = SummaryWriter(log_dir or str(ROOT / str(cfg["logging"]["tensorboard"])))
    except Exception as exc:              # tensorboard 缺失时退化为纯控制台
        if verbose:
            print(f"[w] TensorBoard 不可用({exc}),只写控制台/JSON")

    # 课程调度器:按能力探测而不是 isinstance —— 只有能切实机难度的环境才启用,
    # 否则退火/难度切换会作用在合成环境上,静默污染训练统计。
    cur = Curriculum.from_config(cfg)
    can_switch = (bool(getattr(provider, "supports_difficulty", False)) if multi
                  else hasattr(env, "set_opponent_difficulty"))
    if (not can_switch
            or not bool((cfg.get("curriculum") or {}).get("enabled", True))):
        cur = None
    if init_state is not None and cur is not None and init_state.get("curriculum"):
        cur = Curriculum.from_dict(init_state["curriculum"], stages=cur.stages)
        print(f"[i] 恢复课程进度: 阶段 {cur.stage.name}"
              f"({cur.stats()['stage_decided']} 局/{cur.stats()['stage_win_rate']:.1%})",
              flush=True)

    base_entropy = float(tcfg["entropy_coef"])
    cmode = _curriculum_mode(cfg)
    dswitch = _difficulty_switch(cfg)

    restarts = 0
    stall = 0                      # 连续 fps<5 的 update 计数(卡死看门狗)
    pool = None
    if multi:
        from train.parallel import WorkerPool, merge_episodes, state_to_numpy
        pool = WorkerPool(cfg, provider, n_envs, seed=seed,
                          ref_state=state_to_numpy(ref_net) if ref_net is not None else None,
                          timeout=float(tcfg.get("worker_timeout", 900.0)),
                          max_failures=int(tcfg.get("worker_max_failures", 5)),
                          verbose=verbose)
        owned.append(pool)             # 先登记再启动:启动途中失败也要关掉已起的 worker
        pool.start()
        evaluator.pool = pool
        obs = None
        if verbose:
            print(f"[i] 多环境并行采样: {n_envs} 个 worker, 每个 env 每次更新 {n_steps} 步",
                  flush=True)
    else:
        env, (obs, _info0) = reset_with_restart(cfg, env, action_repeat,
                                                 "训练开始")   # 之后跨 update 连续,不再 reset
    if cur is not None:
        _anneal(env, cur, base_entropy, tcfg)
        if verbose:
            print(f"[课程] 阶段={cur.stage.name} 难度=COM{_target_level(cur, cmode) + 1} "
                  f"模式={cmode} 退火={cur.reward_overrides() or '-'}", flush=True)
    ep_state = {"return": 0.0, "steps": 0, "damage": 0.0, "tags": 0, "comp": {}}
    last_ep_idx = 0
    # 续训时接着原编号:否则 ppo_000010.pt 之类会覆盖旧 checkpoint,日志也会回到 1
    it_base = int(init_state.get("update", 0) or 0) if init_state is not None else 0
    end_it = it_base + total
    global_step = int(init_state.get("global_step", it_base * n_steps)) if init_state is not None else 0
    best_eval_win_rate = 0.0
    best_eval_ci_low = 0.0
    best_eval_ci_high = 0.0
    best_eval_episodes = 0
    latest_eval_win_rate = 0.0
    latest_eval_ci_low = 0.0
    latest_eval_ci_high = 0.0
    latest_eval_episodes = 0
    if init_state is not None:
        best_eval_win_rate = float(init_state.get("best_eval_win_rate", init_state.get("best_win_rate", init_state.get("win_rate", 0.0))) or 0.0)
        latest_eval_win_rate = float(init_state.get("eval_win_rate", init_state.get("win_rate", 0.0)) or 0.0)
        latest_eval_ci_low = float(init_state.get("eval_ci_low", 0.0) or 0.0)
        latest_eval_ci_high = float(init_state.get("eval_ci_high", 0.0) or 0.0)
        latest_eval_episodes = int(init_state.get("eval_episodes", 0) or 0)

    # best.pt 是唯一权威基线: 无论冷启动还是续训, 只要文件存在就以它为准。
    # 之前这段缩进在 `if init_state is not None` 内部, 冷启动(init_state=None)
    # 且目录里已有 best.pt 时, best_eval_ci_low 未定义, 首次 eval 会 NameError;
    # 同时首轮 eval 还会把已有 best.pt 当成"没有 best"直接覆盖。
    best_ckpt_file = ckpt_dir / "best.pt"
    if best_ckpt_file.exists():
        try:
            b_data = torch.load(best_ckpt_file, map_location="cpu")
            b_wr = float(b_data.get("best_eval_win_rate", b_data.get("best_win_rate", b_data.get("win_rate", 0.0))) or 0.0)
            # 直接以 best.pt 覆盖续训断点里可能残留的陈旧 best_eval_win_rate。
            if b_wr > 0.0:
                best_eval_win_rate = b_wr
                best_eval_ci_low = float(b_data.get("eval_ci_low", 0.0) or 0.0)
                best_eval_ci_high = float(b_data.get("eval_ci_high", 0.0) or 0.0)
                best_eval_episodes = int(b_data.get("eval_episodes", 0) or 0)
            b_mode = b_data.get("eval_deterministic")
            if b_mode is not None:
                warn = baseline_mode_warning("argmax" if b_mode else "sample",
                                             eval_mode_name(eval_det))
                if warn and verbose:
                    print(f"[w] {warn}", flush=True)
        except Exception:
            pass
    if verbose and it_base:
        print(f"[i] 续训:update {it_base + 1} -> {end_it}", flush=True)

    explicit_schedule_total = tcfg.get("schedule_total_updates")
    ckpt_total = int(init_state.get("schedule_total_updates") or init_state.get("total_updates") or 0) if init_state is not None else 0
    cfg_total = int(tcfg.get("total_updates") or 0)

    if explicit_schedule_total is not None and int(explicit_schedule_total) > 0:
        schedule_total = int(explicit_schedule_total)
    elif ckpt_total > 0:
        schedule_total = ckpt_total
    elif cfg_total > 0:
        schedule_total = cfg_total
    else:
        schedule_total = max(1, end_it)

    if "lr_final_scale" in tcfg and tcfg["lr_final_scale"] is not None:
        lr_final_scale = float(tcfg["lr_final_scale"])
    elif init_state is not None and "lr_final_scale" in init_state and init_state["lr_final_scale"] is not None:
        lr_final_scale = float(init_state["lr_final_scale"])
    else:
        lr_final_scale = 0.1
    for it in range(it_base + 1, end_it + 1):
        if mode == "anneal":
            lr_now = lr_at(it, 0, schedule_total, lr_base, lr_final_scale)
        else:
            lr_now = lr_base
        for _g in opt.param_groups:
            _g["lr"] = lr_now
        scheduler.last_epoch = it
        scheduler._last_lr = [lr_now]
        stats.learning_rates.append(lr_now)
        if multi:
            t_iter = time.perf_counter()
            results, elapsed = pool.collect(
                net, n_steps, stop_on_done=episode_batch,
                max_steps=(n_steps * 3 if episode_batch else None),
                overrides=cur.reward_overrides() if cur is not None else None)
            merge_episodes(stats, results)
            good = [r for r in results if r is not None and r.get("ok")]
            wfps = [f"{r['fps']:.1f}" if (r is not None and r.get("ok")) else "-"
                    for r in results]
            if not good:
                print(f"[w] 第 {it} 次采样: 全部 worker 失败, 跳过本次更新", flush=True)
                continue
            # 各 worker 先各自 GAE(各自的 last_value/last_done)再拼接, 切序列在 worker 边界断开
            roll, adv, ret = merge_rollouts([r["roll"] for r in good],
                                            float(tcfg["gamma"]), float(tcfg["gae_lambda"]))
            infer_ms = float(np.mean([r["infer_ms"] for r in good]))
        else:
            try:
                roll, (obs, hidden, ep_state), infer_ms, elapsed = collect(
                    net, env, n_steps, hidden, stats, obs, ep_state=ep_state,
                    stop_on_done=episode_batch,
                    max_steps=(n_steps * 3 if episode_batch else None),
                    ref_net=ref_net, ref_hidden=ref_hidden)
                if ref_net is not None:
                    ref_hidden = ep_state.get("ref_hidden", ref_hidden)
            except Exception as exc:
                restarts += 1
                print(f"[w] 第 {it} 次采样失败({type(exc).__name__}: {exc}),第 {restarts} 次重启游戏",
                      flush=True)
                if restarts > 5:
                    raise
                env, (obs, _) = _recover_game(cfg, env, action_repeat, "采样失败后")
                ep_state = {"return": 0.0, "steps": 0, "damage": 0.0, "tags": 0, "comp": {}}
                hidden = net.init_hidden(1)
                if ref_net is not None:
                    ref_hidden = ref_net.init_hidden(1)
                continue
        new_eps = stats.episodes[last_ep_idx:]
        if cur is not None:
            for ep in new_eps:
                cur.observe(ep["result"])          # 标签 = 当前实际在用的难度
        for ep in new_eps:
            if verbose:
                _c = ep.get("counts") or {}
                print(f"    [ep {len(stats.episodes):3d}] {ep['result']:4s}({ep['kind']:4s}) "
                      f"steps={ep['steps']:4d} {ep['secs']:5.1f}s life={ep['lives']} "
                      f"state={ep['bstate']} ret={ep['return']:+.3f} dmg={ep['damage']:+.3f} "
                      f"atk[cmb{_c.get('combo', 0)},ocmb{_c.get('opp_combo', 0)},cnt{_c.get('counter', 0)},"
                      f"ctr{_c.get('countered', 0)},"
                      f"gp{_c.get('guard_punish', 0)},gb{_c.get('guard_block', 0)},"
                      f"gr{_c.get('guard_read', 0)}] "
                      f"sw[tag{ep['tags']},sw{_c.get('switches', 0)},rec{_c.get('tag_recover', 0)}] "
                      f"rw[{_comp_str(ep.get('comp'))}]", flush=True)
        last_ep_idx = len(stats.episodes)

        if not multi:
            adv, ret = compute_gae(roll, float(tcfg["gamma"]), float(tcfg["gae_lambda"]))
        logs = update(net, opt, roll, adv, ret, cfg, ref_net=ref_net, ref_kl_coef=kl_coef)
        global_step += len(roll.actions) if roll.actions is not None else n_steps

        # 多环境: fps = 全部 worker 本次总步数 / 同步采样墙钟;sps 再计入 update 耗时。
        # 停顿看门狗在各 worker 内独立执行(见 parallel._Worker.collect)。
        fps = (len(roll.actions) if multi else n_steps) / max(elapsed, 1e-9)
        sps = len(roll.actions) / max(time.perf_counter() - t_iter, 1e-9) if multi else fps
        # 采样停顿看门狗:游戏状态机卡死时 step() 不抛异常,只是每步吃满 frame_timeout,
        # fps 会从 ~25 掉到 ~2。连续两次即判定卡死 -> 丢弃这批(冻结状态上采到的轨迹
        # 没有学习价值)并重启游戏。阈值取 5:正常最慢也有 ~19(另有进程抢占时)。
        stall = stall + 1 if (fps < 5.0 and not multi) else 0
        if stall >= 2:
            print(f"[w] 采样停顿(连续 {stall} 次 fps<5,本次 {fps:.1f}),丢弃本批并重启游戏",
                  flush=True)
            env, (obs, _) = _recover_game(cfg, env, action_repeat, "采样停顿")
            ep_state = {"return": 0.0, "steps": 0, "damage": 0.0, "tags": 0, "comp": {}}
            hidden = net.init_hidden(1)
            if ref_net is not None:
                ref_hidden = ref_net.init_hidden(1)
            last_ep_idx = len(stats.episodes)
            stall = 0
            continue
        wr = stats.win_rate()
        ep_ret = float(np.mean([e["return"] for e in stats.episodes[-20:]])) if stats.episodes else 0.0
        dmg = float(np.mean([e["damage"] for e in stats.episodes[-20:]])) if stats.episodes else 0.0
        tags = float(np.mean([e["tags"] for e in stats.episodes[-20:]])) if stats.episodes else 0.0
        recent = stats.episodes[-20:]
        comps = _comp_means(recent)
        cnts: Dict[str, float] = {}
        for _e in recent:
            for _k, _v in (_e.get("counts") or {}).items():
                cnts[_k] = cnts.get(_k, 0.0) + float(_v)
        if recent:
            cnts = {k: v / len(recent) for k, v in cnts.items()}
        wins_recent = [e for e in recent if e.get("result") == "win"]
        ko_rate = (sum(1 for e in wins_recent if e.get("kind") == "ko") / len(wins_recent)
                   if wins_recent else 0.0)
        mean_secs = float(np.mean([e.get("secs", 0.0) for e in recent])) if recent else 0.0

        if verbose:
            _ref_s = f"ref_kl={logs['ref_kl']:.4f} " if kl_coef > 0 else ""
            print(f"[{it:3d}/{end_it}] ret={ep_ret:+.3f} win={wr * 100:5.1f}% "
                  f"eps={len(stats.episodes):3d} ko={ko_rate * 100:5.1f}% "
                  f"t={mean_secs:5.1f}s dmg={dmg:+.3f} tag={tags:.1f} "
                  f"cmb={cnts.get('combo', 0.0):.1f} sw={cnts.get('switches', 0.0):.1f} "
                  f"pi={logs['policy_loss']:+.4f} v={logs['value_loss']:.4f} "
                  f"ent={logs['entropy']:.3f} kl={logs['approx_kl']:.4f} {_ref_s}"
                  f"fps={fps:.1f} infer={infer_ms:.2f}ms lr={lr_now:.2e}"
                  + (f" envs={n_envs} wfps={'/'.join(wfps)} sps={sps:.1f}" if multi else ""))
        if writer is not None:
            writer.add_scalar("rollout/return", ep_ret, it)
            writer.add_scalar("rollout/win_rate", wr, it)
            writer.add_scalar("rollout/episodes", len(stats.episodes), it)
            writer.add_scalar("rollout/damage", dmg, it)
            writer.add_scalar("rollout/tag_per_ep", tags, it)
            writer.add_scalar("rollout/ko_rate", ko_rate, it)
            writer.add_scalar("rollout/mean_secs", mean_secs, it)
            for _k, _v in comps.items():
                writer.add_scalar(f"reward/{_k}", _v, it)
            for _k, _v in cnts.items():
                writer.add_scalar(f"combat/{_k}", _v, it)
            writer.add_scalar("loss/policy", logs["policy_loss"], it)
            writer.add_scalar("loss/value", logs["value_loss"], it)
            writer.add_scalar("loss/entropy", logs["entropy"], it)
            writer.add_scalar("train/approx_kl", logs["approx_kl"], it)
            writer.add_scalar("train/clip_frac", logs["clip_frac"], it)
            if kl_coef > 0:
                writer.add_scalar("train/ref_kl", logs["ref_kl"], it)
            writer.add_scalar("perf/fps", fps, it)
            if multi:
                writer.add_scalar("perf/sps", sps, it)
            writer.add_scalar("perf/infer_ms", infer_ms, it)
            writer.add_scalar("train/lr", lr_now, it)
            writer.flush()

        if cur is not None:
            st = cur.stats()
            if cur.should_promote() and cur.promote():
                level = _target_level(cur, cmode)
                cur.mark_applied(level)
                print(f"[课程] 晋级 -> {cur.stage.name} "
                      f"(上一阶段 {st['stage_decided']} 局 "
                      f"{st['stage_win_rate']:.1%}) 目标 COM{level + 1} "
                      f"模式={cmode}", flush=True)
                if writer is not None:
                    writer.add_scalar("curriculum/stage", cur.stage_index, it)
                if level != int(cfg["env"].get("difficulty", level)):
                    if multi:
                        pool.apply_difficulty(level)
                    else:
                        env, new_obs = _apply_difficulty(cfg, env, level, cmode, dswitch,
                                                         action_repeat)
                        if new_obs is not None:
                            obs = new_obs
            _anneal(env, cur, base_entropy, tcfg)

        # 确定性评估回路 (Ticket 01)
        best_target_file = ckpt_dir / "best.pt"
        should_eval = (
            eval_interval > 0 and
            (it % eval_interval == 0 or (it == end_it and not best_target_file.exists()))
        )
        if should_eval:
            target_eval_env = eval_env if eval_env is not None else env
            eval_com = getattr(cur, "current_level", getattr(target_eval_env, "difficulty", int(cfg.get("env", {}).get("difficulty", 0))))
            eval_mode_str = eval_mode_name(eval_det)
            if verbose:
                print(f"[eval @ update {it:3d}] 开始评估 (episodes={eval_episodes}, COM{eval_com + 1}, {eval_mode_str})...", flush=True)
            eval_summary = evaluator.run(net, env=target_eval_env, episodes=eval_episodes, com_level=eval_com, deterministic=eval_det)
            latest_eval_win_rate = float(eval_summary["win_rate"])
            latest_eval_ci_low = float(eval_summary["win_rate_ci"][0])
            latest_eval_ci_high = float(eval_summary["win_rate_ci"][1])
            latest_eval_episodes = int(eval_summary["matches"])
            latest_eval_hp_diff = float(eval_summary["mean_hp_diff"])

            # 只用点估计会追噪声: n=30~50 的 sample 评估 CI 宽达 ±12~17pp,
            # 86.7% 这类上尾样本会被误固化为 best。要求"点估计更高 且 CI 下限也更高"
            # 才算真正进步, 避免 best.pt 被抽样波动反复覆盖。
            is_new_best = (not best_target_file.exists()) or (
                eval_summary["decided"] > 0
                and is_significant_best(
                    latest_eval_win_rate, latest_eval_ci_low,
                    best_eval_win_rate, best_eval_ci_low,
                )
            )
            if verbose:
                tag = " [NEW BEST]" if is_new_best else ""
                print(f"[eval @ update {it:3d}] matches={latest_eval_episodes} decided={eval_summary['decided']} "
                      f"win_rate={latest_eval_win_rate * 100:5.1f}% "
                      f"(95% CI {latest_eval_ci_low * 100:5.1f}% ~ {latest_eval_ci_high * 100:5.1f}%) "
                      f"hp_diff={latest_eval_hp_diff:+6.0f}{tag}", flush=True)

            if writer is not None:
                writer.add_scalar("eval/win_rate", latest_eval_win_rate, it)
                writer.add_scalar("eval/ci_low", latest_eval_ci_low, it)
                writer.add_scalar("eval/ci_high", latest_eval_ci_high, it)
                writer.add_scalar("eval/mean_hp_diff", latest_eval_hp_diff, it)
                writer.add_scalar("eval/matches", latest_eval_episodes, it)
            # Tag 尝试分箱落盘(每次门控一次):按 motion 区间 × ok 分箱聚合,写 JSON。
            # stats.episodes 只在内存,进程退出即丢;门控节奏天然对齐判读点。
            try:
                import json as _json
                _bins: Dict[str, Dict[str, int]] = {}
                _delta_hist: Dict[int, int] = {}
                _motion_hist: Dict[int, Dict[str, int]] = {}
                for _ep in stats.episodes:
                    for _p in (_ep.get("tag_probe") or []):
                        _mo = int(_p.get("motion", 0) or 0)
                        # 与 bridge/shm.py is_attack() 同口径(>=1000 全是攻击):
                        # 0=非攻击按 idle/受击细分,1=普攻(1000-1999),2=必杀(2000-2999),
                        # 3=特殊(3000-3909),4/5=超必杀(3910-4999)。原始 motion 值保留
                        # 在 motion_hist 供事后重分箱,不预设区间结论。
                        if _mo == 0:
                            _k = "idle(0)"
                        elif _mo in (213,) or 200 <= _mo <= 299:
                            _k = "stun/hit(200s,213)"
                        elif 1000 <= _mo <= 1999:
                            _k = "atk1-normal(1000-1999)"
                        elif 2000 <= _mo <= 2999:
                            _k = "atk2-special(2000-2999)"
                        elif 3000 <= _mo <= 3909:
                            _k = "atk3-super(3000-3909)"
                        elif 3910 <= _mo <= 4999:
                            _k = "atk456-ultra(3910-4999)"
                        else:
                            _k = f"other({_mo})"
                        _b = _bins.setdefault(_k, {"n": 0, "ok": 0})
                        _b["n"] += 1
                        _mh = _motion_hist.setdefault(_mo, {"n": 0, "ok": 0})
                        _mh["n"] += 1
                        if int(_p.get("ok", 0) or 0):
                            _b["ok"] += 1
                            _mh["ok"] += 1
                            _d = int(_p.get("delta", 0) or 0)
                            _delta_hist[_d] = _delta_hist.get(_d, 0) + 1
                with open(ckpt_dir / f"tag_probe_{it:06d}.json", "w", encoding="utf-8") as _f:
                    _json.dump({"update": it, "episodes": len(stats.episodes), "bins": _bins,
                                "delta": _delta_hist, "motion_hist": _motion_hist}, _f)
            except Exception as _e:
                print(f"[w] tag-probe 落盘失败: {_e}", flush=True)

            if is_new_best:
                best_eval_win_rate = latest_eval_win_rate
                best_eval_ci_low = latest_eval_ci_low
                best_eval_ci_high = latest_eval_ci_high
                best_eval_episodes = latest_eval_episodes
                best_meta = {
                    "update": it,
                    "global_step": global_step,
                    "total_updates": schedule_total,
                    "schedule_total_updates": schedule_total,
                    "lr": lr_now,
                    "lr_base": lr_base,
                    "lr_mode": mode,
                    "lr_final_scale": lr_final_scale,
                    "best_eval_win_rate": best_eval_win_rate,
                    "best_win_rate": best_eval_win_rate,
                    "eval_win_rate": best_eval_win_rate,
                    "eval_episodes": best_eval_episodes,
                    "eval_ci_low": best_eval_ci_low,
                    "eval_ci_high": best_eval_ci_high,
                    "eval_deterministic": eval_det,
                    "model_state_dict": net.state_dict(),
                    "optimizer_state_dict": opt.state_dict(),
                    "lr_scheduler_state_dict": scheduler.state_dict(),
                    "com_level": eval_com,
                    "ref_kl_coef": kl_coef,
                    "ref_checkpoint": str(ref_path) if ref_path is not None else None,
                    "freeze_trunk": freeze_trunk,
                    # Backward compatibility aliases
                    "model": net.state_dict(),
                    "optimizer": opt.state_dict(),
                    "win_rate": best_eval_win_rate,
                    "stats": stats.__dict__,
                    "curriculum": cur.to_dict() if cur is not None else None,
                }
                torch.save(best_meta, ckpt_dir / "best.pt")
                if cur is not None:
                    cur.update_best(str(ckpt_dir / "best.pt"), best_eval_win_rate)

            # 若评估复用了训练环境,需重置环境以恢复干净的训练状态
            if eval_env is None and not multi:
                obs, _info0 = env.reset()
                hidden = net.init_hidden(1)
                if ref_net is not None:
                    ref_hidden = ref_net.init_hidden(1)
                ep_state = {"return": 0.0, "steps": 0, "damage": 0.0, "tags": 0, "comp": {}}

        save_every = int(cfg.get("checkpoint", {}).get("save_every", 10))
        if it % save_every == 0 or it == end_it:
            current_com = getattr(cur, "current_level", getattr(env, "difficulty", int(cfg.get("env", {}).get("difficulty", 0))))
            periodic_meta = {
                "update": it,
                "global_step": global_step,
                "total_updates": schedule_total,
                "schedule_total_updates": schedule_total,
                "lr": lr_now,
                "lr_base": lr_base,
                "lr_mode": mode,
                "lr_final_scale": lr_final_scale,
                "best_eval_win_rate": best_eval_win_rate,
                "best_win_rate": best_eval_win_rate,
                "eval_win_rate": latest_eval_win_rate,
                "eval_episodes": latest_eval_episodes,
                "eval_ci_low": latest_eval_ci_low,
                "eval_ci_high": latest_eval_ci_high,
                "eval_deterministic": eval_det,
                "model_state_dict": net.state_dict(),
                "optimizer_state_dict": opt.state_dict(),
                "lr_scheduler_state_dict": scheduler.state_dict(),
                "com_level": current_com,
                "ref_kl_coef": kl_coef,
                "ref_checkpoint": str(ref_path) if ref_path is not None else None,
                "freeze_trunk": freeze_trunk,
                # Backward compatibility aliases
                "model": net.state_dict(),
                "optimizer": opt.state_dict(),
                "win_rate": latest_eval_win_rate,
                "stats": stats.__dict__,
                "curriculum": cur.to_dict() if cur is not None else None,
            }
            torch.save(periodic_meta, ckpt_dir / f"ppo_{it:06d}.pt")

    if writer is not None:
        writer.close()
    if not multi:
        env.close()
    return stats


def evaluate(cfg: dict, episodes: int = 10, env=None, init_state: Optional[dict] = None,
             action_repeat: Optional[int] = None, deterministic: bool = True,
             num_envs: int = 1, provider=None) -> int:
    """确定性策略评估:每局结束打印双方血量 + 游戏给出的 winner。

    `num_envs>1` 时把局数分给多个 worker 并行打(env 由 `provider` 构造,缺省实机实例)。
    """
    if int(num_envs) > 1:
        if env is not None:
            raise ValueError("num_envs>1 时 env 由各 worker 经 provider 构造,不能再传 env")
        from train.parallel import GameProvider, evaluate_parallel
        com_level = int((cfg.get("env") or {}).get("difficulty", 0))
        sd = (init_state.get("model_state_dict") or init_state.get("model")) if init_state else \
            PolicyNet(cfg.get("network") or {}).state_dict()

        def _show(i: int, rec: MatchRecord) -> None:
            _c = rec.counts or {}
            print(f"[eval {i + 1:2d}/{episodes}] {rec.result:5s} by={rec.kind:4s} "
                  f"life={rec.lives} steps={rec.frames} {rec.secs:5.1f}s "
                  f"hp={rec.hp_diff:+d} ret={rec.ret:+.3f} state={rec.state} "
                  f"cmb={_c.get('combo', 0)} ocmb={_c.get('opp_combo', 0)} "
                  f"cnt={_c.get('counter', 0)} ctr={_c.get('countered', 0)}", flush=True)

        records = evaluate_parallel(cfg, sd, episodes, int(num_envs),
                                    provider or GameProvider(cfg, action_repeat),
                                    com_level=com_level, deterministic=deterministic,
                                    on_record=_show)
        print(json.dumps(summarize(records, com_level=com_level), ensure_ascii=False))
        return 0
    net = PolicyNet(cfg.get("network") or {})
    if init_state is not None:
        sd = init_state.get("model_state_dict") or init_state.get("model")
        load_weights(net, sd, verbose=True)
    net.eval()
    env = env if env is not None else build_env(cfg, action_repeat)
    records = []
    restarts = 0
    try:
        for ep in range(episodes):
            try:
                obs, info = env.reset()
            except Exception as exc:
                restarts += 1
                print(f"[w] 第 {ep + 1} 局 reset 失败({exc}),重启游戏({restarts})", flush=True)
                if restarts > 5:
                    raise
                try:
                    env.close()
                except Exception:
                    pass
                restart_game()
                env = build_env(cfg, action_repeat)
                env, (obs, info) = reset_with_restart(cfg, env, action_repeat, "评估")
            rec = play_match(net, env, obs, info, deterministic=deterministic)
            records.append(rec)
            print(f"[eval {ep + 1:2d}/{episodes}] {rec.result:5s} by={rec.kind:4s} "
                  f"life={rec.lives} steps={rec.frames} {rec.secs:5.1f}s "
                  f"hp={rec.hp_diff:+d} ret={rec.ret:+.3f} state={rec.state}", flush=True)
    finally:
        try:
            env.close()
        except Exception:
            pass
    summary = summarize(records)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--updates", type=int, default=None)
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--offline", action="store_true", help="用合成环境冒烟,不连游戏")
    ap.add_argument("--resume", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--action-repeat", type=int, default=None,
                    help="每步推进的游戏帧数(1=60Hz, 2=30Hz, 4=15Hz)")
    ap.add_argument("--eval", type=int, default=0,
                    help="只评估:跑 N 局策略,打印每局胜负")
    ap.add_argument("--eval-sample", action="store_true",
                    help="评估时按策略分布采样(与训练胜率同口径);默认取 argmax")
    ap.add_argument("--eval-interval", type=int, default=None,
                    help="每 M 次 PPO 更新执行一次确定性评估(默认 15, 0=禁用)")
    ap.add_argument("--eval-episodes", type=int, default=None,
                    help="每次确定性评估的对局数(默认 30 局)")
    ap.add_argument("--episode-batch", action="store_true",
                    help="每次更新只采到本局结束(可变长,上限 3*n_steps),GAE 末端精确")
    ap.add_argument("--lr", type=float, default=None,
                    help="学习率覆盖值(如 5e-5)")
    ap.add_argument("--lr-mode", choices=["anneal", "constant"], default=None,
                    help="学习率调度模式: anneal(线性退火) 或 constant(恒定)")
    ap.add_argument("--no-lr-decay", action="store_true",
                    help="禁用学习率衰减,全程维持常数学习率(等同于 --lr-mode constant)")
    ap.add_argument("--schedule-total-updates", type=int, default=None,
                    help="显式指定学习率调度总步数(覆盖 checkpoint 与配置中的 schedule_total_updates)")
    ap.add_argument("--cold-start", action="store_true",
                    help="强制全新冷启动训练 (忽略 resume, update=0, 正交初始化权重与重置优化器)")
    ap.add_argument("--ref-kl-coef", type=float, default=None,
                    help="参考策略 KL 惩罚系数(>0 时冻结 best.pt 作参考, 防微调漂移)")
    ap.add_argument("--ref-checkpoint", default=None,
                    help="参考策略 checkpoint(默认 <checkpoint.dir>/best.pt)")
    ap.add_argument("--ent-coef", type=float, default=None,
                    help="覆盖配置中的 entropy_coef(如 0.001)")
    ap.add_argument("--freeze-trunk", action="store_true",
                    help="冻结 LSTM/trunk/critic, 仅训练 actor head(防闭环动力学漂移)")
    ap.add_argument("--num-envs", type=int, default=None,
                    help="并行环境数(默认读 train.num_envs, 1=单环境);>1 时每个 env 一个 worker 进程,"
                         "--steps 表示每个 env 的步数")
    a = ap.parse_args()

    cfg = load_config(a.config)
    if a.seed is not None:
        cfg["train"]["seed"] = a.seed
    if a.episode_batch:
        cfg["train"]["episode_batch"] = True
    if a.eval_interval is not None:
        cfg.setdefault("train", {})["eval_interval"] = a.eval_interval
    if a.eval_episodes is not None:
        cfg.setdefault("train", {})["eval_episodes"] = a.eval_episodes
    if a.lr is not None:
        cfg.setdefault("train", {})["lr"] = a.lr
    if a.schedule_total_updates is not None:
        cfg.setdefault("train", {})["schedule_total_updates"] = a.schedule_total_updates
    if a.no_lr_decay:
        cfg.setdefault("train", {})["lr_mode"] = "constant"
        cfg.setdefault("train", {})["lr_anneal"] = False
    elif a.lr_mode is not None:
        cfg.setdefault("train", {})["lr_mode"] = a.lr_mode
        cfg.setdefault("train", {})["lr_anneal"] = (a.lr_mode == "anneal")
    ar_eff = int(a.action_repeat or cfg.get("env", {}).get("action_repeat", 2))
    if a.num_envs is not None:
        cfg.setdefault("train", {})["num_envs"] = a.num_envs
    n_envs = int(cfg.get("train", {}).get("num_envs", 1) or 1)
    env, provider = None, None
    if a.offline and n_envs > 1:
        from train.parallel import OfflineProvider
        provider = OfflineProvider(action_repeat=ar_eff, seed=int(cfg["train"].get("seed", 0)))
    elif a.offline:
        env = _OfflineEnv(action_repeat=ar_eff)

    state = None
    if a.resume and not a.cold_start:
        state = torch.load(a.resume, map_location="cpu")
        print(f"[i] 载入权重 {a.resume}(update={state.get('update')})")
    elif a.cold_start:
        print("[i] 启动全新冷启动训练 (Cold Start, update=0)")

    if a.eval:
        return evaluate(cfg, episodes=a.eval, env=env, init_state=state,
                        action_repeat=a.action_repeat,
                        deterministic=not a.eval_sample,
                        num_envs=n_envs, provider=provider)

    stats = train(cfg, updates=a.updates, steps=a.steps, env=env,
                  verbose=not a.quiet, init_state=state,
                  action_repeat=a.action_repeat,
                  eval_interval=a.eval_interval,
                  eval_episodes=a.eval_episodes,
                  lr=a.lr,
                  lr_mode=a.lr_mode,
                  no_lr_decay=a.no_lr_decay,
                  schedule_total_updates=a.schedule_total_updates,
                  cold_start=a.cold_start,
                  ref_kl_coef=a.ref_kl_coef,
                  ref_checkpoint=a.ref_checkpoint,
                  ent_coef=a.ent_coef,
                  freeze_trunk=a.freeze_trunk,
                  num_envs=n_envs, provider=provider)
    print(json.dumps({"episodes": len(stats.episodes), "wins": stats.wins,
                      "losses": stats.losses, "draws": stats.draws,
                      "win_rate": stats.win_rate()}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# 离线合成环境(只用于无游戏冒烟测试)
# ---------------------------------------------------------------------------
class _OfflineEnv:
    """生成与 Th155Env 相同结构的随机观测,用于验证训练循环。"""

    team: int = 0
    action_repeat: int = 2

    def __init__(self, max_episode_steps: int = 60, team: int = 0, action_repeat: int = 2,
                 seed: int = 0):
        self.team = int(team)
        self.action_repeat = int(action_repeat)
        self.max_episode_steps = max_episode_steps
        self._t = 0
        self._rng = np.random.default_rng(seed)

    def _obs(self):
        return {
            "self": self._rng.normal(size=obs_builder.SELF_DIM).astype(np.float32) * 0.3,
            "opp": self._rng.normal(size=obs_builder.OPP_DIM).astype(np.float32) * 0.3,
            "global": self._rng.normal(size=obs_builder.GLOBAL_DIM).astype(np.float32) * 0.3,
            "proj": self._rng.normal(size=(obs_builder.MAX_PROJ, obs_builder.PROJ_DIMS)).astype(np.float32),
            "proj_mask": (self._rng.random(obs_builder.MAX_PROJ) > 0.5).astype(np.float32),
            "motion_self": int(self._rng.integers(0, 100)),
            "motion_opp": int(self._rng.integers(0, 100)),
            "action_mask": np.ones(N_ACTIONS, dtype=bool),
        }

    def reset(self, **kw):
        self._t = 0
        return self._obs(), {"action_mask": np.ones(N_ACTIONS, dtype=bool), "battle_state": 8,
                             "state_snapshot": {"teams": [{"life": 5000}, {"life": 5000}]}}

    def step(self, action):
        self._t += 1
        reward = float(self._rng.normal() * 0.1)
        done = self._t >= self.max_episode_steps
        info = {"action_mask": np.ones(N_ACTIONS, dtype=bool), "battle_state": 8,
                "reward_components": {"hp": reward},
                "state_snapshot": {"teams": [{"life": 5000}, {"life": 0 if done else 5000}]}}
        return self._obs(), reward, done, False, info

    def close(self):
        pass


if __name__ == "__main__":
    raise SystemExit(main())
