# AGENTS.md — 工作区指南

## 项目概要

LBoL（Library of Babel）游戏的 BepInEx 联机 MOD，使用 LiteNetLib 实现 UDP 网络通信。C#/.NET 项目。

- **目标框架**: `netstandard2.1`（插件）/ `net9.0`（测试/基准/调试工具）
- **SDK 版本**: .NET 9.0（无 `global.json`）
- **插件入口**: `networkplugin/Plugin.cs` — `BaseUnityPlugin` + HarmonyX + DI

## 构建与测试

```bash
# 构建主插件
dotnet build networkplugin/NetWorkPlugin.csproj -v minimal

# 运行所有测试（50 个）
dotnet test networkplugin/Tests/NetworkPlugin.Tests.csproj

# 基准测试
dotnet run --project networkplugin/Benchmarks/NetworkPlugin.Benchmarks.csproj -c Release

# 部署到游戏（可选）
powershell -ExecutionPolicy Bypass -File copy_networkplugin_dll.ps1
```

## 项目结构

| 项目 | 框架 | 说明 |
|------|------|------|
| `networkplugin/` | netstandard2.1 | **主开发区** ~189 文件, ~60k 行 |
| `networkplugin/Tests/` | net9.0 | 50 个 xUnit 测试 (Moq, FluentAssertions) |
| `networkplugin/Benchmarks/` | net9.0 | BenchmarkDotNet |
| `MyFirstPlugin/` | netstandard2.1 | 示例插件 |
| `UiObjectQueryPlugin/` | netstandard2.1 | HTTP 调试 UI 工具 |
| `debugtools/` | net9.0 | 调试控制台工具 |
| `lbol/` | — | LBoL 游戏源码镜像（5 个子项目） |

## 架构要点

- **DI 容器**: `Microsoft.Extensions.DependencyInjection 9.0.9`，在 `Plugin.cs` 中注册为单例
- **网络**: LiteNetLib 0.8.3.1 UDP 库
- **补丁**: HarmonyX 2.14.0 运行时打补丁，`Plugin.ApplyHarmonyPatchesSafely` 逐个安全应用
- **同步引擎**: `SynchronizationManager` → `EventBufferManager` + `StateCacheManager` + `AvailabilityTracker`
- **三大类已物理拆分**:
  - `TradePanel` → 6 文件（主文件 873 行）
  - `TradeDetailDialog` → 7 文件（主文件 799 行）
  - `NetworkServer` → 3 文件（主文件 548 行）

## 关键目录

- `networkplugin/Core/` — 同步核心
- `networkplugin/Network/Server/` — 服务端 + 路由 + 广播
- `networkplugin/Network/Client/` — 客户端连接与重连
- `networkplugin/Patch/` — Harmony 补丁（占总代码量 46%）
- `networkplugin/UI/Panels/` — 交易面板（6 文件）
- `networkplugin/UI/Dialogs/` — 交易对话框（7 文件）

## 常用文档链接

- [代码质量改进计划](../plan/20260519_networkplugin_code_quality_improvement_plan.md) — 评估 76/100
- [规划路线图](../networkplugin/PLANNING_ROADMAP.md) — v2.7 功能概览
- [多人回归清单](../plan/MULTIPLAYER_REGRESSION_CHECKLIST.md) — 回归测试基线
- [路由回归清单](../plan/NETWORK_ROUTE_REGRESSION_CHECKLIST.md) — FullStateSync/RoomState
- [目录重构计划](../plan/DIRECTORY_REFACTOR_PLAN.md) — 命名空间对齐计划
- [工作区目录指南](../WORKSPACE_DIRECTORY_GUIDE.md) — 完整目录职责说明

## 常见注意事项

- **空 catch**: 项目中 16 处空 catch 已全部修复，新增代码请避免引入新的空 `catch { }`。
- **Harmony Reverse Patch**: `RemoteCardUsePatch.Card_GetActions_Original` 是 reverse patch 桩，误入会抛 `NotImplementedException`。
- **UI 线程**: Harmony 补丁可能运行在任意线程，UI 更新必须通过 `Plugin.RunOnMainThread`。
- **Partial 文件**: TradePanel/TradeDetailDialog/NetworkServer 已拆为多文件，新增方法请加到对应分片文件，不要集中到主文件。
- **魔数**: 已有 5 个常量文件（`NetworkConstants`, `SyncConstants`, `UIConstants`, `LogConstants`, `ServerConstants`），不要硬编码字面量。
- **配置文件漂移**: 同名配置项只在 `ConfigManager` 中绑定一次。
- **Harmony Patch 与 DI**: Harmony 通过反射实例化 Patch 类，**无法使用构造函数注入**。因此 Patch 类必须通过 `ModService.ServiceProvider?.GetService<T>()` 静态获取 DI 服务。这是 Harmony 机制决定的必要模式，不是反模式。非 Patch 类（普通服务/管理器/工具类）仍应使用构造函数注入，不要模仿 Patch 类的写法。
- **UI 锚点与坐标系平衡**: 调整 UI 组件位置时，严禁随意改动同级/关联控件的 Anchor 或 Pivot。若发生错位，禁止采用巨额硬编码 localPosition 偏移（例如 `(350f, -500f)`）强行硬拉，必须统一 Anchor 锚定基准（如顶部居中 `(0.5f, 1f)`）并使用 `anchoredPosition` 维持相对排布。
- **文件操作与删除安全**: 禁止批量删除文件、删除文件夹或目录。严禁使用 `del /s`、`rd /s`、`rmdir /s`、`Remove-Item -Recurse`、`rm -rf` 等。需要删除文件时，只能一次删除一个明确路径的文件（正确示范：`Remove-Item "C:\path\to\file.txt"`）。如果需要批量删除文件，应停止操作，并向用户请求，让用户手动删除。
- **UI 原生艺术字体与材质规范**: 从原游戏 UI 模板（如 `MessageDialog`）复用组件时，弹窗正文（`mainText`）默认使用细宋体/明体，而原生按钮（`ConfirmButton`）使用的是带黄色描边与高光的艺术字体（`TMP_FontAsset` 与 `fontSharedMaterial`）。若需要制作与原游戏高度一致的大标题或醒目文本，切勿直接使用普通正文模板，应从原生按钮的 `TextMeshProUGUI` 提取 `font`、`fontSharedMaterial` 及颜色，实现原生一致的艺术字风格。
- **底栏控件归一化与父级坐标系对齐**: 当主面板被拆分为中心内容展示区（`ContentRoot`）与底部控制栏时，底栏所有同级控件（出价栏、确认/取消按钮、操作按钮）必须统一挂载在同一个父级容器（如 `Frame`）下，使用正向归一化锚点（如 `Y: 0.02 ~ 0.18`）。严禁将底栏控件挂在 `ContentRoot` 下使用负锚点（易随内容区高度变动而漂移），且顶沿必须严格受限于 `ContentRoot` 的底沿（`≤ 0.18`），防止底栏控件插入卡牌/展品展示区。
- **多行文字行带高与防叠字规范**: 在 4K 分辨率或自适应布局中，垂直排列的多行标签/按钮控件，每行的高宽区域（行带高）必须满足 `行带高 ≥ 字高 × 1.4`（建议 2.0 倍以上），且两行之间必须留有显式的局部坐标或锚点间隔（建议局部行距 ≥ 100 像素），否则在特定缩放比例下极易导致上下两行文字叠合穿插。
- **运行时 HTTP 热调参数固化的一致性折算**: 使用 HTTP API 针对运行时活体对象进行几何调优时，若活体对象因历史代码残留在不同父节点下，热调测出的局部坐标或负锚点值不可直接照抄进静态代码，必须换算为目标规范父级坐标系下的归一化值，并确保静态代码中的 `fontSize` 与 `localScale` 乘积（有效字号）与运行时验证的视觉结果严格一致。


### 过程中的错误与修正

| # | 错误 | 表现 | 修正 | 教训 |
|---|------|------|------|------|
| 1 | `UpdateMapIcons` 过滤条件排除本地玩家 | 本地玩家不出现在地图上 | 移除此过滤条件 | 新增功能时必须审查所有过滤条件 |
| 2 | `GetFallbackCharacterId()` 优先级反了且未处理空字符串 | `Id` 可能为 `""`，`??` 不触发回退，导致加载失败 | 优先 `ModelName`，用 `IsNullOrWhiteSpace` 检查 | C# `??` 不处理空字符串；Unity 字段可返回 `""` |
| 3 | 网络事件覆盖本地玩家的 `CharacterId` | 网络消息到达后覆盖 `EnsureSelfPlayer` 设置的值 | 赋值前保护：空 charId 时用 `GetFallbackCharacterId()` | 网络同步和本地状态存在竞争，所有写入点都要保护 |
| 4 | 无本地玩家缓存生命周期管理 | `_selfPlayerId` 变化后旧条目残留，重复或错位 | 新增 `EnsureSelfPlayer_NoThrow()` + 旧条目清理 | 动态 ID 需主动清理旧缓存 |
| 5 | Addressables 返回 1x1 占位符未过滤 | 显示为极小色块 | 增加 `width <= 1 && height <= 1` 过滤 | Addressables "加载成功"≠"资源有效" |
| 6 | `Image.color = Color.red` 诊断残留 | 本地玩家头像显示为红色方块 | 改回 `Color.white` | 诊断修改验证后必须及时清理 |
| 7 | 本地玩家图标尺寸差异化 `(240, 320)` | 本地图标比别人大一倍，重叠 | 移除尺寸覆盖，统一尺寸 | 差异化尺寸需配套调整间距 |
| 8 | `horizontalSpacing` 小于 root 宽度 | 同一节点多个头像重叠 | 最终 `rootSize=(100,140)`，`spacing=130` | 间距必须大于元素宽度 |
| 9 | 头像在 root 中锚定方式不一致 | 不同比例 sprite 视觉高低不一 | Avatar 改为居中锚定 `(0.5,0.5)` | UI 对齐应以边界框中心为基准 |
| 10 | `_selfPlayerId` 重置时机太晚 | `EnsureSelfPlayer` 使用旧 ID，导致找不到本地玩家 | 将 ID 同步移到注入函数之前 | 状态字段必须先同步再使用 |
| 11 | 本地玩家位置兜底链路太长 | 兜底函数依赖可能失败的中间查找 | fallback 直接使用 `CurrentMap.VisitingNode` | 兜底逻辑应该最直接、最少依赖 |
| 13 | 居中锚定后 `sizeDelta.x = 0` | 头像 Image 宽度为 0，完全不可见 | 改为 `sizeDelta = (100, 100)` | 居中锚定下两轴 sizeDelta 都必须非零 |
| 14 | 诊断代码未清理 | `GetRedSprite()`、详细位置日志等残留较多 | 已全部清理，高频 Log 移出，保留关键的 LogError 异常捕获 | 功能验证稳定后应及时清理调试日志，避免日志噪音 |
| 15 | 地图头像无圆形遮罩与边框 | 头像显示为普通矩形框，没有与战斗界面保持圆角和精美边框的一致性 | 引入 `AvatarMask` (使用圆形遮罩) + `Border` 边框，本地玩家金色边框，远程玩家白色边框 | 差异化 UI 需要统一考虑层级与风格对齐 |
| 16 | 调整 UI 控件时随意改动关联组件 Anchor 破坏坐标平衡 | 控件位置严重偏移飞出，需使用巨大硬编码 `localPosition` 补偿，打破卡片/条目整体布局 | 统一同级控件 Anchor 锚点基准（如 `(0.5, 1)`），采用相对 `anchoredPosition` 自上而下线性排布 | 严禁随意修改关联 UI 的 Anchor/Pivot；禁止使用巨额硬编码 localPosition (如 350, -500) 强行拉回错位控件，必须保持坐标系基准统一与布局平衡 |
| 17 | 复用 `MessageDialog.mainText` 作为标题和状态模板 | 标题使用细宋体/明体，与下方原生按钮（艺术字体+描边材质）风格脱节 | 改从原生 `confirmButton` 中提取 `TextMeshProUGUI` 模板及其 `font` / `fontSharedMaterial` 材质球 | LBoL 原生 UI 的大标题与按钮字形有独立艺术字体资源，复用模板时需优先选取按钮字形 |
| 18 | 出价控件纵向高度过小且挂载在 `ContentRoot` 下使用负锚点 | 卡牌/金币/展品三行文字叠在一起，且父级变动时易错位 | 统一挂载至 `Frame` 空间（`Y: 0.02~0.18`），行带高扩大至字高 2.4 倍，行距扩大 62% | 底栏控件必须统一在底栏父级坐标系，行带高必须 ≥ 字高 1.4 倍防止叠字 |
| 19 | 右下角操作栏 `anchorMax.y = 0.20` 超过中心面板底沿 `0.18` | 按钮上沿侵入中心黑色底板与卡牌预览区 | 收缩至 `anchorMax.y = 0.17`，与中间【确认】【取消】按钮严格平齐 | 底栏控件顶沿严禁跨越上方内容容器底沿，必须做空间边界校验 |
| 20 | 运行时 HTTP 热调参数与静态代码中的 `fontSize` / `localScale` 乘积不一致 | 热调效果与重启后 DLL 实际渲染的有效字号存在 10%~20% 偏差 | 将静态代码 `fontSize` 与 `localScale` 严格锚定为热调验证通过的基准数值 | 热调参数固化回静态代码时，必须计算 `fontSize × scale` 的有效字号一致性 |

> 完整过程记录见 [handoffs/handoff-local-player-avatar.md](../handoffs/handoff-local-player-avatar.md)

### 本地玩家头像关键代码路径

```
UpdateMapIcons()
  ├── _selfPlayerId = NetworkIdentityTracker.GetSelfPlayerId() ?? "__local__"  // 先同步 ID
  ├── EnsureVirtualAiDefaultPlayer_NoThrow()
  ├── EnsureSelfPlayer_NoThrow()              // 本地玩家注入 _players + 清理旧缓存
  ├── players 过滤 (IsConnected, LocationX/Y >= 0)
  ├── 兜底：_selfPlayerId 不在 players 中时，用 CurrentMap.VisitingNode 添加
  ├── 强制 Destroy 旧 self icon
  └── foreach player:
        ├── EnsureMapIcon(player)
        │     ├── TryGetAvatarSpriteForPlayer()
        │     ├── 本地玩家 fallback: 复制其他 icon → Koishi → WhiteSprite
        │     └── Root (100x140) 
        │           ├── AvatarMask (100x100，圆形遮罩) -> AvatarImage (100x100)
        │           ├── Border (100x100，圆形边框，金色/白色)
        │           └── Label (名字, isSelf 时带 "[我]" 前缀)
        └── anchoredPosition = nodePos + (startX + i * spacing, 0)
```

### 待办事项

- [x] 复制 DLL 并重启游戏，验证头像显示和对齐
- [x] 若本地玩家仍显示白色方块，诊断 `LoadCharacterAvatarSprite` 路径（`ModelName` 值、Addressables 路径）
- [x] 获取 BepInEx 日志中 `MapIcon`/`Group` 行，确认头像位置计算正确
- [x] 清理诊断代码：`GetRedSprite()`、`_redSprite`/`_redTexture`、详细位置日志、`catch (Exception ex)`
- [x] 参考远程玩家头像模板（圆角 mask、边框）优化本地玩家/所有玩家图标样式

## 交付规范

- 生成 `implementation_plan.md` 和 `walkthrough.md` 交付件时，必须使用**中文**进行编写和表达。

## 文件操作与安全规范

- **禁止批量删除文件、删除文件夹或目录**。
- **严禁使用以下命令**：
  - `del /s`
  - `rd /s`
  - `rmdir /s`
  - `Remove-Item -Recurse`
  - `rm -rf`
- **单文件删除**：需要删除文件时，只能一次删除一个明确路径的文件。
  - 正确示范：`Remove-Item "C:\path\to\file.txt"`
- **批量删除处理**：如果需要批量删除文件，应停止操作，并向用户请求，让用户手动删除。


