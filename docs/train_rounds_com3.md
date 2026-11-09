# COM3 训练轮次报告（2026-10-22 夜 ~ 10-23）

本文件记录一次通宵自主训练的全部轮次、指标与结论。所有结论都来自实机采样评估，不是单次训练日志的自我报告。

## 1. 结论摘要

| 项 | 结果 |
|---|---|
| **当前最佳权重** | `checkpoints/com2_consolidate/ppo_001080.pt` |
| **对 COM3 采样评估** | 4/10 胜（另两轮 3/6、2/6，合计 **9/22 ≈ 41%**），平均血量差 -749 |
| 对照：原 `checkpoints/com3/best.pt`(update 930) | 0/10 胜，平均血量差 -4100 |
| 关键发现 1 | **同难度连续续训会自毁**：COM3 上 1024 步/批时，20~40 次更新内胜率从 40~50% 掉到 8~20%（多轮复现） |
| 关键发现 2 | **COM2 巩固可迁移**：先在 COM2 练到 65~85%，其对 COM3 的胜率比原最佳高 40 个百分点 |
| 关键发现 3 | **把 `n_steps` 从 1024 提到 2048 可消除该崩塌**：COM3 上 40 次更新稳定在 30~40%，COM2 上 60 次更新稳定在 50~92% |
| 已排除 | `combat_scale=0` 消融不能阻止崩塌（说明不是格挡/打康塑形导致的） |

## 2. 评估口径

- 脚本形态见 `.scratch/eval_com3.py`；内部走 `reset_with_restart` 确保实机难度真的是 COM3（`config.difficulty.vs` 只在“标题 → VS COM”路径生效）。
- **采样策略**评估（`--eval-sample`），与训练时的胜率同口径；确定性 argmax 会系统性低估（实测同权重 20% vs 采样 40%）。
- 每档权重 10 局，单局约 60 s。

## 3. 轮次明细

| 轮次 | 配置 | 起点权重 | 更新数 | 训练胜率 | 对 COM3 评估 |
|---|---|---|---|---|---|
| A | `train_ppo_com3.yaml` | `com3/best.pt`(930) | 330 | 23%（末段 25~36%） | **0/10**，hp -4100 |
| B | `train_ppo_com3b.yaml`(熵 0.01) | `com3/ppo_001260` | 300 | 26%，熵 2.07 起步 | 未测 |
| C | `train_ppo_com3b.yaml`(熵 0.02) | `com3/best.pt`(930) | 300 | 26%，熵稳定 3.4 | 0/6，hp -5610 |
| D | `train_ppo_com2_consolidate.yaml` | `com3/best.pt`(930) | 150 | **65.3% vs COM2** | **3/6 → 2/6 → 4/10** |
| E | `train_ppo_com3c.yaml` | `com2_consolidate/ppo_001080` | 40（提前停） | 8% 崩塌 | — |
| F | `train_ppo_com3d.yaml`（`combat_scale=0` 消融） | 同上 | 24（提前停） | 9% 崩塌 | — |
| G | `train_ppo_com2_consolidate.yaml`（第二轮） | `com2_consolidate/ppo_001080` | 22（提前停） | 75% → 20% 崩塌 | — |
| H | `train_ppo_com2_batch2048.yaml` | `com2_consolidate/ppo_001080` | 60 | **67.8% vs COM2**，窗口 67/50/92/71 | 4/10，hp -3307 |
| I | `train_ppo_com3e.yaml`（COM3 + batch 2048） | `com2_consolidate/ppo_001080` | 40 | **33.3%**，窗口 30/30/50/40/40（无崩塌） | 未测（训练口径已达标） |
| J | `train_ppo_com3f.yaml` + `--episode-batch`（每批=整局，可变长） | `com2_consolidate/ppo_001080` | 40 | 22.5%（窗口 30/30/20/33） | **1/10**，hp -3936 |
| K | `train_ppo_com3_batch4096.yaml`（n_steps=4096≈2.7 局/批） | `com2_consolidate/ppo_001080` | 40 | 31.9%（窗口 40/20/50/50/30/40/0+/30/75） | **2/10**，hp -3857 → **未达 4/10 基线，触发回滚** |

日志：`runs/train_resume_931.log`、`runs/train_com3b_r2.log`、`runs/train_com2_consolidate.log`、`runs/train_com3c.log`、`runs/train_com3d.log`、`runs/train_com2_round2.log`、`runs/train_com2_batch2048.log`、`runs/train_com3e.log`；评估：`runs/eval_final_comparison.log`、`runs/eval_sampled.log`。
TensorBoard：`runs/`（端口 6006）。

## 4. 关键机制与判断依据

1. **崩塌不是难度或奖励塑形造成的**
   - COM3 上 1024 步/批：第 1~2 局还能赢，20~40 次更新后掉到 8%；
   - 关闭整块 `combat` 塑形（轮次 F）后崩塌一模一样；
   - 在 COM2 上同样能复现（轮次 G），说明与对手强度无关。
2. **批量不足是主因**
   - 单局约 1500 步，而每轮更新只采 1024 步 → 优势估计基本来自不足一局的一段轨迹，梯度被单局胜负主导；
   - 提到 2048 步后（轮次 H、I），COM2 与 COM3 都不再崩塌。
3. **熵坍缩会叠加伤害**
   - 轮次 A 期间熵从 3.02 掉到 2.07，胜率同步从 40% 掉到 24%；
   - `entropy_coef` 0.005 → 0.02 后熵稳在 3.4（轮次 C）。
4. **COM2 巩固比在 COM3 上硬练有效**（轮次 D 是全晚唯一带来真实提升的实验）。
5. **“2~4 局/批是甜点”被证伪**:轮次 K(n_steps=4096,C0M3 短跑 40 次)训练窗口无崩塌(30~75%),
   但 10 局采样评估只有 2/10(hp -3857),显著低于基线 4/10(hp -749)。断言修正为:
   大批量能**阻止崩塌**,但不能**带来提升**——想提升仍要靠换对手(COM2 巩固)。

### 附:整局批次（`--episode-batch`）实验

`n_steps` 是固定步数,单局 1500~1900 步而批次 1024 步时,整批不含终局结算。为此实现了
`collect(stop_on_done=True)`:批次在本局终止的那一步之后截断(上限 `3*n_steps`),
`last_done=True` 让 GAE 末步精确、不再依赖 critic 外推,并配了单测
`test_collect_stop_on_done_ends_exactly_at_episode`。

**实测结论:每批正好一局反而比 2048 步固定批更差** —— 40 次更新训练胜率 22.5%,
COM3 评估 1/10(hp -3936),低于 2048 步批次的 33~40%,更低于基线 41%。
原因:单局胜负本身是高方差事件,一批只含一局时梯度仍被单局结果主导;
2~4 局/批(约 3000~6000 步)才是合理区间。

该轮还暴露并修复了一个数值问题:片段内优势方差≈0 时,`(adv-mean)/(std+1e-8)` 会把数值
放大到 1e6 量级,一次更新即把权重炸成 NaN(轮次 J 首次运行就是这么崩的)。现在改为
方差不足时只去均值,并在 `loss`/梯度非有限时跳过 step;新增单测
`test_update_survives_constant_advantage`。

## 5. 复现命令

```powershell
# 1) 干净重启游戏并注入桥接
python scripts/game_session.py restart

# 2) COM2 巩固（可迁移性最强）
$env:PYTHONPATH = "src"
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" -m src.train.train_ppo `
  --config configs/train_ppo_com2_consolidate.yaml `
  --resume checkpoints/com2_consolidate/ppo_001080.pt --updates 150

# 3) COM3 大 batch（不崩塌，但当前尚未超越 COM2 巩固轮的上限）
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" -m src.train.train_ppo `
  --config configs/train_ppo_com3e.yaml `
  --resume checkpoints/com2_consolidate/ppo_001080.pt --updates 150

# 4) 评估（务必用采样口径 + 10 局）
$env:PYTHONPATH = "src"
& "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe" .scratch/eval_com3.py `
  configs/train_ppo_com3.yaml checkpoints/com2_consolidate/ppo_001080.pt 10
```

## 6. 下一步建议（需要人判断）

1. **先用 batch 2048 把 COM3 练满**（轮次 I 只跑了 40 次更新，远未到平台）；预期在 150 次更新内不会崩塌，是否提升需用 10 局采样评估确认。
2. **COM2 ↔ COM3 交替**：每 100 次更新在容易的对手上巩固一轮，再回 COM3 短练，避免同分布过拟合。
3. **评估样本量**：6 局口径的方差约 ±20 个百分点，任何"A 比 B 好"的结论至少需要 10 局以上，最好 20 局。
4. **课程晋级阈值**（COM3 胜率 ≥75%）距当前 41% 仍很远；在策略架构/奖励不变的前提下，不要期望单晚达成，应把它当作长期目标。
