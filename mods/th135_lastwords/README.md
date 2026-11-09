# 《东方心绮楼》(TH13.5) 全 10 角色 LastWord (终符) 替换 Mod

本项目面向《东方凭依华》(TH15.5)，将全部 10 名心绮楼原生登场角色的 LastWord（终符）完整替换为《东方心绮楼》(TH13.5) 人气爆发终符演出与判定。

**纯正性保证**：所有角色（包括古明地恋）的所有普通技、必杀技、怪奇技以及 3 张常规符卡（Spell A/B/C）**100% 保持 TH15.5 原版未动**。

---

## 终符列表与对照

| 角色 | 英文名 | 原版 TH15.5 终符 | 替换后 (源自 TH13.5 心绮楼终符) | 演出与机制特性 |
| :--- | :--- | :--- | :--- | :--- |
| **博丽灵梦** | `reimu` | ＊あんな隙間に巫女がいるわけがない＊ | **「夢想天生」** | 45 Hits 结界向心高速刺入 + 终结白核大爆炸 (60fps Hermite 穿刺) |
| **雾雨魔理沙** | `marisa` | ＊ステキ！ 邪花八段咲き！＊ | **「サングレイザー」** | 骑扫帚流星突进 + 全屏极光星屑冲撞 (4800+ 伤害) |
| **云居一轮** | `ichirin` | ＊時代を見通す２１代目の視力＊ | **「華麗なる親父時代」** | 云山八方连续怒涛百裂老爹连拳 (40+ Hits) |
| **圣白莲** | `hijiri` | ＊極速のシルバーフォール＊ | **「アタルヴァ・ヴェーダ」** | 释迦牟尼圣光法相金轮冲天 + 莲华船突击 (6324 伤害) |
| **物部布都** | `futo` | ＊スペース大火自然＊ | **「大火の改新」** | 旋转烈焰法阵 + 全屏地火焚烧 |
| **丰聪耳神子** | `miko` | ＊我が手足となれ、死者の軍勢＊ | **「承詔必謹」** | 帝王冠冕光晕 + 仙道巨剑全屏斩裂 (7872 伤害) |
| **河城荷取** | `nitori` | ＊オカルトアニマル空を飛ぶ！＊ | **「超特大ジャイアントピラニア」** | 螺旋桨潜水装甲 + 超特大食人鱼火箭撕咬 (4340+ 伤害) |
| **二岩猯藏** | `mamizou` | ＊オカルトマニアの孤独な散歩＊ | **「八百八狸囃子」** | 狸猫大军八百八欢腾合奏擂鼓 (7473 伤害) |
| **秦心** | `kokoro` | ＊夜の仮面が割れるとき＊ | **「仮面喪心舞　暗黒能楽」** | 四情绪假面轮转暗黑结界绝望狂舞 (7500 伤害) |
| **古明地恋** | `koishi` | ＊今から電話をするから出てね＊ | **「ブランブリーローズガーデン」** | 荆棘蔷薇花园闭锁 + 爱心大爆破 (6855 伤害) |

> 注：若需要古明地恋的《心绮楼》Boss 普通符卡（本能「フロウディアン」与茨符「コンファインドイノセント」），请使用独立的 `koishi_spells` Mod。

---

## 目录结构

```text
mods/th135_lastwords/
├── README.md                          # 本说明文件
├── apply_lastwords.py                 # 游戏运行时注入与文件同步脚本
└── mod/
    └── data/
        ├── actor/
        │   ├── futo.nut, futo_shot.nut, futo/
        │   ├── hijiri.nut, hijiri_shot.nut, hijiri/
        │   ├── ichirin.nut, ichirin_shot.nut, ichirin/
        │   ├── kokoro.nut, kokoro_shot.nut, kokoro/
        │   ├── mamizou.nut, mamizou_shot.nut, mamizou/
        │   ├── marisa.nut, marisa_shot.nut, marisa/
        │   ├── miko.nut, miko_shot.nut, miko/
        │   ├── nitori.nut, nitori_shot.nut, nitori/
        │   ├── reimu.nut, reimu_shot.nut, reimu/
        │   └── koishi.nut, koishi_shot.nut, koishi_base.nut, koishi_combo.nut, koishi/
        └── font/
            └── spell_font.bmp         # 包含 531 个黑体（SimHei）符卡名称字模，0 乱码
```

---

## 使用方法

1. 启动游戏 `th155.exe`。
2. 运行同步与注入脚本：
   ```powershell
   python d:\programme\155rl\mods\th135_lastwords\apply_lastwords.py
   ```
3. 如仅需同步文件到游戏目录：
   ```powershell
   python d:\programme\155rl\mods\th135_lastwords\apply_lastwords.py --sync-only
   ```
