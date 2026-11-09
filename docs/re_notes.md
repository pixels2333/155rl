# th155.exe v1.21b 逆向笔记（内存地图构建过程）

本文记录 `configs/offsets.yaml` 中每一条关键偏移的**来源与验证方式**，以及
Bridge DLL / Python 读取层依赖的对象图结构。所有结论都可以用仓库内脚本复现。

环境：Windows 10 x64，游戏 32 位进程（WOW64），`th155.exe` SHA256
`fc6708094b53afa3c00584ecb6e9be14bbf57c2fcd568ef8017af27f8b8ee1f8`。

---

## 0. 目标二进制元数据

| 项目 | 值 |
|---|---|
| 文件大小 | 5 287 640 字节 |
| PE TimeDateStamp | 1524477382（2018-04-23T09:56:22Z） |
| 首选基址 | `0x00400000` |
| 实测运行基址 | `0x4A0000`（启用重定位 / ASLR） |
| 架构 | x86 (i386, PE32) |

> 由于每次启动基址不同，`offsets.yaml` 中**全部使用 RVA**。运行时：
> `基址 = TH32CS_SNAPMODULE 快照中 th155.exe 的 modBaseAddr`。

---

## 1. Squirrel VM 定位

| 步骤 | 地址/偏移 | 验证方式 |
|---|---|---|
| 全局 `SQVM*` | RVA `0x4DACE4` | 读取该地址得到一个堆指针，`+0x30` 处的 tag 为 `0x0A000020`（table） |
| roottable | `*(SQVM+0x34)` | 遍历该表可看到 `battle` / `menu` / `input_all` 等全局名 |

**对象布局（实机逐字节核对）**

| 结构 | 偏移 | 说明 |
|---|---|---|
| SQObjectPtr | tag@0, value@4 | tag 低 24 位是类型，`0x00FFFFFF` 掩码 |
| SQString | len@0x14, data@0x1C | **不是 0/8**（早期误记，已用 `scripts/sq_dump.py` 纠正） |
| SQTable | nodes@0x20, num@0x24 | 节点 20 字节：value@0、valueTag@4、keyTag@8、key@12、next@16 |
| SQArray | values@0x18, size@0x1C | 元素间距 8 字节 |
| SQInstance | class@0x1C, userptr@0x20, values@0x2C | 字段间距 8 字节 |
| SQClass | members@0x18（成员表）, 字段数@0x24 | 成员表 value 的低 24 位是字段下标，`0x02000000` 位表示“是字段” |

类型 tag：null `0x01000001`、int `0x05000002`、float `0x05000004`、
bool `0x01000008`、string `0x08000010`、table `0x0A000020`、array `0x08000040`、
closure `0x08000100`、instance `0x0A008000`。

---

## 2. 全局战斗对象

`::battle` 在 th155 中是 **table**（不是 instance），`::battle.team` 是长度为 2 的
**array**，元素是 `PlayerTeamData` 的 instance。

```
roottable
 └─ battle            (table)
     ├─ state         int   8=对战中 / 32=回合结束 / 128=下一回合
     ├─ time          int   剩余帧（99 秒 × 90 = 8910；练习模式恒为 -1）
     ├─ time_unit     int   90
     ├─ count         int   全局帧计数（每帧 +1，可作帧同步信号）
     ├─ round/match_num/winner
     ├─ endWinDemo[2] bool  ← 置 true 可跳过胜利演示
     ├─ endLoseDemo[2] bool
     ├─ PracticeRestart closure（仅练习模式存在）
     ├─ RoundReset    closure（对战模式）
     └─ team[2]       (array of instance)
         ├─ master/slave   Player instance
         ├─ input          Manbow.InputMulti instance
         ├─ life/life_max/regain_life
         ├─ sp/sp_max/mp/op
         ├─ spell_active/spell_use_count/slave_ban/combo_count
         └─ master_name/slave_name（字符串）
```

验证脚本：`scripts/verify_offsets.py`（每 0.25s 采样一次，要求帧计数递增、
计时器递减、坐标落在场地内）。

---

## 3. 角色原生结构（Manbow::Actor2D）

`Player` 的 SQInstance `userpointer` → `Actor2D*`：

| 字段 | 偏移 | 类型 |
|---|---|---|
| x / y | 0x0C / 0x10 | float |
| id | 0x18 | int |
| vx / vy | 0x2C / 0x30 | float |
| direction | 0x38 | float（±1） |
| animation | 0x3C | `AnimationController*` |

`AnimationController`：motion@0x1C、key_take@0x20、key_frame@0x24。

> 实测：站立时 `motion == 0`，走路/攻击时立即变化；`vx/vy` 与位移一致。

---

## 4. 输入结构（Manbow::InputMulti）

`team[i].input` 的 SQInstance `userpointer` → `InputMulti*`：

| 字段 | 偏移 |
|---|---|
| x / y | 0x04 / 0x08 |
| b0 … b11 | 0x0C … 0x38（每个 4 字节，0/1） |
| xr / yr / b0r / b5r | 0x3C / 0x40 / 0x44 / 0x58 |
| 子设备 vector | 0x94/0x98/0x9C |
| 子设备数量 | 0xA0 |
| **last_device** | **0xA4**（-2 = 无，-1 = 键盘） |
| state_writer | 0xCC（每帧把子设备合并进 +0x04） |
| vtable | RVA `0x445360`，vtable[1] = `InputMulti::Update` RVA `0x6F360` |

**入口字节**（用于 hook 前校验）：

```
55 8B EC 51 56 57 8B F9 8B 87 D8 00 00 00 ...
push ebp; mov ebp,esp; push ecx; push esi; push edi; mov edi,ecx
mov eax,[edi+0xD8]        ; 子设备侵入式链表哨兵
```

函数尾部调用 `[edi+0xCC]`（state_writer），因此**在该函数返回后**写 `+0x04..+0x38`
不会被覆盖——这正是 Bridge DLL 的注入点（无竞态）。

### 4.1 输入注入的三条路径

| 方式 | 时序 | 适用场景 |
|---|---|---|
| 直接写 `team[i].input` 原生字段 | 与 `Update()` 竞争，需持续写 300ms+ | 菜单/对局中(下一帧才被读)均可，简单但不精确 |
| **inline hook `InputMulti::Update`（DLL）** | 在合并之后写入，100% 生效 | 训练主用：逐帧精确动作 |
| `command.rsv_x/rsv_k0..` | 内置 AI 就是这么驱动的 | 需要“按键持续帧数”语义时 |

### 4.2 全新启动的坑：`last_device = -2`

游戏启动后若从未收到**真实**按键，`::input_all.GetLastDevice()` 返回 `-2`，
`input.CreateSystemInputDevice(-2)` 返回 null，于是：

* `menu.character_select.device[0]` 恒为 null → 角色选择界面无法推进；
* `BeginBattle()` 把 `-2` 传给 `battle_param.device_id[0]` → 对局的
  `team[i].input` 可能失效。

修复：向 `::input_all` 原生结构 `+0xA4` 写入 `-1`（键盘）。该字段只在真实输入
到来时才刷新，因此写入一次即可（`MenuDriver.seed_last_device()`）。

---

## 5. 弹幕 / 飞行道具

`player.shot_actor` 的 SQInstance `userpointer` → `Actor2DProcGroup`：

```
group + 0x00 : 链表哨兵 (next)
node  + 0x00 : next
node  + 0x04 : prev
node  + 0x08 : Proc*（shared_ptr 裸指针）
```

`Proc*` 与 `Actor2D*` 同布局 → 坐标/速度/朝向/动画偏移直接复用。

验证：`scripts/verify_offsets.py`（统计数量）+ `BridgeOps.shot_positions()`。

---

## 6. 回合重置（实测安全 / 危险路径）

| 模式 | 方法 | 实测 |
|---|---|---|
| 练习 | 向 `team[0].input` 写 `b0..b3 = 1` | **1 帧内完成** ✅ 清空弹幕/特效、双方归位、计量条复原、计时器复位 |
| VS | 什么都不做，等自然 KO | ✅ 稳定，`8 -> 64 -> 32 -> 128 -> 4 -> 8` 约 **11 秒** |
| VS | **闭包槽临时替换**(见 §6.0.2) | ✅ **1 帧(~25ms)** 血量/计时器/站位/弹幕全部归位 |
| VS | 写 `battle.time = 0`（强制超时） | ❌ 数秒后崩溃（`std::bad_function_call`） |
| VS | 写 `battle.endWinDemo/endLoseDemo = true` | ❌ 演示不会被打断，可能**永久卡在 state=32**；与 `time=0` 同用必崩 |

两种危险路径复现率 100%（有 WER minidump 与栈回溯佐证，见 §10）。
**训练循环只允许等待自然 KO。**（练习模式热键虽然更快，但练习模式本身不可靠，见 §6.1）
要做真正的 VS 快速重置，正确做法是在 Bridge DLL 里实现 `sq_call` 调用脚本闭包
`battle.RoundReset()`（issue 03 的候选实现），而不是改写 battle 状态字段。

## 6.0 写内存必须写**完整 tag**:`0x05000002`,不是 raw `0x2`

这是本次逆向里最贵的一课。所有 int 槽(表 value、实例字段、数组元素)在游戏内存里
存的都是**完整 tag**:

```
battle.match_num @0x18cc9494  tag=0x05000002 val=2
battle.state     @0x18cc8fbc  tag=0x05000002 val=32
team0.life       @0x25d727fc  tag=0x05000002 val=0
battle.win[0]    @0x1ddda4a8  tag=0x05000002 val=0
```

Squirrel 的 `_type()` 会 `& 0x00FFFFFF`,所以写 raw tag `0x2` **类型判断仍然通过**、
读取看起来完全正常,但该 `SQObjectPtr` 的高位状态位被破坏,后续脚本执行到它时
抛 `Sqrat::Exception("wrong number of parameters")`,游戏无人捕获 → abort:

```
abort <- __CxxThrowException(pThrowInfo = __TI1?AVException@Sqrat@@) <- sub_40B860
0xC0000409 = __fastfail(7)
```

对照实验(`scripts/repro_ko_crash.py`,每次 ~90s,脚本自己进 VS COM 让 COM 打死 1P):

| 写 `battle.win` 的方式 | 结果 |
|---|---|
| raw tag `0x2`,每步清零 | 一局 KO 后 abort |
| raw tag `0x2`,回合开始清零 | 一局 KO 后 abort |
| **完整 tag `0x05000002`**,每步清零 | ✅ 连打 3 回合(两次 KO)不崩 |
| 完全不写 | ✅ 稳定,但 2 胜后卡在胜负对话(state 32) |

同样的坑也解释了更早的 `battle.match_num = 99` 崩溃——不是"UI 胜点越界",
而是 raw tag 写坏了对象。

**结论:所有写路径统一用完整 tag。** `src/th155/ops.py: write_int_slot` 已固定为
`TAG_INT = 0x05000002` / `TAG_BOOL = 0x01000008`,Bridge DLL 的 `inst_set_int`
本来就用完整 tag。新增写路径时**不要**再用 `RAW_INT`。

### 6.0.1 胜负对话仍然无法自动推进

比赛打满 2 胜后,游戏进入胜负对话/演出,停在 `battle.state = 32` 不再前进。
实测以下手段都无效:注入 `input_all`、注入 `input_talk`、真实按键、写
`battle.endWinDemo/endLoseDemo`。因此训练循环里**每回合开始把 `battle.win` 清零**
(`Th155Env._keep_match_alive`),让比赛永远打不满 2 胜。

### 6.0.2 VS 模式的 1 帧重置:临时替换 `battle.battleUpdate` 闭包槽

VS 模式没有练习模式那种"按键触发重置"的捷径,而 `battle.RoundReset` 是脚本闭包,
调用它需要 Squirrel VM API(`sq_push*` / `sq_call`),逆向成本高、且从 Hook 线程
调 VM 风险大。**实际不需要调 VM**:

> 游戏每帧都会从 `battle` 表里取 `battleUpdate` 闭包并调用它。
> 把这个槽临时指向 `RoundReset` 闭包,游戏下一帧就会替我们调用 `RoundReset`,
> 再下一帧把槽还原即可。

实现(`src/bridge/th155_rl_bridge.cpp`,`ctl_fast_reset` 控制位):

1. `ResolveGraph` 时缓存三个东西:`battle.battleUpdate` 槽地址、原闭包 8 字节、`RoundReset` 闭包 8 字节;
2. 输入 Hook(游戏主线程)里:请求位置 1 时写入 `RoundReset` 闭包 → 置 `g_fastResetPending`;
3. 下一帧 Hook 里还原原闭包,并 `ctl_fast_reset_ack++`。

实测(1P 血量 5378、计时器 8910 满值 8910 的回合中):

```
before  state=8 time=8549 life=[9249, 9999]
+1ms    state=8 time=8910 life=[9999, 9999]      <- 已重置
ack 在 25ms 内返回(约 2 帧:换入 + 还原)
```

两个闭包的参数个数兼容(直接交换不会抛 `Sqrat::Exception`),这是本方案成立的前提。

**只在 `battle.state == 8` 时用**。实测在 KO 演出(`state == 64`)里触发 RoundReset,
状态机会卡死在 64 不再前进(最终 `ensure_fighting` 超时失败,只能重启游戏)。
因此 `Th155Env.reset()` 里只有"回合还在进行但已不新鲜"(例如计时器过半)才走 1 帧重置,
回合已经结束的路径仍然等自然 KO 的 11 秒。
上层用法:`BridgeShm.request_fast_reset()` 直接可用;`Th155Env.reset()` 只在
回合仍在进行(`state == 8`)时先尝试它,失败再退回"等自然 KO"的 11 秒路径。

## 6.1 练习模式在本自动化下不稳定 -> 统一改用 VS COM

| 组合 | 结果 |
|---|---|
| 练习 + `player2 = 3`（内置 COM） | ❌ 进入对局后约 3.5s 崩溃（§10 的 abort） |
| 练习 + `player2 = 0`（站桩靶），未注入 DLL | ❌ 进入对局后 6s 崩溃（同上） |
| VS COM（标题 -> VS COM -> 选人） | ✅ 稳定（多轮验证，含自然 KO 回合切换） |

结论：练习模式经这条"标题 -> PRACTICE -> 选人"的自动路径进入后，游戏会在数秒内
抛 `std::bad_function_call`。**本项目统一使用 VS COM**：
- Stage 0（最弱对手）= VS COM EASY（`::config.difficulty.vs = 0`，com_level 0）；
- Stage 1+ = VS COM NORMAL/HARD/LUNATIC（`::config.difficulty.vs = 1..3`）。

练习模式的代码（`enter_practice` / `practice_reset` / 热键 `b0~b3` 重置）保留作为
参考实现，但训练流程不依赖它。

---

## 7. COM / 难度

`::config.practice`（table）：

| 字段 | 含义 |
|---|---|
| `player2` | `>=3` 且 `==3` → 内置 COM（`Run_Basic_Com()`）；其他值 = 站桩/防御靶 |
| `difficulty` | 0..4 → COM1..COM5，写入后 `Practice_CommonUpdate()` 会自动 `CPU_SetLevel()` |
| `life/sp/mp/op/guard` | 长度 2 的数组，练习开始时套用 |

角色侧可见 `com_difficulty`（0..4）与 `com_level`（0/33/66/100），
由 `CPU_SetLevel()` 同步，可用于观测/校验。

> 注意：`player2` 在**进入练习对局之前**写入才生效；对局中改 `difficulty`
> 会被 `Practice_CommonUpdate()` 检测到并即时生效。

### 7.1 VS COM 的难度传播（踩过的坑，已实测定论）

难度只沿这一条路径生效：`title.cursor_difficulty` →（进入对局时）
`::config.difficulty.vs` → `character_select.Initialize` → 队伍的 `com_difficulty`。

**唯一可靠的设难度方式是在选 VS COM 之前直接写 `::config.difficulty.vs`**
（`MenuDriver.set_vs_difficulty`）。不要在难度子菜单里点选，原因：

1. `title.nut proc.vs_com()` 进入子菜单时把 `cursor_difficulty.val` **重置**为
   `::config.difficulty.vs`；
2. 选定 VS COM 的那次确认键会在**下一帧**被子菜单 `UpdateDifficulty` 消费 ——
   子菜单通常只存在 1~2 帧就被自动确认，`cursor_difficulty.val` 根本来不及写；
3. 于是"在子菜单里改难度"永远无效：写进去的值要么被重置，要么确认时已离场。

实测：直接写 config 后 `enter_vs_com(2)` → `config.difficulty.vs=2`，开局
`com_difficulty=[0, 2]`（COM3）✅。

其他相关事实：
- `::config.difficulty.vs` 的**默认值是 1**（`data/script/config.nut:145`）。
  如果对局不是从标题进来的（游戏本来就在对局里，`ensure_fighting` 直接等下一回合），
  配置会被**静默忽略**，对手停在默认的 COM2。训练器现在会读实机 `com_difficulty`
  比对配置，不一致就重启一次走标题路径。
- **`battle.state` 会残留**：回到标题后旧 battle 表还在，`state` 仍是 8，但
  `battle.team` 已是空数组。判断"是否真的在对局里"必须同时看
  `state ∈ {2,8}` **且** `battle.team` 非空（`MenuDriver.in_match`）。
- 对局中直接写角色 `com_difficulty/com_level/com_baria_rate/com_guard_rate`
  （`BattleOps.set_com_difficulty`）理论上等价于 `CPU_SetLevel()`，但
  `§10`、`scripts/verify_bridge.py`、`menu.choose_difficulty` 三处都记录过
  "对局中改 `com_*` 会崩"。**该路径尚未定论**，用
  `scripts/verify_difficulty.py` 实测后再启用。

### 7.2 不重启进程换难度：暂停菜单回标题（暂不启用）

> **实测结论**：按暂停键 → `ret_title` 这条链路跑下来，`th155.exe` 直接退出
> （无 Python 异常，进程消失）。疑似与 §10 的 `std::bad_function_call` 同源。
> 因此 `configs/train_ppo.yaml` 里 `difficulty_switch: restart`，换难度仍走
> "杀进程重启"；`switch_difficulty()` 保留但未启用。以下是排查出的机制细节：

对局 → 暂停菜单 → `ret_title` → 标题 → 重进 VS COM（选人前写难度）。要点：

- **暂停键写在哪**：`battle_update.nut` 判定 `this.team[i].input.b10 == 1`。
  `battle.team[i].input` 与 `::input_all` **不是同一个对象**（实测指针不同），
  往 `input_all` 写 b10 不会暂停。
- **光标索引不压缩**：`menu.pause.item = ["exit", null, "ret_select",
  "ret_title", "hide", "config"]`，`CreateCursor` 只对 null 做 `SetSkip`，
  索引即原始下标 → `ret_title = 3`，`ret_select = 2`。
- **回放确认**：`ret_title` 走 `::replay.Confirm()`；`config.replay.save_mode == 2`
  时会先弹"保存回放?"对话框，需要再确认一次。

---

## 8. 版本校验

`Th155Game.__init__` 默认校验 `th155.exe` 的**大小 + SHA256**，不匹配直接抛
`VersionMismatch`；`configs/offsets.yaml` 的 `version_check.fail_policy` 记录策略。

---

## 9. 复现清单

```bash
# 1) 状态读取验证（需要在 VS/练习对局中）
python scripts/verify_offsets.py --seconds 4

# 2) 输入注入验证（会驱动 1P 移动 + 攻击）
python scripts/verify_control.py --enter

# 3) 一键：启动游戏 -> 注入 DLL -> 进练习 -> 桥接全量验证
python scripts/e2e.py

# 4) 单独注入 / 读共享内存
python src/bridge/inject.py
python src/bridge/shm.py
python scripts/bridge_debug.py            # 读 DLL 内部调试变量
```

---

## 10. 崩溃分析：游戏自身的 `std::bad_function_call`

复现两次（练习+COM、VS 中写 `battle.time=0`）后的异常签名完全一致：

```
th155.exe 0xc0000409 (STATUS_STACK_BUFFER_OVERRUN = __fastfail)
异常偏移 RVA 0x30C5FF  ->  _abort -> __fastfail(FAST_FAIL_FATAL_APP_EXIT)
EBP 链：_abort <- __CxxThrowException <- sub_4C1280 <- sub_4475B0 <- VM <- 引擎更新
```

`sub_4C1280` 的关键代码（IDA 反编译）：

```c
v3 = *(void ***)(this + 208);            // std::list 头
v4 = *v3;
while ( v4 != v3 ) {
    v5 = v4[13];                          // std::function 的 target
    if ( v5 == 0 ) std::_Xbad_function_call();   // <-- 抛 std::bad_function_call
    if ( (*(vtable+8))(v5) != 0 ) { ... 摘链并释放 ... }
    v4 = v4[0];
}
```

即：游戏某个任务/回调链表里残留了一个**空的 `std::function`**（悬垂的
coroutine/回调），遍历到它就抛异常；异常没被捕获 -> `std::terminate` -> `abort()`。

触发条件（实测）：手动跳回合演出、强制计时器归零、在练习模式里现场创建 COM。
自然流程（正常 KO、正常选人进入）不会触发。

> 复现工具：`scripts/dump_stack.py`（极简 minidump 解析，打印异常码、EBP 链、
> 栈上指向 `.text` 的返回地址）。Windows 默认会把 dump 写到
> `%LOCALAPPDATA%\CrashDumps\`。

## 11. 静态分析工具链（IDA Pro 9.4 + idalib）

本机已安装 `D:\IDA Professional 9.4`，可直接用 idalib 打开 `th155.exe`：

```bash
pip install ida-domain
python "D:/IDA Professional 9.4/idalib/python/py-activate-idalib.py" -d "D:/IDA Professional 9.4"
```
omp 的 `ida` 工具配置（`cfg://ida`）：`python = ...\Python311\python.exe`，
`installDir = D:\IDA Professional 9.4`。打开后即可用
`read <db>:<func|0xaddr>` 看反编译、`:xrefs:` 看调用者。

IDA 中的地址是**首选基址 0x400000** 下的 VA；运行期 dump 里的 VA 是
**实际载入基址（如 0x4A0000）**：`ida_va = dump_va - 实际基址 + 0x400000`。
换算错就会解析到完全无关的函数（本次排查踩过，0x561B6B 实际对应 0x4C1B6B）。
