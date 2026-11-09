# 弹幕判定尺寸（hantei）调查与实机实测完整记录

本文记录**弹幕判定尺寸（Hitbox / Hurtbox / Graze）**的底层逆向事实、物理引擎架构、历史疑点解答、实战采样真值及 Bridge C++ 桥接落地方案。

> **终局定论：所有疑点与取数路径全部找清，实机实战 100% 采样闭环！**
> 
> 1. **核心架构**：底层物理引擎为 **Bullet Physics 2D**（`btBox2dShape` + `btCollisionObject` + `btCollisionWorld`）。
> 2. **取数路径**：**纯内存直读（Zero-Hook，Zero-Risk）**。所有攻击与受击判定常驻在 `Actor2D->anim`（`0x3C`）的 3 个 `vector` 容器中。
> 3. **实测真值**：
>    - **角色受击框 (Hurtbox)**：灵梦待机受击框为 `40.0 x 110.0`（`half_extents = [19.90, 54.90]`, `margin = 0.10`）。
>    - **真实弹幕判定框 (Hitbox)**：实战对局中发射灵梦符札（`motion = 2007`），成功从 `anim + 0x84` 提取出真实判定尺寸 **24.0 x 24.0** 与 **30.0 x 30.0**！
>    - **特效与真弹幕的分离**：彻底查明了前期样本中 `motion = 190` 的对象本质是**纯视觉特效/粒子残影（Effect）**，原生不具备攻击判定盒；只有真正的攻击实体（如 `motion = 2007`）才挂载 Bullet 碰撞盒。
> 4. **工程落地**：C++ Bridge DLL（`th155_rl_bridge.dll`）已完成补丁注入并编译通过，端到端测试（`e2e.py`）全绿（PASS）！

---

## 1. 现象真相：为什么此前弹幕 AABB 恒为点？

实测中活跃对象的 `left/top/right/bottom`（`0x1c/0x20/0x24/0x28`）恒为 `[x, y, x, y]`（`w=0, h=0`）。

### 1.1 逆向机理解密
在 `Actor2D` 构造函数（`sub_4C1110`）中：
```cpp
a1[3] = a2;  // x
a1[7] = a2;  // left   = x
a1[9] = a2;  // right  = x
a1[4] = a3;  // y
a1[8] = a3;  // top    = y
a1[10] = a3; // bottom = y
```
1. **原生设计**：所有 `Actor2D` 在初始化时其 AABB 默认就被设定为自身坐标点；
2. **玩家（Player）**：在每帧姿势更新时，游戏有一段受击盒回写逻辑，将当前受击盒的范围写回 `Actor2D` 的 `0x1c..0x28`（呈现出 `[x-20, y-60, x+20, y+50]` 40x110）；
3. **真实攻击弹幕（Projectile）**：弹幕原生**不使用也不更新** `Actor2D` 自身的 AABB！弹幕的所有碰撞检测完全委托给底层的 Bullet Physics 体系（`anim + 0x84` 的 `btBox2dShape`）；
4. **纯视觉特效对象（Effect）**：如前期困扰调查的 `motion = 190` 对象，本质是拖尾、火花、残影粒子，原生**不参与碰撞，Hitbox 容器为空**，其 AABB 从始至终就是点。
5. **采集层此前盲区**：Python 侧原有的 `shot_list()` 尝试读取 `player.shot_actor`，而该双向链表在游戏运行期间原生为空！弹幕只挂在 `Actor2DGroup`（`group_player`/`group_effect`）与堆分配区中。

---

## 2. 真实架构：Bullet Physics 2D 碰撞体系与对象图

### 2.1 判定盒在内存中的真实挂载结构
碰撞数据全部挂载在 `Actor2D->anim`（`AnimationController2D*`，偏移 `0x3C`）上：

`AnimationController2D`（基类 `AnimationControllerBase`）维护了 3 个 `std::vector<ContactBoxItem>` 容器（每项 8 字节：`{ContactBox* ptr, int id}`）：

| 偏移 | 容器名称 | 说明 | 采样真值 |
|---|---|---|---|
| `anim + 0x78` (`first`), `+0x7C` (`last`) | **受击框列表 (Hurtbox Vector)** | 角色受击区域 | 1 个 (40.0 x 110.0) |
| `anim + 0x84` (`first`), `+0x88` (`last`) | **攻击框列表 (Hitbox Vector)** | 弹幕与攻击判定核心 | 符札 (24.0 x 24.0 / 30.0 x 30.0) |
| `anim + 0x90` (`first`), `+0x94` (`last`) | **擦弹/防御框列表 (Graze/Guard Vector)** | 擦弹判定区域 | 4 个组合框 (53x54, 47x73...) |

每当动画切换动作/帧时，`AnimationController2D::sub_4BA510` 会从帧数据（Take Data，每盒 96 字节）中读取参数，调用 `sub_4BA5E0` 动态更新这些向量中的 Bullet 碰撞体尺寸。

### 2.2 完整的 5 级内存指针链
对于任意一个角色或弹幕的 `Actor2D*` 原生指针 `pActor`：

```
pActor (Actor2D*)
  └─ +0x3C: anim (AnimationController2D*)
       └─ +0x84: vector.first (指向 ContactBoxItem 数组首地址)
            └─ +0x00: box_obj (ContactBox*)
                 └─ +0x0C: col_obj (btCollisionObject*)
                      ├─ +0x10 .. +0x40: btTransform (世界变换矩阵与中心坐标)
                      ├─ +0x180 (384): 局部偏移中心 (ox, oy, oz)
                      └─ +0xCC (204): shape (btCollisionShape* -> btBox2dShape*)
                           ├─ +0x00: vtable (比对基址 + 0x447654)
                           ├─ +0x10: m_localScaling.x (float)
                           ├─ +0x14: m_localScaling.y (float)
                           ├─ +0x20: m_boxHalfExtents.x (float, 半宽)
                           ├─ +0x24: m_boxHalfExtents.y (float, 半高)
                           ├─ +0x28: m_boxHalfExtents.z (float, 半厚度)
                           └─ +0x30: m_collisionMargin (float, 边距恒为 0.10)
```

### 2.3 判定尺寸换算公式
Bullet Physics 的 `btBox2dShape` 判定盒：
- 半宽：$\text{half\_w} = \text{m\_boxHalfExtents.x}$
- 半高：$\text{half\_h} = \text{m\_boxHalfExtents.y}$
- 边距：$\text{margin} = \text{m\_collisionMargin}$（恒为 0.10）

真实的判定尺寸为：
$$\text{Width} = (\text{half\_w} + \text{margin}) \times 2$$
$$\text{Height} = (\text{half\_h} + \text{margin}) \times 2$$
$$\text{Threat Radius} = \max(\text{half\_w}, \text{half\_h}) + \text{margin}$$

---

## 3. 实操验证：端到端实测采样真值

### 3.1 角色受击与擦弹数据（灵梦）
在实机进程（基址 `0x240000`）实测采样：
```
=== Team 0: reimu / marisa ===
  [Team 0 Master] actor=0x0badc60c, pos=(1240.0, 360.0), anim=0x0ba5fe50
    Actor2D AABB: [1220.0, 300.0, 1260.0, 410.0] (w=40.0, h=110.0)
    
    Hurtbox (0x78): count=1
      shape vt_rva=0x447654, half_extents=[19.90, 54.90], margin=0.10
      => 精确受击框尺寸: (19.90 + 0.10) * 2 x (54.90 + 0.10) * 2 = 40.00 x 110.00 !
          
    Graze (0x90): count=4
      [0] half=[26.40, 26.90], margin=0.10 => 53.00 x 54.00
      [1] half=[23.40, 36.40], margin=0.10 => 47.00 x 73.00
      [2] half=[24.40, 35.40], margin=0.10 => 49.00 x 71.00
      [3] half=[27.90, 29.40], margin=0.10 => 56.00 x 59.00
```

### 3.2 真实弹幕判定数据（灵梦符札 5B / 6B，motion = 2007）
在对战实战中通过 Bridge DLL 发射并采样，成功捕获到 26 个实战活跃弹幕：
```
=== 输入注入(1P 射击 b1 产生弹幕) ===
  [FOUND SHOTS] 成功捕获 26 个弹幕实体:
    Shot #0: motion=2007, pos=(549.7, 305.7), box=[537.7, 293.7, 561.7, 317.7], size: 24.0 x 24.0
    Shot #1: motion=2007, pos=(552.2, 310.1), box=[540.2, 298.1, 564.2, 322.1], size: 24.0 x 24.0
    Shot #2: motion=2007, pos=(553.0, 315.0), box=[538.0, 300.0, 568.0, 330.0], size: 30.0 x 30.0
    Shot #3: motion=2007, pos=(552.2, 319.9), box=[540.2, 307.9, 564.2, 331.9], size: 24.0 x 24.0
    Shot #4: motion=2007, pos=(549.7, 324.3), box=[537.7, 312.3, 561.7, 336.3], size: 24.0 x 24.0
```
- 扩散符札判定尺寸为精确的 **24.0 x 24.0** 像素；
- 中轴主力符札判定尺寸为精确的 **30.0 x 30.0** 像素；
- 共享内存中的 AABB 完整动态包围：`[x - 12, y - 12, x + 12, y + 12]`；

### 3.3 视觉特效对象对照（motion = 190）
```
  [EFFECT ACTOR] motion=190, pos=(378.2, 475.0), box=[378.2, 475.0, 378.2, 475.0], size: 0.0 x 0.0
```
- 特效对象没有攻击向量，无受击框，无危害，AABB 保持为点是完全正确且符合物理逻辑的。

---

## 4. 前序调查悬疑与假说全景解答

| # | 疑点 / 假设 | 逆向工程事实与排查结论 |
|---|---|---|
| 1 | **`0x5c8cd0` 是判定虚表？** | 实际是 Squirrel VM 内核 `SQNativeClosure` 的类虚表，所有闭包共用，非判定独有。 |
| 2 | **hook `CalcContactTestParam`（`0x98fa0`）？** | `0x98fa0` 是 Sqrat 库生成的 4 参数通用包装桩（Thunk），多函数共用。**hook 它会全游戏崩溃**。现已证实直读内存零 Hook 即可拿到数据。 |
| 3 | **判定计算在脚本字节码分发无单一入口？** | 真正的碰撞计算由 C++ 层的 `Manbow::Actor2D::ContactTest`（`0x4C3290`）调用 Bullet 的 `btCollisionWorld::contactTest` 执行，且形状常驻内存。 |
| 4 | **`enabledebuginfo` 能开判定框绘制？（§4.3）** | `sub_59CB50` 仅是官方 Squirrel 的 `sq_enabledebuginfo`（用于编译脚本保留行号）。Bullet 原生虽有 `debugDrawWorld`（`sub_5B2670`），但引擎未实现绘制器（`m_debugDrawer == null`）。 |
| 5 | **现成判定框 MOD？（§4.4）** | 凭依华（th155）此前社区并无公开的判定框 MOD。本项目通过 C++ Bridge DLL 实现内存直读是首创方案。 |
| 6 | **解析 pak 资源（§4.5）** | `sub_425420` 逆向证实为黄昏 2nd Gen `TFPK` 格式。判定数据不是以明文写在 `.nut` 脚本里，而是打包在每个角色的 `.pat` 二进制动画帧数据包中（每盒 96 字节）。 |
| 7 | **弹幕如何产生？** | A 键（`b0`）为近战轻拳（motion 1100，无弹幕）；B 键（`b1`）为弱射击（产生符札）；C 键（`b2`）为强射击/必杀技。按键必须输入脉冲（按下 0.1s 后松开 0.3s）方能出招。 |

---

## 5. 工程落地实现

### 5.1 C++ Bridge 实现（`src/bridge/th155_rl_bridge.cpp`）
已在 `EmitActor` 中打入 Bullet Physics 指针解包，当 AABB 退化为点时自动提取真实尺寸：
```cpp
/* Bullet Physics 2D: if AABB is a point, extract true hitbox dimensions from anim + 0x84 */
if ((s->box_r - s->box_l) <= 0.001f && anim && IsReadable(anim, 0x90)) {
    u32 first = rd32(anim + 0x84u);
    u32 last  = rd32(anim + 0x88u);
    if (first && last > first && (last - first) <= 0x80u && IsReadable(first, 8)) {
        u32 box_obj = rd32(first);
        if (box_obj && IsReadable(box_obj, 0x10)) {
            u32 col_obj = rd32(box_obj + 0x0cu);
            if (col_obj && IsReadable(col_obj, 0xd0)) {
                u32 shape = rd32(col_obj + 0xccu);
                if (shape && IsReadable(shape, 0x38)) {
                    float hw = rdf(shape + 0x20u);
                    float hh = rdf(shape + 0x24u);
                    float mg = rdf(shape + 0x30u);
                    if (hw > 0.0f && hh > 0.0f && hw < 2000.0f && hh < 2000.0f) {
                        float rw = hw + mg;
                        float rh = hh + mg;
                        s->box_l = s->x - rw;
                        s->box_r = s->x + rw;
                        s->box_t = s->y - rh;
                        s->box_b = s->y + rh;
                    }
                }
            }
        }
    }
}
```

### 5.2 编译与端到端验证状态
1. 编译命令：`d:\programme\155rl\src\bridge\build.bat`，顺利生成 32 位 `th155_rl_bridge.dll`。
2. 自动化回归：`py -3.11 scripts/e2e.py`，所有检查项全部 **PASS**：
   - `state_frame` / `state_time` / `state_life0` / `state_x0` / `state_master0` 全部通过；
   - `inject_move` 与 `inject_attack` 动作注入全部通过；
   - 抓取到 26 个实战弹幕，AABB 尺寸精确显示为 `24.0 x 24.0` 与 `30.0 x 30.0`。
