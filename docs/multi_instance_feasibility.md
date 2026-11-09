# th155 多开并行采样与加速可行性调查

调查日期：2026-10-31。只做调查，未改 `src/`、未改游戏文件、未动 `checkpoints/`。
标注约定：**【实测】** = 本次在本机跑过并看到结果；**【静态】** = 读 exe 反汇编/导入表得出；**【推断】** = 未直接验证。

## 0. 结论

| 问题 | 结论 |
|---|---|
| 能否多开 | **有条件可行**。原版 exe 不能多开（单实例互斥锁 + 一处写死的共享内存名）。在 `CREATE_SUSPENDED` 启动后用 `WriteProcessMemory` 改两个字符串（只改内存，不改文件），**两个实例同时存活、各自注入桥接 DLL、各自进入 COM3 对战、各自 ~60fps、输入互不串**【实测】。 |
| 后台/无焦点能否跑 | 可以。两个实例都不在前台时，战斗仍以 59.6~60.2 fps 推进，桥接写入的输入生效【实测】。 |
| 能否超过 60 逻辑帧/秒 | **可行**。帧间隔是主循环里的一个立即数 `16666`（微秒）。启动时改成更小的值，战斗中实测 107 / 190 / 331 / 521 fps【实测】。逻辑仍是逐帧推进，帧语义不变【静态】。 |
| 加速能否直接用于训练 | **已落地**（2026-10-31）。锁步做完后，`env.frame_period_us: 4166` 用于 4 实例训练；reset 全程锁步，开局第一帧起由策略控制。实测见 `docs/multi_instance_fix.md` §5.3。 |
| 推荐方案 | 先做多开（N=3~4 个实例 × 60fps，改动可控，收益线性）。锁步做完后再加速（每个实例 2~4×）。 |

## 1. 静态分析

### 1.1 单实例锁【静态 + 实测】

`_WinMain@16`（0x41dc20）一开头：

```c
sub_530630(lpCmdLine);                          // 反调试/看门狗引导,见 1.2
CreateMutexA(NULL, TRUE, "th155");              // 0x41dc6a, 名字在 0x841d38
if (GetLastError() == ERROR_ALREADY_EXISTS) {   // 183
    MessageBoxA(NULL, "ゲームが既に起動しています。", "Error", MB_ICONERROR);
    return 0;
}
```

实测：原版启动第 2 个实例时弹出 `Error` 对话框，第 2 个实例自己退出。

### 1.2 双进程结构与写死的共享内存名（第 1 个实例被连带杀死的原因）

`sub_530630` 在 WinMain 之前运行，作用是反调试和崩溃看门狗：

* **无命令行参数时（父进程，也就是真正的游戏）**：`wsprintfA(buf, "%d", GetCurrentProcess())`，建一个 40 字节的命名映射 `TOKEN_<buf>`，写入自身 pid/线程/窗口句柄，然后 `CreateProcessA("<exe> <buf>")` 拉起子进程，等子进程以调试器身份附加（`IsDebuggerPresent()`）后才继续。
* **有参数时（子进程）**：`OpenFileMappingA("TOKEN_<参数>")`，`DebugActiveProcess(父 pid)`，进入调试循环，负责写崩溃日志（`th155.exe.log` 里的 `cause=app crash` 就是它写的）。窗口标题为 `kyo` 的 `STATIC` 窗口就是它。
* `GetCurrentProcess()` 返回伪句柄 `-1`，所以**所有实例的映射名都是 `TOKEN_-1`**。第 2 个实例的父进程会打开第 1 个实例的映射，并覆盖其中的 pid 和窗口句柄。

实测现象吻合：原版双开时，第 2 个实例弹窗退出，约 3s 后**第 1 个实例也消失了**，`th155.exe.log` 里没有新的崩溃记录。

【推断】完整链路：第 1 个实例的子进程读到被清零的窗口句柄，`IsWindow` 失败后自行退出。调试器退出时，Windows 默认结束被调试进程（`DebugSetProcessKillOnExit` 默认 TRUE），所以游戏本体被带走。§22.6 的"杀任一 th155.exe 全部退出"也是同一机制：杀子进程等于调试器退出；杀父进程后，子进程收到 EXIT_PROCESS 调试事件并退出。

本次实测确认了进程关系：父进程（有 `th155` 主窗口，类名即 mutex 名）→ 子进程（只有 `kyo` 隐藏窗口）。

### 1.3 其它限制【静态】

* **Steam/DRM**：导入表没有 steam_api，字符串里没有 Steam 相关内容。只有 `WinVerifyTrust`，用来校验已加载模块的签名；未签名模块必须位于 System32/SysWOW64/AppPatch/WinSxS，否则写日志并退出（`sub_52F7F0`）。这个检查在启动时跑，也在子进程收到 LOAD_DLL 调试事件时跑。桥接 DLL 未签名却一直能用，【推断】原因是子进程在自己的进程里用父进程的模块句柄调 `GetModuleFileNameA`，取到空串后直接放行。
* **反作弊**：每帧检查 `IsDebuggerPresent` 必须为真（子进程充当调试器）、检查 `NtGlobalFlag`，并按窗口类名/标题查一批工具：`TMeccMain`、`TMainForm`+`SpoilerAL`、`Calc`、`UsaMimi64`、`PMEditor`、`TSearch`、`スペシャルねこまんま`、`うさみみハリケーン` 等（`sub_532140`）。命中即退出。**训练机上不要开 Cheat Engine 这类工具。**
* **看门狗时序**：父进程每帧比较自己和子进程写入 `TOKEN_` 块的 `timeGetTime` 心跳，差值超过 500ms 连续 100 帧就自杀；窗口 `IsHungAppWindow` 也会自杀（`sub_532AF0`）。这一点约束了锁步设计：游戏主线程不能被阻塞太久（见 §4.2）。
* **输入方式**：桥接 hook `Manbow::InputMulti::Update`，在函数返回后直接写设备状态。菜单导航（`MenuDriver`）也是 `WriteProcessMemory` 直写 `InputMulti`。游戏自己的键盘/手柄输入走 DirectInput8/XInput 加一处 `GetAsyncKeyState(VK_SNAPSHOT)`（截图键）。**整条链路不依赖窗口焦点**【实测：两个实例都不在前台时，对战推进、输入生效】。
* **存档/配置文件**：游戏目录下的 `system.dat` 会被游戏改写（本次实测期间 mtime 变到 00:39）。没有看到排他文件锁。【推断】多个实例共用一个目录时，退出时同时写 `system.dat` 存在覆盖/损坏风险。缓解办法：启动前备份 `system.dat`；或者每个实例一个目录，`th155.pak`/`th155b.pak`（约 1.1GB）用硬链接，`system.dat`/`score.dat` 各自一份。

## 2. 多开实测

环境：i5-13400F（10C/16T），Intel Arc B580，显示器 100Hz，`system.dat` 里 vsync 已关。

| 步骤 | 结果 |
|---|---|
| `game_session.py start` 启动 A 并注入 | 正常：pid=28104（窗口进程）+ 19856（`kyo` 子进程），DLL 注入成功 |
| 原版方式再启动 B | B 弹出 `Error`（已在运行）后退出；约 3s 后 A（两个进程）也消失；`find_pids()` 为空 |
| 改内存后启动 A'、B' | 两个都 `CREATE_SUSPENDED` 启动，改 `0x841d38`（mutex 名，`th155`→`th1a1`/`th1b2`）和 `0x832360`（`"%d"`→`a1`/`b2`，于是映射名为 `TOKEN_a1`/`TOKEN_b2`），然后 `ResumeThread`。**20s 观察期内 4 个进程全部存活**；主窗口类名分别为 `th1a1`/`th1b2` |
| 注入 | A' 注入原版 DLL（`Local\th155_rl_bridge_v1`），B' 注入一份只改了 SHM 名的 DLL 拷贝（`…_v2`，放临时目录，用后已删）。两边 magic/version=18 都正确，`ctl_ack` 都在涨 |
| 进入 COM3 对战 | 两线程并行 `ensure_fighting`：A' 成功，B' 第一次失败；B' 单独重试成功（用时 83s）。【推断】失败原因是两个菜单驱动并行时序互扰（GIL/轮询节奏），与焦点无关 |
| 并行帧率 | 同时测 10s：A' 60.2 fps，B' 59.6 fps |
| 输入隔离 | 只向 B' 写 x=+1 持续 1.5s：B' 1P 的 x 从 40.0 变为 64.85；A' 的 x 保持 40.0 |
| 清理 | 按 `CreateProcess` 得到的句柄 `TerminateProcess` 自己的父进程，再确认子进程随之退出；`find_pids()` 回到空 |

改内存的位置都在 exe 映像内（ImageBase 0x400000，实测加载到 0x240000，按 RVA 计算）。写之前先校验原字节（`"%d\0"`、`"th155\0"`、`c7 44 24 28`），版本不符就拒绝写。目标版本 Ver1.21b。

## 3. 速度

### 3.1 帧调度【静态】

主循环 `sub_40DBB0`：

```c
timeBeginPeriod(1);
int period_us = 16666;                    // 0x40dbe8: mov [esp+28h], 411Ah ← 立即数在 0x40dbec
loop {
    sub_532AF0();                         // 看门狗
    sub_40E1A0();                         // 逻辑更新(帧计数 ++dword_8DACE0)
    sub_40E330();                         // 渲染 + Present(SyncInterval = vsync 开关)
    if (vsync_ok && 刷新率 ∈ [59,61]) continue;           // 60Hz 屏且开 vsync 时靠 Present 定速
    _Thrd_sleep(上一帧起点 + period_us);  // QPC 计时,Sleep(ms) 实现
    // 每秒统计一次:若 fps<60 则 period_us -= 33(自适应追帧)
}
```

* 游戏没有内置快进。脚本 API 有 `SkipRender`/`GetFPS`/`Sleep`，可以跳过渲染，但没有倍速。
* 逻辑与渲染 1:1，每次循环推进 1 逻辑帧，所以加速不会改变每帧的游戏语义。
* vsync 开启时，`Present` 会把帧率钳在显示器刷新率（§21.9 的 50/87~90 跳档）。加速必须先关 vsync（当前已关）。

### 3.2 实测（单实例，启动时改 `0x40dbec` 立即数，COM3 对战中只统计 `battle_state==8` 的区间）

| period_us | 目标倍速 | 实测逻辑 fps | 游戏进程 CPU（核） |
|---|---|---|---|
| 16666（原版） | 1× | 59.6 | — |
| 8333 | 2× | 106.8 | — |
| 4166 | 4× | 190.0 | 0.57 |
| 2000 | 8.3× | 330.7 | 0.88 |
| 1000 | 16.7× | 520.5（另一次 490~576） | 0.8~1.2 |

实际 fps 低于目标值，原因是渲染加 `Sleep` 的毫秒粒度。不控制 1P 时，1P 在 520fps 下约 3s 就被打败，回合推进正常、没有崩溃。

**一次自身失误**：第一次加速试验把立即数地址写偏了 4 字节，覆盖了下一条 `call`，游戏以 `0xC0000005` 崩溃（`th155.exe.log` 里新增的那条 crash 就是它）。残留的 `kyo` 子进程按 pid 单独结束。之后改用正确地址，并在写入前校验原字节。

### 3.3 hook 计时函数（Sleep/QPC/timeGetTime）的方案比较【静态 + 推断】

改立即数比 hook 计时函数更好：

* hook `QueryPerformanceCounter`/`timeGetTime` 会同时影响 `sub_532AF0` 看门狗的 500ms 心跳比较（父子两个进程的时间基准会不一致）、音频（DirectSound）和网络代码。
* hook `Sleep` 只能缩短等待，`period_us` 本身仍是 16.6ms 的基准。
* 立即数只用在帧调度这一处，副作用最小。也可以由桥接在运行时改栈上的 `v24`。不过启动前改立即数最简单，只是倍速在进程生命期内固定。【推断】改成 0 等于不限速，配合锁步时游戏会完全由 agent 驱动。

### 3.4 加速的阻碍（必须先解决）

1. **非锁步**：`Th155Env.step()` 写动作后轮询 `shm.frame`（`time.sleep(0.001)`），游戏不等 agent。60Hz 时 agent 有 16.7ms 预算（推理约 1.4ms）。到 4×（4.2ms/帧）时，Python 一轮 step 的开销（读快照、算奖励、推理）就会跨帧。
2. **发布频率**：`BridgeThread` 每 `pSleep(2)` 发布一次快照（`th155_rl_bridge.cpp:1766`），实际周期约 2~3ms。超过约 300fps 时快照会跳帧，即便 agent 足够快也读不到逐帧状态。
3. **时间折算**：`eval.py:298` 用 `steps*repeat/60` 折算秒数，只与帧数有关，加速后仍成立。所有用墙钟的超时（`frame_timeout`、菜单等待）不受影响或更宽松。

## 4. 推荐方案与改动清单

### 4.1 多开（推荐先做）

**实例标识**：建议 SHM 名带游戏 pid，即 `Local\th155_rl_bridge_v1_<pid>`。DLL 用 `GetCurrentProcessId()` 拼名，不需要额外的注入参数。这样做还彻底避开 §22.5 的残留共享内存问题：新进程必然对应新映射，读者打不开旧实例的映射。替代方案是启动时用 `lpEnvironment` 传 `TH155_RL_INSTANCE=<k>`，DLL 读 `GetEnvironmentVariableW`；缺点是同编号重启后会再次打开旧映射。

| 文件 | 函数/位置 | 改动 |
|---|---|---|
| `src/bridge/protocol.h:9` | `TH155_SHM_NAME` | 改为前缀 `L"Local\\th155_rl_bridge_v1_"`，协议版本号不必 bump（结构体不变） |
| `src/bridge/th155_rl_bridge.cpp` ~1893 | `DllMain` 里的 `CreateFileMappingW` | 用 `GetCurrentProcessId()` 拼名。DLL 不链 CRT，要手写十进制转宽字符；`GetCurrentProcessId` 要加进 `InitImports` 和 `kernel32.def` |
| `src/bridge/shm.py:17,164` | `SHM_NAME`、`BridgeShm.open` | `open(pid, timeout)` 按 pid 拼名；保留"只有 1 个实例时自动取该 pid"的便捷路径给现有脚本 |
| `scripts/game_session.py` | `_spawn_on_default_desktop`、`start`、`kill`、`_game_window_pid`、`_show_game_window`、`status` | 启动加 `CREATE_SUSPENDED`；读 PEB 取映像基址；校验原字节后改 `th155`→`th1NN` 和 `%d`→`NN`；`ResumeThread`。`start(instance=k)` 返回 pid；`kill(pid)` 只结束该父进程（子进程随之退出）并等子进程消失；去掉"发现多实例就全杀"。`_game_window_pid` 改按 pid/窗口类名 `th1NN` 精确匹配。注入要等模块加载完，太早会报 299 错误（本次实测踩到过），需要重试 |
| `src/th155/mem.py` / `src/th155/game.py:86` | `Th155Game.__init__` | 增加 `pid` 参数，不再 `find_pid("th155.exe")` 取第一个 |
| `src/th155/session.py`、`menu.py`、`ops.py` | 通过 `Th155Game` 间接用 pid | 透传，基本不用改 |
| `src/env/th155_env.py:136-166,203,220` | `__init__`、`_ensure_live`、`_reset`、`set_opponent_difficulty`、`_ops` 构造 | 新增 `pid`；`BridgeShm.open(pid)`，所有 `Th155Game(writable=True)` 带上 pid |
| `src/train/train_ppo.py` | `restart_game`、`_recover_game`、`switch_difficulty`、`reset_with_restart` | 按实例重启（`game_session start/kill --instance k`），只动出问题的那个实例 |
| `src/train/train_ppo.py` | `build_env`、`collect`、`train` 主循环 | 见下 |

**train_ppo 多环境采样**（当前完全是单环境：`collect()` 循环里 `env.step`，`hidden = init_hidden(1)`，`Rollout.h_in` 形状为 `(T, hidden)`，`update()` 用 `_seq_batches(len, seq_len, dones)` 切单条长序列）：

* 推荐**每个实例一个采样子进程**：持有 env 和一份 CPU 策略副本，每个 update 前从主进程同步 `state_dict`，各自跑现有的 `collect()`，返回 `Rollout` 和 episode 统计。好处是 `collect()`、`hold_engine`、奖励逻辑都不用改，一个游戏卡死只影响一个 worker。
* 主进程：对每个 env 的 rollout 先各自 `compute_gae`（末端 bootstrap 用各自的 `last_value`），再拼接。`h_in` 本来就是逐步存的，拼接后切 64 步序列时，只需让 `_seq_batches` 在 env 交界处强制断开。做法是给 `update()` 传一个 `breaks` 数组，或者在交界处把 `dones` 置 1，前提是 GAE 已经算完。
* `n_steps` 语义改为每个 env 的步数，或者把总步数按 env 数均分；`fps` 看门狗、`stall`、`restarts` 改为按 env 统计；`Stats`/curriculum 合并各 env 的 episodes。
* 备选方案是主进程批量推理 N 个 env（hidden 改成 batch=N），推理更省，但要把 `collect()` 拆成逐步的状态机，改动大很多，不推荐作为第一步。

**工作量估算**【推断】：桥接与 shm 改名 0.5 天；game_session 启动/改内存/按 pid 精确关闭 1 天；`Th155Game`/env 透传 pid 0.5 天；train_ppo 多 worker 采样与 GAE/切片 1.5~2 天；双实例长跑稳定性验证（重启自愈、难度切换、`system.dat`）1 天。**合计约 4.5~5 天**。实例数量上，CPU 预算按每个实例约 0.6~1 核、每个 worker 约 0.3 核算，16 线程机器跑 4 个实例比较稳妥；GPU 余量未测。

### 4.2 加速（在多开之后做）

| 文件 | 改动 |
|---|---|
| `src/bridge/protocol.h` + `shm.py` | 新增锁步控制字段：`ctl_lockstep`、`ctl_step_seq`（Python 写，表示允许推进到第几帧）、`frame_ack`。bump 协议版本 |
| `src/bridge/th155_rl_bridge.cpp` `OnInputUpdated` | 每逻辑帧在输入 hook 里**同步发布快照**，不再依赖 2ms 的 `BridgeThread`。锁步开启时自旋等待 `ctl_step_seq`（`SwitchToThread`），**带超时**（例如 200ms 后按原动作继续），以免触发 `IsHungAppWindow` 和 500ms 心跳看门狗。注意 `InputMulti::Update` 每帧会被调用约 3~5 次（实测 ack/fps≈5），只应在逻辑帧的第一次调用时等待 |
| `src/env/th155_env.py` `step()` | 锁步模式下写完动作再推进 `ctl_step_seq`，等待 `frame_ack`，替换 1ms 轮询 |
| `scripts/game_session.py` | 启动时可选 `--speed`，改 `0x40dbec` 的立即数；锁步时可直接写 0 |

工作量约 1.5~2 天，外加回归：奖励中跨帧的窗口（`guard_window` 等以步计）不受影响，但需要复核逐帧对齐。**风险**：主线程等待过长会被游戏看门狗自杀；改立即数依赖 exe 版本（Ver1.21b），需要字节校验兜底；渲染仍在每帧执行（可尝试脚本 API `SkipRender` 降低开销，未测）。

### 4.3 收益估计【推断】

* 只做多开：N 个实例 × 60 帧/s，4 个实例约为现在的 4 倍样本速度，策略/奖励逻辑零改动。
* 多开 + 锁步加速：上限由推理与 Python step 开销决定（单步推理约 1.4ms）。每个 worker 估计能到 150~250 步/s，4 个实例约为现在的 10 倍以上。需要实测确认。

## 5. 风险汇总

1. 改内存是**版本绑定**的（Ver1.21b 的 RVA）。exe 更新后必须重新定位；字节校验不通过时应拒绝启动。
2. 多个实例共享游戏目录时 `system.dat` 可能被并发改写，需要备份或分目录。
3. 进程管理必须一律按 `CreateProcess` 返回的 pid/句柄操作。每个实例都会出现父+子两个 th155.exe，不能按进程名批量杀，也不能"清理多余实例"（§22.6）。
4. 不要在训练机上运行游戏黑名单里的工具（Cheat Engine 等），否则所有实例都会自杀。
5. 菜单驱动（`ensure_fighting`）并行时出现过一次失败，多实例启动时建议串行进入对战。

## 6. 多环境并行训练（已实现，训练侧）

代码：`src/train/parallel.py`（worker 进程、EnvProvider、WorkerPool）、`src/train/train_ppo.py`（`train`/`update`/`Evaluator`/`evaluate`/`main`）、`scripts/eval_parallel.py`。实例启动/关闭由 `src/th155/instances.py` 提供（Infra 侧）。

### 6.1 用法与配置

* `train.num_envs`（默认 1，CLI `--num-envs`）：1 时走原单环境路径，行为不变；>1 时每个 env 一个 `spawn` worker 进程（`torch.set_num_threads(1)`）。`n_steps`/`--steps` 表示**每个 env** 的步数，一次 update 的样本数是 `num_envs × n_steps`。
* `train.worker_timeout`（默认 900s）：学习者等单个 worker 回复的上限，超时按"死掉"处理。评估按逐局进度计时，即"两局之间"的上限（`docs/multi_instance_fix.md` §5.2）。
* `train.worker_max_failures`（默认 5）：同一 slot 连续失败（采样异常、采样停顿、进程死掉、启动失败）超过该次数才报错退出；成功采样一次清零。
* 离线冒烟：`python src/train/train_ppo.py --offline --num-envs 3 --updates 2 --steps 128`（`OfflineProvider`，每个 slot 的合成环境种子不同）。
* 独立评估：`python scripts/eval_parallel.py <config> <ckpt> <N> --num-envs 4 [--det] [--temp T] [--offline]`，输出与 `.scratch/eval_com3.py` 同口径的 JSON（`summarize` + `deterministic`）。`train_ppo --eval N --num-envs K` 走同一条路径。

### 6.2 数据流

1. 每次 update 学习者把权重（numpy 化的 `state_dict`）和当前课程阶段的奖励覆盖广播给全部 worker；熵系数退火只在学习者侧生效。
2. worker 用现有 `collect()` 采 `n_steps`；对局、`ep_state`、LSTM hidden（含 ref-KL 参考网的 hidden）都留在 worker 内，跨 update 连续。返回 `Rollout`、本批新完成的对局和 fps。学习者同步等全部 worker。
3. 学习者对每个 worker 的 rollout 各自 `compute_gae`（各自的 `last_value`/`last_done`），再按 slot 顺序拼接（`merge_rollouts`）。拼接后的 `Rollout.seg_ends` 标出每段最后一步，`_seq_batches(..., breaks=seg_ends)` 在 worker 边界和 `dones` 处都断开序列。
4. 对局按 slot 顺序并入 `Stats` 与课程（`merge_episodes`），`[ep N]` 行格式不变；`[it/end]` 行末尾追加 ` envs=N wfps=a/b/c sps=X`（每 worker fps 与含 update 耗时的总步/秒），其余字段位置不变，`monitor_m2.py`/`eval_trend.py`/`analyze_run.py` 的正则照常匹配。
5. 课程晋级时 `WorkerPool.apply_difficulty(level)` 让每个 worker 调 `provider.apply_difficulty`，并更新 `cfg.env.difficulty`，之后重启的 worker 也按新难度起 env。课程只在 provider 声明 `supports_difficulty`（GameProvider）时启用，合成环境不启用，与单环境的能力探测一致。

### 6.3 批量化 update

`update()` 不再逐序列前向：每个 minibatch 把序列右侧 pad 到 `max_len`，起始 hidden 堆成 `(1, B, H)`，一次前向；pad 位用该序列最后一步的数据填充（避免 NaN），损失用掩码先对时间求均值、再对序列求均值，与旧实现口径一致（KL、clip_frac、ref-KL、`target_kl` 早停、NaN 跳过、`max_len<2` 跳过都不变）。LSTM 是 `batch_first` 的因果网络，右侧 pad 不影响有效步。

* 等价性单测（`tests/test_parallel_train.py`，旧实现原样留在测试里）：小网络、96 步 rollout、2 epoch，含 ref-KL、`target_kl` 早停、worker 断点四种情形，参数差 ≤ 1e-5，日志项一致。
* 提速（`.scratch/bench_update.py`，默认配置网络、16384 步、seq_len 32、minibatch 8、4 epoch、2 线程、不早停）：旧 25.93s → 新 11.61s，**2.23 倍**；5 个日志项到小数点后 6 位相同。fp32 下 256 次 Adam 步后参数最大差 4.2e-5；同一基准改用 float64（`--f64`）时差为 3.5e-15，说明 fp32 的差只来自求和顺序不同的舍入误差，没有语义差异（float64 下提速 4.04 倍：94.87s → 23.48s）。

### 6.4 EnvProvider 与 GameProvider

接口：`make(slot) -> (env, obs)`、`recover(slot, env, why) -> (env, obs)`、`close(slot, env)`，另有 `apply_difficulty(slot, env, level) -> (env, obs|None)`、`release(slot)`（学习者侧清理已死 worker 的实例）和 `supports_difficulty`。

GameProvider 对实例契约的调用：

* `make`：`pid = instances.launch_instance(slot)` → `env = build_env(cfg, ar, pid=pid)` → `reset_with_restart(cfg, env, ar, why, slot=slot)`；失败时关 env 并 `kill_instance(instances.slot_pid(slot))`。
* `recover`：`_recover_game(cfg, env, ar, why, slot=slot)`。
* `apply_difficulty`：`_apply_difficulty(cfg, env, level, cmode, dswitch, ar, slot=slot)`。
* `close`：`env.close()` 后 `kill_instance(env.pid)`；`release`：`kill_instance(slot_pid(slot))`。
* `make`/`recover`/`apply_difficulty` 都在命名互斥量 `Local\th155_rl_menu_lock` 内执行，菜单驱动全局串行（§5 第 5 条）。持锁 worker 被杀时互斥量变为 abandoned，下一个等待者照常拿到。

### 6.5 容错与分布式评估

* worker 内 env 异常（采样/评估/切难度）：走 `provider.recover`，丢弃本批，本 slot 计一次失败。
* 采样停顿：每个 worker 独立判定（连续两次 fps<5），丢弃本批并 recover。
* worker 进程死掉或超时：学习者 `kill` 残留进程、`provider.release(slot)`，按原 slot 重启 worker（新种子 = `seed + 1000*(slot+1) + 代数`）。本轮用其余 worker 的数据照常 update；全部失败则跳过本轮。
* 训练退出（含异常）：`train()` 的 `finally` 关闭全部 worker，worker 退出前 `provider.close` 关闭自己的实例。
* in-loop 评估：局数用 `split_episodes` 分给各 worker（口径按 `eval.deterministic`）。worker 每打完一局就回传一条 `eval_rec`，记录按"轮次 → slot"顺序合并后交给同一个 `summarize`（Wilson CI）；worker 中途失效时已完成的局保留，缺的局最多再派 3 轮。best 门控、`best.pt`/周期 checkpoint 的元数据与单环境路径相同。评估后各 worker 自己 reset env 和 hidden。
