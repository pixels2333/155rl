# 《东方心绮楼》(TH13.5) → 《东方凭依华》(TH15.5) 招式与动作帧移植：制作心得与踩坑全记录

本文档沉淀了在开发 **`koishi514`（古明地恋 TH13.5 全符卡 + LastWord 跨代还原 Mod）** 过程中，从引擎底层文件重定向、Squirrel VM 脚本兼容、`.pat` 二进制动画格式跨版本转换（`ver=13` → `ver=17`），到 LastWord 时停演出与粒子特效还原的全部核心经验与踩坑记录。

---

## 一、 核心架构与跨代移植全景

| 维度 | 《东方心绮楼》(TH13.5 v1.34b) | 《东方凭依华》(TH15.5 v1.21b) | 跨代移植核心差异 |
| :--- | :--- | :--- | :--- |
| **加密包 `.pak` 索引** | 单块 64B RSA 解密，明文含完整路径与大小 | 每文件 **3 块 64B RSA**，路径哈希 `name_hash = h0 ^ k2`（FNV-1a 小写 Shift-JIS） | 解包/比对 TH15.5 资产时必须先做 3 块 RSA 异或还原 |
| **动作帧文件 `.pat`** | **`ver=13`**：帧内嵌图层，带独立 `.bmb` 粒子表，旋转/缩放/锚点分开存储 | **`ver=17`**：逻辑帧与图层分离，移除 `.bmb` 支持，使用 **4×4 浮点变换矩阵** | 锚点 `(cx, cy)` 必须折算进矩阵平移项 `(tx, ty)`，`.bmb` 需改写为脚本粒子 |
| **贴图颜色字节序** | `[A, R, G, B]`（首字节为 Alpha） | `D3DCOLOR` 小端 `[B, G, R, A]`（末字节为 Alpha，`uint32` 高 8 位为 Alpha） | 直接按 4 字节拷贝会导致透明层变成不透明纯黄/纯色（如 `0xFFFFFF00`） |
| **Squirrel Actor 体系** | 角色与弹幕分属 `this.u`（内部逻辑表）、`this.p`（玩家属性表）、`::actor.target` | 扁平化挂载在 Actor 实例 `this` 上（`this.target`、`this.team`、`this.owner`） | TH13.5 反编译代码中的 `this.u.*` / `this.p.*` / `::actor` 在 TH15.5 中直接调用必崩 |
| **Squirrel 对象槽位** | 动态 Table 属性可自由新增 `<-` | C++ Native `Manbow::Actor2D` 绑定实例，**禁止向未预定义槽位新增字段** | 向 `this` 新增 `this.atkRate_Pat <- 1.0` 会抛出 `the index doesn't exist` 致命异常 |

---

## 二、 踩坑实录与根因剖析（按严重度排序）

### 坑点 1：在 TH15.5 Actor/Shot 实例上使用 `<-` 声明未定义成员变量导致瞬间闪退
- **故障表现**：释放符卡或 LastWord 衍生弹幕时，游戏直接闪退（无弹窗或 Squirrel VM 抛出未捕获异常）。
- **根因剖析**：
  - 在 TH13.5 中，角色与弹幕的自定义状态存储在普通 Squirrel Table（`this.u` 或 `this.p`）中，随时可以用 `<-` 插入新键值。
  - 在 TH15.5 中，角色与弹幕的 `this` 是 C++ 导出的定长类实例（或带严格 delegate 限制的表）。如果原版类定义中没有 `atkRate_Pat` 等字段，执行 `this.atkRate_Pat <- ...` 或直接读取 `this.atkRate_Pat` 会立即触发 `the index 'atkRate_Pat' doesn't exist` 并导致主线程崩溃。
- **标准解法**：
  - **读取时**：必须使用 `"atkRate_Pat" in this ? this.atkRate_Pat : 1.0` 进行存在性防御。
  - **写入时**：仅当 `"atkRate_Pat" in this` 为真时才赋值，或者通过 `SetShot` / `SetFreeObject` 的初始化参数表 `t_`（如 `t_.rate <- ...`）传递倍率。

---

### 坑点 2：TH13.5 特有的 `this.u`、`this.p`、`::actor`、`PlaySE_Actor`、`ReleaseTargetActor` 跨代调用崩溃
- **故障表现**：
  1. 恋恋符卡 B（*「ご先祖様が見ているぞ」*，`Spell_B_Init`）起手第 30 帧切入 `Take 2` 生成 `BossB_Core` / `BossB_Shot` 时立刻闪退。
  2. LastWord（*「ブランブリーローズガーデン」*，`Spell_Climax_Init` → `Spell_Climax_Hit` → `Spell_Climax_Finish`）命中或转场时立刻闪退。
- **根因剖析**：
  - 从 TH13.5 `.nut` 字节码反编译出的脚本大量使用了心绮楼专属 API 与层级结构：
    - `this.u.SetMotion(...)` / `this.u.Climax_Back` / `this.u.BossB_Core`
    - `this.p.demoObject` / `this.p.enableKO`
    - `::actor.target`
    - `this.PlaySE_Actor(id)`
    - `this.ReleaseTargetActor(arr)`
    - 弹幕中通过 `this.owner.motion` 判断母体状态，但当母体不是角色而是上一级子物体（如 `BossB_Shot` 递归生成下一节 `BossB_Shot`）时，`this.owner` 指向是否正确、以及 `this.owner.u` 是否存在。
- **标准解法（跨代 API 映射表）**：

| TH13.5 写法（在 TH15.5 中会崩溃） | TH15.5 正确写法 |
| :--- | :--- |
| `this.u.xxx` / `this.p.xxx` | `this.xxx` |
| `::actor.target[this.target].p.enableKO` | `this.target.enableKO` |
| `::actor.target[this.target].u.DamageGrab_Common.call(...)` | `this.target.DamageGrab_Common(...)` |
| `this.PlaySE_Actor(se_id)` | `this.PlaySE(se_id)` |
| `this.ReleaseTargetActor(this.flag3)` | 遍历数组并对非空元素调用 `a.ReleaseActor()` |
| `SetFreeObject(..., this.u.Climax_Back, null)` | `SetFreeObject(..., this.Climax_Back, {}, this.weakref())`（**注意第 5 参数 `initTable` 传 `{}` 而不要传 `null`！**） |
| `SetShotTrim(...)` | `SetFreeObjectDynamic(...)`（**严禁映射为普通 `SetFreeObject`，否则设置 `this.anime.left/top/width/height/center_x/center_y` 会触发 `trying to set 'null'` 闪退！**） |
| `this.hitTarget = null` | `this.hitTarget = {}`（TH15.5 多段命中重置必须赋空表 `{}`，赋 `null` 会在下次碰撞时触发 `trying to set 'null'` 闪退） |
| `this.SetDamage.call(this.target, dam_, null)` / `this.target.life` | `this.target.SetSelfDamage(dam_)` / `this.target.team.life` |
| `this.(FadeIn\|FadeOut\|BackFadeIn\|BackFadeOut\|ClearFadeEffect\|ClearBackFadeEffect\|BackColorFilter\|BackColorFilterOut\|EraceBackGround\|ResetBackGround)(...)`（在 `*_shot.nut` 中） | `this.owner.\1(...)`（防止弹幕/特效调用 `this.ReleaseActor()` 销毁自身后，`effect.nut` 渐变结束访问 `this.initTable.pare.team` 触发 `the index 'team' does not exist` 闪退） |

---

### 坑点 2.5：`SetShotTrim` 误映射为 `SetFreeObject` 导致秦心（Kokoro）LastWord 抓投后 `trying to set 'null'` 闪退
- **故障表现**：秦心释放 LastWord（*「仮面喪心舞　暗黒能楽」*）起手抓投命中对手后，在 `Spell_ClimaxCatch_Init` 第 30 帧生成 `Climax_Back` 与 `Climax_Back_B` 滚动能面背景时，游戏瞬间闪退，`th155_log.exe.log` 报错：
  - `script error [trying to set 'null']`（连续 2 条，分别对应内层 `t.init.call(this, t)` 与外层 `FreeObjectCommonInit` 异常重抛，最终触发 `Sqrat::Function::Execute` `0xc0000409` 崩溃）。
- **根因剖析**：
  - 在 TH15.5 中，普通 `SetFreeObject` 底层调用 `::manbow.Actor2DProcGroup.CreateActor2D`，其 `this.anime` 绑定的是 C++ 类 `manbow::Animation2D`，该类的 Sqrat 属性表（`varTable`）**不包含** `left`、`top`、`width`、`height`、`center_x`、`center_y` 等动态裁剪属性！
  - 只有 `SetFreeObjectDynamic`（底层调用 `CreateActor2DDynamic`）才将 `this.anime` 绑定为 `manbow::Animation2DDynamic`，支持读写 `this.anime.left = 0; this.anime.height = 800; this.anime.width = 1800;` 等动态 UV 裁剪属性（与 TH15.5 原版 `kokoro_shot.nut` 中 `Climax_Mainten` 的生成方式一致）。
  - TH13.5 中的 `this.SetShotTrim(...)` 正是动态裁剪对象的生成函数。若将其映射为普通 `SetFreeObject`，执行 `this.anime.left = 0` 时会在 Sqrat `_set` 元方法中因找不到属性槽位而报 `trying to set 'null'`。
- **标准解法**：
  - 将 TH13.5 的 `this.SetShotTrim(...)` 统一映射为 `this.SetFreeObjectDynamic(...)`。

---

### 坑点 2.6：在 Squirrel Class 实例（`player_class`）方法内使用 `<-` 触发 `class instances do not support the new slot operator`
- **故障表现**：在角色状态函数（如 `koishi.nut` 的 `Spell_Climax_Init` 写 `this.demoObject <- []`，或 `ichirin.nut` 的 `Spell_Climax_Init` 写 `this.unzanMode <- 0`）执行时直接报错闪退。
- **根因剖析**：
  - 在 TH15.5 中，`CompileFile("data/actor/<char>.nut", this.player_class)` 编译脚本时，文件顶层作用域的 `this` 是 Squirrel **Class 对象**（支持 `<-` 新增类成员变量）；但在进入战斗实例化后，函数运行时的 `this` 是 **Class Instance（类实例）**，Squirrel VM 严禁对类实例使用 `<-` 运算符！
- **标准解法**：
  - 凡是 TH15.5 原版 `player_member.nut` / `<char>_init.nut` 未声明的自定义角色字段（如 `ichirin.nut` 的 `unzanMode`、`unzanMaxTime`、`unzanPart`），必须在 `<char>.nut` 的**文件顶层（函数外部）**使用 `this.unzanMode <- 0;` 声明到 `player_class` 类定义上，而在函数内部一律使用 `=` 赋值！

---

### 坑点 2.7：多级子弹幕/特效链式生成时 `this.owner` 指向子物体而非玩家本体（`player_class`）
- **故障表现**：与古明地恋 `Climax_Back` 踩过的坑完全一致——当 `player` 生成子物体 A（`shot_class` 或 `player_effect_class`），子物体 A 又通过 `this.SetShot` / `this.SetFreeObject` 生成子物体 B 时，在 TH15.5 的 `CreateActor2D` 中 `B.owner` 会被设为 `A`（而非 `player`）。一旦 `B` 访问 `this.owner.flag4`（灵梦 `Climax_LockB_Ball`）、`this.owner.dish`（布都 `Climax_Fire`）、`this.owner.KnockBackTarget`（猯藏 `Climax_Shot_Mamizou`）或 `this.owner.Climax_*`，就会因 `A`（`player_effect_class` / `shot_class`）不存在这些玩家专属槽位而立刻闪退！
- **标准解法**：
  - 在 `*_shot.nut` 的所有 Climax 函数入口统一注入 `this.owner` 溯源归一化逻辑，确保 `this.owner` 始终指向顶层 `player_class` 实例（`actorType == 1`）：
    ```squirrel
    while (this.owner && "actorType" in this.owner && this.owner.actorType != 1 && this.owner.owner && this.owner.owner != this.owner)
    {
        this.owner = this.owner.owner;
    }
    ```

---

### 坑点 2.8：在 `*_shot.nut` 子物体上直接调用 `this.BackFadeIn` / `this.FadeOut` / `this.FadeIn` 导致渐变结束时 `the index 'team' does not exist` 闪退
- **故障表现**：丰聪耳神子（`miko`）释放 LastWord（*「詔を承けては必ず慎め」*）命中并在光柱收束结束（`miko_shot.nut` `Climax_Shot`）30 帧后，游戏必现闪退，`th155_log.exe.log` 报错：
  - `script error [the index 'team' does not exist]`（同样隐藏于博丽灵梦 `reimu_shot.nut` 的 `Climax_Face` 全屏退场白幕中）。
- **根因剖析**：
  - 在 TH15.5 的 `data/actor/script/actor_screen.nut` 中，`FadeIn`、`FadeOut`、`BackFadeIn`、`BackFadeOut`、`ClearFadeEffect`、`ClearBackFadeEffect`、`BackColorFilter`、`BackColorFilterOut`、`EraceBackGround`、`ResetBackGround` 内部都会把调用者的弱引用 `t_.pare <- this.weakref();` 传给屏幕渐变 Actor（`EF_FadeScreen` / `EF_FadeBackScreen`）。
  - 当渐变动画历经 `time_` 帧结束时，`data/script/effect.nut` 的 `EF_BeginFadeIn` / `EF_BeginBackFadeIn` 会执行：
    ```squirrel
    if (this.initTable.pare.team.fade_back == this)
    {
        this.initTable.pare.team.fade_back = null;
    }
    ```
  - 在 `miko_shot.nut` 的 `Climax_Shot` 中，`keyAction` 执行了：
    ```squirrel
    this.BackFadeIn(0.0, 0.0, 0.0, 30);
    this.ReleaseActor();
    ```
    由于 `this` 是弹幕实例（`Climax_Shot`），下一行 `this.ReleaseActor()` 立即销毁了该弹幕，导致渐变特效持有的弱引用 `this.initTable.pare` 变成 `null`！30 帧后渐变结束时访问 `this.initTable.pare.team` 即对 `null` 取 `.team`，直接触发 `script error [the index 'team' does not exist]` 崩溃！
  - 同理，灵梦 `reimu_shot.nut` 的 `Climax_Face` 在第 240 帧调用 `this.FadeOut(1.0, 1.0, 1.0, 120)` 并在第 360 帧调用 `this.ReleaseActor()`，恰好在同一帧销毁自身导致 `pare` 变 `null`。
- **标准解法**：
  - 在 `*_shot.nut`（`is_shot == True`）中，所有对屏幕/背景渐变与滤镜方法（`FadeIn`、`FadeOut`、`BackFadeIn`、`BackFadeOut`、`ClearFadeEffect`、`ClearBackFadeEffect`、`BackColorFilter`、`BackColorFilterOut`、`EraceBackGround`、`ResetBackGround`）的调用，必须统一重写为通过玩家本体调用：`this.owner.<FadeMethod>(...)`（配合坑点 2.7 的 `this.owner` 溯源归一化，保证 `pare` 始终指向存活且拥有 `.team` 属性的 `player_class` 实例）。

---

### 坑点 2.9：Squirrel 3 字节码反编译器跨 `if` 块复用同名局部变量寄存器导致漏写 `local` 触发 `the index 't_' does not exist` 闪退
- **故障表现**：云居一轮（`ichirin`）释放 LastWord（`ichirin.nut` `Spell_Climax_Init`）在第 90 帧准备变身巨大云山时，或灵梦（`reimu_shot.nut` `Climax_LockB_Ball`）在第 100 帧发射追踪激光时，访问 `t_ = {}` 触发 `script error [the index 't_' does not exist]` 闪退。
- **根因剖析**：
  - Squirrel 3 编译器在同一个函数内遇到多个并列的 `if (...) { local t_ = {}; ... }` 块时，会复用同一个物理寄存器（如 `reg = 1`），并在字节码 `localvars` 调试表中记录多条同名但生命周期（`start_pc..end_pc`）不重叠的条目：
    - 例如 `ichirin.nut` `Spell_Climax_Init`：`[('t_', reg=1, start_pc=9, end_pc=50), ('t_', reg=1, start_pc=70, end_pc=103)]`
    - 例如 `reimu_shot.nut` `Climax_LockB_Ball`：`[('t_', reg=1, start_pc=92, end_pc=130), ('t_', reg=1, start_pc=138, end_pc=198)]`
  - 反编译器早期以 `(reg, name)` 作为 `declared_locals` 集合的唯一键，导致第一个 `if (this.count == 25)` 块输出 `local t_ = {};` 后，第二个 `if (this.count == 90)` 块误以为 `t_` 已声明，直接输出了无 `local` 的 `t_ = {};`。
  - 在编译为源码后，第一个 `local t_` 的词法作用域仅限于第一个 `if { ... }` 块内部；第二个 `if` 块中的 `t_ = {}` 因缺少 `local` 关键字，在 Squirrel 运行时退化为向 `this`（实例表）查找并写入 `this.t_`，直接触发 `script error [the index 't_' does not exist]`！
- **标准解法**：
  - 反编译器在判定局部变量首次声明时，`declared_locals` 的键必须包含 `localvars` 条目的起始 PC（即 `(reg, name, start_pc)`），且仅匹配满足 `start_pc <= pc <= end_pc` 的活跃生命周期条目（同时跳过紧邻 `OP_FOREACH` 指令前的编译器自动 `LOADNULLS` 清零指令），确保每个独立代码块内的局部变量均正确输出 `local` 关键字。

---

### 坑点 2.10：河城荷取 (`nitori.nut`) 潜入水下 `this.masterAlpha = 0.0` 在 TH15.5 下未自动恢复导致准星与角色永久隐身
- **故障表现**：河城荷取释放 LastWord（*「スーパースコープ 3D」*）潜入水下后，第一人称准星界面（`Spell_Climax_Cut`）与大招结束从空中跳出（`Spell_Climax_End`）乃至后续整局对战中，荷取本体始终处于完全透明隐身状态。
- **根因剖析**：
  - 在 `nitori.nut` 的 `Spell_Climax_Init` 第 29/30 帧，荷取潜入水中将 `this.masterAlpha = 0.0;`，随后调用 `this.Spell_Climax_Cut(null)`。
  - 在 TH13.5 原版中，荷取拥有专属的每帧颜色更新函数 `NitoriColor()`，只要光学迷彩标志未开启，每帧会自动执行 `this.masterAlpha += 0.0275` 直至恢复为 `1.0`。
  - 而在 TH15.5 中，所有角色统一使用 `CommonColorUpdate()`，该函数**不会**自动递增恢复 `this.masterAlpha`！因此一旦设为 `0.0` 且未在后续状态显式重置，角色就会永久隐身。
- **标准解法**：
  - 在 `nitori.nut` 的 `Spell_Climax_Cut` 开头、`Spell_Climax_End` 开头、以及 `Spell_Climax_Init` 的 `lavelClearEvent` 清理回调中全部显式加上 `this.masterAlpha = 1.0;`。

---

### 坑点 3：`SetFreeObject` / `SetShot` 第 5 参数传 `null` 导致子函数读取 `t.xxx` 或 `this.initTable.pare` 崩溃
- **故障表现**：LastWord 命中进入白屏特写（`Spell_Climax_Hit`）后，在生成背景 `Climax_Back` 或立绘 `Climax_Cut` 时闪退。
- **根因剖析**：
  - TH13.5 中 `SetFreeObject(640, 360, 1.0, this.u.Climax_Back, null)` 允许第 5 参数传 `null`。
  - 但在 TH15.5 中，`SetFreeObject(x, y, dir, func, t, pare)` 会将第 6 参数 `pare` 写入 `t.pare`（即 `this.initTable.pare`），且子物体在递归生成下一层背景（如 `Climax_Back` 每 40 帧生成下一张旋转背景 `Climax_Back`、以及 `Climax_Cut` 在第 181 帧回调 `Spell_Climax_Finish`）时，其 `this.owner` 可能会变成上一级 `FreeObject` 而不是恋恋本体！
- **标准解法**：
  - 调用 `SetFreeObject` 时始终传入空表 `{}` 与 `this.weakref()`：
    ```squirrel
    this.SetFreeObject(640, 360, 1.0, this.Climax_Back, {}, this.weakref()).weakref()
    ```
  - 在子物体内部获取恋恋本体时，使用双重回退：
    ```squirrel
    local p_ = ("pare" in this.initTable && this.initTable.pare) ? this.initTable.pare : (("demoObject" in this.owner) ? this.owner : null);
    ```

---

### 坑点 4：`.pat` 跨版本（`ver=13` → `ver=17`）矩阵平移项 `tx, ty` 归零导致特写贴图只显示右下角 1/4
- **故障表现**：LastWord 命中后不再闪退，但特写背景（`climaxBack.png`）与恋恋立绘（`climaxCutA0000.png`）只有右下角 1/4 出现在屏幕里，左上 3/4 屏幕一片惨白，且放大时沿着左上角诡异拉伸。
- **根因剖析**：
  - **TH13.5 (`ver=13`)** 的图层元素直接存储整数锚点偏移 `(cx, cy)` 与百分比缩放 `(sx%, sy%)`：
    - `7901 Take 0` (`climaxBack.png`, `1280×720`)：`cx = 640, cy = 360, sx = 125%, sy = 125%`
    - `7901 Take 1` (`climaxCutA0000.png`, `875×878`)：`cx = 598, cy = 390, sx = 100%, sy = 100%`
  - **TH15.5 (`ver=17`)** 的图层元素改用 **16 浮点数的 4×4 变换矩阵 `mat[0..15]`**，顶点原始坐标为 `[0..w, 0..h]`，经矩阵变换后坐标为 `(x * sx + tx, y * sy + ty)`。
  - 早期制作 `koishi.pat` 时，`7901 Take 0` 和 `7901 Take 1` 的 `mat[12] (tx)` 与 `mat[13] (ty)` 被填成了 `0.0, 0.0`！当脚本在屏幕中心 `(640, 360)` 生成它们时，图片的左上角 `(0, 0)` 被画在了屏幕中心 `(640, 360)`，导致左半屏与上半屏完全空白！
  - 此外，TH13.5 原版给 `climaxBack.png` 设置了 **`125%` 缩放（`sx = 1.25, sy = 1.25`）**，是因为 `Climax_Back` 有 `±8°` 的随机旋转（`this.rz = (8 - this.rand() % 17) * 0.01745329`），若按 `1.0` 倍缩放旋转 `8°`，`1280×720` 的四个屏幕角落会露出白边。
- **标准解法（`ver=13` → `ver=17` 矩阵换算公式）**：
  - 无旋转时：
    $$sx = \frac{\text{scale\_x}}{100.0}, \quad sy = \frac{\text{scale\_y}}{100.0}$$
    $$tx = -cx \times sx, \quad ty = -cy \times sy$$
  - 因此：
    - `Motion 7901 Take 0` (`climaxBack.png`)：`sx = 1.25, sy = 1.25, tx = -800.0, ty = -450.0`
    - `Motion 7901 Take 1` (`climaxCutA0000.png`)：`sx = 1.0, sy = 1.0, tx = -598.0, ty = -390.0`

---

### 坑点 5：`.pat` 颜色字段 `argb` 字节序反转导致透明隐藏帧变成“不透明小黄人”
- **故障表现**：LastWord 命中进入特写时，`Motion 4901 Take 0/1` 本应将场地上的恋恋本体隐藏，结果场地上却出现了一个不透明纯黄色的恋恋贴图（`stand0000.bmp` / `test_skillA0002.bmp`）。
- **根因剖析**：
  - 在 TH13.5 (`ver=13`) 中，颜色字段按字节依次为 `[Alpha, R, G, B]`，全透明白色的字节序列为 `00 ff ff ff`。
  - 在 TH15.5 (`ver=17`) 中，颜色字段为小端 `uint32` 的 `D3DCOLOR_ARGB(a, r, g, b)`，内存中字节依次为 `[B, G, R, A]`。
  - 若直接把 `00 ff ff ff` 原样拷贝进 TH15.5，按小端读取就是 `0xFFFFFF00`——即 **`Alpha = 0xFF`（完全不透明）、`R = 0xFF`、`G = 0xFF`、`B = 0x00`（纯黄色）**！
- **标准解法**：
  - 从 TH13.5 转换颜色到 TH15.5 时，必须将 `[A, R, G, B]` 重排为小端 `uint32` `(A << 24) | (R << 16) | (G << 8) | B`（内存字节 `[B, G, R, A]`，全透明白色为 `ff ff ff 00` 即 `0x00FFFFFF`）。

---

### 坑点 6：TH15.5 移除 `.bmb` 粒子系统导致 `climaxRose.bmb` 相关 Take 图层数为 0（特效缺失）
- **故障表现**：LastWord 起手大玫瑰爆破（`7900 Take 2`）、特写背景玫瑰风暴（`7901 Take 2`）、以及特写结束后场上的巨型玫瑰喷发（`7901 Take 4`）贴图不全或几乎没有视觉特效。
- **根因剖析**：
  - TH13.5 的 `koishi.pat` 头部包含 5 个 `.bmb` 粒子文件引用（其中 `bmb[2..4]` 指向 `data/actor/koishi/bmsln/climaxRose.bmb`）。
  - 解析 `climaxRose.bmb`（`IBMB7` 格式）的 UTF-16LE 纹理表可知，它内部引用了 `ball`、`pulseA`、`roseC` 三张贴图，用于在爆破中心生成发光球+冲击波，并向四周高频喷射整朵玫瑰与花瓣。
  - TH15.5 的 `.pat` (`ver=17`) 彻底移除了 `.bmb` 表支持，导致转换后的 `7901 Take 2` 与 `7901 Take 4` 的 `nb_layer` 变成了 `0`（空图层），而 `7900 Take 2` 第 0 帧变成了 `tex = -1`（空贴图）。
- **标准解法**：
  1. 在 `koishi.pat` 中将 `Motion 7900 Take 2` 的第 0/1 帧修复为 `texture\roseC.png`（`2.0x` → `2.5x` 绽放玫瑰）。
  2. 在 `koishi_shot.nut` 的 `Climax_RoseExp`、`Climax_BackFire`、`Climax_BackFire_Hit`、`Climax_BackFire_Petal` 中，用 Squirrel 脚本粒子生成器替代 `.bmb` 发射器，组合调用已有的 `Motion 7921 Take 0`（整朵玫瑰）、`Motion 7902`（红花瓣）、`Motion 7903`（蓝花瓣）、`Motion 7904`（发光加算花瓣）。
  3. **特别注意**：在特写时停期间（`SetTimeStop(600)`）由 `Climax_BackFire` 生成的所有粒子，必须显式调用 `this.EnableTimeStop(false)` 与 `this.DrawScreenActorPriority(105)`，否则粒子出生后会立刻被时停冻结在原地，且层级会被背景遮挡！

---

### 坑点 7：`LabelReset()` 在 TH15.5 中不自动绑定动作结束回调，导致 LastWord 收招后角色卡死
- **故障表现**：LastWord 特写结束、打完 90 帧玫瑰喷发伤害后，恋恋切入收招动作 `Motion 4902 Take 1` → `Take 2`（收起玫瑰帽子），动作播完后角色定格在最后一帧无法移动。
- **根因剖析**：
  - 在 TH13.5 中，`LabelClear()` 内部会自动调用 `SetEndMotionCallbackFunction(this.EndtoFreeMove)`，因此任何非循环（`loop_flag = 0`）的末尾 Take 播放完毕后都会自动回到自由移动状态。
  - 在 TH15.5 中，`LabelReset()` 会将 `this.keyAction = null` 清空，且不会默认绑定 `EndtoFreeMove`；当 `4902 Take 2` 播放完毕时，由于 `keyAction[2]` 为空且 `stateLabel = null`，角色永远停留在 `4902 Take 2` 最后一帧。
- **标准解法**：
  - 在 `Spell_Climax_Finish` 中同时配置 `keyAction` 与帧数兜底回调：
    ```squirrel
    this.keyAction = [
        null,
        null,
        this.EndtoFreeMove
    ];
    this.stateLabel = function ()
    {
        if (this.count >= 90)
        {
            this.SetMotion(this.motion, 1);
            this.count = 0;
            this.stateLabel = function ()
            {
                if (this.count >= 74) // Take 1 (60f) + Take 2 (14f)
                {
                    this.EndtoFreeMove();
                }
            };
        }
    };
    ```

---

## 三、 文件编码与多目录同步铁律

1. **脚本编码铁律（`CP932 / Shift-JIS`）**：
   - `koishi.nut` 等包含日文符卡名（如 `"「ブランブリーローズガーデン」"`）的脚本文件，必须以 **`cp932`（Shift-JIS）** 编码读写！若误用 UTF-8 保存，游戏内符卡宣言 UI 会显示乱码甚至触发字符串解析异常。
2. **四目录 100% 同步铁律**：
   - 本地开发与测试涉及以下 4 个目录，由于 `winmm.dll` Mod Loader 会按优先级检索 `mods\koishi514\data\` 与 `data\`，每次修改 `.nut`、`.pat` 或贴图后，必须同步更新全部 4 处并校验 MD5 一致性：
     1. `D:\programme\155rl\koishi514\mod\data\`（源码工作区）
     2. `D:\programme\155rl\koishi514\release\mods\koishi514\data\`（发布包目录）
     3. `D:\[th15.5] 东方凭依华（英文版+日文版）\mods\koishi514\data\`（Mod Loader 挂载目录）
     4. `D:\[th15.5] 东方凭依华（英文版+日文版）\data\`（游戏根目录直读覆盖目录）
3. **调色板（`palette000..007.bmp`）切勿用 TH13.5 覆盖**：
   - TH13.5 与 TH15.5 的普通动作帧 `.bmp` / `.png` 均为相同的 `TFBM` 格式，可直接搬运；但 **`palette*.bmp` 必须保留 TH15.5 原版**，否则 2P/Slave 配色槽位会出现色表错乱。
