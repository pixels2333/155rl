"""Extract 30Hz (``action_repeat = 2``) replay trajectories via live ``th155.exe`` rollout.

Reads ``configs/replay_splits.json`` and ``replay/1519replays - Dauphine 250101-251124 119.zip``,
runs 1..N lockstep ``th155.exe`` worker instances in parallel (serializing only the ~0.15s
``replay/current.rep`` disk load across workers), and writes compressed ``.npz`` files
matching :func:`src.train.posttrain_replay.save_trajectory`.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple
import zipfile

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from th155.instances import find_game_dir  # noqa: E402
from th155.replay_rollout import ReplayWorker, kill_all_instances  # noqa: E402
from train.posttrain_replay import load_trajectory, save_trajectory  # noqa: E402


def perspective_out_path(out_root: Path, split_group: str, rep_file: str, team: int) -> Path:
    """Return output ``.npz`` path, e.g. ``data/replay_trajs/train/batch_1/250103_replay_212807_t0.npz``."""
    parts = Path(rep_file).parts
    if len(parts) >= 2:
        prefix = f"{parts[-2]}_{Path(parts[-1]).stem}"
    else:
        prefix = Path(rep_file).stem
    return out_root / split_group / f"{prefix}_t{int(team)}.npz"


def parse_targets(manifest: dict, targets_spec: str) -> List[Tuple[str, str, int]]:
    """Parse ``--targets`` into ``[(split_group, rep_file, team), ...]``.

    Examples of ``targets_spec``:
      - ``"train:batch_1,val,test"``
      - ``"train:all,val,test"``
      - ``"val,test"``
    """
    items: List[Tuple[str, str, int]] = []
    tokens = [t.strip() for t in targets_spec.split(",") if t.strip()]
    for tok in tokens:
        if tok.startswith("train:"):
            sub = tok.split(":", 1)[1].strip()
            batches = manifest["splits"]["train"]["batches"]
            if sub == "all":
                b_names = sorted(batches.keys())
            else:
                b_names = [sub]
            for b in b_names:
                if b not in batches:
                    raise ValueError(f"Unknown train batch {b!r}, available: {sorted(batches)}")
                for entry in batches[b]:
                    items.append((f"train/{b}", entry["file"], int(entry["team"])))
        elif tok == "train":
            batches = manifest["splits"]["train"]["batches"]
            for b in sorted(batches.keys()):
                for entry in batches[b]:
                    items.append((f"train/{b}", entry["file"], int(entry["team"])))
        elif tok in ("val", "test"):
            for entry in manifest["splits"][tok]:
                items.append((tok, entry["file"], int(entry["team"])))
        else:
            raise ValueError(f"Unrecognized target token: {tok!r}")
    return items


def build_work_queue(
    perspectives: List[Tuple[str, str, int]],
    out_root: Path,
    overwrite: bool = False,
) -> Tuple[List[Tuple[str, Dict[int, Path], Dict[int, str]]], List[Tuple[str, str, int, Path]]]:
    """Group perspectives by ``rep_file`` so mirror replays roll out once for both teams."""
    grouped: Dict[str, Tuple[Dict[int, Path], Dict[int, str]]] = {}
    cached: List[Tuple[str, str, int, Path]] = []

    for split_group, rep_file, team in perspectives:
        out_path = perspective_out_path(out_root, split_group, rep_file, team)
        if not overwrite and out_path.exists():
            try:
                load_trajectory(out_path)
                cached.append((split_group, rep_file, team, out_path))
                continue
            except Exception:
                pass
        if rep_file not in grouped:
            grouped[rep_file] = ({}, {})
        grouped[rep_file][0][team] = out_path
        grouped[rep_file][1][team] = split_group

    work_items = [
        (rep_file, team_paths, team_groups)
        for rep_file, (team_paths, team_groups) in grouped.items()
    ]
    return work_items, cached


def load_reward_cfg(config_path: str | Path) -> dict:
    p = Path(config_path)
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return dict(raw.get("reward", {}))


def main(argv: Optional[List[str]] = None) -> dict:
    ap = argparse.ArgumentParser(description="Extract 30Hz replay trajectories from th155.exe")
    ap.add_argument("--splits", default="configs/replay_splits.json")
    ap.add_argument("--zip", default="replay/1519replays - Dauphine 250101-251124 119.zip")
    ap.add_argument("--out-dir", default="data/replay_trajs")
    ap.add_argument("--targets", default="train:batch_1,val,test")
    ap.add_argument("--config", default="configs/train_ppo_com4_multi_b24k_cpun_lr3e5_e200.yaml")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--period-us", type=int, default=1800)
    ap.add_argument("--action-repeat", type=int, default=2)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)

    manifest = json.loads(Path(args.splits).read_text(encoding="utf-8"))
    reward_cfg = load_reward_cfg(args.config)
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    perspectives = parse_targets(manifest, args.targets)
    work_items, cached = build_work_queue(perspectives, out_root, overwrite=args.overwrite)

    print(
        f"[i] Targets={args.targets} | total_perspectives={len(perspectives)} "
        f"| cached={len(cached)} | replays_to_rollout={len(work_items)} "
        f"| workers={args.workers} | period_us={args.period_us} | action_repeat={args.action_repeat}",
        flush=True,
    )

    # Pre-load raw .rep bytes from zip into memory
    with zipfile.ZipFile(args.zip, "r") as zf:
        rep_bytes_map: Dict[str, bytes] = {}
        for rep_file, _, _ in work_items:
            rep_bytes_map[rep_file] = zf.read(rep_file)
        seed_file = perspectives[0][1]
        seed_bytes = zf.read(seed_file)

    game_dir = Path(find_game_dir())
    cur_rep_path = game_dir / "replay" / "current.rep"
    launch_lock = threading.Lock()
    print_lock = threading.Lock()

    t_wall0 = time.perf_counter()
    results_meta: List[dict] = []

    if work_items:
        kill_all_instances()
        n_workers = max(1, min(int(args.workers), len(work_items)))
        q: queue.Queue = queue.Queue()
        for item in work_items:
            q.put(item)

        def worker_loop(slot: int) -> List[dict]:
            w = ReplayWorker(
                slot=slot,
                period_us=args.period_us,
                action_repeat=args.action_repeat,
                reward_cfg=reward_cfg,
                game_dir=game_dir,
            )
            local_metas: List[dict] = []
            try:
                with print_lock:
                    print(f"[slot {slot}] booting th155.exe and entering menu.replay_select...", flush=True)
                w.boot(seed_bytes, launch_lock=launch_lock)
                with print_lock:
                    print(f"[slot {slot}] ready (pid={w.pid})", flush=True)

                while True:
                    try:
                        rep_file, team_paths, team_groups = q.get_nowait()
                    except queue.Empty:
                        break

                    teams = sorted(team_paths.keys())
                    raw_bytes = rep_bytes_map[rep_file]
                    last_err = None
                    for attempt in range(1, 4):
                        try:
                            if not w.is_alive():
                                w.boot(seed_bytes, launch_lock=launch_lock)
                            trajs, meta = w.rollout_replay(
                                rep_file, raw_bytes, teams, launch_lock=launch_lock
                            )
                            for tm, out_p in team_paths.items():
                                out_p.parent.mkdir(parents=True, exist_ok=True)
                                save_trajectory(out_p, trajs[tm])
                            meta["slot"] = slot
                            meta["split_groups"] = {str(tm): team_groups[tm] for tm in teams}
                            local_metas.append(meta)
                            with print_lock:
                                print(
                                    f"[slot {slot}] OK {rep_file} teams={teams} "
                                    f"T={meta['steps']} rounds={meta['rounds']} "
                                    f"bc_valid={meta['bc_valid_rate']} "
                                    f"rew={meta['reward_sum']} ({meta['elapsed_sec']:.1f}s)",
                                    flush=True,
                                )
                            last_err = None
                            break
                        except Exception as e:
                            last_err = e
                            with print_lock:
                                print(
                                    f"[slot {slot}] WARN attempt {attempt}/3 on {rep_file}: {e}",
                                    flush=True,
                                )
                            try:
                                w.boot(seed_bytes, launch_lock=launch_lock)
                            except Exception:
                                pass
                    if last_err is not None:
                        raise RuntimeError(
                            f"[slot {slot}] Failed to extract {rep_file} after 3 attempts: {last_err}"
                        )
            finally:
                w.close(kill=True)
            return local_metas

        try:
            # Stagger worker boot slightly to avoid simultaneous DLL injection contention
            workers_futures = []
            with ThreadPoolExecutor(max_workers=n_workers) as ex:
                for s in range(n_workers):
                    workers_futures.append(ex.submit(worker_loop, s))
                    time.sleep(1.0)
                for fut in as_completed(workers_futures):
                    results_meta.extend(fut.result())
        finally:
            kill_all_instances()
            try:
                if cur_rep_path.exists():
                    cur_rep_path.unlink()
            except Exception:
                pass

    wall_elapsed = time.perf_counter() - t_wall0

    # Aggregate statistics across all target perspectives
    by_group: Dict[str, List[dict]] = {}
    for split_group, rep_file, team in perspectives:
        out_path = perspective_out_path(out_root, split_group, rep_file, team)
        traj = load_trajectory(out_path)
        T = int(len(traj["actions"]))
        rounds = int(traj["dones"].sum())
        bc_valid = float(traj["bc_valid"].mean())
        rew_sum = float(traj["rewards"].sum())
        term_rews = [float(r) for r, d in zip(traj["rewards"], traj["dones"]) if bool(d)]
        by_group.setdefault(split_group, []).append({
            "file": rep_file,
            "team": team,
            "path": str(out_path.relative_to(out_root)),
            "steps": T,
            "rounds": rounds,
            "bc_valid_rate": round(bc_valid, 6),
            "reward_sum": round(rew_sum, 4),
            "terminal_rewards": [round(x, 4) for x in term_rews],
        })

    summary_groups = {}
    total_steps_all = 0
    total_valid_all = 0
    total_rounds_all = 0
    for grp, recs in sorted(by_group.items()):
        steps_list = [r["steps"] for r in recs]
        rounds_list = [r["rounds"] for r in recs]
        rew_list = [r["reward_sum"] for r in recs]
        valid_steps = sum(int(round(r["bc_valid_rate"] * r["steps"])) for r in recs)
        tot_steps = sum(steps_list)
        total_steps_all += tot_steps
        total_valid_all += valid_steps
        total_rounds_all += sum(rounds_list)
        summary_groups[grp] = {
            "n_perspectives": len(recs),
            "total_steps": tot_steps,
            "mean_steps": round(float(np.mean(steps_list)), 1),
            "min_steps": int(np.min(steps_list)),
            "max_steps": int(np.max(steps_list)),
            "total_rounds": int(sum(rounds_list)),
            "bc_valid_rate": round(valid_steps / max(1, tot_steps), 6),
            "reward_sum_mean": round(float(np.mean(rew_list)), 4),
            "reward_sum_std": round(float(np.std(rew_list)), 4),
            "reward_sum_min": round(float(np.min(rew_list)), 4),
            "reward_sum_max": round(float(np.max(rew_list)), 4),
        }

    summary = {
        "targets": args.targets,
        "action_repeat": args.action_repeat,
        "period_us": args.period_us,
        "workers": args.workers,
        "wall_elapsed_sec": round(wall_elapsed, 2),
        "total_perspectives": len(perspectives),
        "total_steps": total_steps_all,
        "total_rounds": total_rounds_all,
        "overall_bc_valid_rate": round(total_valid_all / max(1, total_steps_all), 6),
        "groups": summary_groups,
        "perspectives": by_group,
    }
    summary_path = out_root / "extraction_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "perspectives"}, indent=2, ensure_ascii=False), flush=True)
    return summary


if __name__ == "__main__":
    main()
