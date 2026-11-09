# 古明地恋（Koishi Komeiji）跨代技能、符卡与动作演变全考（TH13.5 / TH14.5 / TH15.5）

本文档基于《东方凭依华 ～ Antinomy of Common Flowers.》（TH15.5）反编译得到的古明地恋全部 Squirrel 脚本（`data/actor/koishi*.nut`）与动画资源包表，结合前作《东方心绮楼 ～ Hopeless Masquerade.》（TH13.5）与《东方深秘录 ～ Urban Legend in Limbo.》（TH14.5）的角色设计，系统梳理古明地恋在三代黄昏格斗作品中的机制变迁、底层代码映射、残留在 TH15.5 客户端中的完整死代码（Dead Code），以及完全删除的招式清单。

---

## 一、 核心机制演变：从“三槽主动预置”到“单发附带自动预置”

古明地恋在三作中的核心特色均为**“无意识（Super-Ego / Id）预置自动触发系统”**，但在操作逻辑与槽位交互上经历了三次重大重构：

### 1. 底层三槽架构（`sence[0..2]`）
在 `koishi_init.nut:42-100` 与 `koishi_base.nut:104-183` 中，恋恋自 TH13.5 起便拥有 3 个独立的“无意识预置槽”，对应角色周身漂浮的 3 个旋转光标（`SenceObject`，Motion `3899`，Take `0/1/2`）：

| 槽位索引 | 对应维度 | 预置动作函数（`koishi_base.nut`） | 音效 ID | 特效 Take | 预置 Motion |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Slot 0 (`i = 0`)** | **打击槽（Melee / Attack）** | `AutoAttackSet(t)` / `AutoAttackSet_Charge(t)` | `2450` | `0` | `1390` / `1391` |
| **Slot 1 (`i = 1`)** | **射击槽（Shot）** | `AutoShotSet(t)` | `2451` | `1` | `2590` |
| **Slot 2 (`i = 2`)** | **必杀技槽（Special Skill）** | `AutoSkillSet(t)` | `2452` | `2` | `3980` / `3981` |

每个槽位由 8 个并行数组维护状态：
- `autoCount[i]`：预置持续帧数倒计时（归零时触发 `timeFunc[i]` 超时回调或自动释放）。
- `preAutoCount[i]`：预置冷却/保护帧数（在此倒计时归零前，`autoAttack[i]` 条件满足也不会触发）。
- `autoAttack[i]`：每帧在 `AutoAttackCheck()` 中执行的条件检测闭包（返回 `true` 且 `preAutoCount[i] <= 0` 且 `Cancel_Check(autoCancelLevel[i])` 通过时触发 `autoFunc[i]`）。
- `attackType[i]`：当前槽位挂载的招式类型枚举 ID。
- `autoFunc[i]`：条件满足时的触发动作函数。
- `timeFunc[i]`：倒计时归零时的触发动作函数。
- `autoCancelLevel[i]`：无意识自动触发可打断当前动作的取消等级（通常打击为 `30`，射击为 `40`，必杀为 `60` 或 `100`）。
- `autoAttackTimes[i]`：同一预置循环内的累计触发次数（如旧版《刺痛心灵》随着触发次数递增玫瑰藤蔓长度与冷却间隔）。

### 2. 三代机制差异对比

| 特性维度 | TH13.5《东方心绮楼》 | TH14.5《东方深秘录》 | TH15.5《东方凭依华》 |
| :--- | :--- | :--- | :--- |
| **重打击（8A/2A/6A）预置** | 按键先只做**预置姿势**（Motion `1390`/`1391`）装填至 Slot 0，满足条件才实际出招；支持蓄力预置与 `Atk_Fake_Set` 假动作 | 改为**直接出招的同时自动装填 Slot 0**（`Atk_High*_Init` 调用 `*_SetB()`）；但保留出招中按 A 切换预置（`1200..1299` 内按 A） | 沿用 TH14.5 直接出招+自动装填；`AutoAttackSet_Charge` 彻底沦为 0 引用死代码，但 `1200..1299` 派生按 A 主动预置（含 `Atk_Fake_Set`）仍保留在 `koishi_base.nut:579-608` |
| **射击（B / 6B）预置** | 5B/6B 按下时执行 `AutoShotSet`（Motion `2590`）装填至 Slot 1，条件满足自动发射 | 5B 改为直接发射红蓝双螺旋弹；6B 改为直接发射前方心电波（`Shot_Front_Init`）并自动向 Slot 1 装填 `Shot_FrontB_Init` | 沿用 TH14.5；`AutoShotSet`（Motion `2590`）与旧版派生弹幕 `Shot_Normal_B` / `Shot_NormalSub` 留在代码中但 0 引用 |
| **必杀技（C 键）体系** | 玩家在卡组自由装备必杀技；拥有 **Conditioned Teleport（条件反射传送）**、**Fidgety Snatcher（近身抓投）**、**Growing Pain（成长痛）**、**Catch and Rose（伸缩机械臂）**、**Stinging Mind（刺痛心灵）**、**Unanswered Love（单相思玫瑰环）** | 取消卡组装备，改为固定方向必杀技（5C/6C/4C/8C/2C），部分旧版必杀技合并或被替换 | 引入双人凭依（Master/Slave）与 **5C 好奇心（`SP_G`）**；原 5C/旧版 **刺痛心灵（`SP_C`）**、**旧版近身抓投（`SP_D_Init`）**、**成长痛变体（`SP_B2`/`SP_B3`）** 被从输入表中摘除，但函数体 100% 保留 |

---

## 二、 必杀技（Special Skills）跨代对照与代码定位

在 TH15.5 的 `koishi_base.nut:527-577`（`Update_Input`）及 `630-679`（`TeamSkillChain_Input`）中，C 键（`command.rsv_k2`）的 5 个方向被固定绑定为：

| 指令方向 | TH15.5 现行绑定技能 | 对应入口函数 | Motion ID | `attackType[2]` | 前作（TH13.5 / TH14.5）对应位置与变化 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **5C (`rsv_x == 0, rsv_y == 0`)** | **好奇心（Curiosity / 猛进三连斩）** | `SP_G_Init` -> `SP_G_Base` -> `SP_G_Loop` | `3060`, `3061` (`SPShot_G`: `6060`) | `160` -> `161` -> `162` | TH14.5/15.5 新技能；取代了前作中立必杀技槽位（原 **《刺痛心灵》`SP_C`** 或 **《条件反射传送》`SP_A`**） |
| **6C (`rsv_x * dir > 0`)** | **Catch and Rose（抓捕与玫瑰 / 远距机械臂抓投）** | `SP_D2_Init` -> `SP_D_Set` / `SP_D2_Hit` -> `SP_D_Hit` | 预置 `3980`，出招 `3031`，命中 `3036` -> `3035` | `130` | TH13.5 原本分为近身扑抓 **《焦躁的掠夺者》（`SP_D_Init`, Motion `3030`）** 与远距机械臂 **《Catch and Rose》（`SP_D2_Init`, Motion `3031`）**；TH15.5 统一只调用 `SP_D2_Init`，导致 `SP_D_Init` 闲置 |
| **4C (`rsv_x * dir < 0`)** | **Reflex Radar（反射雷达 / 心电图旋转扫描线）** | `SP_E_Set` -> `SP_E_Init` | 预置 `3980`，触发 `3040` (`SPShot_E`: `6040`) | `0`（依赖 `skillE_react` 与 `skillE_line`） | 自 TH14.5 加入并沿用至 TH15.5 |
| **8C (`rsv_y < 0`)** | **Growing Pain（成长痛 / 升龙爱心环）** | `SP_B_First_Init` -> `SP_B_Init` + `SP_B_Set` | `3010` (`SPShot_B`: `6010`) | TH13.5 拥有垂直大升龙变体 **`SP_B2_Init`（Motion `3010`, `vy = -22.5`）** 与空中俯冲变体 **`SP_B3_Init`（Motion `3011`, `vx = 10, vy = +17.5`）**；TH15.5 仅调用 `SP_B_First_Init`，`SP_B2`/`SP_B3` 闲置 |
| **2C (`rsv_y > 0`)** | **Unanswered Love（未被回应的恋心 / 360度玫瑰环）** | `SP_F_Init` -> `SP_F_Bloom_Init` / `SP_F_AutoBloom_Init` | 预置 `3980`，手动绽放 `3051`，超时自动绽放 `3052` | `150` | 自 TH13.5 保留至 TH15.5（在凭依切换 `t.change == true` 时触发 `SP_F_ChangeBloom_Init` 瞬发扩散绽放） |

---

## 三、 完整保留在 TH15.5 代码中但未启用的旧作招式（100% 可恢复）

以下招式在 `koishi.nut`、`koishi_base.nut`、`koishi_shot.nut` 中**代码逻辑、Motion 帧数据、碰撞盒、子机弹幕与音效 ID 均 100% 完整保留**，仅因 `koishi_base.nut` 的输入分发被新招式覆盖而无法在正常对局中触发：

### 1. 必杀技 C：《刺痛心灵》（Stinging Mind）—— `SP_C_Set` & `SP_C_Init`
- **代码位置**：
  - `koishi.nut:4079-4109`：`SP_C_Set(t)`（预置装填至 Slot 2，`attackType[2] = 102`，`preAutoCount = 90`，`autoCount = 60`）
  - `koishi.nut:4111-4192`：`SP_C_Init(t)`（Motion `3020`，音效 `2435`，生成追踪玫瑰藤蔓 `SPShot_C` 并自动续装填下一轮 `attackType[2] = 102`）
  - `koishi_shot.nut:1747-1903`：
    - `SPShot_C(t)`（Motion `6020`，沿目标方向递推延伸藤蔓节点，节点上限 `limit = 2 + 2 * autoAttackTimes[2]`，最高 20 节）
    - `SPShot_C_Rose(t)`（Motion `6021`，音效 `2436`，在偶数藤蔓节点上绽放带攻击判定的荆棘玫瑰）
    - `SPShot_C_Petal(t)`（Motion `6022`，玫瑰爆开散落花瓣特效）
- **原版机制特点**：
  - 首次调用 `SP_C_Set` 装填无意识槽，150 帧（90 预冷 + 60 计时）后自动触发 `SP_C_Init`，或由玩家再次按键主动触发。
  - 每次 `SP_C_Init` 释放时，根据传入的方向参数 `t.k`（`2` 为向斜下 `+40°`、`8` 为向斜上 `-40°`、默认 `5` 为正前方 `0°`）射出玫瑰荆棘链，并在释放完毕后**自动重新装填 `attackType = 102`**！
  - 随着 `autoAttackTimes[2]` 递增，荆棘链长度从 2 节逐步增长到 20 节（同时重装填间隔 `preAutoCount = 90 + autoAttackTimes[2] * 45` 递增），直到恋恋受击（`koishi_base.nut:386` 专门检测 `this.attackType[2] == 102` 清空槽位）或主动取消。
- **注意细节**：原函数 `SP_C_Set(t)` 与 `SP_C_Init(t)` 开头有 `if (!this.Cancel_Check(t.cancel, ...))` 以及读取 `t.mana`、`t.k`、`t.sp`，这是 TH13.5 统一传参表 `{ cancel = 60, mana = 200, sp = 0, k = 5 }` 的格式；在 TH15.5 中恢复时只需传入完整参数表即可零报错原生运行。

### 2. 必杀技 D 原版：《焦躁的掠夺者》（Fidgety Snatcher / 近身扑抓）—— `SP_D_Init`
- **代码位置**：`koishi.nut:4226-4264`（`SP_D_Init`），命中后进入 `SP_D_Hit`（`koishi.nut:4266-4336`）。
- **Motion 与音效**：Motion `3030`，挥爪音效 `2438`；命中后切入 Motion `3035`，音效 `2440` -> `2441` 玫瑰爆破投技。
- **与现行 `SP_D2_Init` 的区别**：
  - `SP_D_Init` 是 TH13.5 原版近身直接指令投动作（Motion `3030`），不发射机械臂子机，直接用本体的打击框抓取面前的敌人并进入 `SP_D_Hit({ fast = false })`（停顿 20 帧后引爆玫瑰）。
  - `SP_D2_Init`（Motion `3031`）则是发射 `SPShot_D_Arm` 机械臂与 `WireA/WireB` 伸缩线抓取远距离敌人，拉回后进入 `SP_D_Hit({ fast = true })`（停顿 2 帧后引爆）。

### 3. 必杀技 B 变体：《成长痛》垂直高升龙与空中急降版 —— `SP_B2_Init` & `SP_B3_Init`
- **代码位置**：
  - `SP_B2_Init(t)`：`koishi.nut:3926-3994`（Motion `3010`，`SetSpeed_XY(0.0, -22.5)` 纯垂直高速冲天升龙，比普通版 `vx=10, vy=-17.5` 飞得更高且不前冲，结束回调 `EndtoFallLoop`）。
  - `SP_B3_Init(t)`：`koishi.nut:3996-4077`（Motion `3011`，`SetSpeed_XY(10.0 * dir, +17.5)` 空中向斜下方高速俯冲钻击，带双爱心环绕 `SPShot_B` 轨迹与多段 `HitCycleUpdate(4)`）。
- **价值**：在原版 TH15.5 中，空中按 8C 也只会出向上升龙 `SP_B_Init`；恢复 `SP_B3_Init` 可让恋恋在空中拥有极具进攻性的俯冲钻击技（Motion `3011`），恢复 `SP_B2_Init` 可提供纯粹的垂直对空升龙。

### 4. 旧版重打击主动预置系统与假动作预置 —— `Atk_High*_Set` / `Atk_Fake_Set` / `AutoAttackSet_Charge`
- **代码位置**：
  - `koishi_base.nut:229-267`：`AutoAttackSet_Charge(t)`（Motion `1391`，带蓄力光环 `SetChargeAura` / `SetChargeAuraB` 的打击槽装填动作）。
  - `koishi_base.nut:579-608`：在 `motion >= 1200 && motion <= 1299`（即 8A/2A/6A 动作期间）再次按下 A 键（`rsv_k0`），可根据方向主动触发：
    - `2A` -> `Atk_HighUnder_Set(null)`（Motion `1390` 主动重置/装填 2A 预置）
    - `8A` -> `Atk_HighUpper_Set(null)`（Motion `1390` 主动重置/装填 8A 预置）
    - `6A` -> `Atk_HighFront_Set(null)`（Motion `1390` 主动重置/装填 6A 预置）
    - `5A / 4A` -> **`Atk_Fake_Set(null)`**（`koishi.nut:862-924`，Motion `1390` 做出预置动作并点亮 Slot 0 光标 180 帧，但 `autoAttack` 恒返回 `false`——专用于欺骗对手的**无意识假动作预置**！）。

### 5. 旧版射击主动预置与强化双螺旋弹 —— `AutoShotSet` / `Shot_Normal_B` / `Shot_NormalSub`
- **代码位置**：
  - `koishi_base.nut:269-306`：`AutoShotSet(t)`（Motion `2590`，音效 `2451`，专门用于向 Slot 1 装填射击预置的独立动作）。
  - `koishi_shot.nut:236-330`：`Shot_Normal_B(t)`（Motion `2009`，Take `3/4`，速度 `12.5`，振幅 `flag4.y = 18.5` 更大的强化版红蓝正弦波弹幕）。
  - `koishi_shot.nut:332-415`：`Shot_NormalSub(t)`（Motion `2009`，速度 `18.0`，高速副弹幕，且带有 `hitResult == 32` 穿弹重置判定）。

### 6. 怪谈（Occult）无条件瞬移派生与分身残影 —— `Okult_AnyHit` & `Occult_Koishi`
- **代码位置**：
  - `koishi.nut:3691-3754`：`Okult_AnyHit(t)`（Motion `2501`，只需 1 帧前摇即可直接拉电话线 `Occult_TelLine` 以 `40.0` 速度冲向对手背后发动 `Okult_Attack` 电话斩，无需等待普通版 `Okult_Init` 的电话铃声波命中）。
  - `koishi_shot.nut:1628-1653`：`Occult_Koishi(t)`（Motion `2509`，在对手身后凭空浮现一个恋恋幻影分身，逐渐显现并逼近对手的惊悚视觉特效）。

---

## 四、 已从 TH15.5 客户端中删除的旧作招式与残迹考证（不可仅靠脚本恢复）

以下招式在 TH13.5 或 TH14.5 中存在，但在 TH15.5 制作过程中被黄昏边境删除了函数体或动画/立绘资源，仅在代码中留下了枚举判断或同名残迹：

### 1. 必杀技 A：《条件反射传送》（Conditioned Teleport）—— `SP_A`
- **代码残迹证据**：
  1. `koishi_base.nut:386`：
     ```squirrel
     if (this.attackType[2] == 100 || this.attackType[2] == 102 || this.attackType[2] == 150 || this.attackType[2] == 160 || this.attackType[2] == 161 || this.attackType[2] == 162)
     ```
     此处清理 Slot 2 预置状态的枚举列表中：
     - `102` = `SP_C`（刺痛心灵）
     - `130` = `SP_D`（Catch and Rose，受击不清除故不在此列）
     - `150` = `SP_F`（单相思玫瑰环）
     - `160/161/162` = `SP_G`（好奇心三连跳）
     - **`100` 正是已被删除函数体的 `SP_A`（Conditioned Teleport，条件反射传送）！**
  2. `koishi_team.nut:319`：
     在 `Team_Change_Skill_FrontB`（凭依换人前冲必杀）中，有一行报错隐患代码：
     ```squirrel
     this.flag4 = this.SetFreeObject(this.x, this.y, this.direction, this.SP_A_Bloom, {}).weakref();
     ```
     这是黄昏程序员从魔理沙（`marisa.nut:2316` 的 `SP_A_Bloom`）复制粘贴凭依换人代码留下的残迹，而恋恋自己的 `SP_A` 早在重做时被移除。
- **是否可恢复**：
  - 原版 `SP_A` 的 Motion `3000` 是否仍存在于 `koishi.pat` 中需视 `.pat` 动作表而定；函数体已删，本 Mod 未实装，仅作档案记录（详见 `02` 文档 B 节）。

### 2. TH13.5 符卡 3：心符「没我的爱」（Heart Sign "Selfless Love" / Subterranean Rose）
- **历史变迁**：
  - 在 TH13.5《东方心绮楼》中，恋恋的三张符卡分别为：
    1. **抑制「Super-Ego」（抑制「超我」）** —— 保留为 TH15.5 的 `Spell_A_Init`（Motion `4000`，将全屏敌人与爱心吸向自身周围造成多段伤害）。
    2. **本能「Id 的解放」（本能「本我的解放」）** —— 保留为 TH15.5 的 `Spell_B_Init`（Motion `4010`，将敌人推开并向全屏呈螺旋状喷射大量爱心弹幕）。
    3. **心符「没我的爱」（心符「没我の愛」）** —— TH13.5 专属符卡。
  - 在 TH14.5《东方深秘录》与 TH15.5《东方凭依华》中，第三张符卡被替换为都市传说主题的 **梦符「祖先大人在看着你哦」（夢符「ご先祖様が見ているぞ」，`Spell_C_Init`，Motion `4020` + `SpellShot_C` `7020`/`7021`）**。
- **残留在 Story Boss 脚本中的基因**：
  - 在 `koishi_boss.nut:186-374`（`Master_Spell_1_Attack`）与 `koishi_boss_shot.nut:113-381`（`Boss_Shot_M1` / `Boss_Shot_M1_OD`）中，剧情模式 Boss 恋恋使用的符卡**本能「Freudian」（本能「フロイディアン」）**会在自身周围不断绽放巨大的玫瑰花并向 12 方向爆射玫瑰弹幕（Motion `4929` 与 `2026`），其弹幕演出结构与旧作玫瑰系符卡一脉相承。

---

## 五、 动作 ID（Motion ID）与音效 ID（SE ID）速查总表

### 1. 角色本体动作（`koishi.nut` / `koishi_base.nut`）
| Motion ID | 名称 / 用途 | 关联函数 | 状态 |
| :--- | :--- | :--- | :--- |
| `0` / `1` / `2` | 站立 / 前走 / 后走 | `Stand_Init` / `MoveFront_Init` / `MoveBack_Init` | 现行 |
| `40` / `41` / `42` | 地面隐身前冲（满隐身穿人）/ 地面后撤 / 空中前冲 | `DashFront_Init` / `DashBack_Init` / `DashFront_Air_Init` | 现行 |
| `1000` / `1500` | 5A / 连打 5AA | `Atk_Low_Init` / `Atk_RushA_Init` | 现行 |
| `1100` / `1101` / `1600` | 远 5A（打喷嚏，`sneeze=120`）/ 喷嚏暴击版 / 连打 5AAA | `Atk_Mid_Init` / `Atk_RushB_Init` | 现行 |
| `1110` / `1750` | 空中 J5A / 连打版 J5A | `Atk_Mid_Air_Init` / `Atk_RushA_Air_Init` | 现行 |
| `1210` / `1211` / `1710` | 2A（无意识自动触发版）/ 2A（手动版）/ 连打 2A | `Atk_HighUnderAuto_Init` / `Atk_HighUnder_Init` / `Atk_RushC_Under_Init` | 现行 |
| `1220` / `1221` / `1223` / `1224` / `1720` | 8A（地面/空中自动版与手动版）/ 连打 8A | `Atk_HighUpper*_Init` / `Atk_RushC_Upper_Init` | 现行 |
| `1230` / `1231` / `1233` / `1234` / `1730` | 6A（地面/空中自动版与手动版）/ 连打 6A | `Atk_HighFront*_Init` / `Atk_RushC_Front_Init` | 现行 |
| `1300` / `1301` | 66A（前冲转圈爱心打击 / 背身版） | `Atk_LowDash_Init` / `Atk_LowDashBack_Init` | 现行 |
| `1310` / `1312` / `1740` | 66B（前冲飞扑 / 隐身穿人背袭版）/ 连打版 | `Atk_HighDash_Init` / `Atk_RushD_Init` | 现行 |
| `1390` | **打击槽主动预置姿势（含假动作预置）** | `AutoAttackSet` / `Atk_Fake_Set` / `Atk_High*_Set` | **半隐藏（需在 1200-1299 动作中按 A）** |
| `1391` | **打击槽蓄力预置姿势** | `AutoAttackSet_Charge` | **未启用（可恢复）** |
| `1802` | 普通地面抓投（Throw） | `Atk_Grab_Hit` / `Atk_Throw` | 现行 |
| `2000` / `2001` | 地面 / 空中 5B、8B、2B 射击 | `Shot_Normal*_Init` | 现行 |
| `2010` / `2011` / `2012` | 地面 6B / 无意识自动触发 6B / 空中 6B | `Shot_Front_Init` / `Shot_FrontB_Init` / `Shot_Front_Air_Init` | 现行 |
| `2020` | 蓄力射击 B（长按 B 释放玫瑰藤蔓） | `Shot_Charge_Fire` | 现行 |
| `2500` / `2501` / `2502` | 怪谈 A+B（接电话 / 电话线瞬移 / 背后电话痛击） | `Okult_Init` / `Okult_Hit` / `Okult_AnyHit` / `Okult_Attack` | `Okult_AnyHit` 未启用 |
| `2590` | **射击槽主动预置姿势** | `AutoShotSet` | **未启用（可恢复）** |
| `3010` | **必杀技 B《成长痛》（斜上升龙 / 垂直高升龙）** | `SP_B_Init` / `SP_B2_Init` | `SP_B2_Init` 未启用 |
| `3011` | **必杀技 B3《成长痛·空中俯冲版》** | `SP_B3_Init` | **未启用（可恢复）** |
| `3020` | **必杀技 C《刺痛心灵》（无意识玫瑰藤蔓释放）** | `SP_C_Init` | **未启用（可恢复）** |
| `3030` | **必杀技 D《焦躁的掠夺者》（原版近身抓投）** | `SP_D_Init` | **未启用（可恢复）** |
| `3031` / `3036` / `3035` | 必杀技 D2《Catch and Rose》（机械臂发射 / 拉回 / 玫瑰引爆） | `SP_D2_Init` / `SP_D2_Hit` / `SP_D_Hit` | 现行 |
| `3040` | 必杀技 E《反射雷达》（引爆心电线） | `SP_E_Init` | 现行 |
| `3051` / `3052` | 必杀技 F《未被回应的恋心》（主动绽放 / 超时自动绽放） | `SP_F_Bloom_Init` / `SP_F_AutoBloom_Init` | 现行 |
| `3060` / `3061` | 必杀技 G《好奇心》（首段起跳斩 / 自动追击二三段斩） | `SP_G_Init` / `SP_G_Loop` | 现行 |
| `3899` | 无意识三槽光标（Take 0/1/2） | `SenceObject` | 现行 |
| `3980` / `3981` | 必杀技槽预置姿势（普通版 / 无敌版） | `AutoSkillSet` | 现行 |
| `4000` / `4010` / `4020` | 符卡 A（超我）/ 符卡 B（本我）/ 符卡 C（祖先大人） | `Spell_A_Init` / `Spell_B_Init` / `Spell_C_Init` | 现行 |
| `4900` / `4901` / `4902` | 怪奇「现在我就在你身后哦」（Last Word 终符） | `Spell_Climax_Init` / `Hit` / `Hit2` / `Finish` | 现行 |
| `4910` / `4920` | Boss 专用施法动作（心雨 / 弗洛伊德玫瑰） | `ST2_Attack` / `Slave_Attack_Sinmyoumaru` | 仅剧情 Boss 使用 |
| `9000` / `9001` / `9002` | 开场动作 A（现身）/ B（接电话）/ 魔理沙双人开场 | `BeginBattleA` / `BeginBattleB` / `BeginBattle_Marisa` | 现行 |
| `9010` / `9011` / `9020` | 胜利动作 A / 胜利动作 B（飞帽子）/ 战败动作 | `WinA` / `WinB` / `Lose` | 现行 |
