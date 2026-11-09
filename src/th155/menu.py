"""纯内存驱动的菜单导航：无需窗口焦点即可进入/退出对战。

原理（见 configs/offsets.yaml）：
  * 菜单光标 (`menu.title.cursor` 等) 每帧从 `::input_all` 读取按键，
    其中 b0 = 确认、b1 = 取消、x/y = 方向。
  * 角色选择阶段读取的是**每名玩家自己的设备** (`menu.character_select.device_current[i]`)。
  * 舞台选择阶段读取 `menu.character_select.device_stage`。
写这些设备的原生字段即可驱动菜单（实测 350~700ms 连写可以稳定命中一帧的按键判定）。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from .sq import RT_MASK, RAW_INSTANCE, RAW_ARRAY

TITLE_ITEMS = ["story", "vs_com", "vs_player", "network", "practice",
               "replay", "music", "config", "exit"]

# menu.pause.item = ["exit", null, "ret_select", "ret_title", "hide", "config"]
# 光标索引是**原始数组下标**(null 只是被 SetSkip 跳过,不压缩索引):
#   0 exit / 1 null / 2 ret_select / 3 ret_title
PAUSE_ITEM_RET_TITLE = 3
PAUSE_ITEM_RET_SELECT = 2
OFFSET_B1 = 0x10           # 取消键
OFFSET_B10 = 0x34          # 暂停键;battle_update.nut 读 team[*].input.b10


class MenuDriver:
    def __init__(self, game):
        self.g = game
        self.p = game.p
        self.r = game.r

    # -- 基础 --------------------------------------------------------------
    def device_native(self, *path) -> Optional[int]:
        """解析路径指向的设备；若指向 array 则取第 0 个元素。"""
        sa = self.g.get_path(*path)
        if sa is None:
            return None
        t, v = self.r.obj(sa)
        raw = t & RT_MASK
        if raw == RAW_ARRAY:
            els = self.r.array(v)
            if not els:
                return None
            t, v = self.r.obj(els[0])
            raw = t & RT_MASK
        if raw != RAW_INSTANCE:
            return None
        return self.g.native(v)

    def hold(self, dev: int, offset: int, value: int, duration: float) -> None:
        t0 = time.time()
        while time.time() - t0 < duration:
            self.p.write_i32(dev + offset, value)

    def press(self, dev: int, offset: int = 0x0C, duration: float = 0.45,
              settle: float = 0.45) -> None:
        self.hold(dev, offset, 1, duration)
        time.sleep(settle)

    def tap_dir(self, dev: int, offset: int, duration: float = 0.06) -> None:
        self.hold(dev, offset, 1, duration)
        time.sleep(0.18)

    def input_all(self) -> int:
        return self.device_native("input_all")

    def cursor_fields(self, *path) -> dict:
        inst = self.g.get_instance(*path)
        if not inst:
            return {}
        cls = self.p.u32(inst + 28)
        members = self.p.u32(cls + 24)
        out = {}
        for k, ka, va in self.r.table_nodes(members):
            if k[0] != "str":
                continue
            idx = self.p.u32(va + 4)
            if idx & 0x02000000:
                out[k[1]] = inst + 44 + (idx & 0xFFFFFF) * 8
        return out

    def set_cursor_val(self, *path, value: int) -> bool:
        f = self.cursor_fields(*path)
        if "val" not in f:
            return False
        self.p.write_i32(f["val"] + 4, value)
        return True

    # -- 高层 --------------------------------------------------------------
    def wait(self, predicate: Callable[[], bool], timeout: float = 15.0,
             interval: float = 0.1, desc: str = "") -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if predicate():
                return True
            time.sleep(interval)
        return False

    def select_live(self) -> bool:
        """角色选择界面是否已经在跑(device[0] 被创建)。"""
        inst = self.g.get_path("menu", "character_select", "device")
        if inst is None:
            return False
        tag, cur = self.r.obj(inst)
        if (tag & 0xFFFFFF) != 0x40:
            return False
        t0, _ = self.r.obj(cur + 0x18)
        return (t0 & 0xFFFFFF) != 0x01

    def in_match(self) -> bool:
        """是否真的在一局对战中。

        只看 `battle.state` 不够:回到标题后旧 battle 表还在,`state` 会残留 8,
        此时 `battle.team` 已经是空数组。真实对局必须同时满足
        "有 team 实例 + state 是 2/8"。
        """
        state = self.g.battle_value("state")
        if state not in (2, 8):
            return False
        try:
            return bool(self.g.team_instances())
        except Exception:                      # noqa: BLE001 - battle 表正在销毁
            return False

    def at_title(self) -> bool:
        """是否真的不在对局里(标题 / 角色选择 / 结算)。"""
        if self.in_match():
            return False
        try:
            state = self.g.battle_value("state")
        except Exception:                      # noqa: BLE001
            return True
        # 结算/加载态仍带着 team 实例,不算回到标题
        if state in (4, 32, 64, 128) and self.g.team_instances():
            return False
        return True

    def title_in_submenu(self) -> bool:
        """标题是否处于子菜单(如 VS COM 的难度选择),即 Update != UpdateMain。"""
        main = self.g.get_path("menu", "title", "UpdateMain")
        cur = self.g.get_path("menu", "title", "Update")
        if main is None or cur is None:
            return False
        a, b = self.r.value(main), self.r.value(cur)
        return getattr(a, "ptr", None) != getattr(b, "ptr", None)

    def title_main_active(self) -> bool:
        vis = self.r.value(self.g.get_path("menu", "title", "visible")) \
            if self.g.get_path("menu", "title", "visible") else None
        return bool(vis) and not self.title_in_submenu() and not self.select_live()

    def battle_input(self, team: int = 0) -> int:
        """对局中真正被读取的输入设备(`battle.team[i].input`)。

        注意它**不是** `::input_all`(实测两者原生指针不同):
        暂停判定在 `battle_update.nut` 里读的是 `team[i].input.b10`,
        往 `input_all` 上按暂停键没有任何效果。
        """
        teams = self.g.team_instances()
        if len(teams) <= team:
            return 0
        return self.g.input_native(teams[team]) or 0

    def press_pause(self, duration: float = 0.25) -> bool:
        """在对局中按暂停键打开暂停菜单。"""
        dev = self.battle_input(0)
        if not dev:
            return False
        self.hold(dev, OFFSET_B10, 1, duration)
        return True

    def pause_active(self) -> bool:
        """暂停菜单是否真的挂上(`menu.pause.state == 0`;Terminate 后是 -1)。"""
        p = self.g.get_path("menu", "pause", "state")
        if p is None:
            return False
        try:
            return int(self.r.value(p) or 0) == 0
        except Exception:                  # noqa: BLE001
            return False

    def to_title(self, timeout: float = 25.0) -> bool:
        """通过暂停菜单回到标题（仅在确实处于对局/结算时操作）。

        路径:对局中按 b10 -> 暂停菜单 -> 光标选 `ret_title` -> 确认
        -> `::replay.Confirm` -> `::loop.End(::menu.title)`。
        回放保存模式为 2 时会先弹"保存回放?"对话框,因此确认后要再确认一次。
        """
        if self.at_title():
            return True
        dev_all = self.input_all()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.at_title():
                return True
            if not self.press_pause():
                return False
            # 等暂停菜单真的挂上(不是"cursor_item 槽位存在"——那个槽平时就有值)
            if not self.wait(self.pause_active, timeout=2.0, interval=0.05):
                continue
            self.set_cursor_val("menu", "pause", "cursor_item", value=PAUSE_ITEM_RET_TITLE)
            time.sleep(0.1)
            self.press(dev_all, 0x0C, 0.35, 0.6)      # 确认 -> ret_title
            if self.wait(self.at_title, timeout=4.0, interval=0.1):
                return True
            # 可能弹出了"保存回放?"对话框:再确认一次(此时不在标题,不会误触标题项)
            self.press(dev_all, 0x0C, 0.25, 0.6)
            if self.wait(self.at_title, timeout=4.0, interval=0.1):
                return True
        return self.at_title()

    def switch_difficulty(self, level: int, timeout: float = 120.0) -> bool:
        """不重启进程地切换 VS COM 难度:回标题主菜单 -> 重进 VS COM。

        难度只有在"标题 -> VS COM -> 难度子菜单确认"这条路上才会写进角色,
        所以必须真的退回**标题主菜单**(角色选择/难度子菜单里都不行)。
        """
        if self.select_live():
            # 已经在角色选择:取消无用,先把这局选完(几十秒),再回标题
            self.advance_select(timeout=90.0)
        if not self.at_title():
            if not self.to_title():
                return False
        if self.title_in_submenu() and not self.back_to_title_menu():
            return False
        if not self.wait_title_ready(timeout=25.0):
            return False
        return self.enter_vs_com(int(level), timeout=timeout)

    def wait_title_ready(self, timeout: float = 40.0) -> bool:
        """等待标题菜单真正可交互(Update == UpdateMain,而不是开机 OP)。"""
        main = self.g.get_path("menu", "title", "UpdateMain")
        cur = self.g.get_path("menu", "title", "Update")
        if main is None or cur is None:
            return False
        main_ref = self.r.value(main)
        t0 = time.time()
        while time.time() - t0 < timeout:
            c = self.r.value(cur)
            if c is not None and getattr(c, "ptr", None) == getattr(main_ref, "ptr", None):
                return True
            time.sleep(0.1)
        return False

    def select_title_item(self, name: str) -> bool:
        """在标题主菜单选中一项并确认。

        **只能按一次确认**:VS COM 的确认会进入「难度选择」子菜单,此时
        `at_title()` 仍为真(还没进对局)。旧实现把子菜单当成"没反应",
        于是又按了一次 —— 这一次相当于在难度子菜单里点了确定,把
        `config.difficulty.vs` 提交成子菜单里的默认值,导致上层
        `choose_difficulty()` 完全失效(实测:配置写 EASY,实机永远 NORMAL)。
        """
        dev = self.input_all()
        if not dev:
            return False
        idx = TITLE_ITEMS.index(name)
        self.wait_title_ready()
        for attempt in range(2):
            t0 = time.time()
            while time.time() - t0 < 0.5:
                self.set_cursor_val("menu", "title", "cursor", value=idx)
                time.sleep(0.02)
            self.press(dev, 0x0C, 0.35, 0.50)
            # 成功 = 标题进入子菜单 / 已经开打。
            # **不能**用 select_live():`character_select.device` 数组在标题下也存在,
            # 会把"还没进子菜单"误判成"已经在角色选择",于是跳过 choose_difficulty,
            # 难度就停在子菜单的默认值(实测永远 NORMAL)。
            if self.wait(lambda: self.title_in_submenu() or self.in_match(),
                         timeout=3.0, interval=0.05):
                return True
            if attempt == 0:
                time.sleep(0.4)               # 淡入淡出中,等一帧再试
        return self.title_in_submenu() or self.in_match()

    def tap(self, key: int = 0x0C, hold: float = 0.12, settle: float = 0.30) -> None:
        """轻按一次确认键(input_all,不需要窗口焦点)。用于推进结算对话。"""
        dev = self.input_all()
        if not dev:
            return
        self.press(dev, key, hold, settle)

    def set_vs_difficulty(self, index: int) -> bool:
        """写 `::config.difficulty.vs`(0=EASY..3=LUNATIC),必须在选 VS COM **之前**。

        为什么不在难度子菜单里点选:实测 `proc.vs_com` 进入子菜单时会把
        `cursor_difficulty.val` 重置成 `::config.difficulty.vs`,而选定 vs_com 的
        那次确认键会在下一帧被子菜单当场消费掉(子菜单只存在 1~2 帧),于是
        `cursor_difficulty.val` 永远等于旧的 vs 值 —— 在子菜单里怎么点都不生效。
        直接改配置最稳:子菜单读到什么、写回什么,都已经是目标难度了。
        """
        from .ops import BattleOps
        ok = BattleOps(self.g).set_table_int("config", "difficulty", "vs",
                                             value=int(index))
        got = self.r.value(self.g.get_path("config", "difficulty", "vs"))
        return bool(ok) and (got is None or int(got) == int(index))

    # 兼容旧名字
    choose_difficulty = set_vs_difficulty

    def battle_started(self) -> bool:
        return self.in_match()

    def select_devices(self) -> list:
        """收集当前菜单真正在用的所有输入设备（character_select 阶段）。"""
        devs = []
        for key in ("device_current", "device"):
            sa = self.g.get_path("menu", "character_select", key)
            if sa is None:
                continue
            t, v = self.r.obj(sa)
            if (t & RT_MASK) != RAW_ARRAY:
                continue
            for el in self.r.array(v):
                t2, v2 = self.r.obj(el)
                if (t2 & RT_MASK) == RAW_INSTANCE:
                    d = self.g.native(v2)
                    if d and d not in devs:
                        devs.append(d)
        d = self.device_native("menu", "character_select", "device_stage")
        if d and d not in devs:
            devs.append(d)
        return devs

    def advance_select(self, max_steps: int = 60, timeout: float = 60.0) -> bool:
        """在角色/颜色/符卡/舞台选择阶段连续确认，直到进入对战。"""
        t0 = time.time()
        step = 0
        while time.time() - t0 < timeout and step < max_steps:
            if self.battle_started():
                return True
            devs = self.select_devices()
            if not devs:
                time.sleep(0.3)
                continue
            for d in devs:
                self.press(d, 0x0C, 0.22, 0.38)
            step += 1
        return self.battle_started()

    # -- 一键入口 ----------------------------------------------------------
    def enter_vs_com(self, difficulty: int = 1, timeout: float = 90.0) -> bool:
        """从标题主菜单进入 VS COM 对战(难度选好后按默认角色直接开打)。"""
        self.seed_last_device()
        if not self.set_vs_difficulty(difficulty):
            return False
        if not self.select_title_item("vs_com"):
            return False
        # 正常情况:难度子菜单会被选 vs_com 的同一次按键连带确认;
        # 若它还停在那里,补一次确认(此时 config 已经是目标值)。
        if self.wait(self.title_in_submenu, timeout=0.35, interval=0.05):
            dev = self.input_all()
            if dev:
                self.press(dev, 0x0C, 0.30, 0.5)
        return self.advance_select(timeout=timeout)

    def back_to_title_menu(self, timeout: float = 8.0) -> bool:
        """从标题子菜单(如 VS COM 的难度选择)退回主菜单。

        **角色选择里取消无效**:`character_select_action.nut` 只在
        `t[0].state == t[1].state == SelectMaster` 时才响应 `device.b1`,
        VS COM 下队伍 1 是 COM,状态不同 → 只能先选完人进对局,再走暂停菜单。
        """
        dev = self.input_all()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.title_main_active():
                return True
            if not self.title_in_submenu():
                return False
            if not dev:
                return False
            self.press(dev, OFFSET_B1, 0.2, 0.45)          # 取消
            time.sleep(0.3)
        return self.title_main_active()

    def ensure_battle(self, difficulty: int = 1, mode: str = "vs_com",
                      timeout: float = 120.0) -> bool:
        """确保处于对战状态:不在对战则重新进入。"""
        if self.in_match():
            return True
        from .ops import BattleOps
        ops = BattleOps(self.g)
        st = self.g.read_state(with_shots=False)
        if st.battle_state in (32, 64, 128, 4) and self.g.team_instances():
            # 回合结算/下一回合加载中:等游戏自己走完,不要手动跳演示
            if ops.wait_next_round(timeout=25.0):
                return True
        self.to_title()
        self.seed_last_device()
        if mode == "practice":
            return self.enter_practice(difficulty=difficulty, timeout=timeout)
        return self.enter_vs_com(difficulty, timeout=timeout)

    def seed_last_device(self, device_id: int = -1) -> bool:
        """把 ::input_all 的 last_device 写成 -1(键盘)。

        全新启动的游戏从未收到真实按键,GetLastDevice() 返回 -2,
        导致 character_select 无法创建输入设备(device[0] 恒为 null)。
        该字段只在有真实输入时才会被刷新,因此写入一次即可长期有效。
        """
        inst = self.g.get_instance("input_all")
        if not inst:
            return False
        self.g.p.write_i32(self.g.native(inst) + 0xA4, device_id)
        return True

    def enter_practice(self, player2: int = 3, difficulty: int = 1,
                       timeout: float = 90.0) -> bool:
        """进入练习模式对战。

        player2/difficulty 必须在**进入对局之前**写入 config.practice：
        对局中改 player2 会让游戏尝试在没有 COM 的情况下创建 COM（崩溃）。
        """
        from .ops import BattleOps
        BattleOps(self.g).set_practice_config(player2=player2, difficulty=difficulty)
        self.seed_last_device()
        self.select_title_item("practice")
        return self.advance_select(timeout=timeout)
