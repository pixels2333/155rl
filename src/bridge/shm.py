"""th155_rl_bridge 共享内存的 Python 客户端。

与 src/bridge/protocol.h 严格一一对应(packed, 1 字节对齐)。
用法:
    from bridge.shm import BridgeShm
    shm = BridgeShm.open(pid)       # 等待该实例的 DLL 建立映射(pid=None = 任一实例)
    st = shm.read_state()           # 一帧完整状态
    shm.set_action(team=0, x=1, b0=0)   # 写入动作
    shm.request_reset()
    shm.enable_lockstep(); shm.advance(1)   # 帧锁步:放行一帧
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Optional

# 每个游戏实例一份映射/事件, 名字后缀 = 宿主 th155.exe 的 pid(与 protocol.h 同步)。
SHM_PREFIX = "Local\\th155_rl_bridge_v1_"
STATE_EVENT_PREFIX = "Local\\th155_rl_state_"
STEP_EVENT_PREFIX = "Local\\th155_rl_step_"
MAGIC = 0x35354854
# 18:激光与特殊弹幕判定感知(is_beam/beam_w/beam_l/beam_angle)。
# 19:按 pid 命名共享内存 + 帧锁步(ctl_lockstep/ctl_step_seq/ls_*)。
# 结构体大小和偏移改变, 必须与 protocol.h 同步 bump。
VERSION = 19
MAX_SHOTS = 512
OCCULT_MAX = 2000   # player_input.nut: op == 2000 才能发动符卡/LastWord

FILE_MAP_ALL_ACCESS = 0x000F001F
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenFileMappingW.restype = wintypes.HANDLE
k32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
k32.MapViewOfFile.restype = ctypes.c_void_p
k32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, ctypes.c_size_t]
k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
k32.OpenEventW.restype = wintypes.HANDLE
k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
k32.SetEvent.argtypes = [wintypes.HANDLE]
k32.SetEvent.restype = wintypes.BOOL
k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.WaitForSingleObject.restype = wintypes.DWORD
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.CloseHandle.restype = wintypes.BOOL


def shm_name(pid: int) -> str:
    return SHM_PREFIX + str(int(pid))


class ActorState(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("x", ctypes.c_float), ("y", ctypes.c_float),
        ("vx", ctypes.c_float), ("vy", ctypes.c_float), ("dir", ctypes.c_float),
        ("motion", ctypes.c_int), ("key_take", ctypes.c_int), ("key_frame", ctypes.c_int),
        ("hit_stop", ctypes.c_int), ("damage_stop", ctypes.c_int),
        ("hit_state", ctypes.c_int),
        ("guard_state", ctypes.c_int),
        ("dash_count", ctypes.c_int), ("graze_state", ctypes.c_int),
        ("box_l", ctypes.c_float), ("box_t", ctypes.c_float),
        ("box_r", ctypes.c_float), ("box_b", ctypes.c_float),
        ("cb_group", ctypes.c_int),
        # 攻击/受击判定(实例字段,已实测有效;见 protocol.h)
        ("atk_id", ctypes.c_int),
        ("hit_count", ctypes.c_int),
        ("hit_result", ctypes.c_int),
        ("atk_lv", ctypes.c_int), ("atk_cancel", ctypes.c_int),
        ("atk_guard_break", ctypes.c_int),
        ("hit_back_flag", ctypes.c_int), ("is_attack", ctypes.c_int),
        ("hit_id", ctypes.c_int),
        ("endure_count", ctypes.c_int), ("endure_max", ctypes.c_int),
        ("stan_count", ctypes.c_int), ("recover", ctypes.c_int),
        ("center_stop", ctypes.c_int),
        ("gravity", ctypes.c_float), ("slide_speed", ctypes.c_float),
        ("atk_range", ctypes.c_int), ("target_dist", ctypes.c_float),
        # 近战攻击判定框(见 protocol.h)。数组长度 4 必须与 C 侧一致,
        # 少写一项会让后续所有字段错位、结构体尺寸不符 -> 读共享内存直接崩。
        ("atk_box_n", ctypes.c_int),
        ("atk_box_x", ctypes.c_float * 4), ("atk_box_y", ctypes.c_float * 4),
        ("atk_box_hw", ctypes.c_float * 4), ("atk_box_hh", ctypes.c_float * 4),
    ]


def is_attack(motion: int) -> int:
    """与 player_check.nut:82 IsAttack() 同口径。0 = 非攻击,1..6 = 分级。"""
    if 4000 <= motion <= 4999:
        return 5 if motion >= 4900 else 4
    if 3910 <= motion <= 3980:
        return 6
    if 3000 <= motion <= 3909:
        return 3
    if 2000 <= motion <= 2999:
        return 2
    if motion >= 1000:
        return 1
    return 0


class ShotState(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("x", ctypes.c_float), ("y", ctypes.c_float),
        ("vx", ctypes.c_float), ("vy", ctypes.c_float), ("dir", ctypes.c_float),
        ("id", ctypes.c_int), ("motion", ctypes.c_int), ("owner", ctypes.c_int),
        ("box_l", ctypes.c_float), ("box_t", ctypes.c_float),
        ("box_r", ctypes.c_float), ("box_b", ctypes.c_float),
        ("cb_group", ctypes.c_int), ("cb_mask", ctypes.c_int),
        ("src", ctypes.c_int),
        ("key_take", ctypes.c_int), ("key_frame", ctypes.c_int),
        ("is_attack", ctypes.c_int),
        ("atk_lv", ctypes.c_int),
        ("is_beam", ctypes.c_int),
        ("beam_w", ctypes.c_float),
        ("beam_l", ctypes.c_float),
        ("beam_angle", ctypes.c_float),
    ]


class Shared(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("magic", ctypes.c_uint), ("version", ctypes.c_uint),
        ("struct_size", ctypes.c_uint), ("reserved0", ctypes.c_uint),
        ("seq", ctypes.c_uint), ("attached", ctypes.c_uint),
        ("frame", ctypes.c_uint),
        ("battle_state", ctypes.c_int),
        ("time", ctypes.c_int), ("time_unit", ctypes.c_int),
        ("round", ctypes.c_int), ("winner", ctypes.c_int), ("match_num", ctypes.c_int),
        ("is_practice", ctypes.c_int),
        ("is_time_stop", ctypes.c_int),
        ("time_stop_count", ctypes.c_int), ("slow_count", ctypes.c_int),
        ("demo_count", ctypes.c_int), ("corner_right_actor", ctypes.c_int),
        ("enable_time_up", ctypes.c_int), ("enable_contact_test", ctypes.c_int),
        ("enable_time_count", ctypes.c_int),
        ("life", ctypes.c_int * 2), ("life_max", ctypes.c_int * 2), ("regain", ctypes.c_int * 2),
        ("sp", ctypes.c_int * 2), ("sp_max", ctypes.c_int * 2),
        ("mp", ctypes.c_int * 2), ("op", ctypes.c_int * 2),
        ("spell_active", ctypes.c_int * 2), ("spell_use_count", ctypes.c_int * 2),
        ("combo_count", ctypes.c_int * 2), ("slave_ban", ctypes.c_int * 2),
        ("com_difficulty", ctypes.c_int * 2), ("com_level", ctypes.c_int * 2),
        ("current", ctypes.c_int * 2),
        ("tag_ready", ctypes.c_int * 2),
        ("spell_ready", ctypes.c_int * 2),
        ("master_name", ctypes.c_char * 24 * 2), ("slave_name", ctypes.c_char * 24 * 2),
        ("combo_damage", ctypes.c_int * 2),
        ("combo_stun", ctypes.c_int * 2), ("damage_scale", ctypes.c_float * 2),
        ("actors", ActorState * 4),
        ("shot_count", ctypes.c_uint),
        ("shots", ShotState * MAX_SHOTS),
        ("ctl_input_enable", ctypes.c_uint),
        ("ctl_team", ctypes.c_uint),
        ("ctl_x", ctypes.c_int), ("ctl_y", ctypes.c_int),
        ("ctl_k", ctypes.c_int * 6),
        ("ctl_reset", ctypes.c_uint), ("ctl_reset_ack", ctypes.c_uint),
        ("ctl_talk", ctypes.c_uint),
        ("ctl_difficulty", ctypes.c_int), ("ctl_player2", ctypes.c_int),
        ("ctl_difficulty_dirty", ctypes.c_uint),
        ("ctl_ack", ctypes.c_uint), ("ctl_heartbeat", ctypes.c_uint),
        ("ctl_fast_reset", ctypes.c_uint), ("ctl_fast_reset_ack", ctypes.c_uint),
        ("ctl_lockstep", ctypes.c_uint), ("ctl_step_seq", ctypes.c_uint),
        ("ctl_lockstep_timeout_ms", ctypes.c_uint),
        ("ls_seq", ctypes.c_uint), ("ls_frame", ctypes.c_uint),
        ("ls_timeouts", ctypes.c_uint), ("ls_wait_us", ctypes.c_uint),
        ("ls_pid", ctypes.c_uint), ("ls_pub_us", ctypes.c_uint),
        ("dbg_loop_count", ctypes.c_uint),
        ("dbg_loop_ms", ctypes.c_uint), ("dbg_pub_ms", ctypes.c_uint),
        ("dbg_resolve_ms", ctypes.c_uint),
        ("dbg_fn_mask", ctypes.c_uint),
        ("dbg_heap_regions", ctypes.c_uint), ("dbg_heap_vt", ctypes.c_uint),
        ("dbg_heap_emit", ctypes.c_uint),
        ("dbg_mbi", ctypes.c_uint * 7),
    ]


class BridgeShm:
    def __init__(self, handle=None, view=None, pid: Optional[int] = None):
        self._h = handle
        self._view = view
        self.s = Shared.from_address(view) if view else None
        self.pid = pid
        # 锁步事件惰性打开;打不开(旧 DLL/事件尚未建立)时退化为轮询 ls_seq。
        self._ev_state = None
        self._ev_step = None

    # -- 生命周期 ----------------------------------------------------------
    @classmethod
    def _try_open(cls, pid: int) -> Optional["BridgeShm"]:
        """打开并校验 pid 对应的映射。映射不存在 → None;协议不匹配 → 抛错。"""
        h = k32.OpenFileMappingW(FILE_MAP_ALL_ACCESS, False, shm_name(pid))
        if not h:
            return None
        view = k32.MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, ctypes.sizeof(Shared))
        if not view:
            k32.CloseHandle(h)
            return None
        obj = cls(h, view, pid=int(pid))
        if (obj.s.magic == MAGIC and obj.s.version == VERSION
                and obj.s.struct_size == ctypes.sizeof(Shared)):
            return obj
        # 版本一致但结构大小不符 = 布局漂移,必须拒连。
        # 只校验 magic+version 时,删过字段的新旧组合会静默错读。
        magic, version, size = obj.s.magic, obj.s.version, obj.s.struct_size
        obj.close()
        raise RuntimeError(
            "共享内存协议不匹配: pid={} magic={:#x} version={} struct_size={} "
            "(期望 version={} size={})。DLL 与 Python 必须一起重建。"
            .format(pid, magic, version, size, VERSION, ctypes.sizeof(Shared)))

    @classmethod
    def open(cls, pid: Optional[int] = None, timeout: float = 5.0,
             create: bool = False) -> "BridgeShm":
        """打开指定 pid 实例的共享内存。

        pid=None 时兼容单实例调用方:每轮重新枚举 th155.exe,返回第一个
        映射可打开且校验通过的实例。
        """
        deadline = time.time() + timeout
        while True:
            if pid is not None:
                candidates = [int(pid)]
            else:
                from th155.mem import find_pids
                candidates = find_pids("th155.exe")
            for p in candidates:
                obj = cls._try_open(p)
                if obj is not None:
                    return obj
            if time.time() > deadline:
                where = f"pid={pid}" if pid is not None else f"候选 pid={candidates}"
                raise TimeoutError(f"共享内存不存在({where}):桥接 DLL 未注入?")
            time.sleep(0.05)

    def close(self) -> None:
        for attr in ("_ev_state", "_ev_step"):
            h = getattr(self, attr, None)
            if h:
                k32.CloseHandle(h)
                setattr(self, attr, None)
        if self._view:
            k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._h:
            k32.CloseHandle(self._h)
            self._h = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -- 读 ---------------------------------------------------------------
    @property
    def alive(self) -> bool:
        a = self.s.ctl_ack
        time.sleep(0.05)
        return self.s.ctl_ack != a or self.s.seq > 0

    def wait_frame(self, last_frame: int, timeout: float = 0.2) -> int:
        """阻塞等待 battle.count 变化(帧同步)。返回新的帧号。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            f = self.s.frame
            if f != last_frame:
                return f
            time.sleep(0.0002)
        return self.s.frame

    @property
    def frame(self) -> int:
        return int(self.s.frame)

    @property
    def battle_state(self) -> int:
        return int(self.s.battle_state)

    def read_state(self, retries: int = 200) -> dict:
        """读一帧完整快照。

        DLL 侧用 seqlock 发布（发布期间 seq 为奇数）。这里若不校验 seq，
        可能读到"motion=3910 却 is_attack=0"这类**自相矛盾**的撕裂组合：
        发布线程在两次字段写入之间被抢占，读者跨过了边界。实测在
        651590 帧的采样里出现了 119 次，训练数据同样会被污染。
        因此重试到前后 seq 一致且为偶数为止；全部失败时退回最后一次读到的值
        （宁可偶尔撕裂，也不能阻塞采样循环）。
        """
        last = None
        for i in range(max(1, retries)):
            s0 = self.s.seq
            if s0 & 1:
                # 发布中(奇数):发布一次要写 512 条弹幕,窗口可达毫秒级。
                # 纯自旋重试会全部落在同一个窗口里,所以这里主动让出 CPU。
                if i & 7 == 7:
                    time.sleep(0.0002)
                continue
            snap = self._read_state_once()
            if self.s.seq == s0:
                return snap
            last = snap
        return last if last is not None else self._read_state_once()

    def _read_state_once(self) -> dict:
        s = self.s
        n = min(s.shot_count, MAX_SHOTS)
        return {
            # attached=1 表示桥接当前**已解析到战斗对象并正在发布**。
            # 0 = 游戏在标题菜单/换场/DLL 刚挂上,此时 frame 恒为 0。
            # 读者必须据此区分"不在战斗"与"战斗卡死",否则会把
            # 菜单状态误判成画面停住而反复重启游戏(实测死循环)。
            "attached": s.attached,
            "frame": s.frame,
            "battle_state": s.battle_state,
            "time": s.time,
            "time_unit": s.time_unit,
            "round": s.round,
            "winner": s.winner,
            "match_num": s.match_num,
            "is_practice": bool(s.is_practice),
            "is_time_stop": bool(s.is_time_stop),
            "time_stop_count": s.time_stop_count,
            "slow_count": s.slow_count,
            "demo_count": s.demo_count,
            "corner_right_actor": s.corner_right_actor,
            "enable_time_up": bool(s.enable_time_up),
            "enable_contact_test": bool(s.enable_contact_test),
            "enable_time_count": bool(s.enable_time_count),
            "teams": [
                {
                    "index": i,
                    "life": s.life[i], "life_max": s.life_max[i], "regain": s.regain[i],
                    "sp": s.sp[i], "sp_max": s.sp_max[i], "mp": s.mp[i], "op": s.op[i],
                    "spell_active": bool(s.spell_active[i]),
                    "spell_use_count": s.spell_use_count[i],
                    "combo_count": s.combo_count[i], "slave_ban": s.slave_ban[i],
                    # v16 伤害结算:之前只能看到 life(结果),看不到这一套连段
                    # 打了多少(combo_damage)、倍率还剩多少(damage_scale)。
                    "combo_damage": s.combo_damage[i],
                    "combo_stun": s.combo_stun[i],
                    "damage_scale": s.damage_scale[i],
                    "com_difficulty": s.com_difficulty[i], "com_level": s.com_level[i],
                    "occult": s.op[i], "occult_max": OCCULT_MAX,
                    "tag_ready": bool(s.tag_ready[i]),
                    "spell_ready": bool(s.spell_ready[i]),
                    "master_name": bytes(s.master_name[i]).split(b"\0")[0].decode("ascii", "replace"),
                    "slave_name": bytes(s.slave_name[i]).split(b"\0")[0].decode("ascii", "replace"),
                    "current": s.current[i],
                    "master": self._actor(0 + i * 2),
                    "slave": self._actor(1 + i * 2),
                }
                for i in (0, 1)
            ],
            "shots": [
                {
                    "x": s.shots[i].x, "y": s.shots[i].y,
                    "vx": s.shots[i].vx, "vy": s.shots[i].vy,
                    "dir": s.shots[i].dir,
                    "id": s.shots[i].id, "motion": s.shots[i].motion,
                    "owner": s.shots[i].owner,
                    "box_l": s.shots[i].box_l, "box_t": s.shots[i].box_t,
                    "box_r": s.shots[i].box_r, "box_b": s.shots[i].box_b,
                    "cb_group": s.shots[i].cb_group, "cb_mask": s.shots[i].cb_mask,
                    "src": s.shots[i].src,
                    "key_take": s.shots[i].key_take,
                    "key_frame": s.shots[i].key_frame,
                    "is_attack": s.shots[i].is_attack,
                    "atk_lv": s.shots[i].atk_lv,
                    "is_beam": s.shots[i].is_beam,
                    "beam_w": s.shots[i].beam_w,
                    "beam_l": s.shots[i].beam_l,
                    "beam_angle": s.shots[i].beam_angle,
                }
                for i in range(n)
            ],
        }

    def _actor(self, idx: int) -> dict:
        a = self.s.actors[idx]
        return {
            "x": a.x, "y": a.y, "vx": a.vx, "vy": a.vy, "dir": a.dir,
            "motion": a.motion, "key_take": a.key_take, "key_frame": a.key_frame,
            "hit_stop": a.hit_stop, "damage_stop": a.damage_stop,
            
            "hit_state": a.hit_state, "guard_state": a.guard_state,
            "dash_count": a.dash_count, "graze_state": a.graze_state,
            "box_l": a.box_l, "box_t": a.box_t,
            "box_r": a.box_r, "box_b": a.box_b, "cb_group": a.cb_group,
            "atk_id": a.atk_id,
            "hit_count": a.hit_count,
            "hit_result": a.hit_result,
            "atk_lv": a.atk_lv, "atk_cancel": a.atk_cancel,
            "atk_guard_break": a.atk_guard_break,
            "hit_back_flag": a.hit_back_flag, "is_attack": a.is_attack,
            # bit14 = 被弹幕/激光命中。旧注释"死字段"已被实测推翻。
            "hit_id": a.hit_id,
            "endure_count": a.endure_count, "endure_max": a.endure_max,
            "stan_count": a.stan_count, "recover": a.recover,
            "center_stop": a.center_stop,
            "gravity": a.gravity, "slide_speed": a.slide_speed,
            "atk_range": a.atk_range, "target_dist": a.target_dist,
            # 近战攻击判定框(世界坐标中心 + 半宽高)。只在出手帧非空,
            # 一次攻击最多 4 个框;空列表 = 这一帧没有攻击判定。
            "atk_boxes": [
                {"x": a.atk_box_x[i], "y": a.atk_box_y[i],
                 "hw": a.atk_box_hw[i], "hh": a.atk_box_hh[i]}
                for i in range(min(int(getattr(a, "atk_box_n", 0) or 0), 4))
            ],
        }

    # -- 写 ---------------------------------------------------------------
    def set_action(self, team: int = 0, x: int = 0, y: int = 0,
                   b0: int = 0, b1: int = 0, b2: int = 0,
                   b3: int = 0, b4: int = 0, b5: int = 0) -> None:
        s = self.s
        s.ctl_team = team
        s.ctl_x = x
        s.ctl_y = y
        s.ctl_k[0] = b0
        s.ctl_k[1] = b1
        s.ctl_k[2] = b2
        s.ctl_k[3] = b3
        s.ctl_k[4] = b4
        s.ctl_k[5] = b5
        s.ctl_input_enable = 1
        s.ctl_heartbeat += 1

    def clear_action(self) -> None:
        self.s.ctl_input_enable = 0
        self.s.ctl_x = 0
        self.s.ctl_y = 0
        for i in range(6):
            self.s.ctl_k[i] = 0

    def request_fast_reset(self, wait: bool = True, timeout: float = 1.0) -> bool:
        """请求 1 帧回合重置(DLL 把 battleUpdate 槽临时换成 RoundReset)。"""
        s = self.s
        ack = s.ctl_fast_reset_ack
        s.ctl_fast_reset = 1
        if not wait:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            if s.ctl_fast_reset_ack != ack:
                return True
            time.sleep(0.002)
        return False

    def request_reset(self, wait: bool = True, timeout: float = 1.0) -> bool:
        s = self.s
        ack = s.ctl_reset_ack
        s.ctl_reset = 1
        if not wait:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            if s.ctl_reset_ack != ack:
                return True
            time.sleep(0.002)
        return False

    def set_difficulty(self, level: int, player2: int = 3) -> None:
        s = self.s
        s.ctl_difficulty = level
        s.ctl_player2 = player2
        s.ctl_difficulty_dirty = 1

    def set_talk(self, enable: bool) -> None:
        self.s.ctl_talk = 1 if enable else 0

    # -- 帧锁步 -----------------------------------------------------------
    def _event(self, which: str):
        """惰性打开锁步事件;失败返回 None(调用方退化为轮询)。"""
        attr = "_ev_state" if which == "state" else "_ev_step"
        h = getattr(self, attr)
        if h or self.pid is None:
            return h
        prefix = STATE_EVENT_PREFIX if which == "state" else STEP_EVENT_PREFIX
        h = k32.OpenEventW(EVENT_MODIFY_STATE | SYNCHRONIZE, False, prefix + str(self.pid))
        if h:
            setattr(self, attr, h)
        return h or None

    def _signal_step(self) -> None:
        h = self._event("step")
        if h:
            k32.SetEvent(h)

    @property
    def lockstep(self) -> bool:
        return bool(self.s is not None and self.s.ctl_lockstep)

    def enable_lockstep(self, timeout_ms: int = 100) -> None:
        """打开帧锁步。先把 ctl_step_seq 对齐到当前 ls_seq,避免一打开就卡住
        已发布的那一帧;timeout_ms 到期 DLL 自行放行(0 = 无限等待)。"""
        s = self.s
        s.ctl_lockstep_timeout_ms = max(0, int(timeout_ms))
        s.ctl_step_seq = s.ls_seq
        s.ctl_lockstep = 1

    def disable_lockstep(self) -> None:
        """关闭锁步并置位 step 事件,立即解除 DLL 可能正在进行的等待。"""
        if self.s is None:
            return
        self.s.ctl_lockstep = 0
        self._signal_step()

    def advance(self, frames: int = 1, timeout: float = 1.0) -> bool:
        """放行 frames 个锁步帧, 每帧等到 DLL 发布下一帧边界(ls_seq 变化)。

        动作必须在调用前写好:DLL 放行后立即把 ctl_x/ctl_y/ctl_k 注入同一帧。
        任一帧在 timeout 内没有新边界 → 返回 False。
        """
        s = self.s
        for _ in range(max(1, int(frames))):
            seq = s.ls_seq
            s.ctl_step_seq = seq
            self._signal_step()
            ev = self._event("state")
            deadline = time.time() + timeout
            while s.ls_seq == seq:
                left = deadline - time.time()
                if left <= 0:
                    return False
                if ev:
                    k32.WaitForSingleObject(ev, max(1, min(5, int(left * 1000))))
                else:
                    time.sleep(0.0002)
        return True


if __name__ == "__main__":
    import sys
    _pid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    with BridgeShm.open(_pid, timeout=1.0) as shm:
        print(f"pid={shm.pid} ls_pid={shm.s.ls_pid}")
        for _ in range(5):
            st = shm.read_state()
            print(f"frame={st['frame']} state={st['battle_state']} time={st['time']} "
                  f"t0={st['teams'][0]['master_name']} hp={st['teams'][0]['life']} "
                  f"x={st['teams'][0]['master']['x']:.1f} shots={len(st['shots'])}")
            time.sleep(0.3)
