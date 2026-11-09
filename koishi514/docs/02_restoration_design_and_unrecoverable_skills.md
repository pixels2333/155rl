# 古明地恋旧作技能还原与重构技术方案（Recoverable vs. Unrecoverable Guide）

本文档详细说明如何在《东方凭依华》（TH15.5）中通过修改角色 Squirrel 脚本（`koishi_base.nut`、`koishi.nut`、`koishi_combo.nut`、`koishi_shot.nut`）恢复古明地恋在《东方心绮楼》（TH13.5）/《东方深秘录》（TH14.5）中的经典技能，并针对已被官方删除的招式提供**基于现有资源的脚本重构实现方案**。

---

## 一、 招式分类总览：原生恢复（本 Mod 已实装） vs. 已删除档案（未实装）

### A. 100% 原生代码恢复项（本 Mod 已实装：仅改 `koishi_base.nut` 输入分发 + `koishi.nut` 四处空表守卫）
1. **`SP_C`《刺痛心灵》（Stinging Mind）**：
   - 原函数 `SP_C_Set`（`koishi.nut:4079`）、`SP_C_Init`（`koishi.nut:4111`）及子机 `SPShot_C` / `SPShot_C_Rose` / `SPShot_C_Petal`（`koishi_shot.nut:1747-1903`）100% 完整。
   - **实装方式**：`koishi_base.nut` 的 5C 分发（`rsv_x == 0`）在 `skillE_line == null` 时改派 `SP_C_Set({cancel=60, mana=200, sp=0})`（`koishi.nut:4081-4086` 要求的三个字段齐全）；已连线时仍走原版 `SP_G_Init`。装填后 60+90 帧由原生 `timeFunc` 自动触发 `SP_C_Init`（Motion `3020`，`SPShot_C` 藤蔓 `6020`/`6021`/`6022`）。
   - **守卫补丁**：`koishi.nut` 仅在 `SP_C_Set` / `SP_C_Init` 入口追加空表守卫（`t == null` 时补全 `cancel/mana/sp/k`），函数体其余逐字节与原版一致。
   - **注意**：`t.k` 决定玫瑰链角度（`8`→`-40°`、`2`→`+40°`、默认 `5`→`0°`），本 Mod 统一传 `k` 缺省（`SP_C_Set` 不传 `k`，由 `SP_C_Init` 守卫补 `k=5`），不做方向派生。
2. **`SP_D_Init`《焦躁的掠夺者》（Fidgety Snatcher，原版近身抓投）与 `SP_D2_Init`《Catch and Rose》双形态并存**：
   - 原函数 `SP_D_Init`（`koishi.nut:4226`，Motion `3030`）100% 完整；命中进 `SP_D_Hit({fast=false})`（`koishi.nut:4266`）。
   - **实装方式**：`koishi_base.nut` 的 6C 分发在 `abs(target.x-x) <= 90 && abs(target.y-y) <= 90`（复用 `koishi_base.nut:610` 已验证的抓取距离写法）时，先置 `attackType[2] = 130`（满足 `SP_D_Init` 的 `koishi.nut:4228` 前置检查）再调用 `SP_D_Init`；否则保持 `SP_D2_Init`（Motion `3031`）。`SP_D_Set` 的 `autoFunc`（`koishi.nut:4212`）未改动，仍只触发 `SP_D2_Init`。
3. **`SP_B2_Init` & `SP_B3_Init`《成长痛》空中双形态**：
   - 原函数 `SP_B2_Init`（`koishi.nut:3926`，Motion `3010`，`vy = -22.5`）与 `SP_B3_Init`（`koishi.nut:3996`，Motion `3011`，`vx = 10, vy = +17.5`）100% 完整（注意：`SP_B3` 实际 Motion 为 `3011`，非旧文档误写的 `3012`）。
   - **实装方式**：
     - **地面（`centerStop^2 <= 1`）8C**：保持 `SP_B_First_Init`。
     - **空中（`centerStop^2 >= 4`）8C**：`y < centerY`（上半屏）派 `SP_B2_Init` 纯垂直升龙；`y >= centerY`（下半屏）派 `SP_B3_Init` 俯冲钻击。`centerStop`/`centerY` 语义复用恋恋全脚本通用写法（如 `futo.nut`/`hijiri.nut`），无需新字段。
     - 两函数入口均追加空表守卫（`t == null` 时补 `cancel=60, sp=0`），其余逐字节与原版一致。
4. **未改动的死代码（仍为 0 引用的档案记录，非本 Mod 实装范围）**：
   - `Atk_High*_Set` / `Atk_Fake_Set`（`koishi_base.nut:579-608` 派生保留）与 `AutoAttackSet_Charge`（Motion `1391`）仍为死代码；`AutoShotSet`（Motion `2590`，`koishi_base.nut:269`）、`Shot_Normal_B`（`koishi_shot.nut:236`）、`Shot_NormalSub` 不在本 Mod 改动范围内，`5B`/`8B`/`2B` 与重打击派生保持原版逐字节一致。

---

### B. 已删减招式档案（函数体/资源缺失，本 Mod 未实装，仅作文档记录）

以下两个 TH13.5 经典招式的函数体已从 TH15.5 客户端删除，**本 Mod 的 `.nut` 文件中不包含它们的任何重构实现**（`grep` 验证：`SP_A_Bloom` / `function SP_A_Set` / `Spell_D_SubterraneanRose` / `Shot_Normal_B` / `Shot_ChargeFull_Rose` 计数均为 0）。记录在此仅为后续工作提供逆向线索：

#### 1. 必杀技 A：《条件反射传送》（Conditioned Teleport，`attackType[2] == 100`）
- **残迹证据**：`koishi_base.nut:386` 的 Slot 2 受击清除枚举中保留 `100`（与 `102`=`SP_C`、`150`=`SP_F`、`160/161/162`=`SP_G` 并列）；`koishi_team.nut:319` 的凭依换人代码引用了恋恋并不存在的 `SP_A_Bloom`（从魔理沙 `marisa.nut:2316` 复制粘贴残留）。
- **为何未实装**：`SP_A_Set` / `SP_A_Init` / `SP_A_Bloom` 函数体、`Occult_Koishi` 分身对象在恋恋脚本中均无定义；凭空捏造触发条件（如冲刺 Motion `40`/`42` + `attackType` 猜测）属于无源码依据的虚构，本 Mod 拒绝捏造。`2C` 保持原版 `SP_F_Init` 不变。
- **未来线索**：可考察 TH13.5 `koishi.pat` 中 Motion `3000` 是否仍存在，或复用恋恋现有的隐身冲刺（Motion `40` 渐变 + `Warp`）/ 电话线瞬移（`Okult_AnyHit` Motion `2501`，注意拼写为 `Okult` 非 `Occult`）做脚本重构，但需先拿到 TH13.5 原脚本对齐参数。

#### 2. TH13.5 符卡 3：心符「没我的爱」（Subterranean Rose / Selfless Love）
- **历史变迁**：TH13.5 三符卡为抑制「Super-Ego」（→TH15.5 `Spell_A_Init` Motion `4000`）、本能「Id 的解放」（→TH15.5 `Spell_B_Init` Motion `4010`）、心符「没我的爱」；TH14.5/15.5 第三符卡被替换为梦符「祖先大人在看着你哦」（`Spell_C_Init` Motion `4020`）。
- **残迹证据**：`koishi_boss.nut:186-374`（`Master_Spell_1_Attack`）与 `koishi_boss_shot.nut`（`Boss_Shot_M1`）中剧情 Boss 符卡本能「Freudian」在周身绽放巨型玫瑰（Motion `4929`/`2026`），演出结构与旧作玫瑰系符卡一脉相承。
- **为何未实装**：符卡函数体、`SpellShot` 弹幕参数、符卡名文本资源均已删除；在 `koishi.nut` 末尾追加 `Spell_D_SubterraneanRose_Init` 会改变文件哈希对应关系且未经 `CompileFile` 明文分支验证，本 Mod 未实装。


---

## 二、 技能槽位替换映射表（Mod 默认方案 vs. 原版对比）

| 输入指令 | 原版 TH15.5 行为 | Mod 还原版（TH13.5 经典模式）行为 | 涉及函数与 Motion |
| :--- | :--- | :--- | :--- |
| **5C（地面/空中中立 C）** | `SP_G_Init`《好奇心》跳跃斩 | **`SP_C_Set` / `SP_C_Init`《刺痛心灵》（无意识玫瑰藤蔓预置与连环引爆）** | `3980` -> `3020` + `SPShot_C` (`6020`/`6021`/`6022`) |
| **5C 槽已装填后** | 超时由 `timeFunc` 自动触发 `SP_C_Init`（`koishi.nut:4186` 为 `CommonAutoAttackSet` 续装填） | 装填后（`attackType[2] == 102`）再次按 **5C** 会再次调用 `SP_C_Set` 刷新预置（60 帧后自动触发 `SP_C_Init` 释放玫瑰链；槽位忙时 `Cancel_Check` 按 `autoCancelLevel` 规则自然失败，不会吞输入） | `SP_C_Set` / `SP_C_Init({cancel=60, mana=200, sp=0})` |
| **6C（前 + C）** | 仅 `SP_D2_Init`（远距机械臂） | **近距离（`abs(target.x-x) <= 90` 且 `abs(target.y-y) <= 90`）先装填 `attackType[2] = 130` 再调用原生 `SP_D_Init`（Motion `3030` 近身扑抓 → 命中进 `SP_D_Hit({fast=false})`）；中远距离保持 `SP_D2_Init`（Motion `3031`）** | `3030` / `3031` -> `3035` |
| **8C（上 + C）** | 地面/空中均为 `SP_B_First_Init` | **地面：原版斜上升龙 `SP_B_First_Init`；空中上半屏：`SP_B3_Init`（Motion `3011` 空中高速俯冲爱心钻）；空中下半屏：`SP_B2_Init`（Motion `3010` `vy=-22.5` 纯垂直冲天高升龙）** | `3010` / `3011` + `SPShot_B` (`6010`) |
| **2C（下 + C）** | `SP_F_Init`《未被回应的恋心》玫瑰环 | **保持原版 `SP_F_Init` 不变**（`SP_A` 函数体已删、无 Slot 1 装填路径，本 Mod 未实装条件反射传送） | `3051` / `3052` |
| **4C（后 + C）** | `SP_E_Set`《反射雷达》心电线 | 未连线时保持 `SP_E_Set`；**已连线（`skillE_line != null`）时再次按 4C 改派 `SP_G_Init`《好奇心》**（兼顾新旧招式全收录） | `3980` -> `3040` / `3060` |
| **重打击（8A/2A/6A）中按 A** | `Atk_High*_Set` / `Atk_Fake_Set`（`koishi_base.nut:579-608` 已有派生） | **保持原版派生不变**（`AutoAttackSet_Charge` 仍为 0 引用死代码，本 Mod 未改动此路径） | `1390` / `1391` |
| **普通射击（5B/8B/2B）** | 直接发射 `Shot_Normal` | **保持原版不变**（`AutoShotSet` 仍为 0 引用死代码，本 Mod 未改动 Slot 1 路径） | `2000` / `2001` |
| **连段自动衔接（Rush Combo）** | 原版 `koishi_combo.nut` 逻辑 | **保持原版不变**（本 Mod 未改动 `koishi_combo.nut`，与原版逐字节一致） | `koishi_combo.nut` |
