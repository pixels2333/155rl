"""随机智能体(带动作掩码)驱动 Th155Env,用于实机冒烟测试。

用法:
    python scripts/random_agent.py                    # 10 个 Episode
    python scripts/random_agent.py --episodes 3 --seed 7
    python scripts/random_agent.py --team 1

前置:游戏已在 VS COM 对战中,且桥接 DLL 已注入(scripts/e2e.py 会一次做完)。
每个 Episode = 一个回合;回合结束后 reset() 会等游戏自然切到下一回合(VS 约 11s)。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from env import Th155Env, action_space        # noqa: E402


LOG_PATH = ROOT / ".scratch" / "th155-rl-ai" / "logs" / "last_step.json"


def _log_step(ep: int, step: int, act: int, rew: float, info: dict) -> None:
    """崩溃取证:每步落盘最后一次状态,游戏挂掉后仍能看到当时的回合/血量/状态。"""
    import json
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    st = info.get("state_snapshot") or {}
    LOG_PATH.write_text(json.dumps({
        "episode": ep, "step": step, "action": act, "reward": round(float(rew), 4),
        "battle_state": info.get("battle_state"), "time": info.get("time"),
        "teams": st.get("teams"), "win": st.get("win"), "round": st.get("round"),
    }, ensure_ascii=False, default=str), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=10)
    ap.add_argument("--team", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-steps", type=int, default=400)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    rng = np.random.default_rng(a.seed)
    env = Th155Env(team=a.team, max_episode_steps=a.max_steps)
    t_all = time.time()
    stats = []
    try:
        for ep in range(a.episodes):
            t0 = time.time()
            obs, info = env.reset()
            steps, ret, illegal = 0, 0.0, 0
            while True:
                mask = np.asarray(info["action_mask"], dtype=bool)
                legal = np.flatnonzero(mask)
                act = int(rng.choice(legal if legal.size else np.arange(action_space.N_ACTIONS)))
                obs, rew, term, trunc, info = env.step(act)
                _log_step(ep, steps + 1, act, rew, info)
                steps += 1
                ret += rew
                illegal += int(info.get("illegal_action", False))
                if a.verbose:
                    d, b, dn, bn = action_space.decode(act)
                    comp = info["reward_components"]
                    print(f"   ep{ep} step{steps:3d} act={dn}{bn:6s} r={rew:+.4f} "
                          f"hp={comp['hp']:+.4f} state={info['battle_state']} t={info['time']}")
                if term or trunc:
                    break
            dt = time.time() - t0
            stats.append((steps, ret, dt))
            print(f"[ep {ep + 1:2d}/{a.episodes}] steps={steps:3d} return={ret:+.3f} "
                  f"wall={dt:5.1f}s state={info['battle_state']} "
                  f"{'truncated' if trunc else 'terminated'} illegal={illegal}")
    finally:
        env.close()

    total = time.time() - t_all
    steps = sum(s for s, _, _ in stats)
    print(f"\n[ok] {len(stats)} episodes, {steps} steps, {total:.1f}s "
          f"({steps / max(total, 1e-9):.1f} steps/s)")
    print(f"     mean return = {np.mean([r for _, r, _ in stats]):+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
