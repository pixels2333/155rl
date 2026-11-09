"""标准化评估套件:确定性策略 × 指定 COM 难度 × N 局 -> Markdown / JSON 报告。

与训练解耦:这里只"跑对局 + 统计",不做任何梯度更新。

口径说明
--------
* **胜率**只按分出胜负的局数算(平局/超时不进分母),**平局率**按总局数算。
* **kind**:`ko` = 回合被提前打死(`battle.time > 0`),`time` = 打满计时。
* **实血差** = 我方剩余实血 − 对方剩余实血(终局快照)。
* **破防**统计对方 `sp_max` 下降次数(灵力上限被削 = 破防)。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

RESULTS = ("win", "lose", "draw")
DEFAULT_OUT_DIR = "runs/eval"


# ---------------------------------------------------------------------------
# 单局判定(训练与评估共用,故放在这里而不是 train_ppo)
# ---------------------------------------------------------------------------
def episode_result(info: dict) -> str:
    """从终局 info 判断胜负。

    优先用游戏自己算出的 `winner`(1 = team0 胜,2 = team1 胜);
    它比"血量是否归零"可靠 —— 被击落的一方退出屏幕后血量可能先归零再回填。
    """
    snap = info.get("state_snapshot") or {}
    teams = snap.get("teams") or []
    if len(teams) < 2:
        return "draw"
    team = int(info.get("team", 0) or 0)
    # battle.winner 在回合开始后**不会被清零**,state=8 时读到的是上一回合的残留值,
    # 只有终局状态(32 回合结束 / 64 KO 演出 / 128 初始化)里它才代表本回合结果。
    if int(info.get("battle_state", 0) or 0) in (32, 64, 128):
        win = int(snap.get("winner", 0) or 0)
        if win in (1, 2):
            return "win" if win - 1 == team else "lose"
    if team == 1:
        teams = [teams[1], teams[0]]
    me, opp = teams[0].get("life", 0), teams[1].get("life", 0)
    if opp <= 0 and me > 0:
        return "win"
    if me <= 0 and opp > 0:
        return "lose"
    return "draw"


def episode_kind(info: dict) -> str:
    """区分回合是怎么结束的:`ko`(提前打死)还是 `time`(打满计时)。

    `Round_KO()` 会把 `enableTimeCount` 关掉并保留 `battle.time` 为正;
    只有打满计时才把 `time` 递减到 0。因此 `time > 0` ⇒ 回合提前结束 = KO。
    """
    try:
        return "ko" if int(info.get("time", 0) or 0) > 0 else "time"
    except (TypeError, ValueError):
        return "time"


# ---------------------------------------------------------------------------
# 单局记录
# ---------------------------------------------------------------------------
@dataclass
class MatchRecord:
    result: str                     # win / lose / draw
    kind: str                       # ko / time
    frames: int                     # 决策步数
    secs: float                     # 墙钟时长(按 60fps 折算)
    hp_diff: int                    # 我方实血 - 对方实血
    tags: int                       # 换人次数
    guard_breaks: int = 0           # 对手被破防次数
    com_level: int = 0              # 内部 0..4,即 COM1..COM5
    state: int = 0                  # 终局 battle_state
    lives: List[int] = field(default_factory=list)
    ret: float = 0.0
    counts: Dict[str, int] = field(default_factory=dict)  # 对局立回计数(combo/opp_combo/counter/…)


# ---------------------------------------------------------------------------
# 统计与报告
# ---------------------------------------------------------------------------
def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if len(values) else 0.0


def _sd(values: Sequence[float]) -> float:
    return float(np.std(values, ddof=1)) if len(values) >= 2 else 0.0


# 95% 正态分位。评估口径统一用 1.96,避免各处各写一个魔数。
Z95 = 1.96


def wilson_interval(successes: int, n: int, z: float = Z95) -> tuple:
    """胜率的 Wilson 区间。小样本(20~50 局)下比正态近似可靠得多。

    依据 docs/train_report_com3_full.md §11.3:20 局样本的区间宽约 ±22 个百分点,
    这正是"41% / 40% / 45%"这些数字互相无法区分的根本原因。
    """
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * float(np.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def mean_interval(values: Sequence[float], z: float = Z95) -> tuple:
    """均值的正态近似区间(n<2 时返回 (0,0))。"""
    n = len(values)
    if n < 2:
        return (0.0, 0.0)
    m = float(np.mean(values))
    half = z * float(np.std(values, ddof=1)) / float(np.sqrt(n))
    return (m - half, m + half)


def proportions_diff_interval(a_wins: int, a_n: int, b_wins: int, b_n: int,
                              z: float = Z95) -> Optional[Dict]:
    """两组独立胜率之差的区间(未合并方差)。区间含 0 ⇒ 差异不显著。

    评估是"跑完 A 再跑 B",天然非配对;这里按独立两比例处理,
    比直接比两个点估计诚实。
    """
    if a_n <= 0 or b_n <= 0:
        return None
    p1, p2 = a_wins / a_n, b_wins / b_n
    diff = p1 - p2
    se = float(np.sqrt(p1 * (1.0 - p1) / a_n + p2 * (1.0 - p2) / b_n))
    lo, hi = diff - z * se, diff + z * se
    return {"diff": diff, "lo": lo, "hi": hi, "se": se, "significant": lo > 0 or hi < 0}


def matches_needed(p: float, half_width: float, z: float = Z95) -> int:
    """要把胜率区间压到 ±half_width,大约需要多少局(p 取经验值)。"""
    if not 0.0 < p < 1.0 or half_width <= 0:
        return 0
    return int(np.ceil(z * z * p * (1.0 - p) / (half_width * half_width)))


def summarize(records: Sequence[MatchRecord], com_level: Optional[int] = None) -> Dict:
    records = list(records)
    total = len(records)
    wins = sum(1 for r in records if r.result == "win")
    losses = sum(1 for r in records if r.result == "lose")
    draws = sum(1 for r in records if r.result == "draw")
    decided = wins + losses
    ko_wins = sum(1 for r in records if r.result == "win" and r.kind == "ko")
    if com_level is None:
        com_level = records[0].com_level if records else 0
    return {
        "com_level": int(com_level) + 1,             # 对外口径 COM1..COM5
        "matches": total,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "decided": decided,
        "win_rate": (wins / decided) if decided else 0.0,
        "win_rate_ci": list(wilson_interval(wins, decided)),
        "draw_rate": (draws / total) if total else 0.0,
        "ko_wins": ko_wins,
        "time_wins": wins - ko_wins,
        "ko_rate": (ko_wins / wins) if wins else 0.0,
        "mean_hp_diff": _mean([r.hp_diff for r in records]),
        "mean_hp_diff_ci": list(mean_interval([r.hp_diff for r in records])),
        "mean_frames": _mean([r.frames for r in records]),
        "mean_secs": _mean([r.secs for r in records]),
        "mean_return": _mean([r.ret for r in records]),
        "tags_per_match": _mean([r.tags for r in records]),
        "guard_breaks_per_match": _mean([r.guard_breaks for r in records]),
        "combo_per_match": _mean([float((r.counts or {}).get("combo", 0) or 0) for r in records]),
        "opp_combo_per_match": _mean([float((r.counts or {}).get("opp_combo", 0) or 0) for r in records]),
        "combo_diff_per_match": _mean([float((r.counts or {}).get("combo", 0) or 0)
                                       - float((r.counts or {}).get("opp_combo", 0) or 0) for r in records]),
        "combo_diff_win": _mean([float((r.counts or {}).get("combo", 0) or 0)
                                 - float((r.counts or {}).get("opp_combo", 0) or 0)
                                 for r in records if r.result == "win"]),
        "combo_diff_lose": _mean([float((r.counts or {}).get("combo", 0) or 0)
                                  - float((r.counts or {}).get("opp_combo", 0) or 0)
                                  for r in records if r.result == "lose"]),
        # 胜负分组差值的实测区间(独立两样本,拒绝"假设 sd=10"的手算 z):
        # 按胜负分组是在结果上选择,只能读相关不能读因果。
        "combo_diff_win_ci": list(mean_interval([float((r.counts or {}).get("combo", 0) or 0)
                                                 - float((r.counts or {}).get("opp_combo", 0) or 0)
                                                 for r in records if r.result == "win"])),
        "combo_diff_lose_ci": list(mean_interval([float((r.counts or {}).get("combo", 0) or 0)
                                                  - float((r.counts or {}).get("opp_combo", 0) or 0)
                                                  for r in records if r.result == "lose"])),
    }


def _ci_text(ci: Optional[Sequence[float]], scale: float = 1.0,
             fmt: str = ".1f", suffix: str = "") -> str:
    """把区间格式化成 `lo ~ hi`;缺失时给 `-`,避免报告里出现 `None`。"""
    if not ci or len(ci) != 2:
        return "-"
    lo, hi = float(ci[0]) * scale, float(ci[1]) * scale
    return f"{lo:{fmt}} ~ {hi:{fmt}}{suffix}"


def compare_summaries(old: Dict, new: Dict, z: float = Z95) -> Dict:
    """比较两份评估结果,给出差值区间与"是否显著"的结论。

    评估是顺序跑的(不配对),所以用独立两比例的差值区间:
    **区间跨 0 就不能说"变强了/变弱了"**,这正是本仓库过去反复踩的坑。
    """
    a_n = int(old.get("decided") or old.get("matches") or 0)
    b_n = int(new.get("decided") or new.get("matches") or 0)
    a_w = int(old.get("wins") or 0)
    b_w = int(new.get("wins") or 0)
    stat = proportions_diff_interval(b_w, b_n, a_w, a_n, z=z)
    hp_a = float(old.get("mean_hp_diff") or 0.0)
    hp_b = float(new.get("mean_hp_diff") or 0.0)
    out = {"old": {"wins": a_w, "n": a_n, "win_rate": (a_w / a_n) if a_n else 0.0},
           "new": {"wins": b_w, "n": b_n, "win_rate": (b_w / b_n) if b_n else 0.0},
           "hp_diff_delta": hp_b - hp_a,
           "diff": stat}
    if stat is None:
        out["verdict"] = "样本不足,无法比较"
    elif stat["significant"]:
        out["verdict"] = ("显著变强" if stat["diff"] > 0 else "显著变弱") + \
            f"(差值 {stat['diff'] * 100:+.1f}pp,95% CI 不含 0)"
    else:
        out["verdict"] = (f"无显著差异(差值 {stat['diff'] * 100:+.1f}pp,"
                          f"95% CI {stat['lo'] * 100:+.1f} ~ {stat['hi'] * 100:+.1f}pp 跨 0)")
    return out


def to_markdown(summary: Dict, ckpt: Optional[str] = None) -> str:
    lines = [
        "## th155 评估报告",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| checkpoint | `{ckpt or '-'}` |",
        f"| COM 难度 | COM{summary['com_level']} |",
        f"| 局数 | {summary['matches']} |",
        f"| 胜率 (分出胜负) | {summary['win_rate'] * 100:.1f}% "
        f"(95% CI {_ci_text(summary.get('win_rate_ci'), 100.0, '.1f', '%')}) |",
        f"| 平局/超时率 | {summary['draw_rate'] * 100:.1f}% |",
        f"| 战绩 | {summary['wins']}胜 {summary['losses']}负 {summary['draws']}平 |",
        f"| KO 胜占比 | {summary['ko_rate'] * 100:.1f}% "
        f"({summary['ko_wins']} KO / {summary['time_wins']} 计时) |",
        f"| 平均实血差 | {summary['mean_hp_diff']:+.0f} "
        f"(95% CI {_ci_text(summary.get('mean_hp_diff_ci'), 1.0, '+.0f', '')}) |",
        f"| 平均回合帧数 | {summary['mean_frames']:.0f} |",
        f"| 平均时长 | {summary['mean_secs']:.1f} s |",
        f"| 场均换人 | {summary['tags_per_match']:.2f} |",
        f"| 场均破防 | {summary['guard_breaks_per_match']:.2f} |",
        f"| 场均连段差 | {summary.get('combo_diff_per_match', 0.0):+.2f} "
        f"(我方 {summary.get('combo_per_match', 0.0):.1f} / 对手 {summary.get('opp_combo_per_match', 0.0):.1f}) |",
        f"| 平均回报 | {summary['mean_return']:+.3f} |",
        "",
    ]
    return "\n".join(lines)


def write_reports(summary: Dict, out_dir: str = DEFAULT_OUT_DIR,
                  ckpt: Optional[str] = None) -> tuple:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"eval_com{summary['com_level']}"
    md_path = out / f"{tag}.md"
    js_path = out / f"{tag}.json"
    md_path.write_text(to_markdown(summary, ckpt), encoding="utf-8")
    js_path.write_text(json.dumps({"checkpoint": ckpt, **summary},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    return str(md_path), str(js_path)


# ---------------------------------------------------------------------------
# 跑局(确定性策略)
# ---------------------------------------------------------------------------
def play_match(net, env, obs, info: dict, com_level: int = 0,
               deterministic: bool = True) -> MatchRecord:
    """从已 reset 的观测打完整一局,返回统计记录。不做 reset。"""
    import torch

    from agents.ppo_agent import to_batch
    from env import action_space

    hidden = net.init_hidden(1)
    total, steps, tags, guard_breaks = 0.0, 0, 0, 0
    team = int(getattr(env, "team", 0) or 0)
    prev_sp = _sp_max(info, team)
    while True:
        with torch.inference_mode():
            batch = {k: v.unsqueeze(0) for k, v in to_batch(obs).items()}
            logits, _, hidden = net.forward(batch, hidden, seq=False)
            if not deterministic:
                tau = getattr(net, "sampling_temperature", 1.0)
                if tau != 1.0 and tau > 0:
                    logits = logits / tau
            dist = net.masked_dist(logits, batch["action_mask"])
            # 训练时的胜率来自**采样**策略;确定性 argmax 会低估会随机应变的打法,
            # 评估两种口径都支持,默认仍用原来的确定性口径以保持历史可比。
            cand = dist.logits if getattr(dist, "logits", None) is not None else dist.probs
            action = int(cand.argmax(-1)) if deterministic else int(dist.sample())
        obs, reward, term, trunc, info = env.step(action)
        total += float(reward)
        steps += 1
        _, btn, _, _ = action_space.decode(int(action))
        if btn >= 0 and btn < len(action_space.BUTTONS) and action_space.BUTTONS[btn] == "Tag":
            tags += 1
        cur_sp = _sp_max(info, team)
        if prev_sp is not None and cur_sp is not None and cur_sp < prev_sp:
            guard_breaks += 1
        prev_sp = cur_sp
        if term or trunc:
            break

    snap = (info.get("state_snapshot") or {}).get("teams") or []
    lives = [t.get("life") for t in snap]
    me, opp = (lives[team], lives[1 - team]) if len(lives) == 2 else (0, 0)
    repeat = float(getattr(env, "action_repeat", 2) or 2)
    return MatchRecord(
        result=episode_result(info),
        kind=episode_kind(info),
        frames=steps,
        secs=steps * repeat / 60.0,
        hp_diff=int((me or 0) - (opp or 0)),
        tags=tags,
        guard_breaks=guard_breaks,
        com_level=int(com_level),
        state=int(info.get("battle_state", 0) or 0),
        lives=[v for v in lives],
        ret=total,
        counts=dict(info.get("combat_counts") or {}),
    )


def play_matches(net, env, matches: int, com_level: int = 0,
                 on_record: Optional[Callable[[int, MatchRecord], None]] = None,
                 deterministic: bool = True) -> List[MatchRecord]:
    """跑 `matches` 局(每局先 reset)。

    难度**不在对局中改**:`eval.main` 会先把目标难度写进配置并重启游戏,
    让对局通过标题菜单以正确难度开始(对局中写 com_* 会崩,见 docs/re_notes.md §10)。
    """
    out: List[MatchRecord] = []
    for i in range(matches):
        obs, info = env.reset()
        rec = play_match(net, env, obs, info, com_level=com_level,
                         deterministic=deterministic)
        out.append(rec)
        if on_record is not None:
            on_record(i, rec)
    return out


def _sp_max(info: dict, team: int) -> Optional[float]:
    teams = (info.get("state_snapshot") or {}).get("teams") or []
    if len(teams) < 2:
        return None
    value = teams[1 - team].get("sp_max")        # 只看对手:被削 = 破防
    return None if value is None else float(value)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="th155 标准化评估")
    ap.add_argument("--ckpt", default=None, help="Checkpoint 路径(--compare 时不需要)")
    ap.add_argument("--com-level", type=int, default=1, choices=range(1, 6),
                    help="目标难度 1..5(= 内部 0..4)")
    ap.add_argument("--matches", type=int, default=50, help="评估局数")
    ap.add_argument("--out", default=DEFAULT_OUT_DIR, help="报告输出目录")
    ap.add_argument("--config", default=None)
    ap.add_argument("--action-repeat", type=int, default=None)
    ap.add_argument("--sample", action="store_true",
                    help="采样口径(默认 argmax)。两种口径在 COM3 上实测无显著差异,"
                         "见 docs/train_report_com3_full.md §11.3")
    ap.add_argument("--temperature", type=float, default=1.0,
                    help="采样温度(仅 --sample 生效;须 >0)")
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"),
                    help="比较两份评估 JSON,直接给出差值区间与显著性,不跑对局")
    ap.add_argument("--no-restart", action="store_true",
                    help="复用当前对局(难度可能不是 --com-level 指定的那个,会打印警告)")
    args = ap.parse_args(argv)

    if args.compare:
        old = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        new = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        cmp = compare_summaries(old, new)
        print(f"[old] {cmp['old']['wins']}/{cmp['old']['n']} = "
              f"{cmp['old']['win_rate'] * 100:.1f}%  hp_diff={old.get('mean_hp_diff', 0):+.0f}")
        print(f"[new] {cmp['new']['wins']}/{cmp['new']['n']} = "
              f"{cmp['new']['win_rate'] * 100:.1f}%  hp_diff={new.get('mean_hp_diff', 0):+.0f}")
        print(f"[结论] {cmp['verdict']}", flush=True)
        return 0
    if not args.ckpt:
        ap.error("--ckpt 必填(除非使用 --compare)")
    if args.temperature <= 0:
        ap.error("--temperature 必须 > 0")

    import torch

    from agents.ppo_agent import PolicyNet, load_weights
    from train.train_ppo import (build_env, load_config, restart_game,
                                 reset_with_restart, switch_difficulty,
                                 _live_com_difficulty)

    internal = int(args.com_level) - 1
    cfg = load_config(args.config)
    cfg.setdefault("env", {})["difficulty"] = internal   # 只在重进对局时生效
    if not args.no_restart:
        # 先走便宜路径:暂停菜单回标题重进(不重启进程);失败才杀进程重启
        if switch_difficulty(internal):
            print("[i] 已回标题按目标难度重进对局(未重启进程)", flush=True)
        elif not restart_game():
            print("[!] 重启游戏失败,尝试在当前对局上评估", flush=True)
    env = build_env(cfg, args.action_repeat)

    state = torch.load(args.ckpt, map_location="cpu")
    sd = state.get("model_state_dict") or state.get("model")
    if sd is None:
        ap.error(f"Checkpoint {args.ckpt} 缺少 model_state_dict 或 model 字段")
    net = PolicyNet(cfg.get("network") or {})
    load_weights(net, sd, verbose=True)
    # 采样温度只在采样口径下有意义;argmax 不受影响(见 PolicyNet.act)。
    net.sampling_temperature = float(args.temperature)
    net.eval()
    print(f"[i] ckpt={args.ckpt} (update={state.get('update')}) COM{args.com_level} "
          f"× {args.matches} 局 口径={'sample' if args.sample else 'argmax'}"
          + (f"(tau={args.temperature:g})" if args.sample else ""), flush=True)

    def on_record(i: int, rec: MatchRecord) -> None:
        print(f"[eval {i + 1:3d}/{args.matches}] {rec.result:5s} by={rec.kind:4s} "
              f"life={rec.lives} frames={rec.frames} {rec.secs:5.1f}s "
              f"hp={rec.hp_diff:+d} tag={rec.tags} gb={rec.guard_breaks}",
              flush=True)

    def _warn_if_wrong_level(tag: str) -> None:
        got = _live_com_difficulty(env)
        if got is not None and got != internal:
            print(f"[w] {tag}:实机对手是 COM{got + 1},而 --com-level={args.com_level};"
                  f"本次统计与难度标签不符", flush=True)

    records: List[MatchRecord] = []
    try:
        env, _ = reset_with_restart(cfg, env, args.action_repeat, "评估开始",
                                    fix_difficulty=not args.no_restart)
        _warn_if_wrong_level("评估开始")
        records = play_matches(net, env, args.matches, com_level=internal,
                               on_record=on_record,
                               deterministic=not args.sample)
    except Exception as exc:                    # noqa: BLE001 - 卡死时重启再试一次
        print(f"[w] 评估中断({type(exc).__name__}: {exc}),重启游戏后重试剩余局数",
              flush=True)
        try:
            env.close()
        except Exception:                       # noqa: BLE001
            pass
        if not restart_game():
            raise
        env = build_env(cfg, args.action_repeat)
        remain = args.matches - len(records)
        env, _ = reset_with_restart(cfg, env, args.action_repeat, "评估重试",
                                    fix_difficulty=not args.no_restart)
        _warn_if_wrong_level("评估重试")
        records += play_matches(net, env, remain, com_level=internal,
                                on_record=on_record,
                                deterministic=not args.sample)
    finally:
        try:
            env.close()
        except Exception:                       # noqa: BLE001
            pass

    summary = summarize(records, com_level=internal)
    md = to_markdown(summary, args.ckpt)
    print(md, flush=True)
    md_path, js_path = write_reports(summary, args.out, args.ckpt)
    print(f"[ok] 报告: {md_path} / {js_path}", flush=True)
    half = (summary["win_rate_ci"][1] - summary["win_rate_ci"][0]) / 2.0
    if half > 0.10:
        need = matches_needed(summary["win_rate"], 0.05)
        print(f"[w] 区间半宽 {half * 100:.1f}pp > 10pp:这个样本量不足以比较 5~10 个百分点的差异。"
              f"要把区间压到 ±5pp 约需 {need} 局。", flush=True)
    return 0 if summary["matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
