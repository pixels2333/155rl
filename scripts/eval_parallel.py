"""多实例并行评估:把 N 局分给多个游戏实例并行打,输出与 .scratch/eval_com3.py 同口径的 JSON。

用法:
    python scripts/eval_parallel.py <config> <ckpt> <N> --num-envs 4            # 采样口径(与训练一致)
    python scripts/eval_parallel.py <config> <ckpt> <N> --num-envs 4 --det      # 确定性(argmax)
    python scripts/eval_parallel.py <config> <ckpt> <N> --num-envs 4 --temp 0.8 # 采样温度
    python scripts/eval_parallel.py <config> <ckpt> <N> --num-envs 3 --offline  # 合成环境冒烟

难度取配置 `env.difficulty`(缺省 2 = COM3);每个实例由 `th155.instances.launch_instance(slot)`
按 slot 启动,评估结束(含异常)时关闭全部 worker 与实例。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from train.eval import MatchRecord, summarize                   # noqa: E402
from train.parallel import GameProvider, OfflineProvider, evaluate_parallel  # noqa: E402
from train.train_ppo import load_config                         # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="th155 多实例并行评估")
    ap.add_argument("config")
    ap.add_argument("ckpt")
    ap.add_argument("episodes", type=int)
    ap.add_argument("--num-envs", type=int, required=True, help="并行实例数")
    ap.add_argument("--det", action="store_true", help="确定性(argmax)口径;默认按策略分布采样")
    ap.add_argument("--temp", type=float, default=None, help="采样温度(仅采样口径生效)")
    ap.add_argument("--offline", action="store_true", help="用合成环境,不启动游戏")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    state = torch.load(a.ckpt, map_location="cpu")
    sd = state.get("model_state_dict") or state.get("model")
    internal = int((cfg.get("env") or {}).get("difficulty", 2))
    if a.offline:
        provider = OfflineProvider(action_repeat=int((cfg.get("env") or {}).get("action_repeat", 2)),
                                   seed=int((cfg.get("train") or {}).get("seed", 0)))
    else:
        provider = GameProvider(cfg)

    def on_record(i: int, rec: MatchRecord) -> None:
        _c = rec.counts or {}
        print(f"[eval {i + 1}/{a.episodes}] {rec.result:5s} life={rec.lives} {rec.secs:5.1f}s "
              f"hp={rec.hp_diff:+d} ret={rec.ret:+.3f} "
              f"cmb={_c.get('combo', 0)} ocmb={_c.get('opp_combo', 0)} "
              f"cnt={_c.get('counter', 0)} ctr={_c.get('countered', 0)}", flush=True)

    recs = evaluate_parallel(cfg, sd, a.episodes, a.num_envs, provider,
                             com_level=internal, deterministic=a.det,
                             temperature=a.temp, on_record=on_record)
    out = summarize(recs, com_level=internal)
    out["deterministic"] = a.det
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
