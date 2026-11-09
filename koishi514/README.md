# koishi514 — 《东方凭依华》(TH15.5) 古明地恋 TH13.5 符卡与 LastWord 替换 Mod

本项目位于 `D:\programme\155rl\koishi514`，保留 TH15.5 原版古明地恋的所有普通技、必杀技与连段（`koishi_base.nut` 与 `koishi_combo.nut` 与原版逐字节一致），仅将选人界面的前两张符卡与 LastWord 替换为《东方心绮楼》(TH13.5) 古明地恋的专属符卡与终符。

---

## 替换内容一览

| 槽位 / 类型 | 原版 TH15.5 | 替换后 (源自 TH13.5) | 关键函数与动画 ID |
| :--- | :--- | :--- | :--- |
| **普通技 / 必杀技** | TH15.5 原版技能 | **未改动**（完全保留原版） | `koishi_base.nut` / `koishi_combo.nut` 与原版逐字节一致 |
| **符卡槽 0 (`spell_a`)** | 抑制「スーパーエゴ」 | **本能「フロウディアン」** (TH13.5 Boss 符卡) | `Spell_A_Init` (`4910`) + `BossA_*` (`7910`~`7912`) |
| **符卡槽 1 (`spell_b`)** | 本能「イドの解放」 | **茨符「コンファインドイノセント」** (TH13.5 Boss 符卡) | `Spell_B_Init` (`4920`) + `BossB_Shot` (`7920`~`7921`) |
| **符卡槽 2 (`spell_c`)** | 夢符「ご先祖様が見ているぞ」 | **夢符「ご先祖様が見ているぞ」**（保留原版） | `Spell_C_Init` (`4020`) |
| **LastWord (终符)** | ＊今から電話をするから出てね＊ | **「ブランブリーローズガーデン」** (TH13.5 LastWord) | `Spell_Climax_Init` (`4900`→`4901`→`4902`) + `Climax_*` (`7900`~`7904`) |

---

## 目录结构与改动文件

```text
koishi514/
├── README.md                                                 # 本说明文件
├── apply_koishi514_mod.py                                    # 磁盘同步 + 运行时 Pak 哈希拦截与门禁补丁脚本
└── mod/
    └── data/
        ├── actor/
        │   ├── koishi.nut                                    # 替换 Spell_A_Init / Spell_B_Init / Spell_Climax_*，保留原版普通技/必杀技
        │   ├── koishi_base.nut                               # 与原版 TH15.5 逐字节一致
        │   ├── koishi_combo.nut                              # 与原版 TH15.5 逐字节一致
        │   ├── koishi_shot.nut                               # 在原版基础上追加 BossA_* / BossB_* / Climax_* 弹幕与演出函数
        │   └── koishi/
        │       ├── koishi.pat                                # 合入 TH13.5 动画 4900..4920, 7900..7921 并保留原版 3904..3912
        │       ├── climaxBack.png                            # TH13.5 终符背景贴图
        │       ├── climaxCutA0000.png                        # TH13.5 终符立绘贴图
        │       └── texture/
        │           ├── climaxBack.png
        │           └── climaxCutA0000.png
        ├── spell/
        │   └── koishi.csv                                    # TFCS 符卡表（槽 0/1 替换为 TH13.5 Boss 符卡名称与消耗）
        └── system/
            └── select/
                └── script/
                    └── animation_spell.nut                   # 选人界面古明地恋符卡立绘/名称显示映射
```

---

## 使用方法

1. 启动 `th155.exe`（停留在标题或菜单界面，尚未进入选人/对战前执行最佳）。
2. 运行注入与同步脚本：
   ```bash
   python D:\programme\155rl\koishi514\apply_koishi514_mod.py --game-dir "D:\[th15.5] 东方凭依华（英文版+日文版）"
   ```
3. 如需在不重启游戏的情况下恢复原版 Pak 映射：
   ```bash
   python D:\programme\155rl\koishi514\apply_koishi514_mod.py --restore
   ```
