"""th155 高层状态读取 / 输入写入（供验证脚本、Gym 环境、bridge 客户端共用）。"""
from __future__ import annotations

import hashlib
import os
import struct
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import yaml

from .mem import Process, find_game_dir, find_pid
from .sq import (SQReader, RT_MASK, RAW_TABLE, RAW_ARRAY, RAW_INSTANCE)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "offsets.yaml")


def load_config(path: str = CONFIG_PATH) -> dict:
    with open(os.path.abspath(path), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class VersionMismatch(RuntimeError):
    pass


@dataclass
class ActorState:
    inst: int
    native: int
    x: float
    y: float
    vx: float
    vy: float
    direction: float
    motion: int
    key_take: int
    key_frame: int


@dataclass
class TeamState:
    inst: int
    index: int
    master_name: str
    slave_name: str
    life: int
    life_max: int
    regain_life: int
    damage_life: int
    sp: int
    sp_max: int
    mp: int
    op: int
    spell_active: bool
    spell_use_count: int
    combo_count: int
    combo_damage: int
    slave_ban: int
    master: Optional[ActorState] = None
    slave: Optional[ActorState] = None
    com_difficulty: int = 0
    com_level: int = 0


@dataclass
class GameState:
    frame: int
    battle_state: int
    time: int
    time_unit: int
    enable_time_count: bool
    round: int
    winner: int
    teams: list = field(default_factory=list)
    shots: list = field(default_factory=list)


class Th155Game:
    """只读/可写内存访问 + 脚本对象图导航。"""

    def __init__(self, writable: bool = False, verify_version: bool = True,
                 config: Optional[dict] = None, pid: Optional[int] = None):
        self.cfg = config or load_config()
        # 多实例: pid 指定绑定哪个游戏进程;None = 第一个 th155.exe(单实例兼容)。
        self.pid = int(pid) if pid is not None else find_pid("th155.exe")
        self.p = Process(self.pid, writable=writable)
        self.r = SQReader(self.p)
        self.base, self.image_size = self.p.module_info("th155.exe")
        self.vm = self.p.u32(self.base + self.cfg["engine"]["vm_global_rva"])
        if not self.vm:
            raise RuntimeError("Squirrel VM 指针为空（游戏可能尚未初始化）")
        if verify_version:
            self.verify_version()
        self._battle = None

    # -- 版本 --------------------------------------------------------------
    def verify_version(self) -> None:
        meta = self.cfg["meta"]
        exe = os.path.join(find_game_dir(), "th155.exe")
        size = os.path.getsize(exe)
        if size != meta["exe_size"]:
            raise VersionMismatch(f"th155.exe 大小不匹配: {size} != {meta['exe_size']}")
        h = hashlib.sha256()
        with open(exe, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != meta["exe_sha256"]:
            raise VersionMismatch("th155.exe SHA256 不匹配（版本不对，拒绝继续）")

    # -- 脚本对象图 --------------------------------------------------------
    @property
    def root(self) -> int:
        return self.r.obj(self.vm + self.cfg["engine"]["vm_roottable_offset"])[1]

    def get_path(self, *names: str) -> Optional[int]:
        """按名字逐级解析，返回值的 SQObject 地址。"""
        cur = self.vm + self.cfg["engine"]["vm_roottable_offset"]
        for n in names:
            t, v = self.r.obj(cur)
            raw = t & RT_MASK
            if raw == RAW_TABLE:
                cur = self.r.table_get(v, n)
            elif raw == RAW_INSTANCE:
                cur = self.r.instance_slot(v, n)
            else:
                return None
            if cur is None:
                return None
        return cur

    def get_table(self, *names: str) -> Optional[int]:
        sa = self.get_path(*names)
        if sa is None:
            return None
        t, v = self.r.obj(sa)
        return v if (t & RT_MASK) == RAW_TABLE else None

    def get_instance(self, *names: str) -> Optional[int]:
        sa = self.get_path(*names)
        if sa is None:
            return None
        t, v = self.r.obj(sa)
        return v if (t & RT_MASK) == RAW_INSTANCE else None

    def field(self, inst: int, name: str):
        sa = self.r.instance_slot(inst, name)
        return self.r.value(sa) if sa is not None else None

    def field_slot(self, inst: int, name: str) -> Optional[int]:
        return self.r.instance_slot(inst, name)

    def field_inst(self, inst: int, name: str) -> Optional[int]:
        """读取实例字段并返回其 SQInstance 指针（非实例返回 None）。"""
        sa = self.r.instance_slot(inst, name)
        if sa is None:
            return None
        t, v = self.r.obj(sa)
        return v if (t & RT_MASK) == RAW_INSTANCE else None

    def set_int(self, inst: int, name: str, value: int) -> None:
        sa = self.r.instance_slot(inst, name)
        if sa is None:
            raise KeyError(name)
        self.p.write_u32(sa, 0x05000002)
        self.p.write_i32(sa + 4, value)

    def set_bool(self, inst: int, name: str, value: bool) -> None:
        sa = self.r.instance_slot(inst, name)
        if sa is None:
            raise KeyError(name)
        self.p.write_u32(sa, 0x01000008)
        self.p.write_u32(sa + 4, 1 if value else 0)

    def set_null(self, inst: int, name: str) -> None:
        sa = self.r.instance_slot(inst, name)
        if sa is None:
            raise KeyError(name)
        self.p.write_u32(sa, 0x01000001)
        self.p.write_u32(sa + 4, 0)

    # -- battle ------------------------------------------------------------
    @property
    def battle(self) -> Optional[int]:
        if self._battle is None:
            self._battle = self.get_table(self.cfg["script"]["battle_table"])
        return self._battle

    def battle_value(self, name: str):
        if not self.battle:
            return None
        sa = self.r.table_get(self.battle, name)
        return self.r.value(sa) if sa else None

    @property
    def frame(self) -> int:
        return int(self.battle_value("count") or 0)

    def team_instances(self) -> list[int]:
        sa = self.r.table_get(self.battle, "team") if self.battle else None
        if not sa:
            return []
        t, arr = self.r.obj(sa)
        if (t & RT_MASK) != RAW_ARRAY:
            return []
        out = []
        for el in self.r.array(arr):
            t2, v2 = self.r.obj(el)
            if (t2 & RT_MASK) == RAW_INSTANCE:
                out.append(v2)
        return out

    # -- 原生结构 ----------------------------------------------------------
    def native(self, inst: int) -> int:
        return self.p.u32(inst + 32)

    def actor_state(self, inst: int) -> ActorState:
        up = self.native(inst)
        anim = self.p.u32(up + 0x3C)
        return ActorState(
            inst=inst, native=up,
            x=self.p.f32(up + 0x0C), y=self.p.f32(up + 0x10),
            vx=self.p.f32(up + 0x2C), vy=self.p.f32(up + 0x30),
            direction=self.p.f32(up + 0x38),
            motion=self.p.i32(anim + 0x1C) if anim else 0,
            key_take=self.p.i32(anim + 0x20) if anim else 0,
            key_frame=self.p.i32(anim + 0x24) if anim else 0,
        )

    def team_state(self, inst: int) -> TeamState:
        g = lambda n: self.field(inst, n)
        master_inst = self.field_inst(inst, "master")
        slave_inst = self.field_inst(inst, "slave")
        st = TeamState(
            inst=inst,
            index=int(g("index") or 0),
            master_name=str(g("master_name") or ""),
            slave_name=str(g("slave_name") or ""),
            life=int(g("life") or 0),
            life_max=int(g("life_max") or 0),
            regain_life=int(g("regain_life") or 0),
            damage_life=int(g("damage_life") or 0),
            sp=int(g("sp") or 0),
            sp_max=int(g("sp_max") or 0),
            mp=int(g("mp") or 0),
            op=int(g("op") or 0),
            spell_active=bool(g("spell_active")),
            spell_use_count=int(g("spell_use_count") or 0),
            combo_count=int(g("combo_count") or 0),
            combo_damage=int(g("combo_damage") or 0),
            slave_ban=int(g("slave_ban") or 0),
        )
        if master_inst:
            st.master = self.actor_state(master_inst)
            st.com_difficulty = int(self.field(master_inst, "com_difficulty") or 0)
            st.com_level = int(self.field(master_inst, "com_level") or 0)
        if slave_inst:
            st.slave = self.actor_state(slave_inst)
        return st

    # -- 弹幕 --------------------------------------------------------------
    def shot_list(self, player_inst: int) -> list[int]:
        """遍历 player.shot_actor 的侵入式双向链表，返回 Proc 裸指针。"""
        sa = self.r.instance_slot(player_inst, "shot_actor")
        if not sa:
            return []
        t, inst = self.r.obj(sa)
        if (t & RT_MASK) != RAW_INSTANCE:
            return []
        up = self.native(inst)
        sentinel = self.p.u32(up)
        out = []
        node = self.p.u32(sentinel)
        guard = 0
        while node != sentinel and guard < 512:
            proc = self.p.u32(node + 0x08)
            if proc:
                out.append(proc)
            node = self.p.u32(node + 0x00)
            guard += 1
        return out

    # -- 输入 --------------------------------------------------------------
    def input_native(self, team_inst: int) -> int:
        inp = self.field_inst(team_inst, "input")
        return self.native(inp) if inp else 0

    def write_input(self, dev_native: int, x: int = 0, y: int = 0,
                    b0: int = 0, b1: int = 0, b2: int = 0,
                    b3: int = 0, b4: int = 0, b5: int = 0) -> None:
        """直接写 Manbow::InputMulti 的按键状态（无需窗口焦点）。"""
        buf = struct.pack("<8i", x, y, b0, b1, b2, b3, b4, b5)
        self.p.write(dev_native + 0x04, buf)

    # -- 状态快照 ----------------------------------------------------------
    def read_state(self, with_shots: bool = True) -> GameState:
        teams = [self.team_state(t) for t in self.team_instances()]
        st = GameState(
            frame=self.frame,
            battle_state=int(self.battle_value("state") or 0),
            time=int(self.battle_value("time") or 0),
            time_unit=int(self.battle_value("time_unit") or 90),
            enable_time_count=bool(self.battle_value("enableTimeCount")),
            round=int(self.battle_value("round") or 0),
            winner=int(self.battle_value("winner") or 0),
            teams=teams,
        )
        if with_shots:
            shots = []
            for team_inst in self.team_instances():
                for key in ("master", "slave"):
                    pl = self.field_inst(team_inst, key)
                    if pl:
                        shots.extend(self.shot_list(pl))
            st.shots = shots
        return st


def state_to_dict(st: GameState) -> dict:
    return asdict(st)
