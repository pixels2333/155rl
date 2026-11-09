# 交接文档：TH13.5 全角色 LastWord 移植与特效修复 (koishi514)

> **创建时间**：2026-11-09  
> **更新时间**：2026-11-09（全 10 角色 TH13.5 原版终符名、`spell_font.bmp` 字模注入与实机闭环验证）  
> **交接目标**：记录 TH13.5（《东方心绮楼》）向 TH15.5（《东方凭依华》，`koishi514` Mod）移植全角色 LastWord（Climax Spell / 终符）的核心进展、逆向资产、已知约束、阻塞问题、`Manbow::FontTexture` 纹理字库补丁与全角色实机连拍验证结果。

---

## 1. 任务背景与核心目标

### 1.1 项目定位
本项目致力于在 **《东方凭依华》（Touhou 15.5 v1.21b）** 框架下，通过内存 Hook、文件注入与 `.pat`/`.nut` 脚本增强，为全部 10 名原心绮楼出场角色（博丽灵梦、雾雨魔理沙、云居一轮、圣白莲、物部布都、丰聪耳神子、河城荷取、古明地恋、二岩猯藏、秦心）完整移植并还原 **《东方心绮楼》（Touhou 13.5）** 原汁原味的人气爆发终极符卡（LastWord / Climax Spell）。

### 1.2 核心成果总览
目前心绮楼全部 10 名角色的终符卡死、黑块、字模乱码（`「####」`）、缺少注册与动画截断等问题已 **100% 彻底攻克并全量实机闭环**：
1. **博丽灵梦（Reimu）「夢想天生」**：紫粉色 3D 透视符箓预合成、向心飞行轨迹、Take 11 原版 `dmg=100` 属性保真、镜头 `zoom=2.0` 尺度适配与 45 Hits 连击终结全阶段闭环；
2. **物部布都（Futo）「大火の改新」**：起手旋转火焰逆时针 3°/帧角速度校准、移除 `direction` 渲染反转乘算、地面火海持续燃烧判定生效；
3. **`spell_font.bmp` 纹理字库补丁（100% 原版心绮楼终符名称 + 0 `#` 占位符）**：逆向解析 `Manbow::FontTexture` 二进制尾部字模表格式，从 `data/font/font_texture_outline24.bmp` 提取并等比缩放心绮楼终符所需的全部 20 个缺失字模（`生華親父時代ギェ改新詔承必謹慎狸囃仮喪暗`）注入 `data/font/spell_font.bmp`，使全部 10 名角色 100% 恢复 TH13.5 原版字节码中的 `UseClimaxSpell` 名称且实机渲染 0 `#` 乱码；
4. **`.nut` 原版纯正性保真**：严格保持 `.nut` 终符脚本忠实于 TH13.5 原版字节码逻辑，不注入非原版的弹幕发射或强制转向逻辑；
5. **全角色自动化巡检引擎**：突破选人界面网格注入、启动期自动挂载 Pak 红黑树补丁、双方对战状态感知、阶段动作连拍（`start`, `active`, `mid`, `finish`）及伤害/退场状态自动统计。

---

## 2. 当前进展与全角色实机验收总表

### 2.1 全 10 角色实机验证状态表（TH13.5 原版终符名称）

| 角色 | TH13.5 原版终符名称 | 原始故障表现 | 修复手段与实机状态 | 实机伤害 | 字模状态 | 动作退场 |
|---|---|---|---|---|---|---|
| **博丽灵梦 (`reimu`)** | `「夢想天生」` | 释放崩溃、符箓未裁剪大方块、运动轨迹错误、红核过大、「生」字缺字模 | 预合成 3D 斜切透视符箓图集；重构 `.pat` 向心动画；恢复 Take 11 原版属性；Take 9 30帧平滑淡出；修补「生」字模 | **8321** (45 Hits) | 100% 原版名无 `#` | 正常 |
| **物部布都 (`futo`)** | `「大火の改新」` | 终符动画卡死、火焰无特效、旋转方向与速度异常、「改新」缺字模 | 移除废弃 `SetSelfDamage` API，校准 `Climax_RollFire` 为逆时针 3°/帧，重构地面燃烧判定；修补「改新」字模 | **持续燃烧** | 100% 原版名无 `#` | 正常 |
| **雾雨魔理沙 (`marisa`)** | `「サングレイザー」` | 召唤出的迷你魔理沙小人衣服变全黑 | 修复调色板与位深冲突（24-bit RGB -> 32-bit ARGB），小人色彩还原，高空下坠爆炸 | **6474** | 100% 原版名无 `#` | 正常 |
| **河城荷取 (`nitori`)** | `「スーパースコープ３Ｄ」` | 释放后游戏直接卡死、屏幕出现巨大纯白方块 | 修复循环状态跳转；识别 `mask.png` 蒙版，在 `.pat` 中切换为加色混合（Add Blend）消除白底方块 | **1088** | 100% 原版名无 `#` | 正常 |
| **二岩猯藏 (`mamizou`)** | `「八百八狸囃子」` | 变身烟雾与爆炸缺失；原名缺失「狸囃」显示为 `「八百八##子」` | 补充 `smokeb.png` 烟雾与 `firea0000.png` 火光；向 `spell_font.bmp` 注入「狸囃」字模，恢复原名 | **7473** | 100% 原版名无 `#` | 正常 (motion 19) |
| **丰聪耳神子 (`miko`)** | `「承詔必謹」` | 缺失光柱与地面特效；原名 4 字全部缺失显示为 `「####」` | Take 0 补充 `laserbasea000.png` 激光束与爆发；向 `spell_font.bmp` 注入「承詔必謹」字模，恢复原名 | **7872** | 100% 原版名无 `#` | 正常 (motion 0) |
| **秦心 (`kokoro`)** | `「仮面喪心舞　暗黒能楽」` | 终符立绘特写花屏/对齐错位；原名缺失「仮喪暗」显示为 `「#面#心舞　#黒能楽」` | 修复 `climaxCut.dds` 纹理尺寸对齐为 4 像素标准步长 (`1280x1016`)；注入「仮喪暗」字模，恢复原名 | **7500** | 100% 原版名无 `#` | 正常 (motion 0) |
| **云居一轮 (`ichirin`)** | `「華麗なる親父時代」` | 出招表未正确注册、释放时逻辑循环死锁；原名缺失「華親父時代」显示为 `「#麗なる####」` | 补齐出招表动作映射，修复 Take 跳转；向 `spell_font.bmp` 注入「華親父時代」字模，恢复原名 | **900帧强化态** | 100% 原版名无 `#` | 正常 (motion 0) |
| **圣白莲 (`hijiri`)** | `「アタルヴァ・ヴェーダ」` | 原名缺失「ェ」显示为 `「アタルヴァ・ヴ#ーダ」`；`4901` 抓取与 `4902` 莲华船终结特效验证 | 向 `spell_font.bmp` 注入「ェ」字模；保持 `hijiri.nut` 原版纯正性；实机验证 `4900 -> 4901 -> 4902` 完整连段 | **6324** (51 Hits) | 100% 原版名无 `#` | 正常 (motion 19) |
| **古明地恋 (`koishi`)** | `「ブランブリーローズガーデン」` | `koishi.csv` 曾被改为其他名称 | 恢复 TH13.5 原版字节码名称 `「ブランブリーローズガーデン」`（原生字库已含全部假名），空中线绳与金色巨型蔷薇连续绽放 | **6855** | 100% 原版名无 `#` | 正常 (motion 0) |

---

### 2.2 关键代码与资产链接

#### 核心脚本与工具
1. [`patch_all_9_pats.py`](file:///d:/programme/155rl/.scratch/patch_all_9_pats.py)
   - 自动化解析 TH13.5 与 TH15.5 差异，为 9 位心绮楼角色动态追加 Climax 纹理并重构 `.pat` 动画动作块的核心生成脚本（包含全角色 `bmb_fallbacks` 帧定义）。
2. [`patch_spell_font.py`](file:///d:/programme/155rl/.scratch/patch_spell_font.py) & [`font_texture_tool.py`](file:///d:/programme/155rl/.scratch/font_texture_tool.py)
   - 解析 `Manbow::FontTexture`（`data/font/spell_font.bmp`）二进制字模表，从 `data/font/font_texture_outline24.bmp` 提取并等比缩放 20 个缺失字模注入 `spell_font.bmp` Row 1 空闲区（`y=27, x=1222..1352`）。
3. [`restore_canonical_lastwords.py`](file:///d:/programme/155rl/.scratch/restore_canonical_lastwords.py)
   - 批量还原并校验全部 10 名角色 `.nut` 脚本与 `koishi.csv` 中的 TH13.5 原版 LastWord 名称。
4. [`apply_koishi514_mod.py`](file:///d:/programme/155rl/koishi514/apply_koishi514_mod.py)
   - Mod 安装、内存 Hook 与四目录强一致同步脚本。执行 `python apply_koishi514_mod.py --sync-only` 可一键同步全部 322 个 Mod 文件至构建与游戏安装目录。
5. [`verify_multichar_climax.py`](file:///d:/programme/155rl/.scratch/verify_multichar_climax.py)
   - 全自动突破选人、对战感知、终符释放连拍巡检脚本。

#### 角色主控与弹幕脚本
- 灵梦：[`reimu.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/reimu.nut)、[`reimu_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/reimu_shot.nut) & [`reimu.pat`](file:///d:/programme/155rl/koishi514/mod/data/actor/reimu/reimu.pat)
- 布都：[`futo.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/futo.nut) & [`futo_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/futo_shot.nut)
- 白莲：[`hijiri.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/hijiri.nut) & [`hijiri_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/hijiri_shot.nut)
- 神子：[`miko.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/miko.nut) & [`miko_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/miko_shot.nut)
- 一轮：[`ichirin.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/ichirin.nut) & [`ichirin_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/ichirin_shot.nut)
- 猯藏：[`mamizou.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/mamizou.nut) & [`mamizou_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/mamizou_shot.nut)
- 秦心：[`kokoro.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/kokoro.nut) & [`kokoro_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/kokoro_shot.nut)
- 恋：[`koishi.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/koishi.nut)、[`koishi_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/koishi_shot.nut) & [`koishi.csv`](file:///d:/programme/155rl/koishi514/mod/data/spell/koishi.csv)
- 魔理沙：[`marisa.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/marisa.nut) & [`marisa_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/marisa_shot.nut)
- 荷取：[`nitori.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/nitori.nut) & [`nitori_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/nitori_shot.nut)
- 符卡字体纹理：[`spell_font.bmp`](file:///d:/programme/155rl/koishi514/mod/data/font/spell_font.bmp)

#### 逆向分析文档沉淀
1. [`th135_reimu_climax_orig.txt`](file:///C:/Users/Administrator/.gemini/antigravity/brain/75d0cda4-061c-4ecb-af52-75c81f803363/scratch/th135_reimu_climax_orig.txt)
   - TH13.5 原版未修改的 16 个 Climax 相关 Squirrel 源码全文（885 行），包含起手、主阶段、收尾及 4 个 `.bmb` 文件头部二进制 Hexdump。
2. [`bmb_emitter_comparison.txt`](file:///C:/Users/Administrator/.gemini/antigravity/brain/75d0cda4-061c-4ecb-af52-75c81f803363/scratch/bmb_emitter_comparison.txt)
   - 详细解码了 TH13.5 `reimu_tenseiRockShot.bmb` 与 `reimu_TenseiA000.bmb` 的 1168 字节 (`0x490`) Bishamon 3D 粒子发射器节点与曲线池（初速度 $v_0 \approx 21.3 \sim 24.0$、加速度 $a = -8.0$、发射周期 10 帧、存活周期 96 帧）。

#### 图像与视觉参考
1. [`reimu_reimu_tenseiFuda.png`](file:///C:/Users/Administrator/.gemini/antigravity/brain/75d0cda4-061c-4ecb-af52-75c81f803363/scratch/reimu_reimu_tenseiFuda.png)
   - 512x512 三合一图集：左列 `(0, 0, 128, 430)` 封魔光柱；中列 `(128, 0, 128, 224)` 核心博丽灵符御札；右列 `(256, 0, 128, 224)` 黑色球形遮罩。
2. [`reimu_reimu_tenseiA000.png`](file:///C:/Users/Administrator/.gemini/antigravity/brain/75d0cda4-061c-4ecb-af52-75c81f803363/scratch/reimu_reimu_tenseiA000.png)
   - 256x256 鲜红色放射状能量球体。
3. [`media_1794147029589_67b4a27c.jpg`](file:///C:/Users/Administrator/.gemini/antigravity/brain/75d0cda4-061c-4ecb-af52-75c81f803363/.user_uploaded/media_1794147029589_67b4a27c.jpg)
   - 用户提供的 TH13.5 Ver1.33 原版实机抓图：中心鲜红光芒白核、灵梦前置切入立绘、右下角高亮透视穿刺御札与 26 Hits 连续受创画面。

#### 实机抓拍验收图库（存放在 `d:\programme\155rl\.scratch\`）
- 灵梦：`v3d_phase1.png`, `v3d_phase2_a.png` ~ `d.png`, `v3d_finish.png`
- 魔理沙：`verify_marisa_start.png`, `verify_marisa_active.png`, `verify_marisa_mid.png`
- 荷取：`verify_nitori_start.png`, `verify_nitori_active.png`, `verify_nitori_mid.png`
- 布都：`futo_roll_a.png` ~ `futo_roll_d.png`
- 神子：`verify_miko_start.png`, `verify_miko_active.png`, `verify_miko_mid.png`（`「承詔必謹」` 0 `#` 乱码，7872 伤害）
- 秦心：`verify_kokoro_start.png`, `verify_kokoro_active.png`, `verify_kokoro_mid.png`, `verify_kokoro_finish.png`（`「仮面喪心舞　暗黒能楽」` 0 `#` 乱码，7500 伤害）
- 一轮：`verify_ichirin_start.png`, `verify_ichirin_active.png`, `verify_ichirin_mid.png`, `verify_ichirin_finish.png`（`「華麗なる親父時代」` 0 `#` 乱码）
- 猯藏：`verify_mamizou_start.png`, `verify_mamizou_active.png`, `verify_mamizou_mid.png`, `verify_mamizou_finish.png`（`「八百八狸囃子」` 0 `#` 乱码，7473 伤害）
- 白莲：`verify_hijiri_start.png`, `verify_hijiri_active.png`, `verify_hijiri_mid.png`, `verify_hijiri_finish.png`（`「アタルヴァ・ヴェーダ」` 0 `#` 乱码，`4901->4902` 莲华船 6324 伤害）
- 恋：`verify_koishi_start.png`, `verify_koishi_active.png`, `verify_koishi_mid.png`, `verify_koishi_finish.png`（`「ブランブリーローズガーデン」` 0 `#` 乱码，6855 伤害）

---

## 3. 核心机制还原与疑难攻克

### 3.1 灵梦「夢想天生」：2D 模拟 3D Bishamon 粒子系统
1. **引擎缺失 3D 粒子系统导致必须用 2D 模拟**：
   - TH13.5 底层集成了商业级 3D 粒子中间件 **Matchlock Bishamon 3**（`Manbow::AnimationControllerBM`, `bmsln`, `IBMB`）；
   - TH15.5 黄昏在引擎层中**完全剔除了 Bishamon 运行时**，无法直接加载 `.bmb`，必须在 `.pat` 精灵动画层通过 2D/2.5D 图层等价表达。
2. **镜头 `zoom = 2.0` 与可视范围错位问题**：
   - 在 `Climax_A1` 主阶段前 180 帧，脚本执行了 `::camera.SetTarget(cameraPos.x, cameraPos.y, 2.0, true)`，摄像机放大了整整 2 倍；
   - 镜头 2 倍放大后，原本 `1280x720` 的屏幕实际可视范围收缩为以对手为中心的 `640x360`（半径约 180~320 像素），符箓发射半径与存活时间必须与视野严格匹配。
3. **3D 梯形透视与轴向斜切（Oblique Perspective Shear）**：
   - 原版截图中的御札呈现近宽远窄与轴向倾斜视角；
   - 在贴图空闲区域预烘焙 3D 透视梯形收窄与紫粉色拖尾光带，避免了运行时仅靠 2D 缩放带来的矩形单薄感。

---

### 3.2 灵梦「夢想天生」：`.nut` 保持 TH13.5 原版纯正性

**原版事实（逐函数核对 `th135_reimu_climax_orig.txt`）**：

| 函数 | 原版实际内容 |
|---|---|
| `Climax_LockA` | `SetMotion(7900, 9)`；`EnableTimeStop(false)`；`keyAction = ReleaseActor`；`stateLabel` 仅 `count++`，`>=30` 时 `ReleaseActor` |
| `Climax_LockA2` | 整段只有 owner 上溯、`SetMotion(7900, 12)`、`EnableTimeStop(false)`、`keyAction = ReleaseActor`。无缩放、无旋转、无位移、无 `stateLabel` |
| `Climax_LockC_Shot` | `SetMotion(7900, 10)`；`keyAction = ReleaseActor`；`flag1 = 45`；`PlaySE(1191)`；`stateLabel` 只做 `count++`，到 `flag1` 时 `count=0; flag1 *= 0.85; tointeger(); 下限 10; PlaySE(1191)` |
| `Climax_LockC_ShotB` | `SetMotion(7900, 10)`；`keyAction = ReleaseActor`；`flag1 = 45`；`sy = 2.0; sx = 2.0; rz = 180°` |

- 全脚本中经 `SetFreeObject` 创建的只有 5 个类：`Climax_LockA`、`Climax_LockC_Shot`、`Climax_LockC_ShotB`、`Climax_Face`、`Climax_FinishExp`。
- **视觉由 `.pat` 承担，`.nut` 不额外注入虚构发射逻辑或非原版朝向覆写逻辑**。

---

### 3.3 灵梦「夢想天生」：BMB 逆向、60fps 高速飞入与平行四边形尺寸校准（2026-11-09 第四轮）

针对前版紫色符箓飞行过慢（`active_dur=16, dur=2` 合 32 帧飞入）及头窄尾宽梯形尺寸与原版不符的问题，结合 IDA Pro 逆向 `th135.exe`（`sub_454D20` / `sub_4588B0` / `sub_48FA70`）与 `reimu_tenseiRockShot.bmb` 二进制曲线池完成精确校准：
1. **`reimu_tenseiRockShot.bmb` 真实参数还原（详见 [`.scratch/bmb_re/report.md`](file:///d:/programme/155rl/.scratch/bmb_re/report.md)）**：
   - 节点树：`Root@0x80` -> `Null@0x310`（缩放 `s = 30 ± 6`，每 7 帧生成 1 组） -> `Null@0x5A0`（包含 `Q0@0x830` "札本体" 与 `Q2@0x1150` "尻尾"）；
   - **飞入速度**：`Null@0x5A0` 在第 14~19 帧通过 Hermite 样条（切线 `24.028`）在 **5~6 个游戏帧**内从外圈 `r = 240`（`8 * 30`）高速射入中心（逐帧半径：`240 -> 151 -> 87 -> 45 -> 19 -> 5.5`，步长高达 `89 -> 64 -> 42 -> 26 -> 14` 单位/帧），前版 32 帧慢了约 6 倍；
   - **几何形状与尺寸**：`Q0`（`+0x478 == 1`，`sub_48FA70`）为 3D 平面四边形，在正交 3×3 旋转矩阵下投影为**两侧严格平行的斜平行四边形**（而非前版头窄尾宽的楔形），配合 `Q2` 拖尾光带构成长条。在 `2x` 屏幕倍率（Phase 1 `camera.zoom=2.0` 或 Phase 2 `ShotB` `sx=sy=2.0`）下，1280×720 屏幕法向条宽恒为 `23.0 px`、前端水平斜切边宽 `64 px`、符身长 `223 px`。
2. **`reimu.pat` Take 10 与 `reimu_tenseiFuda.png` 修正（[`patch_all_9_pats.py`](file:///d:/programme/155rl/.scratch/patch_all_9_pats.py)）**：
   - `build_enhanced_reimu_tensei_fuda`：将符身半宽固定为平行 `half_w = 18.0`（`shear_k = 2.20`, `H_body = 310.0`），并校准紫粉色彩曲线以匹配 TH13.5 实机 RGB 分位数（`R=[221,250,255], G=[76,157,219], B=[199,242,255]`）；
   - `bmb_fallbacks[(7900, 10)]`：改为 `nf10 = 30, dur = 1`（**60fps 逐帧精度**），每条符箓流按 6 帧 Hermite 曲线（`r_frac: 1.000 -> 0.640 -> 0.415 -> 0.220 -> 0.085 -> 0.015`，`sx=0.32, sy=0.36`）高速穿刺入核；
   - 6 条符箓流角度取模 `180°` 余数互不相同（`[21°, 148°, 46°, 124°, 173°, 78°]`），消除 Phase 2 中 `Climax_LockC_Shot`（`rz=0°`）与 `Climax_LockC_ShotB`（`rz=180°`）同角度叠印现象，并根据象限符号自动对齐前端水平斜切边。
3. **原版属性保真与边缘羽化**：
   - 保留 Take 11 原版 `dmg=100` 与 Take 10 原版标志位，Take 9 设为 15 帧 × dur 2 = 30 帧，Take 11 最大缩放控制在 `sx=3.2`。
4. **实机验证**：
   - 通过 [`verify_3d_fuda.py`](file:///d:/programme/155rl/.scratch/verify_3d_fuda.py) 实机验证并通过用户视觉确认。

---

### 3.4 物部布都「大火の改新」：旋转火焰方向与角速度校准（2026-11-09）

针对布都释放 LastWord 时起手旋转火焰（`Climax_RollFire`）视觉表现异常的问题：
1. **根因定位（逆向 BMB 与贴图比对）**：
   - TH13.5 原版 Squirrel 脚本中并不包含代码级旋转逻辑，旋转完全由 Bishamon 3 粒子文件 `futo_rollFire.bmb` 驱动。
   - 前序移植在 [`futo_shot.nut`](file:///d:/programme/155rl/koishi514/mod/data/actor/futo_shot.nut) 中手工添加了 `this.rz += 0.25 * this.direction;`，存在三处明显缺陷：
     - **方向与贴图不符**：`rollFire.png` 为 C 形火环，亮头在下、尾巴在右上，在屏幕上应呈**逆时针旋转**；原写法朝右时为顺时针。
     - **旋转方向反转**：TH15.5 Manbow 引擎对所有 Actor 的 `this.rz` 会在渲染管线中自动按 `direction` 镜像翻转，脚本层再度乘算 `this.direction` 导致一侧方向相反。
     - **转速严重过快**：`0.25 rad/frame` 约合每帧 14.3°；解析 `futo_rollFire.bmb` 得知粒子 Z 轴角速度为每帧 3°（随机 ±1°），前版转速快了近 5 倍。
2. **代码校准与编码安全**：
   - 调整为 `this.rz -= 3.0 * 0.01745329;`，保持纯 ASCII 注释，严格遵循 CP932 编码规范保存。
3. **实机验证**：
   - 编写自动化脚本 [`verify_futo_rollfire.py`](file:///d:/programme/155rl/.scratch/verify_futo_rollfire.py) 突破菜单选择 1P 布都并进入战斗释放终符；
   - 连续抓取 4 帧截图（`futo_roll_a.png` ~ `futo_roll_d.png`），实机证实火焰呈平滑逆时针旋转、速度匀称，后续地面火海判定正常结算。

---

### 3.5 `Manbow::FontTexture` 逆向与 `spell_font.bmp` 缺失字模注入（彻底解决 `#` 占位符）

1. **问题根因**：
   - TH15.5 中战斗画面左上角/右上角的符卡名称由 `data/script/font.nut` 加载 `data/font/spell_font.bmp` 并通过 `this.font.CreateSpellString(text)` 渲染；
   - 原版 `spell_font.bmp` 仅预烘焙了 511 个字模，缺失心绮楼终符名称中的 20 个字符（`生華親父時代ギェ改新詔承必謹慎狸囃仮喪暗`）。当引擎在 `spell_font.bmp` 的 CP932 二分查找表中找不到字符时，会统一回退为 `#`（`0x23`）。
2. **`Manbow::FontTexture` 二进制格式逆向**：
   - 文件由**标准 32-bpp 未压缩 BMP**（`bfSize = 524342`，`2048x64`）与紧跟在 `bfSize` 之后的**自定义字模索引尾部（Trailer）**组成：
     - **头部（4 字节 `<BBH`）**：`font_size = 21`, `unk = 2`, `glyph_count = 511`；
     - **有序编码表（`glyph_count * 2` 字节 `<H`）**：按升序排列的 `uint16` CP932（Shift-JIS）双字节整数值（单字节 ASCII 位于低字节），供 C++ 引擎执行二分查找；
     - **字模记录表（`glyph_count * 8` 字节 `<HHBBBB`）**：每条记录为 `(x, y, w, h, ofs, ch)`：
       - 每个 `(x, y)` 矩形网格利用 BGRA 4 个颜色通道（`ch = 0..3`）叠加存储 4 个独立字模的灰度掩码（白色字芯为 `255`，黑色描边光晕为 `1..254`，透明背景为 `0`）；
       - 第 0 行使用 `y = 65535`（即 `-1`，顶部裁剪 1 像素，实际像素从 BMP 行 `0` 开始，高 `h - 1`）；第 1 行使用 `y = 27`（实际像素从 BMP 行 `y + 1 = 28` 开始，高 `h - 1 = 27`）。
3. **黑体（SimHei）字模软掩码渲染与注入（[`patch_spell_font_simhei.py`](file:///d:/programme/155rl/.scratch/patch_spell_font_simhei.py)）**：
   - 原版 `spell_font.bmp` 采用无衬线黑体（Gothic/Hei）字形，而 `font_texture_outline24.bmp` 为明朝体/衬线体；
   - 使用系统黑体 `C:\Windows\Fonts\simhei.ttf` 拟合原版 `spell_font.bmp` 单通道软掩码参数（`SIZE=23, BASE_Y=27, A=0.60, B=1.80, SIGMA=1.60, GAIN=0.80`，字芯峰值 `254` + 高斯柔化描边光晕），将 20 个缺失字模写入 `spell_font.bmp` 第 1 行空白区（`y=27, x=1222..1352, ch=0..3`），重排生成包含 531 个字模的有序尾部表；
   - 注意：`data/script/font.nut` 在游戏进程启动极早期即加载 `data/font/spell_font.bmp`，因此 [`game_session.py`](file:///d:/programme/155rl/scripts/game_session.py) 在拉起 `th155.exe` 后 0.6s 自动调用 `apply_koishi514_mod.py` 将 Pak 红黑树节点覆写为磁盘直读，确保新字库在初始化前生效。

---

## 4. 关键技术决策与隐性约束 (Decisions & Constraints)

### 4.1 编码铁律
- **必须使用 CP932 (Shift-JIS) 编码保存所有 `.nut` 脚本**。
- 严禁在 `.nut` 代码或注释中写入非 CP932 字符（如简体汉字“爆发”、“向心”等）。Squirrel 解释器在遇到非法多字节序列时会直接抛出解析异常，导致游戏初始化失败。所有代码注释建议统一使用纯 ASCII / 英文。

### 4.2 C++ 引擎与内存安全禁区
1. **渲染链表完整性**：
   - 严禁在 `stateLabel` 帧循环中反复高频调用 `this.DrawActorPriority(...)`。该函数会在 Manbow 底层双向链表中移除并重新插入节点，高频调用会导致堆破坏引发 `0xc0000409 (FAST_FAIL)` 崩溃。只能在 Actor 初始化时调用一次。
2. **Actor 空间索引一致性**：
   - 在移动 Actor 时，优先使用物理初速度 `this.SetSpeed_XY(vx, vy)`，由引擎原生负责位移更新；
   - 如需设置初始出生点，在初始化阶段单次调用 `this.Warp(x, y)`，避免在每帧 `stateLabel` 中直写 `this.x = ...`。
3. **避免跨对象空指针**：
   - 子物体/自由弹幕对象自身的 `this.target` 通常为空，获取受害者坐标时必须严格通过 `this.owner.target.x / y`（并增加存在性判断），严禁直写 `this.target.x`。
4. **移除废弃 API**：
   - TH15.5 不存在 `this.target.SetSelfDamage`，不可盲目照搬 13.5 源码中的专属接口。

### 4.3 四目录强一致同步铁律
每次修改 `d:\programme\155rl\koishi514\mod\` 中的任何 `.nut` 脚本、`.pat` 动画或 `spell_font.bmp` 资产后，**必须立即运行以下命令完成全目录同步**：
```powershell
python d:\programme\155rl\koishi514\apply_koishi514_mod.py --sync-only
```
该命令会自动将全部 322 个 Mod 文件镜像同步至：
1. `d:\programme\155rl\koishi514\release\mods\koishi514`
2. `D:\[th15.5] 东方凭依华（英文版+日文版）\mods\koishi514`
3. `D:\[th15.5] 东方凭依华（英文版+日文版）`（游戏根目录）

### 4.4 文件安全准则
- 严禁批量或递归删除文件/目录（禁止 `Remove-Item -Recurse`、`rm -rf`、`del /s` 等）。

---

## 5. Mod 架构解耦：全 10 角色 LastWord Mod 与古明地恋普通符卡 Mod 独立拆分

### 5.1 拆分背景与设计目标
- **用户目标**：项目主轴为**全角色《东方心绮楼》(TH13.5) 终符 (LastWord) 替换**。此前开发中，古明地恋（Koishi）额外将 TH13.5 Boss 版的两张常规符卡（Spell A: 本能「フロウディアン」、Spell B: 茨符「コンファインドイノセント」）也一并替换了，导致选人界面与实战中常规符卡偏离了 TH15.5 原版体验。
- **解耦目标**：
  1. **Mod 组 1：全 10 角色心绮楼 LastWord Mod (`mods/th135_lastwords`)**
     - 整合全部 10 名角色的心绮楼终符（博丽灵梦、雾雨魔理沙、云居一轮、圣白莲、物部布都、丰聪耳神子、河城荷取、二岩猯藏、秦心、古明地恋）；
     - **全角色所有普通攻击、必杀技及 3 张常规符卡 100% 保持 TH15.5 原版**；
     - 针对古明地恋：还原 TH15.5 原版常规符卡 A（抑制「スーパーエゴ」）与常规符卡 B（本能「イドの解放」），剔除 `BossA_*` / `BossB_*` 冗余代码及 Boss 动作（`4910..4929, 7910..7931`），且不覆盖 `data/spell/koishi.csv` 与 `data/system/select/script/animation_spell.nut`，使选人界面与实战中常规符卡完全回退至原版；
     - 包含 531 字模的黑体 `data/font/spell_font.bmp`，彻底消灭所有终符宣卡 `#` 占位符。
  2. **Mod 组 2：古明地恋心绮楼常规符卡独立 Mod (`mods/koishi_spells`)**
     - 将恋恋心绮楼 Boss 符卡（Spell A: 本能「フロウディアン」与 Spell B: 茨符「コンファインドイノセント」）完全抽离为一个独立的扩展 Mod；
     - 包含自身独立的 `koishi.nut`、`koishi_shot.nut`、`koishi.pat`、`data/spell/koishi.csv`、`data/system/select/script/animation_spell.nut` 以及专有贴图资源。

### 5.2 目录结构与管理脚本

| Mod 组 | 路径 | 核心管理脚本 | 说明 |
|---|---|---|---|
| **全角色 LastWord Mod** | `d:\programme\155rl\mods\th135_lastwords\` | [`apply_lastwords.py`](file:///d:/programme/155rl/mods/th135_lastwords/apply_lastwords.py) | 320 个文件，全 10 角色纯净 LastWord，常规符卡为 TH15.5 原版 |
| **古明地恋常规符卡 Mod** | `d:\programme\155rl\mods\koishi_spells\` | [`apply_koishi_spells.py`](file:///d:/programme/155rl/mods/koishi_spells/apply_koishi_spells.py) | 152 个文件，恋恋 TH13.5 Boss 常规符卡 A & B 独立包 |
| **工作区主干 (持续维护)** | `d:\programme\155rl\koishi514\` | [`apply_koishi514_mod.py`](file:///d:/programme/155rl/koishi514/apply_koishi514_mod.py) | 与 `th135_lastwords` 保持同步，作为核心流水线工作区 |

### 5.3 常用操作指令

- **应用/同步纯净 LastWord Mod**：
  ```powershell
  python d:\programme\155rl\mods\th135_lastwords\apply_lastwords.py --sync-only
  ```
- **注入运行中的游戏进程（LastWord Mod）**：
  ```powershell
  python d:\programme\155rl\mods\th135_lastwords\apply_lastwords.py
  ```
- **单独应用古明地恋心绮楼常规符卡 Mod**：
  ```powershell
  python d:\programme\155rl\mods\koishi_spells\apply_koishi_spells.py --sync-only
  ```

### 5.4 自动化实机回归验证
- 运行实机验证脚本：
  ```powershell
  python d:\programme\155rl\.scratch\verify_multichar_climax.py koishi
  ```
- **实机回归结果**：
  - 自动进入 VS COM 战斗并选定古明地恋；
  - 终符 `4900 -> 4901 -> 4902` 蔷薇法阵与玫瑰花瓣完整展开；
  - 命中造成 **6855** 点真实伤害，在 13.22s 优雅回退至动作 0（待机）；
  - 选人界面与实战中恋恋普通符卡完全保持原版抑制「スーパーエゴ」与本能「イドの解放」，无任何资源冲突或崩溃。

---

## 6. BepInEx 风格原生 Mod 加载器 (winmm.dll) 与虚拟文件系统 (VFS) 架构

### 6.1 架构设计背景
此前 Mod 生效依赖于外部 Python 脚本注入内存（或向 `<game>/data` 物理复制文件），存在两大痛点：
1. 玩家必须通过外部脚本或命令行启动，无法直接双击原版 `th155.exe`；
2. 物理复制到 `<game>/data` 会造成文件污染，多 Mod 切换时残留文件容易引发冲突。

为此，我们基于 `winmm.dll` 原生 DLL 代理与 IAT 钩子，开发了**完全类似 BepInEx 的模块化 Mod 加载系统**：
- **零外部依赖**：游戏启动时系统自动加载 `winmm.dll` 代理，完全无需 Python 外部注入；
- **虚拟文件系统 (VFS)**：通过挂钩 `CreateFileA` / `GetFileAttributesA`，在内存中动态将文件请求重定向至生效 Mod 的内部目录，**绝不复制任何文件到游戏根目录 `data/`**；
- **目录规范与清单**：每个 Mod 独立存放在 `mods/<mod_id>/` 下，通过 `mod.ini` 声明元数据（名称、版本、作者、优先级与默认启用状态）；
- **全局一键开关**：通过游戏根目录的 `mod_loader.ini` 的 `[Mods]` 分区可随时开关指定 Mod（`1`=启用, `0`=禁用），无残留、瞬间生效。

### 6.2 核心工程文件
- C++ 加载器源码：[`d:\programme\155rl\koishi514\loader\src\mod_loader.cpp`](file:///d:/programme/155rl/koishi514/loader/src/mod_loader.cpp)
- 代理定义：[`d:\programme\155rl\koishi514\loader\winmm.def`](file:///d:/programme/155rl/koishi514/loader/winmm.def)
- 编译脚本：`cmd /c "vcvars32.bat && nmake"`
- 产物部署：`D:\[th15.5] 东方凭依华（英文版+日文版）\winmm.dll`
- 主配置文件：[`D:\[th15.5] 东方凭依华（英文版+日文版）\mod_loader.ini`](file:///D:/[th15.5]%20东方凭依华（英文版+日文版）/mod_loader.ini)
- Mod 使用手册：[`d:\programme\155rl\mods\README.md`](file:///d:/programme/155rl/mods/README.md)


