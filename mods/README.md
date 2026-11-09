# 《东方凭依华》(TH15.5) 原生 Mod 加载器 (BepInEx 风格)

本系统采用类似 **BepInEx / Mod Organizer 2** 的架构设计，通过 **`winmm.dll` 原生 DLL 劫持注入 + 虚拟文件系统 (VFS) 动态重定向**，实现了真正的**免解包、零文件污染、多 Mod 并存与独立开关**的现代化 Mod 加载体验。

玩家或开发者只需将游戏 Mod 放入 `mods/` 文件夹，即可直接双击运行原版 `th155.exe`，无需任何外部启动脚本或手动内存注入器。

---

## 一、系统架构与核心特性

```
th155.exe (双击启动)
   └── 自动加载 winmm.dll (代理系统 winmm.dll 并注入加载器)
         ├── 1. 读取 mod_loader.ini (全局开关与控制台选项)
         ├── 2. 扫描 mods/ 目录下所有 Mod 子文件夹与 mod.ini
         ├── 3. 按 [Mods] 配置与优先级 (Priority) 自动解析与覆盖排序
         ├── 4. 挂钩 (IAT Hook) Win32 文件 API (CreateFileA / GetFileAttributes)
         ├── 5. 补丁黄昏 Manbow 引擎门禁 (VerifyModuleSignature / GateCheck)
         └── 6. 监听 PackageManager 并翻转 Pak 索引红黑树节点 (实现免解包直读)
```

1. **完全类似 BepInEx 的模块化体验**：
   - 每个 Mod 是 `mods/` 下的一个独立子文件夹（如 `mods/th135_lastwords`、`mods/koishi_spells`）；
   - 每个 Mod 自带 `mod.ini` 清单，包含 Mod 名称、作者、版本、描述、默认启用状态及加载优先级；
   - 玩家可随时将 Mod 文件夹放入或移出 `mods/`，或在文件夹名后追加 `.disabled` 临时禁用。

2. **全局配置与一键开关 (`mod_loader.ini`)**：
   - 可以在游戏根目录的 `mod_loader.ini` 的 `[Mods]` 分区中为任意 Mod 显式指定 `1`（启用）或 `0`（禁用）；
   - 支持多 Mod 并存：当两个 Mod 提供同一个文件时，高优先级（`Priority` 数值更大）的 Mod 自动生效，低优先级对应文件被覆盖，无任何文件覆盖冲突。

3. **零文件污染 (Virtual File System Overlay)**：
   - **绝不向游戏原始 `data/` 目录复制或残留任何文件**！
   - 通过挂钩 `CreateFileA` / `GetFileAttributesA`，在游戏引擎尝试读取文件时，动态将其重定向到对应生效 Mod 的内部路径；
   - 禁用 Mod 时，只需修改配置，下次启动游戏即瞬间还原，原版文件不受任何影响。

4. **可选实时调试控制台 (BepInEx Console 风格)**：
   - 在 `mod_loader.ini` 中设置 `Console=1`，启动游戏时将自动弹出一个独立的黑框控制台，实时输出 Mod 发现、VFS 映射与文件重定向日志，便于 Mod 调试排错。

---

## 二、目录结构

```
<游戏根目录>/
├── th155.exe                   # 游戏主程序 (v1.21b)
├── winmm.dll                   # 原生 Mod 加载器核心 DLL
├── mod_loader.ini              # 加载器主配置文件
├── mod_loader.log              # 运行日志
└── mods/                       # Mod 插件存放目录 (类似 BepInEx/plugins)
    │
    ├── th135_lastwords/        # 【Mod 1】全角色心绮楼终符 Mod
    │   ├── mod.ini             # Mod 描述清单 (Enabled=1, Priority=100)
    │   └── data/               # 角色脚本、动画与字模资源
    │       ├── actor/
    │       └── font/
    │
    └── koishi_spells/          # 【Mod 2】古明地恋心绮楼 Boss 常规符卡 Mod
        ├── mod.ini             # Mod 描述清单 (Enabled=0, Priority=200)
        └── data/               # 恋恋专属 Boss 符卡脚本、CSV 与动作
            ├── actor/
            ├── spell/
            └── system/
```

---

## 三、常用配置与开关说明

### 1. 主配置文件 `mod_loader.ini`

```ini
[ModLoader]
; 全局总开关：1=启用, 0=禁用所有 Mod
Enabled=1
; Mod 存放根目录
ModsDir=mods
; 日志文件名
LogFile=mod_loader.log
; 实时控制台窗口 (0=关闭, 1=打开调试控制台黑框)
Console=0

[Mods]
; 在此显式开关各个 Mod (1=启用, 0=禁用)
; 若未在此配置，则默认遵循各自 mods/<mod_id>/mod.ini 中的 Enabled 选项
th135_lastwords=1
koishi_spells=0
```

### 2. Mod 清单文件 `mod.ini`

在每个 Mod 文件夹根目录下创建 `mod.ini`：
```ini
[Mod]
Name=东方心绮楼 全角色终符 (LastWord) 替换 Mod
Author=155rl
Version=1.0.0
Description=移植心绮楼全部 10 名角色的 LastWord。全角色普通攻击与常规符卡保持 TH15.5 原版。
Enabled=1
Priority=100
```

---

## 四、当前内置 Mod 说明

| Mod ID | 状态 | 优先级 | 说明 |
|---|---|---|---|
| `th135_lastwords` | **默认启用** | 100 | 心绮楼全 10 角色 LastWord 纯净替换包。所有角色的常规招式与符卡 100% 保持 TH15.5 原版。 |
| `koishi_spells` | **默认禁用** | 200 | 古明地恋 TH13.5 Boss 常规符卡扩展包（本能「フロウディアン」与茨符「コンファインドイノセント」）。启用时因优先级（200 > 100）会自动无缝覆盖恋恋的常规符卡与立绘，而其他 9 角色的 LastWord 仍然正常生效。 |
