"""复合策略网络(Entity MLP + 弹幕 PointNet + LSTM)与 Masked Actor-Critic。

结构(见 issue 05):

    motion_self/opp  -> Embedding(motion_vocab, 16)   (默认 4096;主配置 10240,见 docs §36)
    self(32)+emb     -> MLP(48 -> 128)  ─┐
    opp(34)+emb      -> MLP(50 -> 128)  ─┤
    global(12)       -> MLP(12 -> 64)   ─┼-> concat(448) -> Linear+LN+SiLU(256) -> LSTM(256)
    proj(16x8)+mask  -> PointNet        ─┘                                         ├-> Actor(64)
                            (7->64->64, masked max+mean -> 128)                     └-> Critic(1)

动作掩码:非法动作的 logits 置为 `MASK_VALUE`(-1e8),softmax 后概率在 fp32 下正好是 0.0。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Categorical

from env import action_space, obs_builder

N_ACTIONS = action_space.N_ACTIONS
MASK_VALUE = -1.0e8


def _mlp(in_dim: int, out_dim: int, hidden: Optional[int] = None) -> nn.Sequential:
    h = hidden or out_dim
    return nn.Sequential(
        nn.Linear(in_dim, h), nn.LayerNorm(h), nn.SiLU(),
        nn.Linear(h, out_dim), nn.LayerNorm(out_dim), nn.SiLU(),
    )


class ProjectilePointNet(nn.Module):
    """共享权重的逐点 MLP + 掩码 max/mean 池化。"""

    def __init__(self, dims: int = obs_builder.PROJ_DIMS, hidden: int = 64):
        super().__init__()
        self.point = nn.Sequential(
            nn.Linear(dims, hidden), nn.SiLU(),
            nn.Linear(hidden, hidden), nn.SiLU(),
        )
        self.out_dim = hidden * 2

    def forward(self, proj: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        """proj: (B, K, PROJ_DIMS)  mask: (B, K) 1=有效"""
        h = self.point(proj)                                  # (B, K, H)
        m = mask.unsqueeze(-1)                                # (B, K, 1)
        neg = torch.finfo(h.dtype).min
        pooled_max = h.masked_fill(m == 0, neg).max(dim=1).values
        cnt = m.sum(dim=1).clamp(min=1.0)
        pooled_mean = (h * m).sum(dim=1) / cnt
        pooled_max = torch.where(mask.sum(dim=1, keepdim=True) > 0, pooled_max,
                                 torch.zeros_like(pooled_max))
        return torch.cat([pooled_max, pooled_mean], dim=-1)


class PolicyNet(nn.Module):
    """Masked Recurrent Actor-Critic。"""

    def __init__(self, cfg: Optional[Dict] = None):
        super().__init__()
        c = dict(cfg or {})
        self.motion_vocab = int(c.get("motion_vocab", 4096))
        self.motion_embed = int(c.get("motion_embed", 16))
        team_hidden = int(c.get("team_hidden", 128))
        global_hidden = int(c.get("global_hidden", 64))
        proj_hidden = int(c.get("proj_hidden", 64))
        fuse_hidden = int(c.get("fuse_hidden", 256))
        self.lstm_hidden = int(c.get("lstm_hidden", 256))
        self.lstm_layers = int(c.get("lstm_layers", 1))
        # 采样温度:>0。1.0 = 原采样行为(默认,训练/回归不变);<1 收紧、>1 发散。
        # 注意:COM3 上的实测(docs/train_report_com3_full.md §11.3)显示 τ<1 **没有**可测量的收益,
        # argmax 与 τ=1 的差距也落在评估噪声内(±15~22pp)。此旋钮只提供可配置性,
        # 不要默认改小;若要调,必须用 100+ 局配对评估。
        self.sampling_temperature = float(c.get("sampling_temperature", 1.0))
        self.min_p = float(c.get("min_p", 0.0))
        self.top_k = int(c.get("top_k", 0))

        self.motion = nn.Embedding(self.motion_vocab, self.motion_embed)
        self.self_enc = _mlp(obs_builder.SELF_DIM + self.motion_embed, team_hidden)
        self.opp_enc = _mlp(obs_builder.OPP_DIM + self.motion_embed, team_hidden)
        self.global_enc = _mlp(obs_builder.GLOBAL_DIM, global_hidden)
        self.proj_enc = ProjectilePointNet(obs_builder.PROJ_DIMS, proj_hidden)

        fuse_in = team_hidden * 2 + global_hidden + self.proj_enc.out_dim
        self.fuse = nn.Sequential(
            nn.Linear(fuse_in, fuse_hidden), nn.LayerNorm(fuse_hidden), nn.SiLU(),
        )
        self.lstm = nn.LSTM(fuse_hidden, self.lstm_hidden,
                            num_layers=self.lstm_layers, batch_first=True)
        self.actor = nn.Linear(self.lstm_hidden, N_ACTIONS)
        self.critic = nn.Linear(self.lstm_hidden, 1)

        if bool(c.get("orthogonal_init", False)):
            self.init_orthogonal()

    def init_orthogonal(self) -> "PolicyNet":
        """正交初始化网络权重 (PPO 规范冷启动初始化).

        - 隐藏层 Linear (MLP, fuse, proj): gain=sqrt(2) (适应 SiLU 激活)
        - LSTM 循环权重: gain=1.0
        - Actor 输出头: gain=0.01 (使初始策略接近均匀分布探索)
        - Critic 输出头: gain=1.0
        - 偏置全部置零
        """
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.orthogonal_(m.weight, gain=float(np.sqrt(2)))
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0.0)
            elif isinstance(m, nn.LSTM):
                for name, param in m.named_parameters():
                    if "weight_ih" in name:
                        nn.init.orthogonal_(param, gain=1.0)
                    elif "weight_hh" in name:
                        nn.init.orthogonal_(param, gain=1.0)
                    elif "bias" in name:
                        nn.init.constant_(param, 0.0)
        # Actor head 使用 0.01 增益使得初始策略接近均匀分布探索
        nn.init.orthogonal_(self.actor.weight, gain=0.01)
        if self.actor.bias is not None:
            nn.init.constant_(self.actor.bias, 0.0)
        # Critic head 使用 1.0 增益
        nn.init.orthogonal_(self.critic.weight, gain=1.0)
        if self.critic.bias is not None:
            nn.init.constant_(self.critic.bias, 0.0)
        return self

    # -- 前向 --------------------------------------------------------------
    def encode(self, batch: Dict[str, torch.Tensor]) -> torch.Tensor:
        m_self = self.motion(batch["motion_self"].clamp(0, self.motion_vocab - 1))
        m_opp = self.motion(batch["motion_opp"].clamp(0, self.motion_vocab - 1))
        h_self = self.self_enc(torch.cat([batch["self"], m_self], dim=-1))
        h_opp = self.opp_enc(torch.cat([batch["opp"], m_opp], dim=-1))
        h_glob = self.global_enc(batch["global"])
        h_proj = self.proj_enc(batch["proj"], batch["proj_mask"])
        return self.fuse(torch.cat([h_self, h_opp, h_glob, h_proj], dim=-1))

    def forward(self, batch: Dict[str, torch.Tensor],
                hidden: Optional[Tuple[torch.Tensor, torch.Tensor]] = None,
                seq: bool = False
                ) -> Tuple[torch.Tensor, torch.Tensor, Tuple[torch.Tensor, torch.Tensor]]:
        """seq=True 时输入带时间维 (B, T, ...),返回 (B, T, 64) logits。"""
        if seq:
            b, t = batch["self"].shape[:2]
            flat = {k: v.reshape(b * t, *v.shape[2:]) for k, v in batch.items()}
            x = self.encode(flat).view(b, t, -1)
        else:
            x = self.encode(batch).unsqueeze(1)          # (B, 1, F)
        out, hidden = self.lstm(x, hidden)
        logits = self.actor(out)
        value = self.critic(out).squeeze(-1)
        if not seq:
            logits, value = logits[:, 0], value[:, 0]
        return logits, value, hidden

    # -- 采样 --------------------------------------------------------------
    def masked_dist(self, logits: torch.Tensor, mask: torch.Tensor) -> Categorical:
        masked_logits = logits.masked_fill(mask <= 0, MASK_VALUE)
        min_p = float(getattr(self, "min_p", 0.0) or 0.0)
        top_k = int(getattr(self, "top_k", 0) or 0)
        if logits.dim() == 2 and not self.training and not logits.requires_grad and (min_p > 0.0 or top_k > 0):
            if top_k > 0 and top_k < masked_logits.shape[-1]:
                kth = torch.topk(masked_logits, top_k, dim=-1).values[..., -1:]
                masked_logits = masked_logits.masked_fill(masked_logits < kth, MASK_VALUE)
            if min_p > 0.0:
                probs = torch.softmax(masked_logits, dim=-1)
                max_p = probs.max(dim=-1, keepdim=True).values
                keep = (probs >= min_p) | (probs >= max_p)
                masked_logits = masked_logits.masked_fill(~keep, MASK_VALUE)
        return Categorical(logits=masked_logits)

    def act(self, obs: Dict[str, np.ndarray],
            hidden: Optional[Tuple[torch.Tensor, torch.Tensor]] = None,
            deterministic: bool = False):
        """单步推理:obs 是未批处理的观测字典(numpy)。

        `deterministic=False` 时按 `self.sampling_temperature` 采样(默认 1.0 = 原行为)。
        """
        batch = {k: v.unsqueeze(0) for k, v in to_batch(obs).items()}
        logits, value, hidden = self.forward(batch, hidden, seq=False)
        if not deterministic and self.sampling_temperature != 1.0:
            logits = logits / self.sampling_temperature
        dist = self.masked_dist(logits, batch["action_mask"])
        action = dist.logits.argmax(dim=-1) if deterministic else dist.sample()
        return action, dist.log_prob(action), value.squeeze(-1), hidden

    def init_hidden(self, batch_size: int = 1) -> Tuple[torch.Tensor, torch.Tensor]:
        z = torch.zeros(self.lstm_layers, batch_size, self.lstm_hidden)
        return z, z.clone()

    # -- 权重迁移 ----------------------------------------------------------
    def warm_start(self, sd: Dict[str, torch.Tensor], verbose: bool = True) -> List[str]:
        """把旧 checkpoint 的权重搬到当前结构(仅支持"输入层变宽")。

        背景(v13 观测扩容,见 docs/train_report_com3_full.md §17.13):
            opp_enc 的输入是 cat([opp, motion_emb]),所以 opp 末尾插列会让
            motion 那一整块**整体后移**。旧 = [self32, rel_dx, rel_dy, emb16],
            新 = [self32, gapx, gapy, contact, dx, dy, emb16]。
            "拷前 N 列"会把旧的 motion 分量塞进 contact、丢掉 motion 后几维,
            而且是**静默**错位(形状刚好对得上,不报错)。因此必须按列名对齐。

        规则:
            * self_enc[0] / opp_enc[0] 的第一层按语义列名搬,新列权重置 0
              (新增输入初始不贡献,旧特征上的输出保持不变);
            * global_enc[0] 的第一层按**前缀**搬(GLOBAL_DIM 历次扩容都在尾部,
              见 _prefix_map);
            * proj_enc 保持新初始化并**显式报告**:它的旧 7 维输入在 v17 之前
              恒为全零(docs §17.2),那层权重从未收到过梯度,迁移无意义;
            * 其余参数同形直接拷,不同形跳过并报告。
        返回被跳过的参数名(空列表 = 完全迁移)。
        """
        cur = self.state_dict()
        skipped: List[str] = []
        merged = dict(cur)

        # opp 的旧布局必须靠 self_enc 反推出的 old_self 来解,
        # 不能用当前 SELF_DIM(见 _infer_old_self 的 docstring)。
        old_self = self._infer_old_self(sd)
        opp_map = self._opp_column_map(sd, old_self, verbose)
        self_map = self._self_column_map(sd)
        prefix_map = self._prefix_map(sd, verbose)
        for key, new_val in cur.items():
            old_val = sd.get(key)
            if old_val is None:
                skipped.append(key)
                continue
            if tuple(old_val.shape) == tuple(new_val.shape):
                merged[key] = old_val.to(new_val.dtype)
                continue
            if key == "opp_enc.0.weight" and opp_map is not None:
                w = torch.zeros_like(new_val)
                for ni, oi in opp_map:
                    w[:, ni] = old_val[:, oi].to(new_val.dtype)
                merged[key] = w
                continue
            if key == "self_enc.0.weight" and self_map is not None:
                w = torch.zeros_like(new_val)
                for ni, oi in self_map:
                    w[:, ni] = old_val[:, oi].to(new_val.dtype)
                merged[key] = w
                continue
            if key == "proj_enc.point.0.weight":
                old_in = int(old_val.shape[1])
                # 区分两个时代,不能一律重置:
                #  * old_in <= 7:v17 之前的死输入(proj_mask 恒 0),权重从未收到
                #    梯度,搬来等于搬随机数 -> 重置(见 _silence_proj_branch)。
                #  * old_in >= 8:v17 起弹幕通道已经用真实数据训练过(M3 即 8 维),
                #    前 8 列有真实语义 -> 按前缀迁移,只有新增列置 0。
                #    此时**不能**静音 fuse 的弹幕列,那些列也已训练过。
                if old_in >= 8:
                    w = torch.zeros_like(new_val)
                    w[:, :old_in] = old_val.to(new_val.dtype)
                    merged[key] = w
                    if verbose:
                        print(f"[i] warm_start:{key} 输入宽 {old_in} -> "
                              f"{int(new_val.shape[1])},按前缀迁移"
                              f"(新增 {int(new_val.shape[1]) - old_in} 列权重置 0)",
                              flush=True)
                else:
                    skipped.append(key)
                continue
            if (key == "motion.weight" and old_val.dim() == 2
                    and int(old_val.shape[1]) == int(new_val.shape[1])
                    and 0 < int(old_val.shape[0]) < int(new_val.shape[0])):
                # motion 词表扩容(docs §36):旧模型 encode 把 >= old_v-1 的 motion
                # 全部 clamp 到第 old_v-1 行。前 old_v 行原样拷贝,新增行全部
                # 复制旧第 old_v-1 行 => 加载后对任何 motion 的输出与旧模型一致,
                # 而新增行彼此独立,训练时可以分开学到具体符卡/演出动作。
                old_v = int(old_val.shape[0])
                w = torch.empty_like(new_val)
                w[:old_v] = old_val.to(new_val.dtype)
                w[old_v:] = old_val[old_v - 1].to(new_val.dtype)
                merged[key] = w
                if verbose:
                    print(f"[i] warm_start:motion.weight 词表 {old_v} -> "
                          f"{int(new_val.shape[0])},前 {old_v} 行原样迁移"
                          f"(新增 {int(new_val.shape[0]) - old_v} 行复制旧第 {old_v - 1} 行)",
                          flush=True)
                continue
            if key in prefix_map:
                w = torch.zeros_like(new_val)
                n_old = int(old_val.shape[1])
                w[:, :n_old] = old_val.to(new_val.dtype)
                merged[key] = w
                continue
            if key == "actor.weight" and old_val.shape[0] == 55 and new_val.shape[0] == 56:
                w = torch.zeros_like(new_val)
                w[:55] = old_val[:55].to(new_val.dtype)
                w[55] = 0.0
                merged[key] = w
                if verbose:
                    print("[i] warm_start:actor.weight 55->56 动作迁移完成(TAG_ACTION置0)", flush=True)
                continue
            if key == "actor.bias" and old_val.shape[0] == 55 and new_val.shape[0] == 56:
                b_new = torch.zeros_like(new_val)
                b_new[:55] = old_val[:55].to(new_val.dtype)
                b_new[55] = 0.0
                merged[key] = b_new
                continue
            if key == "actor.weight" and old_val.shape[0] == 64 and new_val.shape[0] == 56:
                w = torch.zeros_like(new_val)
                # old: 9 * 7 + 1 = 64, new: 9 * 6 + 2 = 56
                for d in range(9):
                    for b in range(7):
                        if b == 5:
                            continue
                        old_idx = d * 7 + b
                        new_b = b if b < 5 else b - 1
                        new_idx = d * 6 + new_b
                        w[new_idx] = old_val[old_idx].to(new_val.dtype)
                w[54] = old_val[63].to(new_val.dtype)  # Spell
                w[55] = old_val[5].to(new_val.dtype)   # 中立 + Tag (dir=0, btn=5)
                merged[key] = w
                if verbose:
                    print("[i] warm_start:actor.weight 64->56 动作映射迁移完成(含中立 Tag 与 Spell)", flush=True)
                continue
            if key == "actor.bias" and old_val.shape[0] == 64 and new_val.shape[0] == 56:
                b_new = torch.zeros_like(new_val)
                for d in range(9):
                    for b in range(7):
                        if b == 5:
                            continue
                        old_idx = d * 7 + b
                        new_b = b if b < 5 else b - 1
                        new_idx = d * 6 + new_b
                        b_new[new_idx] = old_val[old_idx].to(new_val.dtype)
                b_new[54] = old_val[63].to(new_val.dtype)  # Spell
                b_new[55] = old_val[5].to(new_val.dtype)   # 中立 + Tag (dir=0, btn=5)
                merged[key] = b_new
                continue
            if key == "actor.weight" and old_val.shape[0] == 64 and new_val.shape[0] == 55:
                w = torch.zeros_like(new_val)
                # old: 9 * 7 + 1 = 64, new: 9 * 6 + 1 = 55 (剔除旧 Tag: b=5)
                for d in range(9):
                    for b in range(7):
                        if b == 5:
                            continue
                        old_idx = d * 7 + b
                        new_b = b if b < 5 else b - 1
                        new_idx = d * 6 + new_b
                        w[new_idx] = old_val[old_idx].to(new_val.dtype)
                w[54] = old_val[63].to(new_val.dtype)
                merged[key] = w
                if verbose:
                    print("[i] warm_start:actor.weight 64->55 动作映射迁移完成(已剔除 Tag)", flush=True)
                continue
            if key == "actor.bias" and old_val.shape[0] == 64 and new_val.shape[0] == 55:
                b_new = torch.zeros_like(new_val)
                for d in range(9):
                    for b in range(7):
                        if b == 5:
                            continue
                        old_idx = d * 7 + b
                        new_b = b if b < 5 else b - 1
                        new_idx = d * 6 + new_b
                        b_new[new_idx] = old_val[old_idx].to(new_val.dtype)
                b_new[54] = old_val[63].to(new_val.dtype)
                merged[key] = b_new
                continue
            skipped.append(key)
        self.load_state_dict(merged)
        # proj_enc 的"跳过"是**有意的重置**,不是迁移失败:它的旧 7 维输入
        # 在 v17 之前恒为全零(docs §17.2,proj_mask 全程 0),那层权重从未收到
        # 梯度。搬来等于搬随机数,所以保持新初始化并单独说明,避免和真正的
        # "布局未知、无法迁移"混为一谈。
        intentional = [k for k in skipped if k in self._PROJ_RESET_KEYS]
        failed = [k for k in skipped if k not in self._PROJ_RESET_KEYS]
        if intentional:
            self._silence_proj_branch()
        if verbose:
            if intentional:
                print(f"[i] warm_start:{', '.join(intentional)} 有意重置"
                      f"(旧输入恒为全零、该层从未收到梯度)", flush=True)
            if failed:
                print(f"[w] warm_start 跳过 {len(failed)} 个形状不兼容参数: "
                      f"{', '.join(failed[:6])}{' …' if len(failed) > 6 else ''}",
                      flush=True)
            elif not intentional:
                print("[i] warm_start:全部参数已迁移", flush=True)
        return skipped

    # 旧输入恒为零、迁移无意义、需要重置而非迁移的层(v17 起:弹幕通道首层)。
    _PROJ_RESET_KEYS = ("proj_enc.point.0.weight",)

    def _silence_proj_branch(self) -> None:
        """把弹幕通道对 `fuse` 的贡献初始化为 0。

        为什么要这么做:旧模型里 `proj_mask` 全程为 0(docs §17.2),
        `ProjectilePointNet` 的 max/mean 池化在全掩码下返回**精确的 0**,
        于是 `proj_enc` 输出恒为 0 —— 它在 `fuse` 里对应的那 128 列权重
        **从未收到过梯度**(乘 0 的输入不传播梯度),仍然是初始化值。

        修好数据链路后,这 128 列一旦乘上非零输入,就会向已训练好的 `fuse`
        注入纯噪声,破坏 warm start 的"新旧网络在旧特征上输出相同"性质。
        把对应列清零后,弹幕信息从**零贡献**开始,由后续训练自己长出需要的量,
        与 self/opp 新列"权重置 0"是同一处理原则。
        """
        fuse0 = self.fuse[0]
        # fuse 输入拼接顺序见 encode():[h_self(team_hidden), h_opp(team_hidden),
        # h_glob(global_hidden), h_proj(proj_hidden*2)]
        h_team = int(self.self_enc[1].normalized_shape[0])
        h_glob = int(self.global_enc[1].normalized_shape[0])
        start = h_team * 2 + h_glob
        stop = start + int(self.proj_enc.out_dim)
        with torch.no_grad():
            fuse0.weight[:, start:stop].zero_()

    def _prefix_map(self, sd: Dict[str, torch.Tensor],
                    verbose: bool = True) -> Dict[str, int]:
        """`global_enc[0]` 的扩列迁移:纯前缀拷贝。

        与 `self/opp` 不同,`global` 没有插在中间的新列,扩容全部发生在尾部:
        `GLOBAL_DIM` 12 -> 15(连段三项)-> 18(时间控制)-> 21(对局阶段)-> 23。
        所以旧列 0..n_old-1 与新列严格同义,新列权重置 0 即可保住旧行为 ——
        这与 `_self_column_map` 的"整体右移 motion_emb"是两种不同情形:
        global 后面**没有** embedding 块,不能套用列号平移。

        **不包括 `proj_enc`**:它的 7 维旧输入在 v17 之前恒为全零(docs §17.2),
        而常数输入不会给权重任何梯度 ⇒ 那层权重等于随机初始化。
        迁移它等于把随机数搬来搬去,没有意义,见 `warm_start` 里的显式重置。

        拒绝条件:旧宽度 >= 新宽度(不是扩容,语义未知)或旧宽度 <= 0。
        返回 {参数名: 旧宽度},只包含可安全前缀拷贝的参数。
        """
        out: Dict[str, int] = {}
        key = "global_enc.0.weight"
        new_in = int(self.global_enc[0].weight.shape[1])
        old = sd.get(key)
        if old is not None:
            old_in = int(old.shape[1])
            if 0 < old_in < new_in:
                out[key] = old_in
                if verbose:
                    print(f"[i] warm_start:{key} 输入宽 {old_in} -> {new_in},"
                          f"按前缀迁移,新增 {new_in - old_in} 列权重置 0", flush=True)
            elif old_in > new_in and verbose:
                print(f"[w] warm_start:{key} 旧输入宽 {old_in} 大于当前 {new_in},"
                      f"无法迁移", flush=True)
        return out

    # self 向量各段的绝对起点。新列一律**追加在 self 尾部**,motion_emb 整体后移,
    # 所以旧 checkpoint 的前 old_self 列与新 self 的前 old_self 列严格一一对应,
    # emb 只需整体平移。v16 把 SELF_DIM 从 37 扩到 40(追加 atk_range/gravity/
    # slide_speed),这条规则让旧权重全部可救,而不是整体作废。
    # 顺序**必须**与 obs_builder 的拼接顺序一致。
    SELF_SEGMENTS = ("base", "combat", "maneuver")

    def _infer_old_self(self, sd: Dict[str, torch.Tensor]) -> Optional[int]:
        """从**旧 checkpoint** 的 self_enc 输入宽度反推旧 SELF_DIM。

        **不能**用当前的 `obs_builder.SELF_DIM` 去反推旧布局 —— self 一扩容
        (v15→v17 从 37 涨到 50),旧宽度会被算错,而且 opp_enc 的形状刚好对得上、
        不报错,结果是静默错位(正是 warm_start 存在的意义所在,不能自己犯)。

        推导:old_in = old_self + motion_embed  =>  old_self = old_in - motion_embed
        校验:old_self 必须落在 [1, 当前 SELF_DIM] 内,否则返回 None(拒绝而非猜)。
        """
        w = sd.get("self_enc.0.weight")
        if w is None:
            return None
        old_self = int(w.shape[1]) - self.motion_embed
        if not (1 <= old_self <= obs_builder.SELF_DIM):
            return None
        return old_self

    def _self_column_map(self, sd: Dict[str, torch.Tensor]
                         ) -> Optional[List[Tuple[int, int]]]:
        """self_enc 输入 = [self(SELF_DIM), motion_emb]。

        旧 checkpoint 的 self 列数 < 新列数时,前 old_self 列按位置对齐,
        motion_emb 整体右移 delta;emb 宽度两侧必须一致,否则放弃迁移
        (宁可作废也不能静默把 motion 分量塞进状态列)。
        """
        key = "self_enc.0.weight"
        old = sd.get(key)
        if old is None:
            return None
        old_total, new_total = int(old.shape[1]), int(self.self_enc[0].weight.shape[1])
        new_self = obs_builder.SELF_DIM
        delta = new_total - old_total
        if delta < 0 or delta >= new_self:
            return None
        old_self = new_self - delta
        old_emb = old_total - old_self
        if old_emb != new_total - new_self:
            return None            # emb 宽度不一致 -> 拒绝
        if old_emb < 0:
            return None
        # map 是 (新列, 旧列)。old_self 段两边列号相同;
        # motion_emb 段:旧列 old_self+k -> 新列 new_self+k。
        m = [(i, i) for i in range(old_self)]
        m += [(new_self + k, old_self + k) for k in range(old_emb)]
        return m

    def _opp_column_map(self, sd: Dict[str, torch.Tensor],
                        old_self: Optional[int] = None,
                        verbose: bool = True) -> Optional[List[Tuple[int, int]]]:
        """opp_enc 输入 = [opp(OPP_DIM), motion_emb],按列名对齐。

        旧版本只有 OPP_TAIL_V12 两列(rel_dx/rel_dy),且语义与现在的 dx/dy 相同
        (rel_dx 文档定义就是 |x_opp-x_self|/width,已验证与 build() 的 dx 等价)。
        """
        key = "opp_enc.0.weight"
        old = sd.get(key)
        if old is None:
            return None
        # 旧布局必须用**旧** SELF_DIM 反推,不能用当前值:
        #   old_in = old_self + old_tail + motion_embed
        # 用当前 SELF_DIM 会在 self 扩容后把 old_tail 算小,恰好命中 V12 分支,
        # 而形状又对得上 —— 静默错位。推不出 old_self 就拒绝。
        old_in = int(old.shape[1])
        if old_self is None:
            if verbose:
                print("[w] warm_start: 无法从 self_enc 反推旧 SELF_DIM,"
                      "opp_enc 保持随机初始化", flush=True)
            return None
        old_tail = old_in - old_self - self.motion_embed
        if old_tail < 0:
            return None
        if old_tail == len(obs_builder.OPP_TAIL):
            old_names = list(obs_builder.OPP_TAIL)
        elif old_tail == len(obs_builder.OPP_TAIL_V12):
            # 旧版 5 列之前的布局:rel_dx/rel_dy 与现在的 dx/dy 同义,其余三列是新增
            old_names = list(obs_builder.OPP_TAIL_V12)
        else:
            if verbose:
                print(f"[w] warm_start 未知的旧 opp 布局(in={old_in}, "
                      f"embed={self.motion_embed}, tail={old_tail}),"
                      f"opp_enc 保持随机初始化", flush=True)
            return None
        mapping: List[Tuple[int, int]] = [(i, i) for i in range(old_self)]
        for oi, name in enumerate(old_names):
            target = obs_builder.OPP_TAIL_ALIAS.get(name, name)
            if target in obs_builder.OPP_TAIL:
                mapping.append((obs_builder.SELF_DIM + obs_builder.OPP_TAIL.index(target),
                                old_self + oi))
        base_new = obs_builder.OPP_DIM
        base_old = old_self + old_tail
        mapping += [(base_new + i, base_old + i) for i in range(self.motion_embed)]
        if verbose:
            migrated = {obs_builder.OPP_TAIL_ALIAS.get(n, n) for n in old_names}
            added = [n for n in obs_builder.OPP_TAIL if n not in migrated]
            if added:
                print(f"[i] warm_start:opp 新增列 {added} 权重置 0,"
                      f"其余按列名迁移", flush=True)
        return mapping


@dataclass
class LoadResult:
    """权重加载结果。`migrated=True` 表示走过按列迁移(形状变了,优化器状态不可复用)。"""
    skipped: List[str] = None
    migrated: bool = False

    def __post_init__(self):
        if self.skipped is None:
            self.skipped = []


def load_weights(net: "PolicyNet", sd: Dict[str, torch.Tensor],
                 verbose: bool = True) -> LoadResult:
    """优先整表加载;形状不兼容时退化为 `warm_start` 的按列迁移。

    `skipped` 非空表示有参数没能迁移(会被随机初始化);`migrated` 表示至少有一层
    的形状变了 —— 这种情况下 Adam 的动量张量形状对不上,必须连同优化器状态一起丢弃。
    """
    try:
        net.load_state_dict(sd)
        return LoadResult()
    except RuntimeError as exc:
        if verbose:
            print(f"[i] 权重形状与本网络不同({type(exc).__name__}),改用 warm_start 迁移",
                  flush=True)
        return LoadResult(skipped=net.warm_start(sd, verbose=verbose), migrated=True)


def to_batch(obs: Dict[str, object], device: str = "cpu") -> Dict[str, torch.Tensor]:
    """把单个观测(或带时间维的观测序列)转成张量字典。"""
    def t(key, dtype=torch.float32):
        # from_numpy 零拷贝;dtype 已由 obs_builder 保证是 float32 / int64
        arr = obs[key]
        if not isinstance(arr, np.ndarray):
            arr = np.asarray(arr)
        tt = torch.from_numpy(arr)
        if tt.dtype != dtype:
            tt = tt.to(dtype)
        return tt.to(device)

    return {
        "self": t("self"), "opp": t("opp"), "global": t("global"),
        "proj": t("proj"), "proj_mask": t("proj_mask"),
        "motion_self": t("motion_self", torch.long),
        "motion_opp": t("motion_opp", torch.long),
        "action_mask": t("action_mask"),
    }


@dataclass
class ActorCritic:
    """训练器用的薄封装:持有网络与设备,提供单步推理与批量评估。"""
    net: PolicyNet
    device: str = "cpu"

    @classmethod
    def build(cls, cfg: Optional[Dict] = None) -> "ActorCritic":
        return cls(net=PolicyNet(cfg), device="cpu")

    def parameters(self):
        return self.net.parameters()

    def state_dict(self):
        return self.net.state_dict()

    def load_state_dict(self, sd):
        self.net.load_state_dict(sd)

    def train(self):
        self.net.train()

    def eval(self):
        self.net.eval()
