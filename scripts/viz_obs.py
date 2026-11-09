"""喂给模型的观测数据实况面板。

只显示 `ObsBuilder.build()` 的输出 —— 也就是策略网络真正看到的东西:
self(50) / opp(55) / global(23) / proj(16x9)+mask / motion 嵌入 / 动作掩码。
**不显示游戏画面**:判断"模型感知对不对"要看张量,而不是看渲染。

用法:
    python scripts/viz_obs.py              # 实时刷新
    python scripts/viz_obs.py --team 1     # 以 team1 为受控方(镜像不变会翻转)

为什么用 tkinter:本机没有 matplotlib/pygame,tkinter 是标准库自带。
为什么只用 Text 不画 Canvas:这个面板要核对的是**数值**,画布只适合核对几何;
而且 Text 每次 set 全量重写,天然没有残影问题。

列名与 obs_builder 严格对齐:模块导入时断言长度等于 SELF_DIM/OPP_DIM/
GLOBAL_DIM/PROJ_DIMS,一旦观测加维而这里没跟,启动即报错,不会静默错位。
"""
from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bridge.shm import BridgeShm                        # noqa: E402
from env.obs_builder import (GLOBAL_DIM, MAX_PROJ,      # noqa: E402
                             OPP_DIM, OPP_TAIL, PROJ_DIMS,
                             SELF_DIM, ObsBuilder)

# ---- 列名:顺序必须与 obs_builder.build()/team_features() 的拼接一致 ----
SELF_COLS = (
    ["active_master", "active_slave"]
    + ["pos_x", "pos_y"]
    + ["wall_back", "wall_front"]
    + ["vel_x", "vel_y"]
    + ["lane_air", "lane_mid", "lane_ground"]
    + ["facing", "motion_norm", "motion_frame"]
    + ["hit_stop", "damage_stop"]
    + ["free", "attacking", "guard", "hitstun", "down", "invincible"]
    + ["hp", "regain"]
    + ["hp_slave", "regain_slave"]
    + ["spirit", "spirit_max"]
    + ["occult", "tag_ready"]
    + ["spell_ready", "spell_active"]
    + ["endure_ratio", "stan", "recover", "center_stop", "target_dist",
       "atk_range", "gravity", "slide_speed", "mp", "graze", "hit_count",
       "key_take", "hit_back_flag", "atk_id", "hit_result", "atk_lv",
       "atk_cancel", "atk_guard_break"]
    # v18:近战攻击判定框(最近的一个)。have=0 表示这一帧没在出手。
    + ["have_atk_box", "atk_reach_x", "atk_reach_y", "atk_w", "atk_h",
       "atk_covers_opp"]
    # v19:命中位掩码。方向是"我打中了谁"(bit23),不是"谁打中我"。
    + ["hit_landed", "hit_mask_any"]
)
OPP_COLS = SELF_COLS + list(OPP_TAIL)
GLOBAL_COLS = [
    "timer_ratio", "hp_adv", "round", "not_in_battle",
    "prev_dx", "prev_dy",
    "btn_0", "btn_1", "btn_2", "btn_3", "btn_4",
    "spell_ready", "combo_damage", "damage_scale", "combo_stun",
    "is_time_stop", "time_stop", "slow",
    "enable_contact_test", "enable_time_count", "enable_time_up",
    "demo_count", "corner_locked", "laser_startup_warning",
]
PROJ_COLS = ["dx", "dy", "vx", "vy", "radial", "own", "gap", "is_attack",
             "half_diag", "is_beam", "beam_threat", "in_cross"]

# 启动即校验:观测加维而这里没跟,直接报错而不是错位显示。
for _name, _cols, _dim in (("SELF", SELF_COLS, SELF_DIM),
                           ("OPP", OPP_COLS, OPP_DIM),
                           ("GLOBAL", GLOBAL_COLS, GLOBAL_DIM),
                           ("PROJ", PROJ_COLS, PROJ_DIMS)):
    if len(_cols) != _dim:
        raise SystemExit(
            f"{_name} 列名数量 {len(_cols)} != 观测维度 {_dim}:"
            f"obs_builder 加/删了特征,请同步 scripts/viz_obs.py 的列名。")

BG, FG, MUTED, DIM = "#0f172a", "#e2e8f0", "#94a3b8", "#334155"
ACCENT, WARN, GOOD = "#38bdf8", "#fbbf24", "#4ade80"


def _fmt(v: float, width: int = 7, prec: int = 3) -> str:
    return f"{v:>{width}.{prec}f}"


class ObsPanel:
    def __init__(self, shm: BridgeShm, team: int = 0):
        self.shm = shm
        self.team = team
        self.ob = ObsBuilder()
        self.root = tk.Tk()
        self.root.title(f"喂给模型的观测数据 — 受控方 team{team}")
        self.root.configure(bg=BG)
        self.text = tk.Text(self.root, bg=BG, fg=FG, font=("Consolas", 10),
                            relief="flat", padx=12, pady=10, width=118,
                            height=52, insertwidth=0)
        self.text.grid(row=0, column=0, sticky="nsew")
        sb = tk.Scrollbar(self.root, command=self.text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=sb.set)
        self.text.tag_configure("h", foreground=ACCENT)
        self.text.tag_configure("m", foreground=MUTED)
        self.text.tag_configure("d", foreground=DIM)
        self.text.tag_configure("w", foreground=WARN)
        self.text.tag_configure("g", foreground=GOOD)
        self.text.configure(state="disabled")
        self.fps = 0.0
        self._t0 = time.perf_counter()
        self._n = 0
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<space>", lambda e: None)

    # -- 渲染 ------------------------------------------------------------
    def _put(self, s: str, tag: str = "") -> None:
        self.text.insert("end", s + "\n", tag)

    def _vector(self, title: str, cols: list, vals) -> None:
        head = f"── {title}  ({len(cols)} 维) "
        self._put(head + "─" * max(0, 78 - len(head)), "h")
        for i in range(0, len(cols), 3):
            cells = []
            for j in range(i, min(i + 3, len(cols))):
                v = float(vals[j])
                # 饱和值(±1)打 ! 标记:这些是被 _clip 截断的量,
                # 数值本身已经丢失分辨率,看到 ! 就知道不能再依赖它做细判。
                mark = "!" if abs(v) > 0.999 else " "
                cells.append(f"{cols[j][:17]:<17}{_fmt(v)}{mark}")
            self._put("  ".join(cells))

    def _proj_table(self, proj, mask) -> None:
        n_on = int(sum(1 for v in mask if v))
        head = f"── proj  ({MAX_PROJ}x{PROJ_DIMS})  活跃 {n_on}/{MAX_PROJ} "
        # 注意:不能写成 f"..." "─" * 40 —— 相邻字符串字面量在解析期就拼接,
        # 乘号会作用在整串上,结果是表头重复 40 次。
        self._put(head + "─" * max(0, 78 - len(head)), "h")
        self._put("  " + "".join(f"{c[:8]:>9}" for c in PROJ_COLS), "m")
        for i in range(MAX_PROJ):
            if not mask[i]:
                continue
            row = "  " + "".join(f"{float(proj[i][j]):>9.3f}"
                                 for j in range(PROJ_DIMS))
            vol = float(proj[i][PROJ_COLS.index("half_diag")])
            tag = "g" if vol > 0.0 else "d"
            self._put(row + ("" if vol > 0 else "   (体积未知)"), tag)
        for i in range(MAX_PROJ):
            if mask[i]:
                continue
            self._put("  " + "".join(f"{'-':>9}" for _ in PROJ_COLS), "d")
            break        # 空槽只示例一行,不刷屏

    def render(self) -> None:
        st = self.shm.read_state()
        try:
            o = self.ob.build(st, controlled_team=self.team, prev_action=0)
        except Exception as exc:                       # noqa: BLE001
            o = None
            err = str(exc)

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self._n += 1
        dt = time.perf_counter() - self._t0
        if dt > 0.5:
            self.fps = self._n / dt
            self._n, self._t0 = 0, time.perf_counter()

        self._put(f"喂给模型的观测   shm_frame={self.shm.frame}  "
                  f"battle_state={st.get('battle_state')}  "
                  f"面板 {self.fps:.1f} Hz", "h")
        self._put(f"shm shots={len(st.get('shots') or [])}   "
                  f"round={st.get('round')}   "
                  f"time={st.get('time')}/{st.get('time_unit')}", "m")

        if o is None:
            self._put(f"\n无法构建观测(游戏可能不在战斗中): {err}", "w")
            self.text.configure(state="disabled")
            self.root.after(120, self.render)
            return

        self._put("")
        self._vector("self", SELF_COLS, o["self"])
        self._put("")
        self._vector("opp", OPP_COLS, o["opp"])
        self._put("")
        self._vector("global", GLOBAL_COLS, o["global"])
        self._put("")
        self._proj_table(o["proj"], o["proj_mask"])
        self._put("")
        n_ok = int(sum(1 for v in o["action_mask"] if v))
        self._put(f"── 离散量 " + "─" * 64)
        self._put(f"  motion_self={o['motion_self']:<7} "
                  f"motion_opp={o['motion_opp']:<7} "
                  f"action_mask 可选 {n_ok}/{len(o['action_mask'])}")
        self.text.configure(state="disabled")
        self.root.after(120, self.render)

    def run(self) -> None:
        self.render()
        self.root.mainloop()


def main() -> int:
    ap = argparse.ArgumentParser()
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
        ObsPanel(shm, team=a.team).run()
    finally:
        shm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
