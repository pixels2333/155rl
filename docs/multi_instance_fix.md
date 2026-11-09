# th155 多开修复记录

日期：2026-10-31。目标：同一台机器上同时跑 4 个 th155.exe，每个实例单独注入桥接 DLL、单独被一个训练 worker 驱动，互不干扰。

前置调查见 [multi_instance_feasibility.md](multi_instance_feasibility.md)。那份文档只验证了"手工改内存可以双开"，本文记录把它做成可用基础设施的过程。

标注：**【实测】** 本机跑过并看到结果；**【静态】** 读 exe 反汇编得出；**【推断】** 未直接验证。目标版本 Ver1.21b，exe 首选基址 0x400000，带 `DYNAMIC_BASE`（DllCharacteristics 0x8140）。

## 1. 原版为什么不能多开

### 1.1 单实例互斥量【静态 + 实测】

`WinMain`（0x41dc20）开头：

```c
CreateMutexA(NULL, TRUE, "th155");            // 0x41dc61 push 0x841d38 → 0x41dc6a call
if (GetLastError() == ERROR_ALREADY_EXISTS)   // 183 = 0xB7
    MessageBoxA(..., "ゲームが既に起動しています。", "Error", ...), return 0;
```

同一个字符串 `"th155"`（VA 0x841d38）在 0x41de8d 处还被用作主窗口类名。

### 1.2 写死的看门狗映射名 `TOKEN_-1`【静态 + 实测】

每个实例是两个进程：

- **父进程**：真正跑游戏逻辑、持有主窗口。启动时执行 `wsprintfA(buf, "%d", GetCurrentProcess())`（格式串在 0x832360，调用点 0x531edb），再和 `"TOKEN_"`（0x832364）拼成映射名，在 0x531f4e 处 `CreateFileMappingA`。然后用 `"%s %s"`（0x832390）拼命令行 `"<exe> <buf>"` 拉起子进程。
- **子进程**：从命令行拿到 `buf`，在 0x5309f2 处 `OpenFileMappingA("TOKEN_" + buf)`，在 0x530a8d 处 `DebugActiveProcess(父进程)`，作为调试器兼看门狗。

`GetCurrentProcess()` 返回伪句柄 `-1`，所以所有实例的映射名都是 `TOKEN_-1`。第 2 个实例的父进程会打开第 1 个实例的映射并覆盖其中内容。

实测：原版方式启动第 2 个实例，它先弹窗退出，约 3 s 后第 1 个实例（父子两个进程）也消失，`th155.exe.log` 里没有崩溃记录。

### 1.3 看门狗约束【静态 + 实测】

`sub_532AF0` 每帧在主循环里运行：

- 比较 `timeGetTime` 与 `TOKEN` 块 +8 处的心跳，差值超过 500 ms 连续 100 次就自杀；
- 0x532b63 处调用 `IsHungAppWindow`，判定挂起也自杀。

另外游戏还检查 `NtGlobalFlag`，并按窗口类名/标题查一批内存修改工具（Cheat Engine 等，`sub_532140`），命中即退出。

因为有这个看门狗，桥接的帧锁步不能无限期阻塞游戏主线程（见 §4）。实测：标题界面把主线程挂起 10 s，期间 `IsHungAppWindow` 一直为 0，恢复后 15 s 内进程存活。实现上仍然保守地把锁步等待上限默认设为 100 ms。

## 2. 修法：挂起启动 + 内存补丁

实现：`src/th155/instances.py`。

### 2.1 启动流程

1. `CreateProcessW(th155.exe, CREATE_SUSPENDED | DETACHED_PROCESS)`，`lpDesktop = "WinSta0\\Default"`，工作目录为游戏目录。
2. `NtQueryInformationProcess(ProcessBasicInformation)` 取 PEB，读 `ImageBaseAddress`。Python 是 64 位，拿到的是 WOW64 进程的 64 位 PEB，字段在 +0x10。
3. 校验原字节，再写同长补丁。写之前用 `VirtualProtectEx` 临时改成可写，写完恢复：

   | VA | 原字节 | 补丁（slot = 0~9） | 作用 |
   |---|---|---|---|
   | 0x841d38 | `th155\0` | `th1r{slot}\0` | 互斥量名和窗口类名各实例不同 |
   | 0x832360 | `%d\0` | `r{slot}\0` | `wsprintfA` 输出变成字面串 `r{slot}`，映射名变成 `TOKEN_r{slot}` |

   原字节不符（exe 版本不对）就抛错并结束这个挂起的进程。
4. `ResumeThread`。
5. 等待父进程或其子进程出现可见、带标题的顶层窗口，取窗口所属 pid 作为注入目标。
6. 用 `MenuDriver(Th155Game(pid=目标)).wait_title_ready()` 等标题菜单可交互。
7. 注入 `src/bridge/th155_rl_bridge.dll`，再用 `BridgeShm.open(pid)` 确认共享内存已建立。
8. 任一步失败：结束已启动的进程树，原样抛出异常。

补丁只改本进程的写时复制页，磁盘上的 exe 不变。

### 2.2 子进程不需要打补丁【推断 + 实测】

子进程是父进程从磁盘重新拉起的原版映像，它的 `"th155"` 串没有被改。【推断】它不会撞互斥量，是因为子进程在 `sub_530630`（`WinMain` 之前运行）里走子进程分支，执行不到 0x41dc61 的互斥量检查；这一点没有逐条反汇编确认。它拿到的映射名来自命令行参数 `r{slot}`，与父进程一致。【实测】4 个补丁实例的父子进程同时存活。

### 2.3 slot 归属

`instance_slot(pid)` 直接读进程内 `基址 + 0x441d38` 处的 6 个字节：形如 `th1r<数字>\0` 就返回该数字，原版实例、看门狗子进程和已退出进程都返回 `None`。`list_instances()` / `slot_pid(slot)` 在此基础上枚举。

这样不需要任何外部登记文件。worker 或学习者被杀后，重新接管同一 slot 时也能找到旧实例。

### 2.4 结束实例

`kill_instance(pid)`：先收集子进程，`taskkill /PID <pid> /T /F`，然后轮询，直到父子进程全部退出（有残留就逐个补杀），超时抛 `TimeoutError`。

只按 pid 结束，绝不按进程名批量杀。`scripts/game_session.py kill` 会结束所有 th155.exe，多实例运行时不能用。

### 2.5 slot 范围

补丁串只有 1 个字符位，slot 只能是 0~9，越界抛 `ValueError`。

## 3. 桥接 DLL：按 pid 命名

原 DLL 只有一个固定映射名 `Local\th155_rl_bridge_v1`。可行性调查时，第二个实例只能注入一份手工改了映射名的 DLL 拷贝。现在 DLL 自己按宿主 pid 命名（协议 v19，`protocol.h` / `shm.py` 同步）：

| 对象 | 名字 |
|---|---|
| 共享内存 | `Local\th155_rl_bridge_v1_<pid>` |
| 状态事件（DLL 发布一帧后置位） | `Local\th155_rl_state_<pid>` |
| 放行事件（Python 放行一帧后置位） | `Local\th155_rl_step_<pid>` |

- pid 取自 TEB：`fs:[0x20]`（x86 的 `ClientId.UniqueProcess`），不需要导入 `GetCurrentProcessId`。
- DLL 不链 CRT，名字由 `BuildName` 逐字拼接十进制 pid。
- 共享内存里新增 `ls_pid` 自检字段。
- Python 侧 `BridgeShm.open(pid)` 按 pid 打开；`pid=None` 时枚举所有 th155.exe，返回第一个能打开且校验通过的映射，兼容单实例脚本。

新进程必然对应新映射，所以也不会再打开已退出实例留下的旧映射。

实测：`pid=17732 ls_pid=17732`。

## 4. 帧锁步

多个实例抢 CPU 时，Python 的 step 可能跟不上 60 fps，`action_repeat=1` 的逐帧语义会丢。所以在桥接里加了可选的帧锁步。

### 4.1 帧边界

`sub_40E1A0` 先扫一遍输入设备（0x40E21B），再 `inc [帧计数]`（0x40E21D，RVA 0x4DACE0，配置项 `engine.frame_counter_rva`），然后才跑本帧逻辑。`OnInputUpdated` 里，帧计数变化后第一次对战设备（`g_dev[0]` / `g_dev[1]`）的 `InputMulti::Update` 返回时，上一帧逻辑已经跑完，本帧逻辑还没开始。这里调用 `LockstepBoundary()`：

1. 持锁同步发布状态，`ls_seq + 1`，置位状态事件；
2. 只在 `battle_state == 8` 时阻塞，演出和菜单期间只发布不等待；
3. 等 Python 把 `ctl_step_seq` 写到不小于 `ls_seq`，或关闭锁步；
4. 等待超过 `ctl_lockstep_timeout_ms` 时 `ls_timeouts + 1`，进入 stalled 状态，直到 `ctl_step_seq` 变化前不再阻塞；
5. 放行后立刻读取本帧动作，不等 `BridgeThread` 2 ms 一次的拷贝。

`BridgeThread` 与主线程的发布由自旋锁（`_InterlockedCompareExchange`）互斥。锁步期间，如果主线程 200 ms 内发布过，`BridgeThread` 就不重复发布，只在主线程停发时兜底。

Python 侧：`BridgeShm.enable_lockstep / disable_lockstep / advance`。`Th155Env(lockstep=True)` 在 reset 期间关闭锁步，开打后打开；`close()` 时关闭。训练配置项是 `env.lockstep` 和 `env.lockstep_timeout_ms`（默认 100）。

### 4.2 构建失败：无 CRT 下的 64 位乘除

第一次构建链接失败：

```
error LNK2019: 无法解析的外部符号 __alldiv，函数 "void __cdecl LockstepBoundary(void)" 中引用了该符号
error LNK2019: 无法解析的外部符号 __allmul ...
```

x86 上的 64 位乘除由 CRT 的 `__allmul` / `__alldiv` 实现，而 DLL 不链 CRT。改成全 32 位：

- `Qpc()` 只取 `QueryPerformanceCounter` 的低 32 位，差值按无符号回绕计算；
- 初始化时 `g_qpcPerUs = (u32)freq / 1000000`，只在频率能放进 32 位时启用（Win10+ 的 QPC 频率是 10 MHz）；
- 超时比较改为 `QpcUs(t1, Qpc()) > timeout_ms * 1000`。

10 MHz 下低 32 位约 429 s 回绕一次，单次测量的区间远小于这个值。改完后 `BUILD_OK`。

另外，构建前必须先结束加载了这个 DLL 的游戏实例，否则 DLL 文件被占用，写不进去。

## 5. 验证【实测】

| 项目 | 结果 |
|---|---|
| 单实例锁步，随机动作 600 步 | 59.1 步/s，帧号间隔全为 1（`frame_gaps=0`），`ls_timeouts=0`；发布耗时中位数 3453 µs、p95 6568 µs；等待放行中位数 715 µs |
| 补丁实例与原版实例并存 | 原版实例（`game_session.py start`，pid 8936）运行时启动 slot 0，两者都存活 |
| 串行启动 4 个实例 | slot 0 用时 18.4 s，slot 1 18.7 s，slot 2 20.3 s，slot 3 20.6 s |
| 并存检查 | `list_instances()` 返回 `{0: 6156, 1: 47856, 2: 28992, 3: 19168}` |

锁步烟测开头有一次 reset 报"等待新回合超时"，触发了一次游戏重启，重启后 600 步正常。根因见 §5.1。

### 5.1 reset 超时：锁步 100 ms 超时让空闲 worker 的游戏自由运行【实测】

worker 在等其它 worker 或学习者 update 时不调 `step`。锁步 100 ms 超时一到，DLL 就放行，游戏按旧动作（无输入）自由运行。COM3 在这段时间里打空闲的 1P，打满 2 胜后进入胜负对话，下一次 reset 等不到新回合，只能重启实例。

修正：`configs/train_ppo_com3_multi.yaml` 设 `lockstep_timeout_ms: 0`（无限等待）。实测锁步挂起 240 s：进程存活，窗口不判 hung，放行后正常推进。看门狗只在主循环里自检，主线程停在锁步里时它也一起停。

### 5.2 并行评估整轮丢失：超时按整轮计【实测】

第一次 100 局 4 实例评估：每个 worker 分到 25 局（约 40 min），而学习者只在整轮结束时收一次回复，`worker_timeout` 是 900 s。15 min 后 4 个 worker 同时被判"评估无回复"并重启，已打完的局全部丢掉。

修正（`src/train/parallel.py`）：

- worker 每打完一局就发 `("eval_rec", rec)`，最终回复只带完成局数；
- `WorkerPool._gather(progress=...)` 每收到一条进度消息就把该 slot 的计时清零，`worker_timeout` 变成"两局之间"的上限；
- `WorkerPool.evaluate` 合并流式记录。worker 中途失效时，已打完的局照常计入，缺的局下一轮补派；`on_record` 按到达顺序实时回调，`scripts/eval_parallel.py` 因此能逐局打印进度。

回归测试：`tests/test_parallel_train.py::TestWorkerPool::test_eval_longer_than_timeout_keeps_every_record`。单 worker、超时 10 s，4 局共约 12 s，4 局全部保留，worker 没有被重启。

### 5.3 加速：帧间隔补丁 + reset 全程锁步【实测】

`launch_instance(slot, period_us=)` 在挂起状态下校验 0x40dbe8 处 `c7 44 24 28 1a 41 00 00`（`mov [esp+28h], 16666`），再改写立即数。配置项是 `env.frame_period_us`，由 `train_ppo.frame_period_us(cfg)` 读取，`GameProvider.make`、按 slot 重启、切难度重启三条启动路径都透传。主循环每秒统计一次：若历史最高 fps 仍低于 60，period 就减 33（启动追帧）；跑到过 60 fps 之后 period 不再变。

战斗中锁步由策略定速，游戏一次循环推进一个逻辑帧，逐帧语义与 60 fps 相同；加速只缩短演出、菜单和回合之间的墙钟时间。period 4166 µs 实测：对手为 COM3，随机动作 3 局都正常 KO，战斗中 93~181 步/s，KO 到下一回合的 reset 从 11 s 降到 3.6 s。

**加速暴露的开局空窗**：旧 `reset` 先关锁步，走完回合切换、清胜场、写回合计时器（`_apply_round_units` 里有 `sleep(0.08)` 的墙钟循环）后才开锁步。这段时间游戏自由运行，COM 先动。空窗随帧率变化：1x 时 10~12 帧，4166 µs 时 32~38 帧。

修正（`src/env/th155_env.py`）：

- reset 全程保持锁步。DLL 只在 state 8 的帧边界阻塞，演出和菜单照常推进；新回合第一帧就停住，之后的写操作都在游戏停住时完成；
- `_hold_lockstep()`：锁步已开时不再调 `enable_lockstep`。后者会把 `ctl_step_seq` 对齐到 `ls_seq`，等于放行 DLL 当前停住的那一帧；
- 只有 1 帧重置（回合中途 reset）需要游戏自由跑几帧，所以只在这条路径上先关锁步，完成后重新打开；
- `_apply_round_units` 改为直接从内存读 `battle.time`。游戏停住时，共享内存快照还是帧边界上的旧值，看不到刚写入的计时器。

修正后复测：1x 和 4166 µs 下，首局第一步前 `battle.count` 都是 451；第 30 步时 count=481、time=8880、`enableTimeCount=True`（99 s 计时器已写入且在走）。1 帧重置路径：0.01 s 完成，双方满血，time=8910。

### 5.4 锁步帧边界的发布耗时：区域缓存【实测】

4 实例训练时每个实例只有 41~58 帧/s。共享内存里 `ls_pub_us`（主线程在帧边界同步发布状态的耗时）均值 7~17 ms，`ls_wait_us`（等策略放行）约 4 ms。外部挂起主线程做栈采样，52% 的样本停在 `IsReadable` 的 `VirtualQuery` 上：发布一帧要验证约 90 个对象指针，每个都是一次内核调用。

`PubReadable` 在一次 `PublishState` 内缓存已确认可读的整块区域（16 项，只缓存可读结论），发布路径的 14 处调用改用它；扫描线程不变。双算版本 103 万次调用不一致 0 次。单实例 A/B：`ls_pub_us` 5984 → 894 µs，91.9 → 175.1 步/s。4 实例训练：每实例 41~58 → 107~140 帧/s，合计 145~230 → 355~400 步/s。

## 6. 过程中的错误与修正

| # | 错误 | 表现 | 修正 |
|---|---|---|---|
| 1 | 原版双开 | 第 2 个实例弹窗退出，约 3 s 后第 1 个也消失 | 定位到 `TOKEN_-1` 共用映射，补丁 0x832360 格式串 |
| 2 | 第二实例注入改名 DLL 拷贝（可行性调查时的临时做法） | 每多一个实例就要一份 DLL | DLL 按宿主 pid 命名映射和事件 |
| 3 | 注入目标选成了空壳进程，或注入过早 | `CreateToolhelp32Snapshot` 报 299，或注入后无效果 | 按可见窗口选父进程，等标题菜单就绪后再注入 |
| 4 | 担心锁步阻塞触发看门狗 | — | 实测挂起主线程 10 s、240 s 都存活；训练配置改为无限等待（§5.1） |
| 5 | 锁步代码用了 64 位乘除 | 链接缺 `__alldiv` / `__allmul` | QPC 改为 32 位计数和 `g_qpcPerUs` |
| 6 | 构建时旧实例占用 DLL | DLL 无法覆盖 | 构建前按 pid 结束加载该 DLL 的实例 |
| 7 | 锁步 100 ms 超时 | 空闲 worker 的游戏被 COM3 打满 2 胜，reset 失败重启 | `lockstep_timeout_ms: 0`（§5.1） |
| 8 | 并行评估只在整轮结束时回复 | 100 局评估 15 min 后 4 个 worker 全被判失效，记录全丢 | 逐局回传，超时按两局之间计（§5.2） |
| 9 | reset 期间关锁步 | 开局 COM 先动 10~38 帧，帧数随帧率变 | reset 全程锁步（§5.3） |
| 10 | 发布路径每个指针都调 `VirtualQuery` | 锁步每帧发布 7~17 ms，实例 41~58 帧/s | 单次发布内的可读区域缓存（§5.4） |

## 7. 用法

```powershell
# 启动 / 结束 / 列出（需 PYTHONPATH=src）
py -3.11 -m th155.instances launch 0 1 2 3
py -3.11 -m th155.instances kill 2
py -3.11 -m th155.instances list

# 4 实例并行评估
py -3.11 scripts/eval_parallel.py <config> <ckpt> <局数> --num-envs 4

# 4 实例并行训练（锁步 + 每个 env 1024 步，合计 4096）
py -3.11 -m train.train_ppo --config configs/train_ppo_com3_multi.yaml --resume <ckpt>
```

训练和评估由 `train.parallel.GameProvider` 按 slot 调用 `launch_instance` / `kill_instance` / `slot_pid`，你不需要手工启动实例。菜单驱动在命名互斥量 `Local\th155_rl_menu_lock` 里串行执行。

## 8. 限制与风险

1. **版本绑定**：0x841d38、0x832360、帧计数 RVA 0x4DACE0 都只对 Ver1.21b 有效。原字节校验不过时拒绝启动。
2. **slot 只能 0~9**。
3. **`system.dat` 共用**：多个实例共用一个游戏目录，退出时可能同时写 `system.dat`【推断】。需要时先备份，或每个实例用单独目录（`.pak` 硬链接）。
4. **不要按进程名杀**：`game_session.py kill`，以及单实例路径的 `restart_game(slot=None)`，会结束全部 th155.exe，包括其它 slot。
5. **工具黑名单**：训练机上开 Cheat Engine 这类工具，所有实例都会自杀。
6. **锁步超时**：训练配置用 0（无限等待）。若改回非 0，空闲 worker 的游戏会自由运行（§5.1）。
7. **帧间隔补丁**：0x40dbe8 同样绑定 Ver1.21b；`period_us` 限制在 [1000, 16666]。

## 9. 改动文件

| 文件 | 改动 |
|---|---|
| `src/th155/instances.py`（新） | 挂起启动、PEB 取基址、校验并补丁、等窗口和标题、注入、按 pid 树结束、按补丁串识别 slot、CLI；`period_us` 帧间隔补丁 |
| `src/bridge/th155_rl_bridge.cpp` | 按 pid 命名映射和事件；`LockstepBoundary`；发布自旋锁；32 位 QPC；发布路径 `PubReadable` 区域缓存 |
| `src/bridge/protocol.h`、`src/bridge/shm.py` | 协议 v19：锁步字段、`ls_pid`、`ls_pub_us`；`open(pid)`、锁步 API |
| `configs/offsets.yaml` | `engine.frame_counter_rva: 0x4DACE0` |
| `src/th155/game.py`、`src/env/th155_env.py` | `pid` 参数；env 的 `lockstep` / `lockstep_timeout_ms`；reset 全程锁步 |
| `src/train/train_ppo.py`、`src/train/parallel.py` | `build_env(pid=)`；按 slot 重启实例；`GameProvider`；`frame_period_us`；评估逐局回传 |
| `configs/train_ppo_com3_multi.yaml`（新） | 4 实例 + 锁步（无限等待）+ `frame_period_us: 4166` |
