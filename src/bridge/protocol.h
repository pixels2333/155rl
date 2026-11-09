// th155_rl_bridge 共享内存协议(Python 侧镜像见 src/bridge/shm.py)
// 命名映射: "Local\\th155_rl_bridge_v1_<pid>"(每个游戏实例一份,pid = 宿主 th155.exe)
// 锁步事件: "Local\\th155_rl_state_<pid>"(DLL 发布一帧后置位) /
//           "Local\\th155_rl_step_<pid>"(Python 放行一帧后置位),均为自动复位事件。
//
// 注意:本文件被 /NODEFAULTLIB + 无 SDK 头文件的方式编译,
//       因此不使用 <stdint.h> 的 uint32_t/int32_t,直接用原生类型。
#ifndef TH155_BRIDGE_PROTOCOL_H
#define TH155_BRIDGE_PROTOCOL_H

#define TH155_SHM_PREFIX   L"Local\\th155_rl_bridge_v1_"
#define TH155_STATE_PREFIX L"Local\\th155_rl_state_"
#define TH155_STEP_PREFIX  L"Local\\th155_rl_step_"
#define TH155_SHM_MAGIC  0x35354854u   /* 'TH55' little-endian */
/* 版本史:
   14 = 攻击归因(is_attack) + actor/shot 扩容
   15 = 硬直/霸体/受身(endure/stan/recover/center_stop/target_dist + 3 角色属性)
   16 = 队伍伤害结算(combo_damage/damage_scale/combo_stun)
   17 = 删除 invin/baria/ground/hit_id/attack_state/damage_life。
   18 = 激光与特殊弹幕判定感知(is_beam/beam_w/beam_l/beam_angle + 旋转 AABB 修正)。
   19 = 按 pid 命名共享内存 + 帧锁步(ctl_lockstep/ctl_step_seq/ls_*)。
       增删字段会改变结构体大小与偏移, 必须 bump 版本。 */
#define TH155_SHM_VERSION 19u
#define TH155_MAX_SHOTS  512

/* IsAttack 口径:与 decomp/.../actor/script/player_check.nut:82 完全一致。
   掉血帧实测攻击方 motion = 0/1/2/1000/1212/1500/1600/1720/2000/3031,
   所以 "motion >= 3000 才算攻击" 是错的(会漏掉 1000..2999 全部近身招)。
   返回 0 = 非攻击;1..6 = IsAttack() 的对应分级。 */
static int th155_is_attack(int motion) {
    if (motion >= 4000 && motion <= 4999) return (motion >= 4900) ? 5 : 4;
    if (motion >= 3910 && motion <= 3980) return 6;
    if (motion >= 3000 && motion <= 3909) return 3;
    if (motion >= 2000 && motion <= 2999) return 2;
    if (motion >= 1000) return 1;
    return 0;
}

#pragma pack(push, 1)

typedef struct Th155ActorState {
    float        x, y, vx, vy, dir;
    int          motion, key_take, key_frame;
    /* 注意:没有 invin / baria。
       `player.invin` 实测恒为 0(dash 无敌是 dashCount,不是它);
       `player.baria` 实测恒为 False。二者都是死字段,留着只会让下游误判。 */
    /* 注意:没有 ground —— 30s 连续采样恒为 0,是死字段。 */
    int          hit_stop, damage_stop, hit_state;
    int          guard_state;            /* != 0 = 正在防御(CalcContactTestGuard) */
    int          dash_count;             /* > 0 = 冲刺中(实测期间伤害为常规的 1/68) */
    int          graze_state;            /* 2 = 擦弹判定激活 */
    /* 受击框(AABB):播放器/角色 = 40x110,即 (x±20, y-60..y+50)。
       实测这就是碰撞判定用的矩形(`hitLeft/Top/Right/Bottom` 恒 0,是死字段)。 */
    float        box_l, box_t, box_r, box_b;
    int          cb_group;               /* 玩家的 callbackGroup: 1/2 = team+1 */
    /* ---- 攻击/受击判定(全部为实例字段,已实测有效) ----
       atk_id      : 本次攻击的唯一 id,同一招内不变,用于去重/判定"新的一次攻击"
       hit_count   : 自己已命中的次数(递增即说明刚才这一下打中了)
       hit_result  : 上一次命中结算结果
       hit_back_flag: 被打背/反击标志(43 次掉血里 91% 同帧变化)
       is_attack   : th155_is_attack(motion) != 0,与原生 IsAttack() 同口径
       注意:没有 attack_state —— 实测 25s 内零变化,是死字段。 */
    /* hit_id:**输出侧命中登记 —— "这一帧我打中了"**。
       双向提升度检验(8363 帧)给出:
         对手掩码 -> 我方掉血  bit26 14.1x / bit14 10.8x / bit23 8.6x / 整掩码 6.1x
         我方掩码 -> 对手掉血  bit14 4.2x / 整掩码 2.7x
       两个方向都显著,确认是"谁打中人谁亮",不是受击侧。
       掉血相对掩码有延迟,不能用"掉血当帧掩码为 0"否定本结论。
       bit14 是主命中源,观测里单独取这一位。 */
    int          atk_id, hit_count, hit_result;
    /* ---- 帧级攻击数据 temp_atk_data(v17) ----
       这是此前判定为"不可达"的帧级攻击数据的**真实载体**。
       它是玩家实例上的一个 SQInstance 子对象(字段:
       cancelCount / direction / disableGuard / atk_id / attackLV),
       实测存在率 75%(4263/5684 帧),attackLV 有 5 种取值(0/10/20/30/50),
       cancelCount 有 5 种(含 99 = 不可取消),direction 有 2 种。
       之前只探过 actor 上的同名 null 字段与 native 偏移,漏了这一层。
       atk_lv = attackLV;atk_cancel = cancelCount(99 = 不可取消);
       atk_guard_break = disableGuard(1 = 该攻击无视防御)。 */
    int          atk_lv, atk_cancel, atk_guard_break;
    int          hit_back_flag, is_attack;
    int          hit_id;                 /* 输出侧命中登记,bit14 = 主命中源 */
    /* ---- 硬直/破防/受身(2026-10 补全) ----
       这四项是**唯一**能解释"我现在能不能动"的字段:
         hit_stop / damage_stop 只给出"还剩几帧不能动",但看不到"总硬直有多长";
         endure_count / endure_max 决定这一下会不会打破霸体(高难度 COM 的核心机制);
         stan_count > 0 = 处于气绝(被连段的最危险状态);
         recover > 0 = 正在受身/起身,此时攻击无效。
       实测取值样例:t0.master stan_count=-840, hit_count=0~72,
       endure_count=120 / endure_max=200。 */
    int          endure_count, endure_max;   /* 霸体: 余量 / 上限 */
    int          stan_count;                 /* > 0 = 气绝中 */
    int          recover;                    /* > 0 = 受身/起身中 */
    int          center_stop;                /* 被推到版边/角色重叠时的制动 */
    float        gravity;                    /* 当前重力(baseGravity, 0.7/0.85) */
    float        slide_speed;                /* 当前滑行初速(17.0/19.5) */
    int          atk_range;                  /* 该角色普攻射程(70 / 100) */
    float        target_dist;                /* 与锁定目标的水平距离(带符号) */
    /* ---- 近战攻击判定框(2026-10 补) ----
       这是"我这一下会打到哪"的唯一直接数据,之前只采了受击框,
       所以视图里只有别人能打到我,看不到我能打到别人。
       来源与弹幕同一条链:anim+0x84 向量 -> ContactBoxItem ->
       btCollisionObject(+0x40/+0x44 = 世界坐标) -> btBox2dShape(+0x20/0x24 半宽高)。
       注意:**不是** +0x0C/+0x10 —— +0x0C..+0x38 是 btTransform 的 3x3 基矩阵
       (单位阵,0/1 交替),按坐标读会得到 (0,-1) 这种假值。m_origin 在 +0x40。
       实测向量里**每两个框一个有效**(第二个恒为 (0,0),是占位),
       一次攻击最多 4 个有效框(motion 1210/3040 都出现过)。
       坐标系:x/y 是框中心的世界坐标,半径由 btBox2dShape 给出(局部轴对齐,
       角色朝向已体现在 x 上,不再单独翻转)。
       覆盖率:玩家采样里只有 12.6% 的帧有非空攻击框(其余是待机/受击/移动),
       这是正常的 —— 攻击框只在出手帧存在。 */
    int          atk_box_n;                          /* 有效攻击框个数 0..4 */
    float        atk_box_x[4], atk_box_y[4];         /* 世界坐标中心 */
    float        atk_box_hw[4], atk_box_hh[4];       /* 半宽 / 半高 */
} Th155ActorState;

typedef struct Th155ShotState {
    float        x, y, vx, vy, dir;
    /* 注意:没有 rx/ry。旧版本把 Actor2D+0x40/+0x44 当旋转量读,实测
       +0x40 恒等于 anim-0x10(结构内依赖指针)、+0x44 恒为 1(计数),
       按 float 读出来是 5.4e-32 / 1.4e-45 这种非规格化垃圾值。 */
    int          id, motion, owner;      /* owner: 0/1 = team, -1 = 未知/中立 */
    /* 判定框(AABB)。玩家/角色是真实矩形;弹幕退化成点(w=h=0),
       此时用 (x,y) 做点-矩形距离即可。 */
    float        box_l, box_t, box_r, box_b;
    int          cb_group;               /* callbackGroup: 16=team0 弹幕, 32=team1 弹幕 */
    int          cb_mask;
    int          src;                    /* 采集来源: 0=group_player 1=group_effect 2=堆扫描 */
    /* 弹幕自身的动画状态与攻击口径。
       is_attack 只是 player_check.nut:82 的 motion 区间分级(>=1000 即 1),
       它**不能**区分"会伤人的动作"与 6030/6050 这类演出特效 ——
       真正的 flagAttack 在帧数据里,不在 Actor2D 上暴露。 */
    int          key_take, key_frame;
    int          is_attack;              /* th155_is_attack(motion) */
    /* ---- 弹幕的帧级攻击数据(v17b) ----
       弹幕实例上也有 temp_atk_data 子对象(存在率 50%,6/12 条)。
       实测 attackLV=20、atk_id=8388608、cancelCount=1、disableGuard=False。
       这是区分"会伤人的攻击弹幕"与"6030 演出特效"的**直接依据** ——
       此前只能靠 motion 区间猜(is_attack 对两者都返回非 0)。
       atk_lv = 0 ⇒ 该对象不是攻击源。
       注:不采弹幕的攻击 id。曾以为它可与攻击方 actor.atk_id 匹配
       ("哪一发打中的"),实测**不成立**: 用 shm 里权威的弹幕实体比对,
       攻击方 atk_id 只出现在弹幕 atk_id 集合中的 17.2%(60/349),
       二者不是同一个 id 空间。此前测出的 73.5% 是扫描对象偏移时
       误读到了攻击方自身的 id,已被推翻。 */
    int          atk_lv;
    /* ---- 激光与特殊判定几何(v18) ----
       is_beam    : 1 = 持续光束/激光(魔理沙 4009/2018 等或长宽比>=3 的射线), 0 = 常规点弹幕
       beam_w     : 光束判定宽度 (2 * 半宽)
       beam_l     : 光束有效长度 (2 * 半长)
       beam_angle : 射线发射角度 (rad) */
    int          is_beam;
    float        beam_w, beam_l, beam_angle;
} Th155ShotState;

typedef struct Th155Shared {
    /* ---- 头 ---- */
    unsigned int magic;
    unsigned int version;
    unsigned int struct_size;            /* sizeof(Th155Shared) 的低 32 位。
                                            版本号相同但字段增删过时,偏移会不一致;
                                            Python 侧据此拒连,避免静默错读。 */
    unsigned int reserved0;
    unsigned int seq;                    /* DLL 每发布一帧 +1 */
    unsigned int attached;               /* 1 = 对象图已解析 */
    unsigned int frame;                  /* battle.count */
    int          battle_state;
    int          time, time_unit;
    int          round, winner, match_num;
    int          is_practice;            /* 1 = 练习模式(存在 PracticeRestart) */
    /* ---- 时间控制(v17) ----
       这是战斗节奏的核心,此前完全没采。实测:
         is_time_stop    {False, True}          时停中(命中/符卡演出期间的停顿)
         time_stop_count 0..60+(50 种取值)      时停剩余帧
         slow_count      0..240+(195 种取值)    慢动作剩余帧
       三者决定"游戏时钟是否在走",而所有 motion/key_frame 都挂在它上面 ——
       没有它们,模型无法区分"对手在动"与"画面停住"。 */
    int          is_time_stop;
    int          time_stop_count;
    int          slow_count;
    /* ---- 对局阶段与演示(v17) ----
       全量审计 battle 表 115 个键后补入。实测:
         demoCount              0..150+(150 种) 演出进度计数
         corner_right_actor     {-1, 0..}        右侧版边锁定的角色 id
         enableTimeUp           {False,True}     计时器是否可耗尽(时间到判定)
         enable_contact_test    {False,True}     碰撞判定是否启用(演出期关闭)
         enableTimeCount        {False,True}     计时器是否在走
       最后两项尤其关键:它们为 False 时,掉血与掉血计时都**不可信**。 */
    int          demo_count;
    int          corner_right_actor;
    int          enable_time_up;
    int          enable_contact_test;
    int          enable_time_count;

    /* ---- 队伍 ---- */
    int          life[2], life_max[2], regain[2];
    int          sp[2], sp_max[2], mp[2], op[2];
    int          spell_active[2], spell_use_count[2], combo_count[2], slave_ban[2];
    int          com_difficulty[2], com_level[2];
    int          current[2];             /* 当前前台角色: 0 = master, 1 = slave */
    int          tag_ready[2];           /* 1 = 换人可用(slave 存活且 slave_ban == 0) */
    int          spell_ready[2];         /* 1 = 符卡可发动(spell_use_count > 0 且未激活) */
    char         master_name[2][24], slave_name[2][24];
    /* ---- 伤害结算 / 连段(v16) ----
       之前只有 life(结果),看不到"这一套连段打了多少、倍率多少、还剩几帧硬直"。
       实测 20s: combo_damage 0..2021(32 种取值)、damage_scale 0.46..1.0(23 种)、
       combo_stun 0..100(15 种,30% 帧非零)。
       注:这里**不采** damage_life —— 实测 1596 帧中 53.8% 与 life 相同、
       63.8% 等于 life_max,是"上次结算后的残余血量",与 life 冗余且语义易误解。
       combo_damage 是**当前连段的累计伤害**,连段结束才清零 —— 这是判断
       "该继续压制还是收手"的唯一依据。 */
    int          combo_damage[2];        /* 当前连段累计伤害 */
    int          combo_stun[2];          /* 连段内的气绝累积 */
    float        damage_scale[2];        /* 伤害倍率(连段递减 1.0 -> 0.5) */

    /* ---- 角色 ---- */
    Th155ActorState actors[4];           /* [0]=t0.master [1]=t0.slave [2]=t1.master [3]=t1.slave */

    /* ---- 弹幕 ---- */
    unsigned int shot_count;
    Th155ShotState shots[TH155_MAX_SHOTS];

    /* ---- 控制块(Python -> DLL) ---- */
    volatile unsigned int ctl_input_enable;   /* 1 = 每帧写入设备 */
    volatile unsigned int ctl_team;           /* 目标队伍 0/1 */
    volatile int          ctl_x, ctl_y;
    volatile int          ctl_k[6];           /* b0..b5 */
    volatile unsigned int ctl_reset;          /* 写 1 = 请求瞬间重置 */
    volatile unsigned int ctl_reset_ack;      /* DLL 每完成一次重置 +1 */
    volatile unsigned int ctl_talk;           /* 1 = 每帧给 ::input_talk 注入 b0 */
    volatile int          ctl_difficulty;     /* -1 = 不改; 0..4 = COM1..COM5 */
    volatile int          ctl_player2;        /* -1 = 不改; 3 = 内置 COM */
    volatile unsigned int ctl_difficulty_dirty;
    volatile unsigned int ctl_ack;            /* DLL 每帧 +1, 证明线程活着 */
    volatile unsigned int ctl_heartbeat;      /* Python 每帧 +1 */
    volatile unsigned int ctl_fast_reset;     /* 写 1 = 请求 1 帧回合重置 */
    volatile unsigned int ctl_fast_reset_ack; /* DLL 每完成一次重置 +1 */
    /* ---- 帧锁步(v19) ----
       ctl_lockstep=1 时,DLL 在每帧输入更新(上一帧逻辑已跑完)处同步发布状态,
       ls_seq+1 并置位 state 事件,然后阻塞游戏主线程,直到 Python 把
       ctl_step_seq 写到 >= ls_seq(放行这一帧)或关闭锁步。
       只在 battle_state == 8 时阻塞;演出/菜单期间照常发布但不等待。
       ctl_lockstep_timeout_ms = 0 表示无限等待(Python 退出前必须关锁步)。 */
    volatile unsigned int ctl_lockstep;
    volatile unsigned int ctl_step_seq;
    volatile unsigned int ctl_lockstep_timeout_ms;
    volatile unsigned int ls_seq;             /* DLL 每个锁步帧边界 +1 */
    volatile unsigned int ls_frame;           /* 该边界处的引擎帧计数(RVA 0x4DACE0) */
    volatile unsigned int ls_timeouts;        /* 等待放行超时次数 */
    volatile unsigned int ls_wait_us;         /* 上一帧等待放行的耗时(微秒) */
    volatile unsigned int ls_pid;             /* 宿主进程 pid(自检) */
    volatile unsigned int ls_pub_us;          /* 上一次锁步帧边界同步发布耗时(微秒) */

    /* ---- 诊断(只读) ---- */
    volatile unsigned int dbg_loop_count;     /* 发布线程循环次数 */
    volatile unsigned int dbg_loop_ms;        /* 上一轮循环耗时 */
    volatile unsigned int dbg_pub_ms;         /* 上一次发布耗时 */
    volatile unsigned int dbg_resolve_ms;     /* 上一次对象图解析耗时 */
    volatile unsigned int dbg_fn_mask;        /* 已解析的 kernel32 导出位图 */
    volatile unsigned int dbg_heap_regions;   /* 上一轮堆扫描访问的区域数 */
    volatile unsigned int dbg_heap_vt;        /* 上一轮 vtable 命中数 */
    volatile unsigned int dbg_heap_emit;      /* 上一轮堆扫描产出条数 */
    volatile unsigned int dbg_mbi[7];         /* 首个 MBI 原始字 [0]base [1]allocbase [2]size [3]state [4]prot [5]type [6]seen */
} Th155Shared;

#pragma pack(pop)

#endif /* TH155_BRIDGE_PROTOCOL_H */
