"""战场几何可视化:判定框、角色位置、弹幕体积。

用法:
    python scripts/viz_battle.py            # 实时跟随
    python scripts/viz_battle.py --team 1   # 以 team1 为受控方

与 `viz_obs.py` 的分工:
  * `viz_obs.py` 看**数值** —— 策略网络真正吃进去的张量,用来核对感知是否正确;
  * 本文件看**几何** —— 判定框画在哪、有多大的体积、离角色多远,用来核对
    "数值到底对应屏幕上的哪个东西"。

画什么、为什么这样画:
  * 角色画**两层**:外层是受击判定(实测 40x110,即 x±20 / y-60..y+50),
    内层是本体位置点。二者不是一回事,混在一起就没法判断"离挨打还有多远"。
  * 弹幕框宽高为 0 时画成小圆点(判定框退化成点、体积未知),非 0 才画矩形。
    这个区分必须保留 —— 统一画成点就看不出来"体积有没有进观测"。
  * 颜色按团队:team0 蓝、team1 红;实心 = 真弹幕(cb_mask!=0),虚框 = 演出。

坐标系:Actor2D 是屏幕坐标,原点左上、y 向下,世界 1280x720
(与 obs_builder.DEFAULTS 一致)。画布按 scale 等比缩放,不改长宽比。
"""
from __future__ import annotations

import argparse
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bridge.shm import BridgeShm                      # noqa: E402
from env.obs_builder import (MAX_PROJ, PROJ_COL_GAP,  # noqa: E402
                             PROJ_COL_HALF_DIAG, PROJ_DIMS, ObsBuilder)

WORLD_W, WORLD_H = 1280.0, 720.0
STAGE_FLOOR = 640.0

C_TEAM = ("#3b82f6", "#ef4444")
C_ATK = ("#22d3ee", "#fb923c")     # 攻击框:team0 青 / team1 橙
C_BG, C_GRID, C_TEXT, C_MUTED = "#0f172a", "#1e293b", "#e2e8f0", "#64748b"


class BattleView:
    def __init__(self, shm: BridgeShm, scale: float = 1.0, team: int = 0):
        self.shm = shm
        self.scale = scale
        self.team = team
        self.ob = ObsBuilder()
        self.root = tk.Tk()
        self.root.title("th155 战场几何 — 判定框 / 体积")
        self.root.configure(bg=C_BG)
        cw, ch = int(WORLD_W * scale), int(WORLD_H * scale)
        self.canvas = tk.Canvas(self.root, width=cw, height=ch, bg=C_BG,
                                highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.panel = tk.Text(self.root, width=40, bg="#111827", fg=C_TEXT,
                             font=("Consolas", 9), relief="flat", padx=8,
                             pady=8)
        self.panel.grid(row=0, column=1, sticky="nsew")
        self.info = tk.Label(self.root, text="", bg=C_BG, fg=C_MUTED,
                             font=("Consolas", 10), anchor="w")
        self.info.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self._draw_stage()

    # -- 坐标 -------------------------------------------------------------
    def _sx(self, x: float) -> float:
        return x * self.scale

    def _sy(self, y: float) -> float:
        return y * self.scale

    def _draw_stage(self) -> None:
        """静态网格。**不带 dyn tag**,所以不会被每帧的 delete 清掉。"""
        c = self.canvas
        c.create_rectangle(0, 0, self._sx(WORLD_W), self._sy(STAGE_FLOOR),
                           outline=C_GRID, width=1)
        for x in range(0, int(WORLD_W) + 1, 160):
            c.create_line(self._sx(x), 0, self._sx(x), self._sy(STAGE_FLOOR),
                          fill=C_GRID, dash=(2, 6))
            c.create_text(self._sx(x) + 3, 8, text=str(x), fill=C_MUTED,
                          anchor="nw", font=("Consolas", 8))
        for y in range(0, int(STAGE_FLOOR) + 1, 160):
            c.create_line(0, self._sy(y), self._sx(WORLD_W), self._sy(y),
                          fill=C_GRID, dash=(2, 6))
            c.create_text(3, self._sy(y) + 2, text=str(y), fill=C_MUTED,
                          anchor="nw", font=("Consolas", 8))
        c.create_line(0, self._sy(STAGE_FLOOR), self._sx(WORLD_W),
                      self._sy(STAGE_FLOOR), fill=C_MUTED, width=2)

    def _box(self, b: dict, color: str, width: int = 2,
             dash=None) -> None:
        l, t = float(b.get("box_l", 0.0)), float(b.get("box_t", 0.0))
        r, btm = float(b.get("box_r", 0.0)), float(b.get("box_b", 0.0))
        if r - l <= 0.01 and btm - t <= 0.01:
            x, y = self._sx(float(b.get("x", 0.0))), self._sy(float(b.get("y", 0.0)))
            self.canvas.create_oval(x - 3, y - 3, x + 3, y + 3,
                                    outline=color, width=2, tags="dyn")
            return
        self.canvas.create_rectangle(self._sx(l), self._sy(t), self._sx(r),
                                     self._sy(btm), outline=color, width=width,
                                     dash=dash, tags="dyn")

    # -- 渲染 -------------------------------------------------------------
    def frame(self) -> None:
        self._render(self.shm.read_state())
        self.root.after(16, self.frame)

    def _render(self, st: dict) -> None:
        c = self.canvas
        # 清理必须在 _render 内部:_render 是唯一绘制入口,任何直接调用
        # (测试、单帧调试)都要先清,否则旧框层层叠加成残影。
        c.delete("dyn")
        teams = st.get("teams") or []
        shots = st.get("shots") or []

        n_real = n_vol = n_atk = 0
        for s in shots:
            own = s.get("owner", -1)
            col = C_TEAM[own] if own in (0, 1) else C_MUTED
            real = (s.get("cb_mask") or 0) != 0
            has_vol = (s.get("box_r", 0.0) - s.get("box_l", 0.0)) > 0.01
            if real:
                n_real += 1
                n_vol += int(has_vol)
            if has_vol:
                c.create_rectangle(self._sx(s["box_l"]), self._sy(s["box_t"]),
                                   self._sx(s["box_r"]), self._sy(s["box_b"]),
                                   outline=col, width=2 if real else 1,
                                   dash=None if real else (2, 3), tags="dyn")
            else:
                x, y = self._sx(s["x"]), self._sy(s["y"])
                c.create_oval(x - 2, y - 2, x + 2, y + 2, outline=col,
                              fill=col if real else "", width=1, tags="dyn")

        for i, t in enumerate(teams[:2]):
            for slot in ("master", "slave"):
                a = t.get(slot) or {}
                if not a:
                    continue
                active = (slot == "master") == (t.get("current", 0) == 0)
                self._box(a, C_TEAM[i], width=2 if active else 1,
                          dash=None if active else (3, 3))
                x, y = self._sx(a.get("x", 0.0)), self._sy(a.get("y", 0.0))
                r = 5 if active else 3
                c.create_oval(x - r, y - r, x + r, y + r,
                              fill=C_TEAM[i] if active else "",
                              outline=C_TEAM[i], width=2, tags="dyn")
                c.create_text(x + 8, y - 10,
                              text=f"T{i}{slot[0].upper()} x={a.get('x', 0):.0f}",
                              fill=C_TEAM[i], anchor="w",
                              font=("Consolas", 8), tags="dyn")
                # ---- 近战攻击判定框 ----
                # 与受击框的区别:受击框回答"别人能不能打到我",
                # 攻击框回答"我这一下能打到哪"。只在出手帧存在,
                # 一次攻击最多 4 个(实测 motion 1210/3040 是 4 个)。
                for k, bx in enumerate(a.get("atk_boxes") or []):
                    n_atk += 1
                    hw, hh = float(bx["hw"]), float(bx["hh"])
                    bx0 = self._sx(float(bx["x"]) - hw)
                    bx1 = self._sx(float(bx["x"]) + hw)
                    by0 = self._sy(float(bx["y"]) - hh)
                    by1 = self._sy(float(bx["y"]) + hh)
                    c.create_rectangle(bx0, by0, bx1, by1,
                                       outline=C_ATK[i], width=2,
                                       tags="dyn")
                    c.create_text((bx0 + bx1) / 2, (by0 + by1) / 2,
                                  text=f"{hw * 2:.0f}x{hh * 2:.0f}",
                                  fill=C_ATK[i], font=("Consolas", 7),
                                  tags="dyn")

        lines = [f"battle_state = {st.get('battle_state')}   "
                 f"frame = {self.shm.frame}",
                 f"round {st.get('round')}   time {st.get('time', 0)}/60", ""]
        for i, t in enumerate(teams[:2]):
            lines.append(f"team{i} {t.get('master_name', '?')}"
                         f" / {t.get('slave_name', '?')}")
            lines.append(f"  life {t.get('life')}/{t.get('life_max')}"
                         f"   spirit {(t.get('sp', 0)) / 100:.1f}"
                         f"   occult {t.get('occult')}/2000")
        lines += ["", f"shots {len(shots)}   真弹幕 {n_real}   有体积 {n_vol}"
                      f" ({n_vol / max(n_real, 1) * 100:.0f}%)",
                  f"攻击判定框 {n_atk} 个(青/橙实线)", ""]

        try:
            o = self.ob.build(st, controlled_team=self.team, prev_action=0)
            proj, mask = o["proj"], o["proj_mask"]
        except Exception as exc:                       # noqa: BLE001
            proj, mask = None, []
            lines.append(f"obs 构建失败: {exc}")
        if proj is not None:
            lines.append(f"proj ({MAX_PROJ},{PROJ_DIMS})  "
                         f"活跃 {int(sum(1 for v in mask if v))} 个")
            lines.append(f"{'gap':>8}{'半对角':>9}")
            for row, mk in zip(proj, mask):
                if mk:
                    lines.append(f"{row[PROJ_COL_GAP]:>8.3f}"
                                 f"{row[PROJ_COL_HALF_DIAG]:>9.3f}")
        lines += ["", "Esc 退出"]
        self.panel.delete("1.0", "end")
        self.panel.insert("1.0", "\n".join(lines))
        self.info.configure(
            text=f"shots {len(shots)}  真弹幕 {n_real}  有体积 {n_vol}  "
                 f"({n_vol / max(n_real, 1) * 100:.0f}%)  攻击框 {n_atk}   "
                 "Esc=退出")

    def run(self) -> None:
        self.frame()
        self.root.mainloop()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--team", type=int, default=0, choices=(0, 1))
    ap.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args()
    try:
        shm = BridgeShm.open(timeout=a.timeout)
    except Exception as exc:                           # noqa: BLE001
        print(f"共享内存不可用: {exc}\n先启动游戏并注入 DLL:\n"
              f"  python scripts/game_session.py restart")
        return 1
    try:
        BattleView(shm, scale=a.scale, team=a.team).run()
    finally:
        shm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
