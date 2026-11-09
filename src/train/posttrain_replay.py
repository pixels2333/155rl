"""离线 Replay 后训练:SFT / AWBC + 双向 KL 锚定 + Critic 锚定。

输入是由回放提炼的轨迹 ``.npz``(30Hz 决策步,`action_repeat = 2`),每个文件
对应一个 replay perspective,键见 :data:`TRAJ_KEYS`。

流程: ``prepare_rollouts``(因果前向记 hidden + GAE) -> ``posttrain_epoch``
(多轮 minibatch 更新) -> ``evaluate_dataset``(纯评估) -> ``run_posttrain``
(整套循环 + ``best_val.pt`` / ``last.pt`` 落盘)。CLI 见 ``main()``。
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import time

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agents.ppo_agent import N_ACTIONS, PolicyNet, load_weights  # noqa: E402
from env.action_space import GUARD_ACTIONS  # noqa: E402
from train.train_ppo import _seq_batches  # noqa: E402

OBS_KEYS = ("self", "opp", "global", "proj", "proj_mask", "motion_self",
            "motion_opp", "action_mask")
TRAJ_KEYS = OBS_KEYS + ("actions", "bc_valid", "rewards", "dones")

ACTION_GROUPS = ("move", "guard", "attack_ABC", "occult", "dash_D", "spell", "tag")


def action_group(a: int) -> str:
    """把 0..55 动作分到诊断组(移动 / 格挡 / 攻击 / Occult / D / 符卡 / 换人)。"""
    if a == 54:
        return "spell"
    if a == 55:
        return "tag"
    d, b = divmod(int(a), 6)
    if b == 0:
        return "guard" if d * 6 in GUARD_ACTIONS else "move"
    if b == 5:
        return "occult"
    if b == 4:
        return "dash_D"
    return "attack_ABC"


# 长按 move/guard 的持续步(人类真正的"决策"只在切换步与战术动作上)。
HOLD_GROUPS = ("move", "guard")


def compute_step_weights(actions: np.ndarray, dones: np.ndarray,
                         repeat_move_weight: float = 1.0,
                         attack_weight: float = 1.0,
                         move_weight: float = 1.0) -> np.ndarray:
    """逐步 BC 权重:``move`` 切换步(含回合首步)乘 ``move_weight``,``move``
    长按续步乘 ``move_weight * repeat_move_weight``(0 即零模仿走路);非 move
    回合首步恒为 1(决策步);同动作长按的 guard 续步乘 ``repeat_move_weight``;
    ``attack_ABC`` 步乘 ``attack_weight``;剩下(切换步/dash/occult/spell/tag)
    为 1。默认三系数皆 1 即行为不变。"""
    acts = np.asarray(actions)
    dn = np.asarray(dones).astype(bool)
    w = np.ones(len(acts), dtype=np.float32)
    for i in range(len(acts)):
        g = action_group(int(acts[i]))
        if g == "move":
            w[i] = move_weight
            if i > 0 and not dn[i - 1] and acts[i] == acts[i - 1]:
                w[i] = move_weight * repeat_move_weight
            continue
        if i == 0 or dn[i - 1]:
            continue
        if acts[i] == acts[i - 1] and g in HOLD_GROUPS:
            w[i] = repeat_move_weight
        elif g == "attack_ABC":
            w[i] = attack_weight
    return w


def validate_trajectory(traj: Dict[str, np.ndarray], source: str = "") -> int:
    """校验轨迹字典的键/长度/形状一致性;返回步数 T。"""
    missing = [k for k in TRAJ_KEYS if k not in traj]
    if missing:
        raise ValueError(f"{source}: trajectory missing keys {missing}")
    lens = {k: np.shape(traj[k])[0] for k in TRAJ_KEYS}
    if len(set(lens.values())) != 1:
        raise ValueError(f"{source}: inconsistent lengths {lens}")
    t = next(iter(lens.values()))
    if np.shape(traj["action_mask"])[1:] != (N_ACTIONS,):
        raise ValueError(f"{source}: action_mask shape {np.shape(traj['action_mask'])}")
    return int(t)


def save_trajectory(path: str | Path, traj: Dict[str, np.ndarray]) -> None:
    """按 :data:`TRAJ_KEYS` 契约写单个轨迹 ``.npz``(额外键如视角元数据原样保留)。"""
    validate_trajectory(traj, source=str(path))
    np.savez_compressed(str(path), **{k: np.asarray(v) for k, v in traj.items()
                                      if not k.startswith("_")})


def load_trajectory(path: str | Path) -> Dict[str, np.ndarray]:
    """读单个轨迹 ``.npz`` 并校验;返回 ``{key: np.ndarray}``(含视角元数据键)。"""
    with np.load(str(path), allow_pickle=False) as z:
        traj = {k: z[k] for k in z.files}
    validate_trajectory(traj, source=str(path))
    return traj


def load_npz_dir(path: str | Path) -> List[Dict[str, np.ndarray]]:
    """按文件名排序读入目录下全部 ``.npz`` 轨迹;附带 ``_source``/``_name``。"""
    out = []
    for p in sorted(Path(path).glob("*.npz")):
        traj = load_trajectory(p)
        traj["_source"] = str(p)
        traj["_name"] = p.name
        out.append(traj)
    return out


# 视角身份键(提炼器写入):精确对应 replay_splits.json 的 (file, team) 条目。
PERSPECTIVE_KEYS = ("source_file", "team")


def read_perspective_id(path: str | Path) -> Tuple[Optional[str], Optional[int], str]:
    """读轨迹 ``.npz`` 的视角身份,返回 ``(rep_file|None, team|None, stem)``。

    首选文件内 ``source_file``/``team`` 键(精确,含日期目录);缺失时退化解析
    文件名 ``<rep_stem>_t<team>.npz``(日期不可恢复,``rep_file`` 为 None)。
    """
    p = Path(path)
    with np.load(str(p), allow_pickle=False) as z:
        if "source_file" in z.files and "team" in z.files:
            file = str(z["source_file"].reshape(-1)[0])
            team = int(z["team"].reshape(-1)[0])
            return file, team, p.stem
    stem = p.stem
    team = None
    if len(stem) > 3 and stem[-3:-1] == "_t" and stem[-1] in "01":
        team = int(stem[-1])
        stem = stem[:-3]
    return None, team, stem


def gae(rewards: np.ndarray, values: np.ndarray, dones: np.ndarray,
        gamma: float = 0.995, lam: float = 0.95,
        last_value: float = 0.0, last_done: bool = True) -> Tuple[np.ndarray, np.ndarray]:
    """GAE 优势与 lambda-return(与 ``train_ppo.compute_gae`` 同口径)。

    ``dones[t]`` 为真表示回合在 t 结束;轨迹截断处由 ``last_value/last_done``
    描述(离线回放恒为 ``0.0/True``,即回合自然结束)。
    """
    rewards = np.asarray(rewards, dtype=np.float32)
    values = np.asarray(values, dtype=np.float32)
    dones = np.asarray(dones).astype(bool)
    n = len(rewards)
    adv = np.zeros(n, dtype=np.float32)
    last = 0.0
    for t in reversed(range(n)):
        nonterminal = 0.0 if (bool(dones[t]) or (t == n - 1 and last_done)) else 1.0
        next_value = last_value if t == n - 1 else float(values[t + 1])
        delta = float(rewards[t]) + gamma * next_value * nonterminal - float(values[t])
        last = delta + gamma * lam * nonterminal * last
        adv[t] = last
    return adv, (adv + values).astype(np.float32)


def _obs_tensors(traj: Dict[str, np.ndarray], device: str = "cpu") -> Dict[str, torch.Tensor]:
    return {
        "self": torch.as_tensor(traj["self"], dtype=torch.float32).to(device),
        "opp": torch.as_tensor(traj["opp"], dtype=torch.float32).to(device),
        "global": torch.as_tensor(traj["global"], dtype=torch.float32).to(device),
        "proj": torch.as_tensor(traj["proj"], dtype=torch.float32).to(device),
        "proj_mask": torch.as_tensor(traj["proj_mask"], dtype=torch.float32).to(device),
        "motion_self": torch.as_tensor(traj["motion_self"], dtype=torch.long).to(device),
        "motion_opp": torch.as_tensor(traj["motion_opp"], dtype=torch.long).to(device),
        "action_mask": torch.as_tensor(traj["action_mask"], dtype=torch.float32).to(device),
    }


ENCODE_CHUNK = 4096  # batched net.encode chunk (feed-forward, no recurrence)
LSTM_GROUP_POSITIONS = 200_000  # padded (B * Lmax) cap per stepping group


def _round_segments(dones: np.ndarray, t: int) -> List[Tuple[int, int]]:
    """回合段 [s, e):从 0 起,每次 dones 之后断开(与逐步前向的 reset 语义一致)。"""
    cuts = [0] + [int(i) + 1 for i in np.nonzero(dones)[0] if int(i) + 1 < t]
    bounds = cuts + [t]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


@torch.no_grad()
def refresh_rollouts(rollouts: List[dict], net: PolicyNet, ref_net: PolicyNet,
                     gamma: float = 0.995, lam: float = 0.95,
                     device: str = "cpu") -> List[dict]:
    """用当前 ``net``/``ref_net`` 刷新 hidden 与 GAE(每 epoch 可重算,消灭 stale)。

    因果逐步前向:``h_in[t]`` 记录第 t 步**之前**的 hidden;``dones[t-1]`` 为真
    则第 t 步从零 hidden 重起(与在线 PPO 的 episode 边界语义一致)。

    向量化(数学与旧逐步循环完全一致):``net.encode`` 是纯前馈,整条轨迹按
    chunk 一次性批量编码;回合段之间 hidden 不连续,把各段 pad 后组成 batch,
    用一次 LSTM 调用推进所有段的同一步(batch 维各自独立,短段 pad 位从不被读)。
    ``ref_values`` 按段一次 seq 前向取 critic 输出。
    """
    if not rollouts:
        return rollouts
    layers, hidden_dim = net.lstm_layers, net.lstm_hidden
    # 1. 整轨迹批量编码 (T, F)。
    enc: List[Tuple[torch.Tensor, torch.Tensor]] = []
    feat = 0
    for roll in rollouts:
        t = roll["T"]
        obs = _obs_tensors(roll["obs"], device)
        pair = []
        for model in (net, ref_net):
            parts = [model.encode({k: v[s:s + ENCODE_CHUNK] for k, v in obs.items()})
                     for s in range(0, t, ENCODE_CHUNK)]
            pair.append(torch.cat(parts, dim=0).cpu())
        feat = pair[0].shape[1]
        enc.append((pair[0], pair[1]))
    # 2. 跨 rollout 收集回合段并贪心分组 (B * Lmax 上限)。
    segs: List[Tuple[int, int, int]] = []  # (roll_idx, s, e)
    for r, roll in enumerate(rollouts):
        for s, e in _round_segments(np.asarray(roll["dones"]).astype(bool), roll["T"]):
            segs.append((r, s, e))
    segs.sort(key=lambda x: -(x[2] - x[1]))
    groups: List[List[Tuple[int, int, int]]] = []
    for seg in segs:
        placed = False
        for g in groups:
            lmax = max(max(e - s for _, s, e in g), seg[2] - seg[1])
            if (len(g) + 1) * lmax <= LSTM_GROUP_POSITIONS:
                g.append(seg)
                placed = True
                break
        if not placed:
            groups.append([seg])
    # 3. 输出缓冲。
    h_bufs, ref_bufs, ref_values = [], [], []
    for roll in rollouts:
        t = roll["T"]
        h_bufs.append(np.zeros((2, t, layers, hidden_dim), dtype=np.float32))
        ref_bufs.append(np.zeros((2, t, layers, hidden_dim), dtype=np.float32))
        ref_values.append(np.zeros(t, dtype=np.float32))
    # 4. 分组并行步进 (net hidden + ref hidden)。
    for g in groups:
        b = len(g)
        lens = [e - s for _, s, e in g]
        lmax = max(lens)
        x_pad = torch.zeros(b, lmax, feat, device=device)
        xr_pad = torch.zeros(b, lmax, feat, device=device)
        for j, (r, s, e) in enumerate(g):
            x_pad[j, :e - s] = enc[r][0][s:e].to(device)
            xr_pad[j, :e - s] = enc[r][1][s:e].to(device)
        h = (torch.zeros(layers, b, hidden_dim, device=device),
             torch.zeros(layers, b, hidden_dim, device=device))
        rh = (torch.zeros(layers, b, hidden_dim, device=device),
              torch.zeros(layers, b, hidden_dim, device=device))
        active = np.array(lens)[:, None] > np.arange(lmax)[None, :]
        for k in range(lmax):
            m = active[:, k]
            if m.any():
                hm = torch.as_tensor(m, device=device)
                for hc in (0, 1):
                    h_bufs_j = h[hc][:, hm, :].permute(1, 0, 2).cpu().numpy()
                    rh_bufs_j = rh[hc][:, hm, :].permute(1, 0, 2).cpu().numpy()
                    jj = 0
                    for j, (r, s, e) in enumerate(g):
                        if k < lens[j]:
                            h_bufs[r][hc, s + k] = h_bufs_j[jj]
                            ref_bufs[r][hc, s + k] = rh_bufs_j[jj]
                            jj += 1
            _, h = net.lstm(x_pad[:, k:k + 1, :], h)
            _, rh = ref_net.lstm(xr_pad[:, k:k + 1, :], rh)
    # 5. 按段 seq 前向取 ref critic 值。
    for r, s, e in segs:
        out, _ = ref_net.lstm(enc[r][1][s:e].unsqueeze(0).to(device), None)
        ref_values[r][s:e] = ref_net.critic(out).squeeze(-1)[0].cpu().numpy()
    # 6. GAE + 落盘(与 train_ppo.update 同口径)。
    for r, roll in enumerate(rollouts):
        dones = np.asarray(roll["dones"]).astype(bool)
        adv, ret = gae(roll["rewards"], ref_values[r], dones, gamma, lam, 0.0, True)
        roll["h_in"] = (h_bufs[r][0].copy(), h_bufs[r][1].copy())
        roll["ref_h_in"] = (ref_bufs[r][0].copy(), ref_bufs[r][1].copy())
        roll["ref_values"] = torch.as_tensor(ref_values[r])
        roll["adv"] = torch.as_tensor(adv)
        roll["ret"] = torch.as_tensor(ret)
    return rollouts


def prepare_rollouts(trajs: List[Dict[str, np.ndarray]], net: PolicyNet,
                     ref_net: PolicyNet, gamma: float = 0.995, lam: float = 0.95,
                     seq_len: int = 64, device: str = "cpu",
                     repeat_move_weight: float = 1.0,
                     attack_weight: float = 1.0,
                     move_weight: float = 1.0) -> List[dict]:
    """把 ``.npz`` 轨迹变成可训练 rollouts:张量 + hidden + GAE + BPTT 切分。

    三个权重系数烘焙进 ``roll["step_w"]``(静态,只与动作/dones 有关,refresh
    不改变它);改系数需重新 prepare(很快)。"""
    rollouts = []
    for traj in trajs:
        t = validate_trajectory(traj, source=str(traj.get("_source", "")))
        dones = np.asarray(traj["dones"]).astype(bool)
        rollouts.append({
            "obs": {k: torch.as_tensor(np.asarray(traj[k])) for k in OBS_KEYS},
            "actions": torch.as_tensor(np.asarray(traj["actions"]), dtype=torch.long),
            "bc_valid": torch.as_tensor(np.asarray(traj["bc_valid"]).astype(bool)),
            "masks": torch.as_tensor(np.asarray(traj["action_mask"]), dtype=torch.float32),
            "rewards": np.asarray(traj["rewards"], dtype=np.float32),
            "dones": dones,
            "step_w": torch.as_tensor(compute_step_weights(
                traj["actions"], dones, repeat_move_weight, attack_weight,
                move_weight)),
            "T": t,
            "name": str(traj.get("_name", "")),
            "seqs": _seq_batches(t, seq_len, dones),
        })
    return refresh_rollouts(rollouts, net, ref_net, gamma, lam, device)


def apply_trainable_scope(net: PolicyNet, freeze_critic: bool = False,
                          freeze_backbone: bool = False) -> None:
    """``freeze_critic=True`` 只冻结 ``net.critic``;``freeze_backbone=True``
    则冻结编码器 + LSTM + Critic,只留 ``fuse`` + ``actor`` 可训(LSTM 表征
    与 Critic 与基座逐 bit 一致,零 recurrent drift)。"""
    if freeze_backbone:
        for name in ("motion", "self_enc", "opp_enc", "global_enc",
                     "proj_enc", "lstm", "critic"):
            for p in getattr(net, name).parameters():
                p.requires_grad = False
        for name in ("fuse", "actor"):
            for p in getattr(net, name).parameters():
                p.requires_grad = True
        return
    for p in net.critic.parameters():
        p.requires_grad = not freeze_critic


def apply_logit_temperature(net: PolicyNet,
                            logit_temperature: float = 1.0) -> PolicyNet:
    """actor 输出层权重/bias 同乘 ``1/T``:logit 相对差按比例缩放,分布变锐
    (T<1)或变平(T>1),动作排序(argmax)不变。``1.0`` 时原样返回。"""
    if logit_temperature != 1.0:
        scale = 1.0 / float(logit_temperature)
        with torch.no_grad():
            net.actor.weight.mul_(scale)
            if net.actor.bias is not None:
                net.actor.bias.mul_(scale)
    return net


def _gather_seqs(rollouts: List[dict]) -> List[Tuple[int, int, int]]:
    return [(r, s, e) for r, roll in enumerate(rollouts) for s, e in roll["seqs"]]


def _minibatch(rollouts: List[dict], chunk: List[Tuple[int, int, int]],
               device: str = "cpu"):
    """按 ``train_ppo.update`` 同款右侧 pad 组 minibatch;返回张量命名元组。"""
    max_len = max(e - s for _, s, e in chunk)
    starts = torch.tensor([s for _, s, _ in chunk], dtype=torch.long)
    lens = torch.tensor([e - s for _, s, e in chunk], dtype=torch.float32)
    steps = torch.arange(max_len)
    valid = steps.unsqueeze(0) < lens.unsqueeze(1)
    idx = torch.minimum(starts.unsqueeze(1) + steps.unsqueeze(0),
                        (starts + lens.long() - 1).unsqueeze(1))
    m = valid.float()
    sub = {k: torch.stack([rollouts[r]["obs"][k][idx[i]].to(device)
                           for i, (r, _, _) in enumerate(chunk)]) for k in OBS_KEYS}
    # 时间/动作类张量直接按 (B, T) 索引。
    act = torch.stack([rollouts[r]["actions"][idx[i]] for i, (r, _, _) in enumerate(chunk)])
    bc = torch.stack([rollouts[r]["bc_valid"][idx[i]] for i, (r, _, _) in enumerate(chunk)])
    msk = torch.stack([rollouts[r]["masks"][idx[i]].to(device)
                       for i, (r, _, _) in enumerate(chunk)])
    adv = torch.stack([rollouts[r]["adv"][idx[i]] for i, (r, _, _) in enumerate(chunk)])
    ret = torch.stack([rollouts[r]["ret"][idx[i]] for i, (r, _, _) in enumerate(chunk)])
    rv = torch.stack([rollouts[r]["ref_values"][idx[i]] for i, (r, _, _) in enumerate(chunk)])
    if all("step_w" in rollouts[r] for r, _, _ in chunk):
        w = torch.stack([rollouts[r]["step_w"][idx[i]] for i, (r, _, _) in enumerate(chunk)])
    else:  # 旧手写 rollouts 兼容:权重恒 1
        w = torch.ones(len(chunk), max_len)
    # 起始 hidden:((B, L, H) h, (B, L, H) c) -> ((L, B, H) h, (L, B, H) c)。
    h0 = tuple(torch.stack([torch.as_tensor(rollouts[r]["h_in"][hc][s])
                            for r, s, _ in chunk]).transpose(0, 1).contiguous()
               for hc in (0, 1))
    rh0 = tuple(torch.stack([torch.as_tensor(rollouts[r]["ref_h_in"][hc][s])
                             for r, s, _ in chunk]).transpose(0, 1).contiguous()
                for hc in (0, 1))
    return {"sub": sub, "m": m.to(device), "act": act.to(device),
            "bc": bc.to(device), "msk": msk, "adv": adv.to(device),
            "ret": ret.to(device), "rv": rv.to(device), "w": w.to(device),
            "h0": tuple(t.to(device) for t in h0),
            "rh0": tuple(t.to(device) for t in rh0), "lens": lens}

def posttrain_epoch(net: PolicyNet, ref_net: PolicyNet, opt: torch.optim.Optimizer,
                    rollouts: List[dict], *, seq_len: int = 64, mb_seqs: int = 16,
                    mode: str = "awbc", awbc_beta: float = 1.0,
                    bc_coef: float = 1.0, kl_coef: float = 0.1, kl_dir: str = "both",
                    value_coef: float = 0.5, critic_mode: str = "blended",
                    alpha_v: float = 0.7, entropy_coef: float = 0.0,
                    ent_cap_coef: float = 0.0,
                    max_grad_norm: float = 0.5, freeze_critic: bool = False,
                    freeze_backbone: bool = False,
                    adv_min: Optional[float] = None,
                    device: str = "cpu") -> dict:
    """跑一轮后训练 epoch;返回平均日志字典。

    损失 ``bc_coef * bc_loss + kl_coef * kl_loss + value_coef * value_loss
    - entropy_coef * entropy + ent_cap_coef * ent_cap``;``freeze_critic``/
    ``freeze_backbone=True`` 时冻结相应参数并强制 ``value_coef = 0.0``。
    ``ent_cap`` = 逐状态 ``relu(H(pi) - H(ref))^2`` 均值:只允许把概率质量
    从尾部搬向人类战术动作,不允许分布比 ref 更平。BC 掩码 = 有效步 ×
    ``step_w``(transition/组加权)[× ``adv > adv_min``];KL 锚定始终覆盖
    100% 步,不受两者影响。
    """
    if mode not in ("sft", "awbc"):
        raise ValueError(f"unknown mode {mode}")
    if kl_dir not in ("forward", "reverse", "both"):
        raise ValueError(f"unknown kl_dir {kl_dir}")
    if critic_mode not in ("anchor", "blended"):
        raise ValueError(f"unknown critic_mode {critic_mode}")
    if freeze_critic or freeze_backbone:
        apply_trainable_scope(net, freeze_critic, freeze_backbone)
        value_coef = 0.0

    seqs = _gather_seqs(rollouts)
    order = np.random.permutation(len(seqs))
    logs = {"bc_loss": 0.0, "kl": 0.0, "value_loss": 0.0, "entropy": 0.0,
            "ent_cap": 0.0,
            "top1_acc": 0.0, "top3_acc": 0.0, "n_bc_valid": 0, "n_steps": 0,
            "n_updates": 0, "skipped": 0}
    c1_sum = c3_sum = bc_sum = 0.0

    for i in range(0, len(order), mb_seqs):
        chunk = [seqs[j] for j in order[i:i + mb_seqs]]
        if not chunk:
            continue
        max_len = max(e - s for _, s, e in chunk)
        if max_len < 2:
            continue
        mb = _minibatch(rollouts, chunk, device)
        m, lens = mb["m"], mb["lens"].to(device)

        def _seq_mean(x: torch.Tensor) -> torch.Tensor:
            return (x * m).sum(1) / lens  # (B,)

        logits, values, _ = net.forward(mb["sub"], mb["h0"], seq=True)
        with torch.no_grad():
            ref_logits, ref_values_mb, _ = ref_net.forward(mb["sub"], mb["rh0"], seq=True)
            ref_dist_frozen = ref_net.masked_dist(ref_logits, mb["msk"])
        dist = net.masked_dist(logits, mb["msk"])
        ref_dist = ref_dist_frozen
        logp = dist.log_prob(mb["act"])
        bc_m = m * mb["bc"].float() * mb["w"]
        if adv_min is not None:
            bc_m = bc_m * (mb["adv"] > adv_min).float()

        if mode == "sft":
            w = torch.ones_like(logp)
        else:
            w = torch.exp(torch.clamp(mb["adv"] / max(awbc_beta, 1e-8), -2.0, 2.0))
            denom = w[bc_m > 0].mean() + 1e-8 if bool((bc_m > 0).any()) else 1.0
            w = w / denom
        bc_loss = (w.detach() * logp * bc_m).sum() / (bc_m.sum() + 1e-8)
        bc_loss = -bc_loss

        with torch.no_grad():
            pred1 = dist.logits.argmax(dim=-1)
            top3 = torch.topk(dist.logits, min(3, N_ACTIONS), dim=-1).indices
            hit1 = (pred1 == mb["act"]).float() * bc_m
            hit3 = (top3 == mb["act"].unsqueeze(-1)).any(dim=-1).float() * bc_m
            c1_sum += float(hit1.sum())
            c3_sum += float(hit3.sum())
            bc_sum += float(bc_m.sum())

        if kl_dir == "forward":
            kl_t = torch.distributions.kl_divergence(ref_dist, dist)
        elif kl_dir == "reverse":
            kl_t = torch.distributions.kl_divergence(dist, ref_dist)
        else:
            kl_t = 0.5 * (torch.distributions.kl_divergence(dist, ref_dist)
                          + torch.distributions.kl_divergence(ref_dist, dist))
        kl_loss = _seq_mean(kl_t).mean()

        if value_coef > 0.0:
            v_anchor = (values - ref_values_mb.detach()) ** 2
            if critic_mode == "anchor":
                v_err = v_anchor
            else:
                v_err = alpha_v * v_anchor + (1.0 - alpha_v) * (values - mb["ret"]) ** 2
            value_loss = _seq_mean(v_err).mean()
        else:
            value_loss = torch.zeros((), device=device)
        entropy = _seq_mean(dist.entropy()).mean()
        ent_excess = torch.relu(dist.entropy() - ref_dist.entropy().detach())
        ent_cap_loss = _seq_mean(ent_excess ** 2).mean()

        loss = (bc_coef * bc_loss + kl_coef * kl_loss
                + value_coef * value_loss - entropy_coef * entropy
                + ent_cap_coef * ent_cap_loss)
        opt.zero_grad()
        if not torch.isfinite(loss):
            logs["skipped"] += 1
            continue
        loss.backward()
        nn.utils.clip_grad_norm_(net.parameters(), max_grad_norm)
        if any(p.grad is not None and not torch.isfinite(p.grad).all()
               for p in net.parameters()):
            logs["skipped"] += 1
            opt.zero_grad(set_to_none=True)
            continue
        opt.step()

        logs["bc_loss"] += float(bc_loss.detach())
        logs["kl"] += float(kl_loss.detach())
        logs["value_loss"] += float(value_loss.detach())
        logs["entropy"] += float(entropy.detach())
        logs["ent_cap"] += float(ent_cap_loss.detach())
        logs["n_bc_valid"] += int(bc_m.sum())
        logs["n_steps"] += int(m.sum())
        logs["n_updates"] += 1

    n = max(logs["n_updates"], 1)
    for k in ("bc_loss", "kl", "value_loss", "entropy", "ent_cap"):
        logs[k] /= n
    logs["top1_acc"] = c1_sum / max(bc_sum, 1e-8)
    logs["top3_acc"] = c3_sum / max(bc_sum, 1e-8)
    return logs


@torch.no_grad()
def evaluate_dataset(net: PolicyNet, ref_net: PolicyNet, rollouts: List[dict], *,
                     seq_len: int = 64, mb_seqs: int = 16, kl_dir: str = "both",
                     device: str = "cpu") -> dict:
    """纯评估(无梯度):BC 拟合度 + KL 偏离 + Critic 失配 + 动作组诊断。"""
    was_training = net.training
    net.eval()
    ref_net.eval()
    nll_sum = kl_sum = ent_sum = verr_sum = verr_ret_sum = 0.0
    m_sum = bc_sum = c1_sum = c3_sum = 0.0
    pos_nll_sum = pos_c1_sum = pos_sum = 0.0
    groups: Dict[str, Dict[str, float]] = {}
    seqs = _gather_seqs(rollouts)
    for i in range(0, max(len(seqs), 1), mb_seqs):
        chunk = seqs[i:i + mb_seqs]
        if not chunk:
            continue
        mb = _minibatch(rollouts, chunk, device)
        m, lens = mb["m"], mb["lens"].to(device)
        logits, values, _ = net.forward(mb["sub"], mb["h0"], seq=True)
        ref_logits, ref_values_mb, _ = ref_net.forward(mb["sub"], mb["rh0"], seq=True)
        dist = net.masked_dist(logits, mb["msk"])
        ref_dist = ref_net.masked_dist(ref_logits, mb["msk"])
        logp = dist.log_prob(mb["act"])
        bc_m = m * mb["bc"].float()
        nll_sum += float((-logp * bc_m).sum())
        bc_sum += float(bc_m.sum())
        m_sum += float(m.sum())
        if kl_dir == "forward":
            kl_t = torch.distributions.kl_divergence(ref_dist, dist)
        elif kl_dir == "reverse":
            kl_t = torch.distributions.kl_divergence(dist, ref_dist)
        else:
            kl_t = 0.5 * (torch.distributions.kl_divergence(dist, ref_dist)
                          + torch.distributions.kl_divergence(ref_dist, dist))
        kl_sum += float((kl_t * m).sum())
        ent_sum += float((dist.entropy() * m).sum())
        verr_sum += float((((values - ref_values_mb) ** 2) * m).sum())
        verr_ret_sum += float((((values - mb["ret"]) ** 2) * m).sum())
        pred1 = dist.logits.argmax(dim=-1)
        top3 = torch.topk(dist.logits, min(3, N_ACTIONS), dim=-1).indices
        c1_sum += float((((pred1 == mb["act"]).float()) * bc_m).sum())
        c3_sum += float((((top3 == mb["act"].unsqueeze(-1)).any(-1)).float() * bc_m).sum())
        pos_m = bc_m * (mb["adv"] > 0).float()
        pos_nll_sum += float((-logp * pos_m).sum())
        pos_c1_sum += float((((pred1 == mb["act"]).float()) * pos_m).sum())
        pos_sum += float(pos_m.sum())
        acts = mb["act"].cpu().numpy()
        lp = logp.cpu().numpy()
        bm = bc_m.cpu().numpy()
        p1 = (pred1 == mb["act"]).cpu().numpy()
        for b in range(acts.shape[0]):
            for t in range(acts.shape[1]):
                if bm[b, t] <= 0:
                    continue
                g = groups.setdefault(action_group(int(acts[b, t])),
                                      {"n": 0.0, "nll": 0.0, "hit1": 0.0})
                g["n"] += 1.0
                g["nll"] += float(-lp[b, t])
                g["hit1"] += float(p1[b, t])
    if was_training:
        net.train()
    out = {
        "bc_nll": nll_sum / max(bc_sum, 1e-8),
        "top1_acc": c1_sum / max(bc_sum, 1e-8),
        "top3_acc": c3_sum / max(bc_sum, 1e-8),
        "pos_adv_bc_nll": pos_nll_sum / max(pos_sum, 1e-8),
        "pos_adv_top1": pos_c1_sum / max(pos_sum, 1e-8),
        "pos_adv_n": int(pos_sum),
        "kl_div": kl_sum / max(m_sum, 1e-8),
        "value_loss": verr_sum / max(m_sum, 1e-8),
        "value_ret_mse": verr_ret_sum / max(m_sum, 1e-8),
        "entropy": ent_sum / max(m_sum, 1e-8),
        "n_steps": int(m_sum),
        "n_bc_valid": int(bc_sum),
        "groups": {g: {"n": int(v["n"]), "nll": v["nll"] / max(v["n"], 1e-8),
                       "top1": v["hit1"] / max(v["n"], 1e-8)}
                   for g, v in sorted(groups.items())},
    }
    return out


def _infer_net_cfg(sd: dict) -> dict:
    """从 checkpoint 权重形状反推 PolicyNet 结构超参(显式传入的 net_cfg 优先)。

    键缺失或动作头维度不符直接抛错 —— 后训练绝不静默迁移/丢权重。
    """
    need = ("motion.weight", "actor.weight", "critic.weight", "lstm.weight_hh_l0",
            "self_enc.0.weight", "opp_enc.0.weight", "global_enc.0.weight",
            "proj_enc.point.0.weight", "fuse.0.weight")
    missing = [k for k in need if k not in sd]
    if missing:
        raise ValueError(f"checkpoint 缺少结构键 {missing},无法反推网络结构")
    n_actions = int(sd["actor.weight"].shape[0])
    if n_actions != N_ACTIONS:
        raise ValueError(f"checkpoint 动作头 {n_actions} != 本仓库 {N_ACTIONS},拒绝迁移")
    lstm_hidden = int(sd["lstm.weight_hh_l0"].shape[1])
    layers = 0
    while f"lstm.weight_hh_l{layers}" in sd:
        layers += 1
    cfg = {
        "motion_vocab": int(sd["motion.weight"].shape[0]),
        "motion_embed": int(sd["motion.weight"].shape[1]),
        "team_hidden": int(sd["self_enc.0.weight"].shape[0]),
        "global_hidden": int(sd["global_enc.0.weight"].shape[0]),
        "proj_hidden": int(sd["proj_enc.point.0.weight"].shape[0]),
        "fuse_hidden": int(sd["fuse.0.weight"].shape[0]),
        "lstm_hidden": lstm_hidden,
        "lstm_layers": layers,
    }
    return cfg


def load_base_checkpoint(path: str | Path, net_cfg: Optional[dict] = None):
    """读基座 checkpoint,返回 ``(net, meta)``。

    网络结构按 checkpoint 权重形状反推(``motion_vocab`` 等),显式 ``net_cfg``
    只做覆盖;加载后若有任一参数被跳过/迁移直接抛错,绝不静默丢权重。
    """
    data = torch.load(str(path), map_location="cpu", weights_only=False)
    sd = data.get("model_state_dict") or data.get("model")
    if sd is None:
        raise ValueError(f"checkpoint {path} 缺少 model_state_dict/model 字段")
    cfg = _infer_net_cfg(sd)
    if net_cfg:
        cfg.update(net_cfg)
    net = PolicyNet(cfg)
    res = load_weights(net, sd, verbose=False)
    if res.skipped or res.migrated:
        raise RuntimeError(f"checkpoint {path} 未能完整加载 "
                           f"(skipped={res.skipped}, migrated={res.migrated})")
    return net, dict(data)


def save_posttrain_checkpoint(path: str | Path, net: PolicyNet,
                              opt: Optional[torch.optim.Optimizer],
                              base_meta: dict, posttrain_info: dict) -> None:
    """保存后训练 checkpoint:保留基座全部元数据 + ``posttrain_info``。"""
    meta = dict(base_meta)
    sd = {k: v.detach().cpu() for k, v in net.state_dict().items()}
    meta["model_state_dict"] = sd
    meta["model"] = sd
    if opt is not None:
        meta["optimizer_state_dict"] = opt.state_dict()
        meta["optimizer"] = opt.state_dict()
    meta["posttrain_info"] = dict(posttrain_info)
    torch.save(meta, str(path))


def run_posttrain(net: PolicyNet, ref_net: PolicyNet, opt: torch.optim.Optimizer,
                  train_rolls: List[dict], val_rolls: Optional[List[dict]] = None,
                  test_rolls: Optional[List[dict]] = None, *, epochs: int = 3,
                  out_dir: Optional[str | Path] = None, base_meta: Optional[dict] = None,
                  posttrain_info: Optional[dict] = None, device: str = "cpu",
                  recompute_hidden_each_epoch: bool = True,
                  gamma: float = 0.995, lam: float = 0.95,
                  target_kl: float = 0.0, **epoch_kw) -> dict:
    """整套后训练循环;每轮在 val 上选优,落盘 ``best_val.pt`` 与 ``last.pt``。

    先记 ``epoch: 0`` 基线(基座策略在 val/test 上的零样本评估),再跑
    ``epoch: 1..N``;``best_val.pt`` 只在 1..N 中选优(基线只做对照,不落盘)。
    """
    history: List[dict] = []
    best_val = float("inf")
    out = Path(out_dir) if out_dir else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
    if val_rolls or test_rolls:
        base: dict = {"epoch": 0}
        if val_rolls:
            base.update({f"val_{k}": v for k, v in
                         evaluate_dataset(net, ref_net, val_rolls, device=device).items()
                         if isinstance(v, (int, float))})
        if test_rolls:
            base.update({f"test_{k}": v for k, v in
                         evaluate_dataset(net, ref_net, test_rolls, device=device).items()
                         if isinstance(v, (int, float))})
        history.append(base)
    for ep in range(epochs):
        if recompute_hidden_each_epoch and ep > 0:
            refresh_rollouts(train_rolls, net, ref_net, gamma, lam, device)
        logs = posttrain_epoch(net, ref_net, opt, train_rolls, device=device, **epoch_kw)
        val_metrics = (evaluate_dataset(net, ref_net, val_rolls, device=device)
                       if val_rolls else {})
        rec = {"epoch": ep + 1, **{f"train_{k}": v for k, v in logs.items()
                                   if isinstance(v, (int, float))},
               **{f"val_{k}": v for k, v in val_metrics.items()
                  if isinstance(v, (int, float))}}
        history.append(rec)
        improved = bool(val_metrics) and val_metrics["bc_nll"] < best_val
        if improved:
            best_val = float(val_metrics["bc_nll"])
            if out is not None:
                save_posttrain_checkpoint(out / "best_val.pt", net, opt, base_meta or {},
                                          {**(posttrain_info or {}), **rec,
                                           "best_val_nll": best_val})
        if target_kl > 0 and logs.get("kl", 0.0) > target_kl * 1.5:
            break
    if out is not None:
        save_posttrain_checkpoint(out / "last.pt", net, opt, base_meta or {},
                                  {**(posttrain_info or {}), "epochs_done": len(history),
                                   "best_val_nll": best_val})
    result = {"history": history, "best_val_nll": best_val}
    if test_rolls:
        result["test"] = evaluate_dataset(net, ref_net, test_rolls, device=device)
    return result


def _match_manifest(manifest: dict, split: str, batches: str = "all") -> List[Tuple[str, int]]:
    """取 manifest 某 split (+ train 的 batch 子集)的精确视角列表 ``[(file, team)]``。

    ``batches``: ``"all"`` | ``"batch_1"`` | ``"batch_1,batch_3"``(仅 train)。
    镜像对局的同一 ``file`` 会出现两次(``team`` 0 与 1),同进同出。
    """
    if split == "train":
        avail = manifest["splits"]["train"]["batches"]
        if batches in (None, "all"):
            persps = [p for b in avail.values() for p in b]
        else:
            wanted = [b.strip() for b in batches.split(",") if b.strip()]
            unknown = [b for b in wanted if b not in avail]
            if unknown:
                raise ValueError(f"未知的 batch {unknown},可选 {sorted(avail)}")
            persps = [p for b in wanted for p in avail[b]]
    else:
        if batches not in (None, "all"):
            raise ValueError("--batches 仅对 train split 有效")
        persps = manifest["splits"][split]
    return [(p["file"], int(p["team"])) for p in persps]


def load_selected_trajectories(npz_paths: List[str | Path],
                               perspectives: List[Tuple[str, int]]) -> List[dict]:
    """按精确 ``(file, team)`` 视角集合筛选并载入轨迹 ``.npz``。

    身份判定(:func:`read_perspective_id`):首选文件内 ``source_file``/``team``
    键;缺失时按文件名 ``<rep_stem>_t<team>`` 在候选视角的 stem+team 上精确匹配
    (歧义或无法定位时跳过该文件,绝不张冠李戴)。同一视角出现多次时只保留
    首个(按路径排序),避免重复计数。
    """
    want = set(perspectives)
    by_stem: Dict[Tuple[str, Optional[int]], List[Tuple[str, int]]] = {}
    for f, t in want:
        by_stem.setdefault((Path(f).stem, t), []).append((f, t))
    out = []
    seen = set()
    for npz in sorted(npz_paths, key=str):  # 确定性顺序:同视角重复时取首个
        file, team, stem = read_perspective_id(npz)
        hit: Optional[Tuple[str, int]] = None
        if file is not None and team is not None:
            if (file, team) in want:
                hit = (file, team)
        else:
            cands = by_stem.get((stem, team), [])
            if len(cands) == 1:
                hit = cands[0]
        if hit is None or hit in seen:
            continue
        seen.add(hit)
        traj = load_trajectory(npz)
        traj["_source"] = str(npz)
        traj["_name"] = Path(npz).name
        traj["_perspective"] = hit
        out.append(traj)
    return out


def main(argv: Optional[List[str]] = None) -> dict:
    """CLI:基座 + 轨迹目录 -> 后训练 -> best_val.pt / last.pt。"""
    ap = argparse.ArgumentParser(description="离线 Replay 后训练 (SFT/AWBC+KL 锚定)")
    ap.add_argument("--base-ckpt", default="checkpoints/com4_multi_b24k_cpun_e200/best.pt")
    ap.add_argument("--train-dir", default="")
    ap.add_argument("--val-dir", default="")
    ap.add_argument("--test-dir", default="")
    ap.add_argument("--data-root", default="", help="配合 --splits 按 manifest 选 .npz")
    ap.add_argument("--splits", default="")
    ap.add_argument("--batches", default="all",
                    help="train batch 子集(batch_1/batch_1,batch_2/all,需 --splits)")
    ap.add_argument("--out-dir", default="checkpoints/posttrain_replay")
    ap.add_argument("--mode", default="awbc", choices=["sft", "awbc"])
    ap.add_argument("--awbc-beta", type=float, default=1.0)
    ap.add_argument("--adv-min", type=float, default=None,
                    help="仅在 adv > adv_min 步上算 BC(不过滤评估);默认 None 不过滤")
    ap.add_argument("--repeat-move-weight", type=float, default=1.0,
                    help="同动作长按 move/guard 续步权重(<1 降权)")
    ap.add_argument("--attack-weight", type=float, default=1.0,
                    help="attack_ABC 步权重(>1 加权)")
    ap.add_argument("--move-weight", type=float, default=1.0,
                    help="move 组步权重(0 即零模仿走路)")
    ap.add_argument("--bc-coef", type=float, default=1.0)
    ap.add_argument("--kl-coef", type=float, default=0.1)
    ap.add_argument("--kl-dir", default="both", choices=["forward", "reverse", "both"])
    ap.add_argument("--value-coef", type=float, default=0.5)
    ap.add_argument("--critic-mode", default="blended", choices=["anchor", "blended"])
    ap.add_argument("--alpha-v", type=float, default=0.7)
    ap.add_argument("--entropy-coef", type=float, default=0.0)
    ap.add_argument("--ent-cap-coef", type=float, default=0.0,
                    help="逐状态 relu(H(pi)-H(ref))^2 惩罚,防熵增")
    ap.add_argument("--gamma", type=float, default=0.995)
    ap.add_argument("--lam", type=float, default=0.95)
    ap.add_argument("--seq-len", type=int, default=64)
    ap.add_argument("--mb-seqs", type=int, default=16)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--max-grad-norm", type=float, default=0.5)
    ap.add_argument("--target-kl", type=float, default=0.0)
    ap.add_argument("--logit-temperature", type=float, default=1.0,
                    help="训后 actor 权重/bias 同乘 1/T 并存 best_val_sharpened.pt")
    ap.add_argument("--freeze-critic", action="store_true")
    ap.add_argument("--freeze-backbone", action="store_true",
                    help="冻结编码器+LSTM+Critic,只训 fuse+actor(零 recurrent drift)")
    ap.add_argument("--no-recompute", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args(argv)

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    random.seed(args.seed)

    net, base_meta = load_base_checkpoint(args.base_ckpt)
    ref_net = copy.deepcopy(net)  # 与 net 同结构同权重,保证 KL 锚点严格对齐
    net.to(args.device)
    ref_net.to(args.device)
    ref_net.requires_grad_(False)
    ref_net.eval()

    if args.batches not in (None, "all") and not args.splits:
        ap.error("--batches 需要配合 --splits 使用")
    if args.data_root and args.splits:
        manifest = json.loads(Path(args.splits).read_text(encoding="utf-8"))
        all_npz = sorted(Path(args.data_root).rglob("*.npz"))
        train_trajs = load_selected_trajectories(
            all_npz, _match_manifest(manifest, "train", args.batches))
        val_trajs = load_selected_trajectories(all_npz, _match_manifest(manifest, "val"))
        test_trajs = load_selected_trajectories(all_npz, _match_manifest(manifest, "test"))
    else:
        if not args.train_dir:
            ap.error("需要 --train-dir(或 --data-root + --splits)")
        train_trajs = load_npz_dir(args.train_dir)
        val_trajs = load_npz_dir(args.val_dir) if args.val_dir else []
        test_trajs = load_npz_dir(args.test_dir) if args.test_dir else []
    if not train_trajs:
        ap.error("训练集为空:检查 --train-dir / manifest 匹配规则")
    print(f"[i] trajectories: train={len(train_trajs)} val={len(val_trajs)} "
          f"test={len(test_trajs)} batches={args.batches}", flush=True)

    opt = torch.optim.Adam(net.parameters(), lr=args.lr)
    wkw = {"repeat_move_weight": args.repeat_move_weight,
           "attack_weight": args.attack_weight, "move_weight": args.move_weight}
    train_rolls = prepare_rollouts(train_trajs, net, ref_net, args.gamma, args.lam,
                                   args.seq_len, args.device, **wkw)
    val_rolls = (prepare_rollouts(val_trajs, net, ref_net, args.gamma, args.lam,
                                  args.seq_len, args.device, **wkw) if val_trajs else None)
    test_rolls = (prepare_rollouts(test_trajs, net, ref_net, args.gamma, args.lam,
                                   args.seq_len, args.device, **wkw) if test_trajs else None)
    info = {"base_ckpt": str(args.base_ckpt), "mode": args.mode,
            "kl_coef": args.kl_coef, "kl_dir": args.kl_dir,
            "bc_coef": args.bc_coef, "value_coef": args.value_coef,
            "critic_mode": args.critic_mode, "alpha_v": args.alpha_v,
            "awbc_beta": args.awbc_beta, "adv_min": args.adv_min,
            "repeat_move_weight": args.repeat_move_weight,
            "attack_weight": args.attack_weight, "move_weight": args.move_weight,
            "freeze_critic": args.freeze_critic,
            "freeze_backbone": args.freeze_backbone,
            "ent_cap_coef": args.ent_cap_coef,
            "logit_temperature": args.logit_temperature,
            "batches": args.batches,
            "seed": args.seed, "n_train": len(train_trajs), "n_val": len(val_trajs),
            "n_test": len(test_trajs)}
    result = run_posttrain(net, ref_net, opt, train_rolls, val_rolls, test_rolls,
                           epochs=args.epochs, out_dir=args.out_dir, base_meta=base_meta,
                           posttrain_info=info, device=args.device,
                           recompute_hidden_each_epoch=not args.no_recompute,
                           gamma=args.gamma, lam=args.lam, target_kl=args.target_kl,
                           seq_len=args.seq_len, mb_seqs=args.mb_seqs, mode=args.mode,
                           awbc_beta=args.awbc_beta, adv_min=args.adv_min,
                           bc_coef=args.bc_coef,
                           kl_coef=args.kl_coef, kl_dir=args.kl_dir,
                           value_coef=args.value_coef, critic_mode=args.critic_mode,
                           alpha_v=args.alpha_v, entropy_coef=args.entropy_coef,
                           ent_cap_coef=args.ent_cap_coef,
                           max_grad_norm=args.max_grad_norm,
                           freeze_critic=args.freeze_critic,
                           freeze_backbone=args.freeze_backbone)
    if args.logit_temperature != 1.0 and args.out_dir:
        best_path = Path(args.out_dir) / "best_val.pt"
        if best_path.exists():
            sharp_net, sharp_meta = load_base_checkpoint(best_path)
            apply_logit_temperature(sharp_net, args.logit_temperature)
            clean_meta = {k: v for k, v in sharp_meta.items()
                          if k not in ("optimizer_state_dict", "optimizer")}
            base_info = dict(sharp_meta.get("posttrain_info", {}))
            save_posttrain_checkpoint(
                Path(args.out_dir) / "best_val_sharpened.pt", sharp_net, None,
                clean_meta,
                {**base_info, "sharpened_from": "best_val.pt",
                 "logit_temperature": args.logit_temperature})
            print(f"[i] sharpened checkpoint saved "
                  f"(T={args.logit_temperature})", flush=True)
    print(json.dumps({"best_val_nll": result["best_val_nll"],
                      "epochs": len(result["history"]),
                      "test": result.get("test", {})}, indent=1, ensure_ascii=False))
    return result


if __name__ == "__main__":
    main()
