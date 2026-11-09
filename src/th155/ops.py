"""对战内操作:跳过结算演示、瞬间重置、COM 难度/练习配置写入。

全部为纯内存写入(不依赖窗口焦点,也不需要调用 Squirrel VM)。
"""
from __future__ import annotations

import struct
import time
from typing import Optional

from .sq import RT_MASK, RAW_ARRAY, RAW_INT, RAW_BOOL

# 完整 tag(与游戏自身写入一致);raw tag 只用于比较
TAG_INT = 0x05000002
TAG_BOOL = 0x01000008

STATE_FIGHTING = 8
STATE_ROUND_END = 32


class BattleOps:
    def __init__(self, game):
        self.g = game
        self.p = game.p
        self.r = game.r

    # -- 通用写入 ----------------------------------------------------------
    def _array_elems(self, *path):
        sa = self.g.get_path(*path)
        if sa is None:
            return None, []
        t, v = self.r.obj(sa)
        if (t & RT_MASK) != RAW_ARRAY:
            return None, []
        return v, self.r.array(v)

    def write_int_slot(self, slot: int, value: int) -> None:
        """写 int 槽。

        **必须写完整 tag `0x05000002`**,不能写 raw 形式 `0x2`:
        游戏自己写入时用的是完整 tag(实测 battle.match_num / team.life / win 元素
        的 tag 都是 0x05000002),写 raw tag 会让该对象在后续脚本执行中失效。
        """
        self.p.write(slot, struct.pack("<II", TAG_INT, value & 0xFFFFFFFF))

    def write_bool_slot(self, slot: int, value: bool) -> None:
        self.p.write(slot, struct.pack("<II", TAG_BOOL, 1 if value else 0))

    def set_table_int(self, *path, value: int) -> bool:
        """把 table 的某个 int 字段写入(路径最后一段是字段名)。"""
        sa = self.g.get_path(*path)
        if sa is None:
            return False
        self.write_int_slot(sa, value)
        return True

    def set_table_bool(self, *path, value: bool) -> bool:
        """把 table 的某个 bool 字段写入(路径最后一段是字段名)。"""
        sa = self.g.get_path(*path)
        if sa is None:
            return False
        self.write_bool_slot(sa, value)
        return True

    def set_array_int(self, *path, index: int, value: int) -> bool:
        _, elems = self._array_elems(*path)
        if not elems or index >= len(elems):
            return False
        self.write_int_slot(elems[index], value)
        return True

    # -- 跳过结算/胜负演示 --------------------------------------------------
    def skip_demo(self) -> int:
        """把 battle.endWinDemo / endLoseDemo / endDrawDemo / endTimeDemo 全置 true。

        !! 危险 !! 只在**演示开始之前**设置才有意义。实测:
          * 回合结束后(state 32/64)再写,演示不会被打断,可能永久卡在 32;
          * 与 battle.time=0 同时使用会触发游戏自身的 std::bad_function_call
            崩溃(空 std::function 任务)。
        训练循环请只用 round_reset()/wait_next_round()。
        """
        n = 0
        for name in ("endWinDemo", "endLoseDemo", "endDrawDemo", "endTimeDemo"):
            _, elems = self._array_elems("battle", name)
            for el in elems:
                self.write_bool_slot(el, True)
                n += 1
        return n

    def wait_state(self, states, timeout: float = 20.0, interval: float = 0.05) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            v = self.g.battle_value("state")
            if v in states:
                return True
            time.sleep(interval)
        return False

    def ensure_fighting(self, timeout: float = 30.0) -> bool:
        """确保处于可操作的对战回合(state 8)。"""
        v = self.g.battle_value("state")
        if v == STATE_FIGHTING:
            return True
        if v == STATE_ROUND_END:
            self.skip_demo()
        return self.wait_state((STATE_FIGHTING,), timeout=timeout)

    # -- 瞬间重置 ----------------------------------------------------------
    def practice_reset(self, duration: float = 0.30, settle: float = 0.25) -> None:
        """练习模式的瞬间重置:同时按住 b0~b3(游戏内置快捷键)。

        battle_practice.nut: 四个键同时为 1 -> PracticeRestart()
        清空场上弹幕/特效,双方归位,计时器与计量条复原。
        """
        st = self.g.read_state(with_shots=False)
        dev = self.g.input_native(st.teams[0].inst)
        if not dev:
            raise RuntimeError("找不到 1P 输入设备")
        t0 = time.time()
        while time.time() - t0 < duration:
            self.p.write(dev + 0x0C, struct.pack("<4i", 1, 1, 1, 1))
        self.p.write(dev + 0x0C, struct.pack("<4i", 0, 0, 0, 0))
        time.sleep(settle)

    def round_reset(self, timeout: float = 25.0, force_timeout: bool = True) -> bool:
        """安全地推进到下一个回合(返回 True = 已处于可操作的新回合)。

        只做两件事:把 battle.time 归零触发回合结束,然后**等待游戏自己**
        完成 64/32 -> 128 -> 4 -> 8 的状态迁移(约 8 秒)。
        不要在这里写 endWinDemo/endLoseDemo:实测在 VS 模式下手动跳演示会让
        状态机停在 32,或在回合中途抛出 std::bad_function_call 直接崩溃
        (游戏自身的 dangling coroutine bug,详见 docs/re_notes.md §10)。
        """
        st = self.g.read_state(with_shots=False)
        if st.battle_state == STATE_FIGHTING:
            if not force_timeout:
                return False
            self.set_table_int("battle", "time", value=0)
            # 先等状态机真的离开 8,否则 wait_state(8) 会立刻返回
            t0 = time.time()
            while time.time() - t0 < 5.0:
                if self.g.battle_value("state") != STATE_FIGHTING:
                    break
                time.sleep(0.02)
        return self.wait_state((STATE_FIGHTING,), timeout=timeout, interval=0.05)

    def reset_win(self) -> bool:
        """把 battle.win[0..1] 清零,让比赛永远打不满 2 胜。

        比赛打满 2 胜会进入胜负对话,该场景在自动化下无法推进(实测注入
        input_all / input_talk / 真实按键都无效),只能重启游戏。
        写 `battle.win` 必须用**完整 tag**(见 `write_int_slot`),否则会破坏
        SQObjectPtr 的 tag,后续脚本执行时抛 `Sqrat::Exception` 直接崩游戏。
        """
        sa = self.g.get_path("battle", "win")
        if sa is None:
            return False
        t, arr = self.r.obj(sa)
        if (t & RT_MASK) != RAW_ARRAY or not arr:
            return False
        for slot in self.r.array(arr)[:2]:
            self.write_int_slot(slot, 0)
        return True

    def win_values(self) -> list:
        """读取 battle.win[0..1](每位选手已赢的回合数)。"""
        sa = self.g.get_path("battle", "win")
        if sa is None:
            return []
        t, arr = self.r.obj(sa)
        if (t & RT_MASK) != RAW_ARRAY or not arr:
            return []
        out = []
        for slot in self.r.array(arr)[:2]:
            _, v = self.r.obj(slot)
            out.append(int(v))
        return out

    def match_over(self) -> bool:
        """比赛是否已打满 2 胜(胜负对话在自动化下无法推进,只能重启游戏)。"""
        try:
            return any(v >= 2 for v in self.win_values())
        except Exception:
            return False

    def wait_next_round(self, timeout: float = 25.0) -> bool:
        """等到下一个可操作回合(state=8)。"""
        return self.wait_state((STATE_FIGHTING,), timeout=timeout, interval=0.05)

    # -- 练习模式配置 ------------------------------------------------------
    def set_practice_config(self, player2: Optional[int] = None,
                            difficulty: Optional[int] = None,
                            life: Optional[int] = None,
                            sp: Optional[int] = None,
                            mp: Optional[int] = None,
                            op: Optional[int] = None,
                            guard: Optional[int] = None) -> dict:
        """写 ::config.practice.* (下次 PracticeRestart/进入练习时生效)。"""
        done = {}
        if player2 is not None:
            done["player2"] = self.set_table_int("config", "practice", "player2", value=player2)
        if difficulty is not None:
            done["difficulty"] = self.set_table_int("config", "practice", "difficulty",
                                                    value=difficulty)
        for key, val in (("life", life), ("sp", sp), ("mp", mp), ("op", op), ("guard", guard)):
            if val is None:
                continue
            for i in (0, 1):
                done[f"{key}{i}"] = self.set_array_int("config", "practice", key, index=i, value=val)
        return done

    def set_difficulty(self, level: int) -> bool:
        """对局中实时改 COM 难度(仅练习模式有效)。

        写 ::config.practice.difficulty;Practice_CommonUpdate 检测到与角色
        com_difficulty 不一致时会自动 CPU_SetLevel()。
        VS COM 模式的难度在进入对局前由 title.cursor_difficulty -> 
        ::config.difficulty.vs 决定,对局中请直接用 set_com_difficulty()。
        """
        return bool(self.set_practice_config(difficulty=int(level)).get("difficulty"))

    # CPU_SetLevel() 的等价表(com_function.nut):level, baria_rate, guard_rate
    COM_LEVELS = {
        0: (0, 0, 25),
        1: (33, 25, 33),
        2: (66, 33, 50),
        3: (100, 66, 90),
        4: (100, 90, 95),
    }

    def set_com_difficulty(self, team: int, level: int) -> bool:
        """等价于脚本的 CPU_SetLevel(level):同时写 com_level/baria/guard。

        只写 com_level 而不写 com_baria_rate/com_guard_rate 会让内置 COM 的
        决策状态机进入不一致状态(实测随后抛 std::bad_function_call 崩溃)。

        master 与 slave 都要写:完全凭依换人后,场上的 COM 可能是 slave,
        只改 master 会让难度在换人后"变回去"。
        """
        lvl, baria, guard = self.COM_LEVELS.get(level, self.COM_LEVELS[1])
        st = self.g.read_state(with_shots=False)
        done = 0
        for who in ("master", "slave"):
            pl = self.g.field_inst(st.teams[team].inst, who)
            if not pl:
                continue
            for name, val in (("com_difficulty", level), ("com_level", lvl),
                              ("com_baria_rate", baria), ("com_guard_rate", guard)):
                slot = self.g.field_slot(pl, name)
                if slot:
                    self.write_int_slot(slot, val)
                    done += 1
        return done >= 2

    # -- 弹幕 --------------------------------------------------------------
    def shot_positions(self, limit: int = 512) -> list:
        """返回 [(proc, x, y, vx, vy, dir, motion)]。"""
        out = []
        for team_inst in self.g.team_instances():
            for key in ("master", "slave"):
                pl = self.g.field_inst(team_inst, key)
                if not pl:
                    continue
                for proc in self.g.shot_list(pl)[:limit]:
                    raw = self.p.read(proc, 0x50)
                    if len(raw) < 0x40:
                        continue
                    x, y, vx, vy = struct.unpack_from("<4f", raw, 0x0C)
                    d, = struct.unpack_from("<f", raw, 0x38)
                    anim = struct.unpack_from("<I", raw, 0x3C)[0]
                    mo = self.p.u32(anim + 0x1C) if anim else -1
                    out.append((proc, x, y, vx, vy, d, mo))
        return out
