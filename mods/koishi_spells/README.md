# 古明地恋《东方心绮楼》(TH13.5) Boss 普通符卡独立替换 Mod

本项目将《东方凭依华》(TH15.5) 古明地恋的前两张普通符卡替换为《东方心绮楼》(TH13.5) 古明地恋专属 Boss 符卡。

---

## 替换内容

| 符卡槽位 | 原版 TH15.5 | 替换后 (源自 TH13.5) | 消耗 | 技能特性 |
| :--- | :--- | :--- | :--- | :--- |
| **符卡槽 0 (`spell_a`)** | 抑制「スーパーエゴ」 | **本能「フロウディアン」** | 1000 SP | 双阶段向心斥力/引力弹幕爆发 (`Spell_A_Init` + `Spell_BossA2`) |
| **符卡槽 1 (`spell_b`)** | 本能「イドの解放」 | **茨符「コンファインドイノセント」** | 1000 SP | 全屏荆棘蔷薇荆棘陷阱 (`Spell_B_Init` + `BossB_Shot`) |
| **符卡槽 2 (`spell_c`)** | 夢符「ご先祖様が見ているぞ」 | **夢符「ご先祖様が見ているぞ」** | 500 SP | 完全保留 TH15.5 原版 |

---

## 目录结构

```text
mods/koishi_spells/
├── README.md                          # 本说明
├── apply_koishi_spells.py             # 游戏运行时注入与文件同步脚本
└── mod/
    └── data/
        ├── actor/
        │   ├── koishi.nut             # 替换 Spell_A_Init, Spell_BossA2, Spell_B_Init
        │   ├── koishi_shot.nut        # 追加 BossA_* 与 BossB_* 弹幕判定
        │   ├── koishi_base.nut        # 保持原版一致
        │   ├── koishi_combo.nut       # 保持原版一致
        │   └── koishi/                # 包含 BossA/BossB 动画与贴图
        ├── font/
        │   └── spell_font.bmp         # 黑体符卡字模
        ├── spell/
        │   └── koishi.csv             # 符卡选人界面名称与消耗定义
        └── system/
            └── select/
                └── script/
                    └── animation_spell.nut # 选人界面符卡展示映射
```

---

## 使用方法

1. 启动游戏 `th155.exe`。
2. 运行同步与注入脚本：
   ```powershell
   python d:\programme\155rl\mods\koishi_spells\apply_koishi_spells.py
   ```
3. 如仅需同步文件：
   ```powershell
   python d:\programme\155rl\mods\koishi_spells\apply_koishi_spells.py --sync-only
   ```
