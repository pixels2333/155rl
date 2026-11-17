# 东方心绮楼 (TH13.5) 凭依华还原补丁包 — thcrap 专用格式

本目录是将**背景观众像素小人还原 Mod**、**古明地恋心绮楼 Boss 符卡 Mod** 与 **全角色心绮楼终符 (LastWord) Mod** 按照 **thcrap (Touhou Community Reliant Automatic Patcher)** 官方标准格式转换生成的补丁包。

---

## 包含补丁列表

| 补丁 ID | 补丁名称 | 说明 |
|---|---|---|
| `th135_bg_mobs` | **背景观众像素小人还原** | 还原 11 张地图原版小人动画与客串角色。**内置 `th155.js` 内存 Binhack**，自动热修补 `Matrix::Vec3Transform` 引擎缺陷，无需外挂 DLL。 |
| `koishi_spells` | **古明地恋 Boss 符卡还原** | 替换恋恋常规符卡为心绮楼 Boss 符卡（本能「フロウディ安」、茨符「封闭的纯真」及专属立绘演出）。 |
| `th135_lastwords` | **全角色心绮楼终符还原** | 还原心绮楼全 10 角色的人气爆发终符演出与贴图。 |

---

## 安装与使用方法

### 方式一：作为独立本地仓库放入 thcrap（推荐）

1. 将本文件夹（`thcrap_patches/`）重命名为 `th135_restoration`，并移动复制到您的 `thcrap/repos/` 目录下：
   ```
   thcrap/
   └── repos/
       ├── thpatch/
       └── th135_restoration/          <-- 放入此处
           ├── repo.js
           ├── th135_bg_mobs/
           │   ├── patch.js
           │   ├── th155.js
           │   └── th155/data/...
           ├── koishi_spells/
           │   ├── patch.js
           │   └── th155/data/...
           └── th135_lastwords/
               ├── patch.js
               └── th155/data/...
   ```

2. 打开您的 thcrap 运行配置文件（例如 `thcrap/config/th155-zh-hans.js` 或自定义的 `.js` 配置），在 `"patches"` 数组的前面加入要启用的 Mod：
   ```json
   {
     "game": "th155",
     "patches": [
       { "archive": "repos/thpatch/lang_zh-hans/" },
       { "archive": "repos/th135_restoration/koishi_spells/" },
       { "archive": "repos/th135_restoration/th135_lastwords/" },
       { "archive": "repos/th135_restoration/th135_bg_mobs/" },
       { "archive": "repos/thpatch/base_tasofro/" },
       { "archive": "repos/thpatch/base_tsa/" }
     ]
   }
   ```

3. 正常双击 `thcrap_loader.exe` 启动对应的配置即可生效！

---

### 方式二：单独引用任意补丁

如果只需要背景小人，只需在任意配置中添加：
```json
{ "archive": "repos/th135_restoration/th135_bg_mobs/" }
```
thcrap 将自动检测到 `th155.js` 中的 `vec3transform_fix` 并在游戏启动时自动对内存进行修补，保证背景小人 100% 正确投影与显示。
