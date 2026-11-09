"""守护进程确定性评估锚定与早停/回滚安全熔断机制 (Ticket 05).

核心职责:
1. 解析 Checkpoint 元数据中的 best_eval_win_rate, eval_win_rate, 95% Wilson 置信区间与评估轮次。
2. 守护监控基准锚定于客观确定性评估指标, 杜绝发散权重持续污染后续训练。
3. 熔断与回滚逻辑 (Circuit Breaker & Rollback):
   - 若当前轮次的确定性评估胜率较历史最佳 best.pt 发生显著衰退 (胜率下降 >= 15pp 或 Wilson 区间完全不重叠),
     或连续 2 轮对局无任何正向收益:
     - 自动触发熔断机制;
     - 隔离发散断点 (quarantine diverged checkpoints), 避免污染后续断点检索与训练;
     - 将下一轮启动的 --resume 目标强制回滚重置为经审计的 best.pt;
     - 若回滚后仍无改善, 生成 STOP_TRAINING 标志文件并安全退出, 记录报警日志至 watchdog.log。
4. 记录历史最高胜率与最近 N 轮评估趋势。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch

from train.eval import wilson_interval


@dataclass
class CheckpointMetrics:
    """从 checkpoint 中解析提取的评估元数据指标。"""
    path: Path
    update: int = 0
    global_step: int = 0
    best_eval_win_rate: float = 0.0
    eval_win_rate: float = 0.0
    eval_ci_low: float = 0.0
    eval_ci_high: float = 1.0
    eval_episodes: int = 0
    is_valid: bool = True
    error_msg: str = ""


def load_checkpoint_metrics(
    path: Path | str,
    retries: int = 3,
    retry_delay: float = 0.5,
) -> CheckpointMetrics:
    """安全读取 checkpoint 中的确定性评估元数据, 具备全路径错误容忍、重试与旧版兼容性。"""
    ckpt_path = Path(path)
    if not ckpt_path.exists():
        return CheckpointMetrics(path=ckpt_path, is_valid=False, error_msg=f"File not found: {ckpt_path}")

    data = None
    last_exc = None
    for attempt in range(max(1, retries)):
        try:
            data = torch.load(ckpt_path, map_location="cpu", weights_only=False)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(retry_delay)

    if last_exc is not None or data is None:
        return CheckpointMetrics(path=ckpt_path, is_valid=False, error_msg=f"Failed to load checkpoint: {last_exc}")

    if not isinstance(data, dict):
        return CheckpointMetrics(
            path=ckpt_path,
            is_valid=False,
            error_msg=f"Checkpoint root must be a dict, got {type(data).__name__}",
        )

    # 1. 提取 best_eval_win_rate(向后兼容 best_win_rate, win_rate)
    raw_best_wr = data.get("best_eval_win_rate")
    if raw_best_wr is None:
        raw_best_wr = data.get("best_win_rate")
    if raw_best_wr is None:
        raw_best_wr = data.get("win_rate", 0.0)
    try:
        best_eval_win_rate = float(raw_best_wr) if raw_best_wr is not None else 0.0
    except (ValueError, TypeError):
        best_eval_win_rate = 0.0

    # 2. 提取当前轮次实际 eval_win_rate
    raw_eval_wr = data.get("eval_win_rate")
    if raw_eval_wr is None:
        raw_eval_wr = data.get("win_rate")
    if raw_eval_wr is None:
        raw_eval_wr = best_eval_win_rate
    try:
        eval_win_rate = float(raw_eval_wr) if raw_eval_wr is not None else best_eval_win_rate
    except (ValueError, TypeError):
        eval_win_rate = best_eval_win_rate

    # 3. 提取评估局数与更新步数
    try:
        episodes = int(data.get("eval_episodes", 0) or 0)
    except (ValueError, TypeError):
        episodes = 0

    try:
        update = int(data.get("update", 0) or 0)
    except (ValueError, TypeError):
        update = 0

    try:
        global_step = int(data.get("global_step", 0) or 0)
    except (ValueError, TypeError):
        global_step = 0

    # 4. 提取或重算 Wilson 置信区间
    ci_low_val = data.get("eval_ci_low")
    ci_high_val = data.get("eval_ci_high")

    if ci_low_val is not None and ci_high_val is not None:
        try:
            ci_low = float(ci_low_val)
            ci_high = float(ci_high_val)
        except (ValueError, TypeError):
            ci_low, ci_high = 0.0, 1.0
    elif episodes > 0 and 0.0 <= eval_win_rate <= 1.0:
        # 若保存时未固化 CI 但有评估局数, 按 Ticket 01 统一标准补全 Wilson 区间
        wins = int(round(eval_win_rate * episodes))
        ci_low, ci_high = wilson_interval(wins, episodes)
    else:
        ci_low, ci_high = 0.0, 1.0

    return CheckpointMetrics(
        path=ckpt_path,
        update=update,
        global_step=global_step,
        best_eval_win_rate=best_eval_win_rate,
        eval_win_rate=eval_win_rate,
        eval_ci_low=ci_low,
        eval_ci_high=ci_high,
        eval_episodes=episodes,
        is_valid=True,
    )


def quarantine_diverged_checkpoints(
    ckpt_dir: Path | str,
    keep_up_to_update: int,
    quarantine_subdir: str = "quarantine_diverged",
) -> List[Path]:
    """将高于 keep_up_to_update 的发散断点隔离归档, 杜绝毒药权重持续污染断点检索。

    参数:
        ckpt_dir: Checkpoint 根目录
        keep_up_to_update: 经审计有效的最大 update 编号 (通常来自回滚目标 best.pt)
        quarantine_subdir: 隔离子目录名称 (默认 'quarantine_diverged')

    返回:
        被隔离归档的文件原始路径列表
    """
    ckpt_dir = Path(ckpt_dir)
    if not ckpt_dir.exists():
        return []

    target_dir = ckpt_dir / quarantine_subdir
    moved: List[Path] = []
    for p in list(ckpt_dir.glob("ppo_*.pt")):
        stem = p.stem.split("_")[-1]
        if stem.isdigit():
            u = int(stem)
            if u > keep_up_to_update:
                target_dir.mkdir(parents=True, exist_ok=True)
                dest = target_dir / p.name
                p.rename(dest)
                moved.append(p)
    return moved


def find_latest_checkpoint(
    ckpt_dir: Path | str,
    prefer_mtime: bool = False,
) -> Optional[Path]:
    """查找最新 checkpoint。

    若 prefer_mtime 为 True, 优先按文件最后修改时间排序;
    否则默认按 update 编号排序。
    没有 ppo_*.pt 时退回 best.pt。
    """
    ckpt_dir = Path(ckpt_dir)
    if not ckpt_dir.exists():
        return None

    candidates = []
    for p in ckpt_dir.glob("ppo_*.pt"):
        stem = p.stem.split("_")[-1]
        if stem.isdigit():
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((int(stem), mtime, p))

    b = ckpt_dir / "best.pt"
    if b.exists():
        bm = load_checkpoint_metrics(b)
        if bm.is_valid and bm.update > 0:
            try:
                bmtime = b.stat().st_mtime
            except OSError:
                bmtime = 0.0
            candidates.append((bm.update, bmtime, b))

    if candidates:
        if prefer_mtime:
            candidates.sort(key=lambda item: (item[1], item[0]))
        else:
            candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[-1][2]

    b = ckpt_dir / "best.pt"
    return b if b.exists() else None


def find_best_checkpoint(ckpt_dir: Path | str) -> Optional[Path]:
    """查找经审计的 best.pt; 若不存在则回退至拥有最高 best_eval_win_rate 的 ppo_*.pt。"""
    ckpt_dir = Path(ckpt_dir)
    if not ckpt_dir.exists():
        return None
    b = ckpt_dir / "best.pt"
    if b.exists():
        return b
    best_candidate, best_wr = None, -1.0
    for p in ckpt_dir.glob("ppo_*.pt"):
        m = load_checkpoint_metrics(p)
        if m.is_valid and m.best_eval_win_rate > best_wr:
            best_candidate, best_wr = p, m.best_eval_win_rate
    return best_candidate


@dataclass
class CircuitBreakerDecision:
    """熔断决策结果。"""
    action: str  # "CONTINUE", "ROLLBACK", "STOP", "RETRY"
    reason: str = ""
    target_ckpt: Optional[Path] = None
    current_win_rate: float = 0.0
    best_win_rate: float = 0.0
    decay_amount: float = 0.0
    consecutive_decays: int = 0
    is_rolled_back: bool = False
    metrics: Optional[CheckpointMetrics] = None


class WatchdogCircuitBreaker:
    """守护进程安全熔断器。

    跟踪历史最佳基准胜率与置信区间, 在模型发生指标劣化或无收益停滞时自动回滚,
    并在连续衰退失控时生成 STOP_TRAINING 标志文件安全停机。
    """

    def __init__(
        self,
        decay_threshold: float = 0.20,
        consecutive_decay_limit: int = 3,
        baseline_win_rate: float = 0.0,
        max_crashes: int = 3,
        stop_file: Optional[Path] = None,
        log_file: Optional[Path] = None,
    ):
        self.decay_threshold = float(decay_threshold)
        self.consecutive_decay_limit = int(consecutive_decay_limit)
        self.baseline_win_rate = float(baseline_win_rate)
        self.max_crashes = int(max_crashes)
        self.consecutive_crashes: int = 0
        self.best_ci_low: float = 0.0
        self.best_ci_high: float = 1.0
        self.consecutive_decays: int = 0
        self.is_rolled_back: bool = False
        self.total_rollbacks: int = 0
        self.stop_file = Path(stop_file) if stop_file is not None else None
        self.log_file = Path(log_file) if log_file is not None else None
        self.history: List[Dict] = []

    def _format_msg(self, msg: str) -> str:
        return f"[{time.strftime('%H:%M:%S')}] {msg}"

    def log(self, msg: str) -> None:
        line = self._format_msg(msg)
        print(line, flush=True)
        if self.log_file is not None:
            try:
                self.log_file.parent.mkdir(parents=True, exist_ok=True)
                with self.log_file.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def write_stop_file(self, reason: str = "") -> None:
        """生成 STOP_TRAINING 标志文件。"""
        if self.stop_file is not None:
            try:
                self.stop_file.parent.mkdir(parents=True, exist_ok=True)
                content = f"STOP_TRAINING: {reason}\n" if reason else "stop\n"
                self.stop_file.write_text(content, encoding="utf-8")
            except Exception as e:
                self.log(f"[WARN] 写入 STOP_TRAINING 失败: {e}")

    def get_recent_trend(self, n: int = 5) -> List[Dict]:
        """获取最近 N 轮评估趋势记录。"""
        return self.history[-n:] if n > 0 else list(self.history)

    def evaluate_round(
        self,
        current_metrics: CheckpointMetrics,
        best_metrics: Optional[CheckpointMetrics] = None,
        fallback_best_path: Optional[Path] = None,
    ) -> CircuitBreakerDecision:
        """在每轮训练结束后评估指标健康度, 作出续训、回滚或停机决策。"""
        # 1. 确定经审计的 best.pt 路径与初始基线
        best_ckpt_path = None
        if best_metrics is not None and best_metrics.is_valid:
            best_ckpt_path = best_metrics.path
            # 只信 best_eval_win_rate(经 CI 门控写入 best.pt 的值);
            # 若混入本轮 eval_win_rate, 一次 30~50 局的幸运采样就会永久抬高 baseline。
            effective_best_wr = best_metrics.best_eval_win_rate
            if self.baseline_win_rate <= 0.0 or effective_best_wr > self.baseline_win_rate:
                self.baseline_win_rate = effective_best_wr
            if best_metrics.eval_ci_low > 0.0 or best_metrics.eval_ci_high < 1.0:
                self.best_ci_low = best_metrics.eval_ci_low
                self.best_ci_high = best_metrics.eval_ci_high
        elif fallback_best_path is not None:
            best_ckpt_path = Path(fallback_best_path)

        # 容错: 若当前 checkpoint 损坏或不可读
        if not current_metrics.is_valid:
            err_msg = f"Checkpoint 损坏或不可读: {current_metrics.error_msg}"
            self.log(f"[WARN] {err_msg}")
            if self.is_rolled_back:
                stop_reason = f"熔断停机: 回滚后产出断点损坏无法读取 ({current_metrics.error_msg})"
                self.write_stop_file(reason=stop_reason)
                self.log(f"[FATAL CIRCUIT BREAKER] {stop_reason}")
                dec = CircuitBreakerDecision(
                    action="STOP",
                    reason=stop_reason,
                    target_ckpt=best_ckpt_path,
                    current_win_rate=0.0,
                    best_win_rate=self.baseline_win_rate,
                    decay_amount=0.0,
                    consecutive_decays=self.consecutive_decays,
                    is_rolled_back=True,
                    metrics=current_metrics,
                )
                self.history.append({"time": time.time(), "metrics": current_metrics, "decision": dec})
                return dec

            if best_ckpt_path is not None and best_ckpt_path.exists():
                self.is_rolled_back = True
                self.total_rollbacks += 1
                rollback_reason = f"触发熔断回滚: {err_msg}, 下一轮续训强制回滚至 {best_ckpt_path.name}"
                self.log(f"[CIRCUIT BREAKER] {rollback_reason}")
                dec = CircuitBreakerDecision(
                    action="ROLLBACK",
                    reason=rollback_reason,
                    target_ckpt=best_ckpt_path,
                    current_win_rate=0.0,
                    best_win_rate=self.baseline_win_rate,
                    decay_amount=0.0,
                    consecutive_decays=self.consecutive_decays,
                    is_rolled_back=True,
                    metrics=current_metrics,
                )
                self.history.append({"time": time.time(), "metrics": current_metrics, "decision": dec})
                return dec
            else:
                stop_reason = f"熔断停机: {err_msg}, 且无有效历史最佳断点可供回滚"
                self.write_stop_file(reason=stop_reason)
                dec = CircuitBreakerDecision(
                    action="STOP",
                    reason=stop_reason,
                    target_ckpt=None,
                    current_win_rate=0.0,
                    best_win_rate=self.baseline_win_rate,
                    decay_amount=0.0,
                    consecutive_decays=self.consecutive_decays,
                    is_rolled_back=self.is_rolled_back,
                    metrics=current_metrics,
                )
                self.history.append({"time": time.time(), "metrics": current_metrics, "decision": dec})
                return dec

        if best_ckpt_path is None:
            best_ckpt_path = current_metrics.path

        prev_baseline = self.baseline_win_rate
        curr_wr = current_metrics.eval_win_rate

        # 2. 判断是否创新高或成功恢复 (>= baseline)
        if curr_wr >= prev_baseline and prev_baseline > 0.0:
            # 恢复基线时保持 prev_baseline。上面已用 best.pt 的 best_eval_win_rate
            # 做过一次权威抬升; 这里若再读当前 checkpoint 的 best_eval, 会把
            # 重基线前残留的 0.8667 重新灌回 baseline。
            self.baseline_win_rate = prev_baseline
            if current_metrics.eval_ci_low > 0.0 or current_metrics.eval_ci_high < 1.0:
                self.best_ci_low = current_metrics.eval_ci_low
                self.best_ci_high = current_metrics.eval_ci_high
            self.consecutive_decays = 0
            self.is_rolled_back = False

            action_desc = "创新高" if curr_wr > prev_baseline else "恢复基线"
            decision = CircuitBreakerDecision(
                action="CONTINUE",
                reason=f"指标{action_desc}: 胜率={curr_wr * 100:.1f}%, 保持正常续训",
                target_ckpt=current_metrics.path,
                current_win_rate=curr_wr,
                best_win_rate=self.baseline_win_rate,
                decay_amount=0.0,
                consecutive_decays=0,
                is_rolled_back=False,
                metrics=current_metrics,
            )
            self.history.append({"time": time.time(), "metrics": current_metrics, "decision": decision})
            return decision

        # 若此前尚未建立有效 baseline (例如冷启动第 1 轮), 则以当前指标为初始基线
        if self.baseline_win_rate <= 0.0:
            self.baseline_win_rate = max(curr_wr, current_metrics.best_eval_win_rate)
            if current_metrics.eval_ci_low > 0.0 or current_metrics.eval_ci_high < 1.0:
                self.best_ci_low = current_metrics.eval_ci_low
                self.best_ci_high = current_metrics.eval_ci_high
            decision = CircuitBreakerDecision(
                action="CONTINUE",
                reason=f"初始化基线胜率: {self.baseline_win_rate * 100:.1f}%",
                target_ckpt=current_metrics.path,
                current_win_rate=curr_wr,
                best_win_rate=self.baseline_win_rate,
                decay_amount=0.0,
                consecutive_decays=0,
                is_rolled_back=False,
                metrics=current_metrics,
            )
            self.history.append({"time": time.time(), "metrics": current_metrics, "decision": decision})
            return decision

        # 3. 未能达到基线: 计算衰退幅度并检查熔断条件
        decay = max(0.0, self.baseline_win_rate - curr_wr)

        # 触发条件 A: 较最佳单轮显著衰退 (默认 >= 20pp)
        decay_significant = (decay >= self.decay_threshold - 1e-9)

        # 触发条件 B: Wilson 置信区间完全不重叠 (当前 CI 上限 < 最佳 CI 下限, p < 0.05 统计显著)
        has_best_ci = (self.best_ci_low > 0.0) or (best_metrics is not None and best_metrics.eval_episodes > 0)
        has_curr_ci = (current_metrics.eval_episodes > 0 and current_metrics.eval_ci_high < 1.0)
        disjoint_ci = bool(has_best_ci and has_curr_ci and (current_metrics.eval_ci_high < self.best_ci_low - 1e-9))

        # 触发条件 C: 连续多轮温和衰退 (每轮至少 threshold/2, 默认 10pp)
        # 单轮 30~50 局 sample eval 的 95% CI 约 ±12~17pp; 任意"低于基线"都算衰退
        # 会让 59% 的真实策略在 2 轮内被误杀。这里只统计有实际幅度的连续衰退。
        sustained_margin = self.decay_threshold * 0.5
        sustained_decay = (decay >= sustained_margin - 1e-9)
        if sustained_decay or disjoint_ci:
            self.consecutive_decays += 1
        else:
            self.consecutive_decays = 0
        consecutive_limit_reached = (self.consecutive_decays >= self.consecutive_decay_limit)

        is_trigger = decay_significant or disjoint_ci or consecutive_limit_reached

        if is_trigger:
            trigger_reasons = []
            if decay_significant:
                trigger_reasons.append(f"胜率严重下降 {decay * 100:.1f}pp (>= {self.decay_threshold * 100:.0f}pp)")
            if disjoint_ci:
                trigger_reasons.append(
                    f"Wilson 区间完全不重叠 (当前 [{current_metrics.eval_ci_low * 100:.1f}%, {current_metrics.eval_ci_high * 100:.1f}%] "
                    f"< 历史最佳下限 {self.best_ci_low * 100:.1f}%)"
                )
            if consecutive_limit_reached and not (decay_significant or disjoint_ci):
                trigger_reasons.append(
                    f"连续 {self.consecutive_decays} 轮温和衰退 (每轮 >= {sustained_margin * 100:.1f}pp)"
                )

            trigger_desc = "; ".join(trigger_reasons)

            # 4. 判断当前是否已处于回滚状态
            if self.is_rolled_back:
                # 已经回滚至 best.pt 运行了一轮, 但依然无法改善 -> 触发终极熔断停机
                stop_reason = (
                    f"熔断停机: 回滚至 {best_ckpt_path.name} 后仍无改善 "
                    f"(当前胜率 {curr_wr * 100:.1f}% vs 最佳 {self.baseline_win_rate * 100:.1f}%); {trigger_desc}"
                )
                self.write_stop_file(reason=stop_reason)
                self.log(f"[FATAL CIRCUIT BREAKER] 生成 STOP_TRAINING 熔断停机: {stop_reason}")

                decision = CircuitBreakerDecision(
                    action="STOP",
                    reason=stop_reason,
                    target_ckpt=best_ckpt_path,
                    current_win_rate=curr_wr,
                    best_win_rate=self.baseline_win_rate,
                    decay_amount=decay,
                    consecutive_decays=self.consecutive_decays,
                    is_rolled_back=True,
                    metrics=current_metrics,
                )
                self.history.append({"time": time.time(), "metrics": current_metrics, "decision": decision})
                return decision

            # 首次触发劣化: 触发安全回滚
            if best_ckpt_path is None or (not best_ckpt_path.exists() and best_ckpt_path == current_metrics.path):
                stop_reason = f"熔断停机: {trigger_desc}, 且未找到可供回滚的有效 best.pt"
                self.write_stop_file(reason=stop_reason)
                self.log(f"[FATAL CIRCUIT BREAKER] {stop_reason}")
                decision = CircuitBreakerDecision(
                    action="STOP",
                    reason=stop_reason,
                    target_ckpt=None,
                    current_win_rate=curr_wr,
                    best_win_rate=self.baseline_win_rate,
                    decay_amount=decay,
                    consecutive_decays=self.consecutive_decays,
                    is_rolled_back=False,
                    metrics=current_metrics,
                )
                self.history.append({"time": time.time(), "metrics": current_metrics, "decision": decision})
                return decision

            self.is_rolled_back = True
            self.total_rollbacks += 1
            rollback_reason = (
                f"触发熔断回滚: {trigger_desc}, 下一轮续训强制回滚至经审计的 best.pt ({best_ckpt_path.name})"
            )
            self.log(f"[CIRCUIT BREAKER] {rollback_reason}")

            decision = CircuitBreakerDecision(
                action="ROLLBACK",
                reason=rollback_reason,
                target_ckpt=best_ckpt_path,
                current_win_rate=curr_wr,
                best_win_rate=self.baseline_win_rate,
                decay_amount=decay,
                consecutive_decays=self.consecutive_decays,
                is_rolled_back=True,
                metrics=current_metrics,
            )
            self.history.append({"time": time.time(), "metrics": current_metrics, "decision": decision})
            return decision

        # 5. 轻微波动在容忍范围内 (未达连续轮次上限且非显著衰退)
        continue_reason = (
            f"轻微衰退在容忍范围内 (第 {self.consecutive_decays}/{self.consecutive_decay_limit} 轮, "
            f"下降 {decay * 100:.1f}pp), 继续观察"
        )
        self.log(f"[WATCHDOG] {continue_reason}")

        decision = CircuitBreakerDecision(
            action="CONTINUE",
            reason=continue_reason,
            target_ckpt=current_metrics.path,
            current_win_rate=curr_wr,
            best_win_rate=self.baseline_win_rate,
            decay_amount=decay,
            consecutive_decays=self.consecutive_decays,
            is_rolled_back=False,
            metrics=current_metrics,
        )
        self.history.append({"time": time.time(), "metrics": current_metrics, "decision": decision})
        return decision
