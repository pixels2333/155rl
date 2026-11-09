"""自动化课程调度器:COM 1 -> COM 5 的动态难度晋级与奖励退火。

设计要点
--------
* **纯逻辑**:窗口统计、晋级判定、难度轮转、退火参数、断点序列化都在这里,
  不碰游戏,可完全无头测试(`tests/test_curriculum.py`)。
* **实机切难度**由调用方执行(训练器 / `src/train/eval.py`):
  VS 模式下必须用 `BattleOps.set_com_difficulty()`,它同时写
  `com_difficulty / com_level / com_baria_rate / com_guard_rate`。
  只写 `com_level` 会让内置 COM 的状态机进入不一致状态并崩溃(实测
  `std::bad_function_call`)。注意 `::config.practice.difficulty` 那条路
  只对练习模式生效,而练习模式在自动化下不可用(docs/re_notes.md §6.1)。
* **窗口口径**:只统计当前阶段的最近 `window` 局,避免上一阶段的高胜率
  把 Agent 一路"带飞"到高难度;`decided` 不含平局。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

RESULTS = ("win", "lose", "draw")


@dataclass(frozen=True)
class Stage:
    """一个课程阶段。

    com_levels 用内部编号 0..4(= 人类口径 COM1..COM5);
    `promote_win_rate = None` 表示这是最后一阶,不再晋级。
    """

    name: str
    com_levels: Tuple[int, ...]
    promote_win_rate: Optional[float] = None
    promote_min_decided: Optional[int] = None      # None -> 用 Curriculum.min_decided
    entropy_scale: float = 1.0
    reward_overrides: Dict[str, float] = field(default_factory=dict)


DEFAULT_STAGES: Tuple[Stage, ...] = (
    Stage("com1", (0,), promote_win_rate=0.80, entropy_scale=1.0),
    Stage("com2_3", (1, 2), promote_win_rate=0.75, entropy_scale=0.7),
    Stage("com4_5", (3, 4), promote_win_rate=None, entropy_scale=0.4,
          # 高阶博弈里"拉近距离"的启发式会误导,退火到 0
          reward_overrides={"w_approach": 0.0}),
)


class Curriculum:
    """按胜率推进 COM 难度的课程调度器。"""

    def __init__(self, stages: Optional[Sequence[Stage]] = None,
                 window: int = 100, min_decided: int = 30):
        if not stages:
            stages = DEFAULT_STAGES
        stages = tuple(stages)
        if not stages:
            raise ValueError("stages 不能为空")
        for st in stages:
            if not st.com_levels:
                raise ValueError(f"阶段 {st.name} 没有 com_levels")
        self.stages: Tuple[Stage, ...] = stages
        self.window = int(window)
        self.min_decided = int(min_decided)
        self.stage_index = 0
        self.current_level = self.stages[0].com_levels[0]   # 当前实际在用的难度
        self._rr = 0                                   # 混合难度轮转指针
        self._history: Deque[Dict] = deque(maxlen=self.window)
        self._best: Dict[str, Tuple[float, str]] = {}

    # -- 阶段 --------------------------------------------------------------
    @property
    def stage(self) -> Stage:
        return self.stages[self.stage_index]

    @property
    def com_levels(self) -> Tuple[int, ...]:
        return tuple(self.stage.com_levels)

    def peek_com_level(self) -> int:
        """下一个要用的难度(不推进轮转)。"""
        levels = self.com_levels
        return levels[self._rr % len(levels)]

    def pick_com_level(self) -> int:
        """取当前阶段该用的难度并推进轮转;混合阶段交替,保证样本均衡。"""
        level = self.peek_com_level()
        self._rr += 1
        self.current_level = level
        return level

    # -- 统计 --------------------------------------------------------------
    def mark_applied(self, level: int) -> None:
        """记录"实际已经生效"的难度(不推进轮转)。

        晋级后重进对局时用:此时难度是外部设定的,轮转指针不应被消费,
        但后续 `observe` 的标签必须反映真实难度。
        """
        self.current_level = int(level)

    def observe(self, result: str, com_level: Optional[int] = None) -> None:
        """记录一局结果;`com_level` 缺省用当前实际在用的难度。"""
        if result not in RESULTS:
            raise ValueError(f"未知的对局结果 {result!r},应为 {RESULTS}")
        level = self.current_level if com_level is None else int(com_level)
        self._history.append({"stage": self.stage_index, "result": result,
                              "level": int(level)})

    def _recent(self) -> List[Dict]:
        return list(self._history)

    def stats(self) -> Dict:
        recent = self._recent()
        at_stage = [r for r in recent if r["stage"] == self.stage_index]
        return {
            "stage": self.stage.name,
            "stage_index": self.stage_index,
            "matches": len(recent),
            "decided": sum(1 for r in recent if r["result"] != "draw"),
            "win_rate": _rate(recent),
            "stage_matches": len(at_stage),
            "stage_decided": sum(1 for r in at_stage if r["result"] != "draw"),
            "stage_win_rate": _rate(at_stage),
        }

    # -- 晋级 --------------------------------------------------------------
    def should_promote(self) -> bool:
        stage = self.stage
        if stage.promote_win_rate is None:
            return False
        st = self.stats()
        need = stage.promote_min_decided or self.min_decided
        return st["stage_decided"] >= need and st["stage_win_rate"] >= stage.promote_win_rate

    def promote(self) -> bool:
        if not self.should_promote():
            return False
        self.stage_index += 1
        self._rr = 0
        return True

    # -- 退火 --------------------------------------------------------------
    def reward_overrides(self) -> Dict[str, float]:
        return dict(self.stage.reward_overrides)

    def entropy_scale(self) -> float:
        return float(self.stage.entropy_scale)

    # -- 各阶段最优 --------------------------------------------------------
    def update_best(self, path: str, win_rate: float) -> bool:
        """记录当前阶段的最优 Checkpoint;返回是否刷新。"""
        key = self.stage.name
        prev = self._best.get(key)
        if prev is not None and win_rate <= prev[0]:
            return False
        self._best[key] = (float(win_rate), str(path))
        return True

    def best_checkpoint(self) -> Optional[str]:
        got = self._best.get(self.stage.name)
        return got[1] if got else None

    # -- 序列化 ------------------------------------------------------------
    def to_dict(self) -> Dict:
        return {
            "stage_index": self.stage_index,
            "current_level": self.current_level,
            "rr": self._rr,
            "window": self.window,
            "min_decided": self.min_decided,
            "history": self._recent(),
            "best": {k: [v[0], v[1]] for k, v in self._best.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict, stages: Optional[Sequence[Stage]] = None) -> "Curriculum":
        cur = cls(stages=stages, window=int(data.get("window", 100)),
                  min_decided=int(data.get("min_decided", 30)))
        cur.stage_index = max(0, min(int(data.get("stage_index", 0)),
                                     len(cur.stages) - 1))
        cur._rr = int(data.get("rr", 0))
        cur.current_level = int(data.get("current_level", cur.com_levels[0]))
        if cur.current_level not in cur.stage.com_levels:
            # 换过配置(例如 COM2 的存档拿进 COM3 的课程)时,旧难度不能沿用
            cur.current_level = cur.stage.com_levels[0]
        for rec in data.get("history") or []:
            si = int(rec["stage"])
            level = int(rec.get("level", 0))
            # 只保留"在当前 stages 定义下仍然成立"的战绩:阶段序号可能被重新
            # 定义过(COM2 轮次的 stage0=COM2,COM3 轮次的 stage0=COM3),
            # 把旧难度的胜率搬过来会让课程立刻误判晋级。
            if not (0 <= si < len(cur.stages)):
                continue
            if level not in cur.stages[si].com_levels:
                continue
            cur._history.append({"stage": si,
                                 "result": rec["result"],
                                 "level": level})
        for name, pair in (data.get("best") or {}).items():
            cur._best[name] = (float(pair[0]), str(pair[1]))
        return cur

    @classmethod
    def from_config(cls, cfg: Optional[Dict]) -> "Curriculum":
        """从 `configs/train_ppo.yaml` 的 `curriculum:` 段构造;缺省用 DEFAULT_STAGES。"""
        block = dict((cfg or {}).get("curriculum") or {})
        raw = block.get("stages")
        stages: Optional[List[Stage]] = None
        if raw:
            stages = [Stage(name=str(s.get("name", f"stage{i}")),
                            com_levels=tuple(int(x) for x in s.get("com_levels") or ()),
                            promote_win_rate=(None if s.get("promote_win_rate") is None
                                              else float(s["promote_win_rate"])),
                            promote_min_decided=(None if s.get("promote_min_decided") is None
                                                 else int(s["promote_min_decided"])),
                            entropy_scale=float(s.get("entropy_scale", 1.0)),
                            reward_overrides=dict(s.get("reward_overrides") or {}))
                      for i, s in enumerate(raw)]
        return cls(stages=stages,
                   window=int(block.get("window", 100)),
                   min_decided=int(block.get("min_decided", 30)))


def _rate(records: Iterable[Dict]) -> float:
    records = list(records)
    decided = [r for r in records if r["result"] != "draw"]
    if not decided:
        return 0.0
    return sum(1 for r in decided if r["result"] == "win") / len(decided)
