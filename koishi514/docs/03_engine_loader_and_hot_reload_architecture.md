# TH15.5 引擎文件加载器逆向与免解包热加载架构

本文档记录了对 `th155.exe`（《东方凭依华》主程序）中 Pak 归档管理器与 Squirrel 脚本编译器（`manbow.CompileFile`）的 IDA 逆向分析结果，以及 `koishi514` Mod 所采用的**运行时内存 Pak 索引拦截 + 本地磁盘脚本直读**方案。

---

## 一、 `th155.exe` 资源加载与 Squirrel 编译链路

### 1. 关键函数地址（ImageBase = `0x400000`；下为 RVA；门禁语义已按磁盘镜像反汇编订正）
| 地址 (VA) | RVA | 符号 / 功能描述（按磁盘镜像 `0x26120` / `0x23E50` / `0x243B0` / `0x24E40` 实测反汇编） |
| :--- | :--- | :--- |
| `0x8DAD18` | `0x4DAD18` | **`g_PackageManager` 全局单例指针**（`pkg+0x20` = Pak 红黑树头；`pkg+0x28/0x2C` = 7 元数组；`pkg+0x30/0x34/0x38` 实测全 `0`（空容器）；`pkg+0x1C == 0x28` 为门禁内的长度阈值） |
| `0x426120` | `0x026120` | **路径门禁 `GateCheck(pkg, path, len)`（旧文档曾误写为 RegisterDiskPath / 前缀放行，均为错误）**：`data/` 前缀命中即拒绝（见 §2），白名单容器为空，非 `data/` 相对路径同样落回失败 |
| `0x4243B0` | `0x0243B0` | `FindPakEntry(path)`：归一化→FNV-1a→取负→查 `pkg+0x20` 红黑树 |
| `0x423E50` | `0x023E50` | `OpenFileReader(path, out)`：Pak 命中走 Pak 分支；未命中则过空串检查→`0x26120` 门禁→`0x2DE80 CreateFileA` 磁盘分支；任一失败返回 `al=0` |
| `0x424E40` | `0x024E40` | `manbow::CompileFile`：`call 0x423E50` 后 `test al,al / jne` 成功分支（含 `0xFAFA` 字节码 vs 明文 `sq_compilebuffer` 双分支）；失败抛 Squirrel 异常 |

### 2. 路径哈希算法（Negative FNV-1a 32-bit）
在 `0x4243B0` 中，引擎对传入的文件路径（如 `"data/actor/koishi_base.nut"`）先做归一化：
1. 将 ASCII 大写字母 `'A'..'Z'` 转为小写 `'a'..'z'`（Shift-JIS 双字节字符原样跳过）；
2. 将正斜杠 `'/'`（`0x2F`）统一替换为反斜杠 `'\\'`（`0x5C`）；
3. 计算标准 FNV-1a 32-bit 哈希，最后取算术负值（`(-hash) & 0xFFFFFFFF`）：

```python
def th155_pak_hash(path: str) -> int:
    s = path.replace('/', '\\').lower().encode('shift_jis')
    h = 0x811C9DC5
    for b in s:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return (-h) & 0xFFFFFFFF
```

古明地恋相关脚本的哈希值如下（`apply_koishi514_mod.py::th155_pak_hash` 实测，与运行中 `th155.exe` 的 Pak 红黑树节点 `key` 完全一致）：
| 脚本路径 | 归一化路径 | Negative FNV-1a Hash |
| :--- | :--- | :--- |
| `data/actor/koishi.nut` | `data\actor\koishi.nut` | `0x92A830A4` |
| `data/actor/koishi_base.nut` | `data\actor\koishi_base.nut` | `0x8CA7182E` |
| `data/actor/koishi_combo.nut` | `data\actor\koishi_combo.nut` | `0x8CDAE403` |
| `data/actor/koishi_init.nut` | `data\actor\koishi_init.nut` | `0xD78911A7` |
| `data/actor/koishi_shot.nut` | `data\actor\koishi_shot.nut` | `0x0268DD65` |
| `data/actor/koishi_team.nut` | `data\actor\koishi_team.nut` | `0xC1C00ECA` |

---

## 二、 磁盘直读链路结论（按磁盘镜像反汇编订正：`data/` 磁盘回退永久不可用）

> **最终结论（实测，不可再反转）：`key ^= 1` 隐藏 Pak 键后，`data/actor/koishi*.nut` 在 `0x26120` 门禁必失败，磁盘分支不可达。** 不要再让用户"重启后进选人验证"——该验证不可能成功。内存键补丁本身可逆（PID `39920` 上写入/回滚均成功），但它只制造 Pak 未命中，而未命中之后是死路。

### 1. `0x26120` 门禁原文（磁盘镜像 `RVA 0x26120`，`0x426120` 起）
```asm
0x00426153  push 0x811C9DC5        ; FNV offset basis
0x0042615B  call 0x432D70          ; FNV-1a(min(strlen,4))，大写→小写归一化
0x00426163  cmp  eax, 0xD872E2A5   ; == FNV1a("data")？
0x00426168  je   0x4261F0          ; 相等→跳失败返回（xor al,al; ret 8）
```

`0xD872E2A5` 经实测即 `FNV1a("data")`（`'data'` 四字节正哈希），不是负哈希、不是其它前缀。**凡 `path[0..3]` 归一化后为 `data`（含 `data/actor/koishi*.nut` 全体）都走 `je 0x4261F0`（`xor al,al; ret 8` 失败返回），连后面的 `X:` / `.\` / 白名单分支都到不了。**

### 2. 非 `data/` 路径同样走不到磁盘
- `0x42616E` 起：`len <= pkg+0x1C(0x28)` 走 `jbe 0x4261FB`（`.\` 前缀检查分支）；超长才进 `X:` 盘符检查（`0x426177-85`），再进 `pkg+0x30` 容器循环。
- 运行中实测 `pkg+0x30/0x34/0x38` 全 `0`（空容器），`pkg+0x28` 的 7 元数组经复核是**长度-哈希并行数组区的一部分而非可用白名单**（`pkg+0x0C/0x10/0x14/0x18/0x1C` 指向的堆区 `0x0083C268-70` 为空向量起止指针）。空容器比较必然失败 → `xor al,al` → `0x423E50` 的 `test al,al / je 0xFC3F2D` 失败返回 → `CompileFile` 抛 Squirrel 异常。
- 因此结论与 advisor 一致：**`data/` 被直接拒绝，非 `data/` 死于空容器——磁盘分支对本 Mod 永久不可达。**

### 3. 对 Mod 方案的影响
1. `key ^= 1` 实验已证明可逆读写红黑树节点，但语义上只是"把 Pak 路堵死"，而磁盘路本身被 `0x26120` 焊死。**该链路不可验证、不可生效，不要再安排用户进游戏验证。**
2. 现行 `apply_koishi514_mod.py` 已是安全模式（只同步文件 + 只读诊断，不写内存），保持现状即可；同步到游戏目录的 `.nut` 在 Pak 命中时是惰性文件，不影响原版运行。
3. 若将来要让 Mod 真正生效，候选方向（二选一，需另立任务实测，**本任务不执行**）：
   - (a) Pak 内替换：按 `th155.pak` 格式重打包对应条目（需先逆向 Pak 加密/压缩，风险低于改代码段）；
   - (b) 门禁补丁：在 `0x426120` 入口或 `0x23E96 call 0x26120` 调用点做放行补丁（如 advisor 提议的入口 `mov al,1; ret 8` 5 字节补丁，需 `VirtualProtectEx` 改页属性 + 指令缓存刷新，且每次游戏更新都要重做）。**在补丁落地并实测 `CompileFile` 成功前，README 不得出现"热重载生效"表述。**

