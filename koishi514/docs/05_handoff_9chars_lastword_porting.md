# 工作交接文档 (Handoff)：TH13.5 全角色 LastWord（心绮楼终符）跨代移植至 TH15.5（已全部完工）

> **更新时间**：2026-11-08  
> **当前状态**：✅ **100% 完工并已完成 4 大目标目录全量同步验证（共 10 名角色、245 个 Mod 文件）**  
> **关联前序文档**：[`04_mod_porting_lessons_and_pitfalls.md`](file:///d:/programme/155rl/koishi514/docs/04_mod_porting_lessons_and_pitfalls.md)

---

## 1. 任务背景与核心目标

- **原始任务**：参考 [`04_mod_porting_lessons_and_pitfalls.md`](file:///d:/programme/155rl/koishi514/docs/04_mod_porting_lessons_and_pitfalls.md) 中完成古明地恋（`koishi`）跨代移植的方法论，将《东方心绮楼》(TH13.5 v1.34b) 其余 **9 名可用角色**的 **LastWord（Climax Spell / 终符 / 人气爆发技）** 完整移植到《东方凭依华》(TH15.5 v1.21b) 的 `koishi514` Mod 框架中。
- **目标角色列表（共 9 名新增角色 + 已完成的 `koishi`，合计 10 名心绮楼角色）**：
  1. `reimu`（博丽灵梦）— **「梦想天生」**
  2. `marisa`（雾雨魔理沙）— **「サングレイザー」（Sungrazer / 掠日彗星）**
  3. `ichirin`（云居一轮 & 云山）— **「华丽なる亲父时代」（华丽的老爹时代）**
  4. `hijiri`（圣白莲）— **「アグニレイディアンス」（Agni Radiance / 大日如来激光）**
  5. `futo`（物部布都）— **「大火の改新」**
  6. `miko`（丰聪耳神子）— **「诏を承けては必ず慎め」（承诏必慎）**
  7. `nitori`（河城荷取）— **「スーパースコープ 3D」（Super Scope 3D）**
  8. `mamizou`（二岩猯藏）— **「百鬼夜行の大行进」**
  9. `kokoro`（秦心）— **「假面丧心舞 暗黑能乐」**
  10. `koishi`（古明地恋，前序已完工）— **「ブランブリーローズガーデン」（Brambly Rose Garden）+ Boss 符卡 1/2**
- **范围边界（严格遵守）**：
  - **仅移植 LastWord（`Spell_Climax_*` 与 `Climax_*`）**：其余 9 名角色的普通符卡（`Spell_A_Init`、`Spell_B_Init`、`Spell_C_Init`）、怪奇技（`Okult_Init`）及常规必杀技保持 **TH15.5 原版逻辑不变**。
  - **古明地恋（`koishi`）保持已完工状态不变**。

---

## 2. 已完成交付清单（100% 完工）

1. **TH13.5 与 TH15.5 全 9 角色资产解包与提取（100% 完成）**
   - 解包脚本：[`d:\programme\155rl\.scratch\extract_all_9_chars.py`](file:///d:/programme/155rl/.scratch/extract_all_9_chars.py)
   - 提取目录：`D:\programme\155rl\.scratch\th135_all\` 与 `D:\programme\155rl\.scratch\th155_all\`。
2. **全 9 角色 `.pat` 动画补丁与专属贴图转换（100% 完成并通过 `parse_pat_v17` 零误差校验）**
   - 自动化构建脚本：[`d:\programme\155rl\.scratch\patch_all_9_pats.py`](file:///d:/programme/155rl/.scratch/patch_all_9_pats.py)
   - 已生成全部 9 名角色的 `<char>.pat` (`ver=17`)、18 张全屏特写贴图（`climax.png`、`climax_light.png`）及所有 `texture/*.png` 特效贴图（含 TH13.5 专属 8-bit `TFBM` 经 TH13.5 `palette000.bmp` 无损解码转换的 32-bit RGBA `.png` 贴图，以及 `reimu_tenseiA000.png`、`reimu_tenseiFuda.png`）。
   - 所有生成的 `.pat` 均通过 `parse_pat_v17` 严格校验：`pos == len(data)` 且 Climax 动作中 `NONE_sprites = 0`。
3. **Squirrel 3.x 字节码 AST 反编译器与 TH13.5 → TH15.5 引擎自适应转换（100% 完成）**
   - AST 反编译器：[`d:\programme\155rl\.scratch\decompile_sq3_climax.py`](file:///d:/programme/155rl/.scratch/decompile_sq3_climax.py)
   - 全 9 角色 Climax 反编译输出：[`d:\programme\155rl\.scratch\decompiled_9chars_climax.txt`](file:///d:/programme/155rl/.scratch/decompiled_9chars_climax.txt)（4,609 行）
   - 拼接与语法/编码校验脚本：[`d:\programme\155rl\.scratch\build_all_9_nuts.py`](file:///d:/programme/155rl/.scratch/build_all_9_nuts.py)
   - 全部 18 个脚本文件（9 个 `<char>.nut` + 9 个 `<char>_shot.nut`）已以 `cp932` 编码生成至 `D:\programme\155rl\koishi514\mod\data\actor\`，并通过 `cp932` 编码校验、括号平衡校验、函数唯一性校验及全量静态/生命周期深度检查（[`deep_check_all_9_lastwords.py`](file:///d:/programme/155rl/.scratch/deep_check_all_9_lastwords.py) 与 [`audit_9chars_climax.py`](file:///d:/programme/155rl/.scratch/audit_9chars_climax.py) 均 0 告警通过）。
4. **4 大目标目录全量同步与热加载支持（100% 完成）**
   - 部署脚本：[`d:\programme\155rl\koishi514\apply_koishi514_mod.py`](file:///d:/programme/155rl/koishi514/apply_koishi514_mod.py)
   - 已将全部 **245 个 Mod 文件**同步至 4 个目标目录：
     1. `D:\programme\155rl\koishi514\mod\data\`
     2. `D:\programme\155rl\koishi514\release\mods\koishi514\data\`
     3. `D:\[th15.5] 东方凭依华（英文版+日文版）\mods\koishi514\data\`
     4. `D:\[th15.5] 东方凭依华（英文版+日文版）\data\`

---

## 3. 核心逆向发现与跨代适配技术规范

### 3.1 `ver=13` (TH13.5) 与 `ver=17` (TH15.5) `.pat` 二进制格式完整对照

#### (1) 文件头与纹理表（Part 1 & Part 2）
- **`ver=13` 头部**：
  - `version (u8 = 13)`、`nb_entries (u8)`、`nb_entries * 5B`
  - `num_tex (u16)` + `num_tex` 个纹理项：
    - `t_type in (0, 128)`：`u32 len` + `cp932 name`
    - `t_type in (1, 129)`：`u32 len` + `cp932 name` + `<hhhh` (`x, y, w, h`)
    - `t_type == 4`：`u32 len` + `cp932 name` + `<iiii` (`x, y, w, h`)
    - `t_type in (2, 130)`：内嵌 `TFBM` 贴图，含 `18B` 头部（`<4sBBIII`: `b'TFBM'`, `ver=0`, `bpp(8或32)`, `w`, `h`, `pitch`）+ `u32 comp_size` + `comp_size` 字节的 `zlib` 压缩像素流。
  - `nb_bmb (u16)` + `nb_bmb * 128B`（定长 `cp932` `.bmb` 路径表）。
- **`ver=17` 头部**：
  - `version (u8 = 17)`、`nb_entries (u8)`、`nb_entries * 5B` + **额外 2 字节 (`u16`)**
  - `num_tex (u16)` + `num_tex` 个纹理项（格式与 `ver=13` 相同）。
  - **无 `.bmb` 表**（直接进入 `nb_takes (u32)`）。

#### (2) Take 头部与多定义覆盖规则（Part 3）
- **`ver=13` Take 头部**：
  - 先读 `ch (u8), id1 (i32)`：
    - 若 `id1 == -1` (`0xFFFFFFFF`)：这是 **13 字节的重定向 Take**（`<Biii`: `ch, -1, target_motion_id, target_take`），无后续帧数据。
    - 否则读取 11 字节 `<BiIBB` (`ch, id1, u2, loop_flag, ib`) + `ib * 66B`（`<H16f` `.bmb` 绑定项）+ `nf (u32)`。
  - **多块重定义规则**：`ver=13` 的 `.pat` 中同一个 `motion_id` (`id1 > 0`) 可能在文件中出现多次（后出现的定义覆盖先前的定义）。解析时遇到 `id1 > 0` 必须重置 `motions[id1] = []`，只保留最后一次有效定义。
- **`ver=17` Take 头部**：
  - 固定 **13 字节 `<iiBI`**：`(id1, u2, loop_flag, nf)`
    - Take 0 的 `id1 = motion_id`（正整数）；同一 Motion 下后续 Take (`take >= 1`) 的 `id1 = -2` (`0xFFFFFFFE`)。

#### (3) 逻辑帧（Frames）、判定框（Hitboxes）与图层矩阵转换公式
- **`ver=17` 单帧结构（从 `ver=13` 精确转换）**：
  1. `dur (u16)` + `stats_raw (56B)` + `boxcount (u8 = c0 + c1 + c2)`（共 **59 字节**）
  2. 3 个判定框列表（`b0`, `b1`, `atk`）：每个列表为 `cnt (u8)` + `cnt * 19B`（`<Bffffh`: `flag=0, half_w, half_h, center_x, center_y, rot`），其中：
     $$\text{half\_w} = \frac{x_2 - x_1}{2.0}, \quad \text{half\_h} = \frac{y_2 - y_1}{2.0}, \quad \text{center\_x} = \frac{x_1 + x_2}{2.0}, \quad \text{center\_y} = \frac{y_1 + y_2}{2.0}$$
  3. 关键点列表（`pts`）：`cnt (u8)` + `cnt * 8B` (`<ii`)。
- **`ver=17` 图层关键帧（78 字节 `<16fIhhhhh`）**：
  $$\theta = \text{rz} \times \frac{\pi}{180.0}, \quad sx = \frac{\text{sx\_pct}}{100.0}, \quad sy = \frac{\text{sy\_pct}}{100.0}$$
  $$m_0 = sx \cos\theta, \quad m_1 = sx \sin\theta, \quad m_4 = -sy \sin\theta, \quad m_5 = sy \cos\theta$$
  $$m_{12} = -cx \cdot m_0 - cy \cdot m_4, \quad m_{13} = -cx \cdot m_1 - cy \cdot m_5$$
  $$\text{mat} = [m_0, m_1, 0, 0, \; m_4, m_5, 0, 0, \; 0, 0, 1, 0, \; m_{12}, m_{13}, 0, 1]$$
  $$\text{argb} = (a \ll 24) \mid (r \ll 16) \mid (g \ll 8) \mid b$$

---

### 3.2 新增关键引擎差异与修复准则（在 [`04_mod_porting_lessons_and_pitfalls.md`](file:///d:/programme/155rl/koishi514/docs/04_mod_porting_lessons_and_pitfalls.md) 基础上的重要补充）

在对全部 9 名角色的 4,609 行反编译 Climax 代码进行全属性/全局符号与生命周期审计时，发现并解决了以下 TH13.5 与 TH15.5 引擎核心差异：

1. **`FreeObjectCommonInit` 在 TH15.5 中不会初始化 `this.target`（导致 `this.target` 为 `null` 崩溃）**：
   - 在 TH15.5 的 `shot_function.nut` 中，`ShotCommonInit` 会设置 `this.target = this.owner.team.target.weakref();`；但在 `player_effect_function.nut` 中，`FreeObjectCommonInit` 完全不设置 `this.target`（保持 `player_effect_member.nut` 中的默认值 `null`）。
   - 而在 TH13.5 中，许多角色的 Climax 函数（如 `reimu_shot.nut` 的 `Climax_Wait`、`kokoro_shot.nut` 的 `ClimaxEnemyMask`）既可能通过 `SetShot` 也可能通过 `SetFreeObject` 生成，且内部直接访问 `this.target.x` / `this.target.type`。
   - **修复方案**：在 `*_shot.nut` 的所有 Climax 函数入口，只要函数体内引用了 `this.target`，自动在函数开头注入 `this.target = this.owner.target.weakref();`。
2. **`this.LabelClear()` 在 TH15.5 中会提前触发 `lavelClearEvent` 销毁终符背景并恢复倒计时**：
   - 在 TH15.5 (`actor_game.nut`) 中，`UseClimaxSpell` 会注册 `this.lavelClearEvent = function() { this.lastword.Deactivate(); ::battle.enableTimeUp = true; }`。
   - 只要角色调用 `this.LabelClear()`，就会立即执行并清空 `this.lavelClearEvent`，同时把 `endMotionCallbackFunction` 重置为 `this.EndtoFreeMove`！
   - 而在 TH13.5 中，几乎所有 Climax 多段子状态（如 `Spell_Climax_Hit`、`Spell_ClimaxCatch_Init`、`Climax_A1`、`Spell_Climax_Normal` 等）在切换阶段时都调用了 `this.LabelClear()`。
   - **修复方案**：仅在起手 `Spell_Climax_Init` 中保留 `this.LabelClear()`（且位于 `this.UseClimaxSpell` 之前），而在所有后续 Climax 子状态函数中将 `this.LabelClear()` 替换为 `this.LabelReset()`！
3. **全局对象与内置方法映射表**：
   - `::renderer.ShowBackground(...)` → `::graphics.ShowBackground(...)`（`mamizou.nut`, `kokoro.nut`）
   - `::manbow.renderer.slot.actor` / `::renderer.slot.actor` → `::graphics.slot.actor`
   - `::effect.EF_HitSmashC` / `::effect.EF_HitSmashB` → `this.EF_HitSmashC` / `this.EF_HitSmashB`
   - `::stage.scrollBottom` → `::battle.scroll_bottom`（`futo_shot.nut`）
   - `::game.battleTime` → `99`（`nitori_shot.nut`）
   - `this.PlaySE_Actor(id)` → `this.PlaySE(id)`
   - `this.Set_Vibration(v)` → `::camera.Shake(v)`
   - `::object.EnableTimeStop.call(this, false)` → `this.EnableTimeStop(false)`
   - `this.SetShotTrim(...)` → `this.SetFreeObjectDynamic(...)`（并将第 5 参数 `null` 转为 `{}`；注意必须用 `SetFreeObjectDynamic` 绑定 `manbow::Animation2DDynamic`，否则秦心 `Climax_Back` / `Climax_Back_B` 读写 `this.anime.left/top/width/height/center_x/center_y` 会触发 `script error [trying to set 'null']` 闪退！）
   - `this.Mana_ADD(v, stop)` → `this.team.AddMP(v, stop)`，`this.mana` → `this.team.mp`（`ichirin.nut`）
   - `this.Math_GetDirection(a, b, dir)` → `this.atan2(b.y - a.y, (b.x - a.x) * dir)`（`ichirin_shot.nut`）
   - `this.Math_GetDistance3D(a, b)` → `((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y))`（`nitori_shot.nut`）
   - `this.actor[this.owner]` → `this.owner`（`nitori_shot.nut`）
   - `this.hitTarget = null;` → `this.hitTarget = {};`（`ichirin_shot.nut`, `futo_shot.nut` 多段命中重置必须用空表 `{}`）
   - `this.target.life` → `this.target.team.life`（全角色通用：在 TH15.5 中 `player.life` 恒为 `1`，真实队伍血量在 `this.target.team.life`，涵盖 `marisa.nut`、`nitori_shot.nut`、`mamizou.nut`、`futo_shot.nut`），`this.SetDamage.call(this.target, dam_, null)` → `this.target.SetSelfDamage(dam_)`（`futo_shot.nut` `Climax_Fire` 灼烧扣血适配）
   - `this.(FadeIn|FadeOut|BackFadeIn|BackFadeOut|ClearFadeEffect|ClearBackFadeEffect|BackColorFilter|BackColorFilterOut|EraceBackGround|ResetBackGround)(...)`（在 `*_shot.nut` 中）→ `this.owner.\1(...)`（解决 `miko_shot.nut` `Climax_Shot` 与 `reimu_shot.nut` `Climax_Face` 弹幕销毁后 `effect.nut` 访问 `this.initTable.pare.team` 报 `the index 'team' does not exist` 闪退）
   - 剔除 TH13.5 专属无效调用：`BeginClimaxSpellBgm`、`SetSpellBackTime`、`SetSpellBack(0, 3)`、`PopularCheer`。
4. **子物体链式生成 `this.owner` 玩家本体溯源归一化（全 9 角色 `*_shot.nut` 通用防护）**：
   - 在 TH13.5 中所有弹幕/特效的 `this.owner` 均指向顶层 Player，而在 TH15.5 中子物体再生成孙物体时 `this.owner` 会变成上一级 `shot_class` 或 `player_effect_class`。
   - 在所有 `*_shot.nut` Climax 函数入口注入 `while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner) { this.owner = this.owner.owner; }`，确保 `this.owner` 始终指向 `player_class`（`actorType == 1`）。
5. **在 `*_shot.nut` 中调用 `actor_screen.nut` 渐变方法必须绑定 `this.owner`（修复神子/灵梦终符结束闪退）**：
   - TH15.5 的 `FadeIn` / `FadeOut` / `BackFadeIn` / `BackFadeOut` 等方法会将 `t_.pare <- this.weakref()` 存入渐变特效，并在渐变结束帧访问 `this.initTable.pare.team.fade_back`。若在 `*_shot.nut` 中以 `this.BackFadeIn(...)` 调用并紧接着 `this.ReleaseActor()`，弱引用 `pare` 变为 `null`，渐变结束时必现 `script error [the index 'team' does not exist]` 闪退。因此 `*_shot.nut` 中所有屏幕/背景渐变调用统一重写为 `this.owner.<FadeMethod>(...)`。
6. **Squirrel 3 反编译器块级 `local` 寄存器复用生命周期修复（修复一轮/灵梦 `the index 't_' does not exist` 闪退）**：
   - 当同一函数内多个并列 `if` 块复用同一局部变量寄存器（如 `reg=1` 的 `t_`）时，`localvars` 表具有多条不同 `(start_pc, end_pc)` 范围的记录。反编译器将 `declared_locals` 键绑定为 `(reg, name, start_pc)` 并校验 `start_pc <= pc <= end_pc`（同时忽略紧邻 `OP_FOREACH` 前的 `LOADNULLS`），确保每个独立代码块均正确输出 `local t_ = {};`。
7. **云居一轮 (`ichirin`) 巨大云山状态机专属适配与异常中断清理**：
   - 在 `ichirin.nut` 顶层类作用域（`Spell_Climax_Init` 函数定义上方）声明 `this.unzanMode <- 0; this.unzanMaxTime <- 0; this.unzanPart <- null;`，在 `Spell_Climax_Init` 内部使用 `=` 赋值（避免在 Squirrel 类实例上使用 `<-` 触发 `class instances do not support the new slot operator`）。
   - 在 `Spell_Climax_Normal` 中将 TH13.5 的 `if (this.team == 1) target_ = "player2"` 改为直接使用 `this.target`。
   - 在巨大云山各状态（`Spell_Climax_Normal`, `Spell_Climax_Turn`, `Spell_Climax_AtkLow`, `Spell_Climax_AtkHigh`, `Spell_Climax_AtkHighFront`）的 `stateLabel` 每帧递减 `if (this.unzanMaxTime > 0) this.unzanMaxTime--;`，确保巨大云山持续时间结束后正常退场。
   - 在 `Spell_Climax_Init` 的 `lavelClearEvent` 中增加对 `this.unzanPart` 部件数组的遍历销毁、`this.baseDefRate = 1.0;` 与 `this.unzanMode = 0;` 的兜底恢复。
   - 在 `ichirin_shot.nut` 的 `ClimaxHighShot_Laser` 生成 `ClimaxHighShot_Mini` 时注入 `t_.name <- this.weakref();`。
8. **河城荷取 (`nitori`) 潜水透明状态 `this.masterAlpha = 1.0` 显式恢复**：
   - TH13.5 荷取潜入水下设置 `this.masterAlpha = 0.0;` 后依赖 TH13.5 专属 `NitoriColor()` 每帧自动递增恢复，而 TH15.5 `CommonColorUpdate()` 不会恢复 `masterAlpha`。在 `nitori.nut` 的 `Spell_Climax_Cut` 开头、`Spell_Climax_End` 开头、以及 `Spell_Climax_Init` 的 `lavelClearEvent` 中显式注入 `this.masterAlpha = 1.0;`，防止准星与角色收招后隐身。
9. **FreeObject（`player_effect_class`）实例无 `priority` 槽位，裸写/裸读必现 `the index 'priority' does not exist` 闪退（2026-11-08 云居一轮 LastWord 开场实机崩溃复盘）**：
   - **故障表现**：云居一轮释放 LastWord 开场瞬间闪退，`th155_log.exe.log` 连续 2 条 `script error [the index 'priority' does not exist]`（内层 `t.init.call(this, t)` + 外层 `FreeObjectCommonInit` 重抛），随后 `0xc0000409` fail-fast 崩溃（`addr=0x88c5ff`）。
   - **根因剖析**：
     - TH13.5 的 Actor 自带可读写 `priority` 槽位，反编译出的 `this.priority = t.priority;`（`ichirin_shot.nut` `ClimaxSmoke`）、`this.priority = 180/200;` + `t_.priority <- this.priority;`（`reimu_shot.nut` `Climax_LockB_Ball/LightCore/LightWave/LightWaveB`）在 TH13.5 下合法。
     - 但在 TH15.5 中，所有 Actor2D 派生类的渲染层级只通过 `actor_screen.nut` 的 `DrawActorPriority()/DrawScreenActorPriority()/DrawBackActorPriority()` + `drawPriority` 字段维护（`actor_member.nut` 仅声明 `this.drawPriority <- 0`，全源码无任何 `this.priority <-` 槽位声明）；`SetFreeObject` 生成的 `player_effect_class` 实例缺失该槽位，读写即抛 `the index 'priority' does not exist`。
     - 注意 `SetShot` 生成的 `shot_class` 实例经 C++ 绑定仍可裸写 `this.priority`（如原版 `SPShot_B` 的 `this.priority = 200` 全对局高频执行无异常，恋 `SPShot_C` 亦然），故仅需修复 FreeObject 侧；为统一与防回归，反编译器对 `*_shot.nut` Climax 输出统一改写。
   - **修复方案**：
     - `this.priority = <expr>;` → `this.DrawActorPriority(<expr>);`（`ichirin_shot.nut` `ClimaxSmoke` / `ClimaxShotRing` / `ClimaxShotHigh` / `ClimaxHighShotRing`、`reimu_shot.nut` `Climax_LockB_Ball/LightCore/LightWave/WaveB`、`miko_shot.nut` 死代码 `Climax_Back` 的 `this.priority = 580`）。
     - `t_.priority <- this.priority;` → `t_.priority <- this.drawPriority;`（`reimu_shot.nut` `Climax_LockB_Ball` ×2、`Climax_LockB_LightWave` ×1；`DrawActorPriority()` 会同步写入 `this.drawPriority`，读写链闭环）。
     - 反编译器（`decompile_sq3_climax.py` `decompile_top_function` `is_shot` 分支）新增同名自动改写规则，后续重跑 `build_all_9_nuts.py` 不再回归。
     - `SetShot` 生成的 shot 侧裸写（如 `ClimaxShotBeam` 的 `this.priority = 200`、各角色 `SPShot_*/SpellShot_*` 原版写法）保持不动。
10. **其余 8 角色同类隐患复查（2026-11-08，用户要求，结论：零新增修复）**：
   - **范围**：`reimu/marisa/hijiri/futo/miko/nitori/mamizou/kokoro` 全部已移植 Climax 函数（`ichirin/koishi` 为昨日修复项，不在复查范围）。
   - **方法**：逻辑行解析全树 1194 个 `Set*` 生成点（`audit_spawns.json`，多行调用按括号深度合并）→ 按角色作用域 join 生成表键与函数读键（同名函数跨角色不合并）；`this.*` 槽位对照 254 项词汇表（`audit_vocab.json`：5 个 member 文件声明 + 全 vanilla 脚本写集合；`priority` 缺席、`drawPriority` 存在）；H3–H6 模式 grep 全覆盖。
   - **H1（表键缺失）**：仅 2 候选，均安全——`ichirin.ClimaxHighShot_Laser` 的 `t.rot` 经 `LaserPoint` 的 `t_={point,rot}` 上游透传；`nitori.Climax_FallBullet` 的 `motion/pos` 经 `this.initTable` 上游表透传（scout 逐行验证）。`futo.Climax_Shot` 的 `t.rot/t.vec` 与其唯一生成点 `VAR{rot,vec}` 吻合；`miko` 的 `{}` 生成点对应的是 miko 自家无读键的 `Climax_Shot`，非 koishi 同名函数。
   - **H2（非法槽位）**：13 处标记全系误报——`this.direction/x` 写是引擎容忍的原版惯用法（全树 47/24 处，含非 Climax 原版区间）；`unzan*` 在 `ichirin.nut L3162-3164` 文件作用域已声明；`ClimaxShotBeam L2703` 的 `priority=200` 是 `SetShot` 生成的 shot 类（安全，与第 9 条 keep-list 一致）。
   - **H3–H6**：零命中——`LabelClear` 仅出现在 `Init`；全部 `Fade*` 作用于玩家本体/自身/玩家 owner；`koishi Climax_Cut L3324` 自身 `FadeOut(20f @151→171)` 安全：调用者无自销毁、存活过 171（`Finish` 在 181 才调），且 `effect_class/team` 经 `battle_team.nut:889-895` 赋予全部 4 类、`EF_*` 经 `Actor2D` 基类继承，调用时无缺槽；`kokoro` 的 `anime.*` 全系 `SetFreeObjectDynamic` 生成；`hitTarget={}` 为原版命中后惯用法（4 处）；`SetShotTrim` 零出现。
   - **死代码（定义-only、永不执行，非 bug，不修复）**：`reimu.Climax_Back`（`t.owner`）、`miko.Climax_Back`（`t.owner`）、`koishi.Climax_Shot`（`t.keyTake/rot`，现役为 `Climax_ShotB`）、`ichirin.ClimaxShotHigh`（`init.point`）、`mamizou Climax_SmokeWallA/B/C/D`、`reimu Climax_LockA2/LockA_Core`（含其链下 LockB 子函数）、`marisa Climax_CutA`、`hijiri ClimaxEffect_DashSpark`、`hijiri Climax_FInish`（typo 变体，`Climax_B` 经 `EndtoFallLoop` 结束从未调用）。
   - **工具教训**：shell `grep` 在本 cp932 树下对 `this.direction =` 漏报（报 0 处，实 47 处），本复查一律以 python+cp932 直读为 ground truth；跨角色同名函数必须按角色作用域分别 join。

---

## 4. 全 9 角色 `.nut` 替换行号与规模总表

| 角色 | `<char>.nut` 原版替换区间 | 新注入 Climax 函数数 | `<char>_shot.nut` 原版替换区间 | 新注入 Climax Shot 函数数 | 生成 `.nut` 大小 | 生成 `_shot.nut` 大小 |
| :--- | :--- | :---: | :--- | :---: | :---: | :---: |
| **`reimu`** | `L3502..L3813` | 4 | `L3481..L3942` | 12 | 78,051 B | 85,049 B |
| **`marisa`** | `L3245..L3504` | 3 | `L3085..L3355` | 6 | 72,970 B | 67,954 B |
| **`ichirin`** | `L3162..L3348` | 9 | `L2374..L3056` | 17 | 68,277 B | 65,481 B |
| **`hijiri`** | `L3464..L3651` | 4 | `L1910..L2124` | 5 | 78,641 B | 42,341 B |
| **`futo`** | `L2963..L3202` | 2 | `L3324..L3682` | 8 | 62,601 B | 78,019 B |
| **`miko`** | `L2894..L3174` | 3 | `L2815..L3040` | 3 | 58,296 B | 61,577 B |
| **`nitori`** | `L3538..L3750` | 3 | `L2875..L3963` | 8 | 81,732 B | 75,630 B |
| **`mamizou`** | `L3505..L3685` | 5 | `L3093..L3414` | 12 | 81,161 B | 79,543 B |
| **`kokoro`** | `L3420..L3593` | 4 | `L3770..L4091` | 6 | 72,466 B | 90,704 B |

---

## 5. 关键文件与脚本索引

| 路径 | 用途说明 |
| :--- | :--- |
| [`d:\programme\155rl\koishi514\docs\04_mod_porting_lessons_and_pitfalls.md`](file:///d:/programme/155rl/koishi514/docs/04_mod_porting_lessons_and_pitfalls.md) | 跨代移植踩坑实录与 API 映射准则 |
| [`d:\programme\155rl\koishi514\apply_koishi514_mod.py`](file:///d:/programme/155rl/koishi514/apply_koishi514_mod.py) | 自动化部署、4 目录同步（`--sync-only`）与运行时热注入脚本（自动扫描全部 245 个 Mod 文件） |
| [`d:\programme\155rl\koishi514\loader\src\mod_loader.cpp`](file:///d:/programme/155rl/koishi514/loader/src/mod_loader.cpp) | `winmm.dll` 通用文件重定向钩子（自动扫描 `mods\koishi514\data` 下全部 10 名角色文件并翻转 Pak 红黑树哈希） |
| [`d:\programme\155rl\.scratch\patch_all_9_pats.py`](file:///d:/programme/155rl/.scratch/patch_all_9_pats.py) | 全 9 角色 `.pat` (`ver=13 -> ver=17`) 补丁与贴图自动转换构建器 |
| [`d:\programme\155rl\.scratch\decompile_sq3_climax.py`](file:///d:/programme/155rl/.scratch/decompile_sq3_climax.py) | Squirrel 3.x Climax AST 反编译器与 TH13.5 → TH15.5 API 自动转换器 |
| [`d:\programme\155rl\.scratch\build_all_9_nuts.py`](file:///d:/programme/155rl/.scratch/build_all_9_nuts.py) | 全 9 角色 `<char>.nut` 与 `<char>_shot.nut` 自动拼接与语法/编码校验脚本 |
| [`d:\programme\155rl\.scratch\deep_check_all_9_lastwords.py`](file:///d:/programme/155rl/.scratch/deep_check_all_9_lastwords.py) | 全 9 角色 `.pat` 动作帧数/`loop_flag` 匹配性、局部变量词法作用域与收招生命周期深度检查脚本 |
| [`d:\programme\155rl\.scratch\audit_9chars_climax.py`](file:///d:/programme/155rl/.scratch/audit_9chars_climax.py) | 全 9 角色 Climax 属性槽位、跨表调用与引擎符号静态审计脚本 |
| [`d:\programme\155rl\.scratch\decompiled_9chars_climax.txt`](file:///d:/programme/155rl/.scratch/decompiled_9chars_climax.txt) | 9 名角色全部 Climax 函数的反编译源码汇总（4,609 行） |

---

## 6. 日常使用与云端打包说明

- **一键同步 4 大目录**：
  ```powershell
  python d:\programme\155rl\koishi514\apply_koishi514_mod.py --sync-only
  ```
- **独立发布包目录**：
  - `D:\programme\155rl\koishi514\release\` 下已包含完整的 `winmm.dll`、`mod_loader.ini` 与 `mods\koishi514\data\`（共 245 个文件，涵盖全部 10 名心绮楼角色），可直接打包上传云端或解压至任意 TH15.5 v1.21b 游戏根目录即插即用。
