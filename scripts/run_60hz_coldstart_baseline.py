"""60Hz 全感知全新冷启动基线训练与评估流水线 (Ticket 06).

功能:
1. 载入 configs/train_ppo_com3_60hz_coldstart.yaml 配置。
2. 从 update=0 开启全新冷启动训练 (正交初始化权重, 优化器从零构建)。
3. 在指定轮次 (默认 40 updates) 周期性执行确定性评估, 固化最佳 best.pt 与完整元数据。
4. 产出首份 60Hz 全感知 COM3 基线评估报告 (Markdown / JSON 与 Wilson 置信区间)。

用法:
    # 无游戏环境快速基线冒烟 (离线模拟):
    python scripts/run_60hz_coldstart_baseline.py --offline --updates 10 --eval-episodes 20

    # 完整实机冷启动训练 (需运行中的游戏):
    python scripts/run_60hz_coldstart_baseline.py --updates 40
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / ".scratch"))

import torch
from agents.ppo_agent import PolicyNet, load_weights
from train.eval import to_markdown, write_reports
import train.train_ppo as tp
from train.train_ppo import Evaluator, _OfflineEnv, train


def run_baseline(
    config_path: str = "configs/train_ppo_com3_60hz_coldstart.yaml",
    updates: int = 40,
    eval_episodes: int = 30,
    eval_interval: Optional[int] = None,
    offline: bool = False,
    out_dir: str = "runs/eval",
    verbose: bool = True,
) -> dict:
    cfg = tp.load_config(str(ROOT / config_path))

    # 确保 60Hz 与正交初始化
    cfg["env"]["action_repeat"] = 1
    cfg["network"]["orthogonal_init"] = True
    cfg["train"]["orthogonal_init"] = True
    raw_ckpt_dir = Path(cfg["checkpoint"]["dir"])
    cfg["checkpoint"]["dir"] = str(raw_ckpt_dir if raw_ckpt_dir.is_absolute() else (ROOT / raw_ckpt_dir))

    eff_eval_interval = eval_interval
    if eff_eval_interval is None:
        cfg_interval = int(cfg.get("eval", {}).get("interval", 15))
        eff_eval_interval = min(cfg_interval, updates) if updates > 0 else cfg_interval
    if eff_eval_interval <= 0:
        eff_eval_interval = max(1, updates)

    if verbose:
        print("=" * 70)
        print("【60Hz 全感知全新冷启动训练流水线 (Ticket 06)】")
        print(f"配置文件: {config_path}")
        print(f"决策频率: {60 // cfg['env']['action_repeat']}Hz (action_repeat={cfg['env']['action_repeat']})")
        print(f"目标难度: COM{cfg['env']['difficulty'] + 1} (Hard)")
        print(f"目标轮数: {updates} updates")
        print(f"单批步数: {cfg['train']['n_steps']} steps (seq_len={cfg['train']['seq_len']})")
        print(f"评估周期: 每 {eff_eval_interval} updates 评估一次")
        print(f"运行模式: {'离线模拟 (_OfflineEnv)' if offline else '实机进程 (Th155Env)'}")
        print("=" * 70, flush=True)

    env = None
    eval_env = None
    if offline:
        env = _OfflineEnv(max_episode_steps=cfg["env"]["max_episode_steps"] // 10, action_repeat=1)
        eval_env = _OfflineEnv(max_episode_steps=cfg["env"]["max_episode_steps"] // 10, action_repeat=1)

    # 1. 执行全新冷启动训练 (init_state=None, cold_start=True)
    stats = train(
        cfg,
        updates=updates,
        env=env,
        eval_env=eval_env,
        eval_interval=eff_eval_interval,
        eval_episodes=eval_episodes,
        verbose=verbose,
        init_state=None,  # 确保 update=0 冷启动
        cold_start=True,
    )

    ckpt_dir = Path(cfg["checkpoint"]["dir"])
    best_pt = ckpt_dir / "best.pt"
    if not best_pt.exists():
        # 若未生成 best.pt 则以最新 ppo_*.pt 为准
        checkpoints = sorted(ckpt_dir.glob("ppo_*.pt"))
        if checkpoints:
            best_pt = checkpoints[-1]

    if not best_pt.exists():
        raise FileNotFoundError(f"冷启动训练未能生成有效的 Checkpoint 权重: {best_pt}")

    # 2. 载入最佳权重执行确定性最终评估并生成报告
    if verbose:
        print("\n" + "=" * 70)
        print("【执行 60Hz 全感知确定性基准评估】")
        print(f"评估模型: {best_pt}")
        print(f"评估规模: {eval_episodes} 局 (argmax 决策, COM{cfg['env']['difficulty'] + 1})")
        print("=" * 70, flush=True)

    net = PolicyNet(cfg.get("network") or {})
    sd = torch.load(best_pt, map_location="cpu")
    # best.pt 可能是词表扩容前的旧权重(docs §36),形状不同时走 warm_start 迁移
    load_weights(net, sd.get("model_state_dict") or sd.get("model"), verbose=verbose)
    net.eval()

    post_eval_env = (
        _OfflineEnv(max_episode_steps=cfg["env"]["max_episode_steps"] // 10, action_repeat=1)
        if offline else tp.build_env(cfg, action_repeat=cfg["env"]["action_repeat"])
    )
    try:
        evaluator = Evaluator(env=post_eval_env, default_episodes=eval_episodes)
        summary = evaluator.run(
            net,
            env=post_eval_env,
            episodes=eval_episodes,
            com_level=cfg["env"]["difficulty"],
            deterministic=True,
        )
    finally:
        post_eval_env.close()

    # 3. 输出 Markdown 与 JSON 评估报告
    out_path = Path(out_dir) if Path(out_dir).is_absolute() else (ROOT / out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    report_tag = "eval_com3_60hz_coldstart"
    md_file = out_path / f"{report_tag}.md"
    js_file = out_path / f"{report_tag}.json"

    try:
        rel_ckpt = str(best_pt.relative_to(ROOT))
    except ValueError:
        rel_ckpt = str(best_pt)
    md_text = to_markdown(summary, ckpt=rel_ckpt)
    # 丰富 60Hz 全感知架构特性说明
    spec_header = (
        "# 东方凭依华 (th155) 60Hz 全感知冷启动 COM3 基线评估报告\n\n"
        "- **架构版本**: 60Hz 逐帧决策反应 (`action_repeat=1`, 延迟 16.7ms)\n"
        "- **动作空间**: 55 裁剪离散动作空间 (彻底移除 Tag 按键)\n"
        "- **防守引擎**: Hold Guard Engine 持续格挡状态机 (背向敌方锁定维持)\n"
        "- **弹幕/激光感知**: 12 维射线几何判定 (is_beam, 垂线威胁度, 扫射截面, startup warning)\n"
        "- **训练初始化**: 全新正交初始化 (Cold Start, update=0, 杜绝历史旧权重噪声)\n\n"
    )
    full_md = spec_header + md_text
    md_file.write_text(full_md, encoding="utf-8")

    js_data = {
        "architecture": {
            "hz": 60,
            "action_repeat": 1,
            "actions": 56,
            "hold_guard_engine": True,
            "laser_special_hitbox": True,
            "cold_start": True,
        },
        "checkpoint": str(best_pt),
        "updates": updates,
        "summary": summary,
    }
    js_file.write_text(json.dumps(js_data, indent=2, ensure_ascii=False), encoding="utf-8")

    if verbose:
        print(f"\n[+] 基线报告已产出:")
        print(f"    - Markdown: {md_file}")
        print(f"    - JSON:     {js_file}")
        print("\n" + full_md)

    return js_data


def main():
    parser = argparse.ArgumentParser(description="60Hz Cold Start Baseline Pipeline")
    parser.add_argument("--config", default="configs/train_ppo_com3_60hz_coldstart.yaml")
    parser.add_argument("--updates", type=int, default=40)
    parser.add_argument("--eval-episodes", type=int, default=30)
    parser.add_argument("--eval-interval", type=int, default=None)
    parser.add_argument("--offline", action="store_true", help="使用离线合成环境执行完整全链路验证")
    parser.add_argument("--out-dir", default="runs/eval")
    args = parser.parse_args()

    run_baseline(
        config_path=args.config,
        updates=args.updates,
        eval_episodes=args.eval_episodes,
        eval_interval=args.eval_interval,
        offline=args.offline,
        out_dir=args.out_dir,
    )


if __name__ == "__main__":
    main()
