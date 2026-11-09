"""Engine-in-the-loop replay rollout & 30Hz trajectory extraction for th155.exe.

Drives ``th155.exe`` through ``menu.replay_select`` -> ``::replay.Load("replay/current.rep")``
under lockstep (``ctl_lockstep = 1``), applies the in-memory ``BridgeThread`` UAF fix
so background ``PublishState`` never races with Squirrel GC during lockstep, and
extracts exact 30Hz (``action_repeat = 2``) trajectories matching ``ObsBuilder``,
``RewardCalculator``, and ``posttrain_replay.TRAJ_KEYS``.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from pathlib import Path
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from bridge.shm import BridgeShm
from env.obs_builder import ObsBuilder, facing_sign
from env.reward import RewardCalculator, active_actor, snapshot
from th155.game import Th155Game
from th155.instances import find_game_dir, kill_instance, launch_instance, list_instances, slot_pid
from th155.mem import Process, find_pids
from th155.menu import MenuDriver
from th155.replay_parser import frame_to_action_projected, parse_bytes

PAGE_EXECUTE_READWRITE = 0x40


def kill_all_instances() -> None:
    """Kill all running ``th155.exe`` instances across all slots."""
    for _slot, pid in list(list_instances().items()):
        try:
            kill_instance(pid)
        except Exception:
            pass
    for pid in list(find_pids("th155.exe")):
        try:
            kill_instance(pid)
        except Exception:
            pass


def patch_bridge_for_lockstep_replay(proc: Process) -> int:
    """Patch ``th155_rl_bridge.dll`` in-memory for fast, crash-free lockstep replay.

    1. ``+0x123D``: ``cmp eax, 1500`` -> ``cmp eax, 100`` so ``BridgeThread``
       re-resolves the Squirrel object graph after 100ms of no frame change
       during round transitions / match end.
    2. ``+0x1512``: ``jmp +0x161C`` so ``BridgeThread`` skips background
       ``PublishState(&g_rs)`` while ``LockstepBoundary`` on the main game
       thread already publishes every frame (eliminates the Squirrel GC UAF
       race on ``actor.shot_list`` / ``battle.team``).
    """
    bridge_base, _ = proc.module_info("th155_rl_bridge.dll")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.VirtualProtectEx.argtypes = [
        wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, ctypes.POINTER(wt.DWORD)
    ]
    k32.VirtualProtectEx.restype = wt.BOOL

    old_prot = wt.DWORD(0)
    k32.VirtualProtectEx(
        proc.h, ctypes.c_void_p(bridge_base + 0x1000), 0x1000,
        PAGE_EXECUTE_READWRITE, ctypes.byref(old_prot),
    )
    # 1. Resolve timeout: 1500ms -> 100ms
    proc.write(bridge_base + 0x123D, b"\x3d\x64\x00\x00\x00")
    # 2. Skip background PublishState(&rs) in BridgeThread (LockstepBoundary publishes every frame)
    proc.write(bridge_base + 0x1512, b"\xe9\x05\x01\x00\x00\x90\x90")
    k32.VirtualProtectEx(
        proc.h, ctypes.c_void_p(bridge_base + 0x1000), 0x1000,
        old_prot.value, ctypes.byref(old_prot),
    )
    return bridge_base


def select_window_action(
    stream: Sequence[int],
    k: int,
    s_int: int,
    mask_list: List[bool],
    action_repeat: int = 2,
) -> Tuple[int, bool]:
    """Select ``(action_t, bc_valid_t)`` for the ``[k, k + action_repeat - 1]`` window.

    - Decode frame ``k`` (``v0``) -> ``(act0, valid0)`` via :func:`frame_to_action_projected`.
    - If ``k > 0`` and ``(v0 & 0x1FF) != (stream[k - 1] & 0x1FF)`` (new input transition
      on frame ``k``), use ``(act0, valid0)``.
    - Else if ``action_repeat >= 2`` and ``k + 1 < len(stream)`` and frame ``k + 1``
      (``v1``) has a button press transition ``(v1 & 0x1F0) != 0`` while ``(v0 & 0x1F0) == 0``
      and ``valid1`` is True, prefer ``(act1, valid1)`` so a 1-frame button tap on
      ``k + 1`` is never missed.
    - Otherwise use ``(act0, valid0)``.
    """
    n = len(stream)
    k_idx = min(max(0, int(k)), n - 1)
    v0 = int(stream[k_idx])
    act0, valid0 = frame_to_action_projected(v0, s_int, mask_list)

    if k_idx > 0 and (v0 & 0x1FF) != (int(stream[k_idx - 1]) & 0x1FF):
        return int(act0), bool(valid0)
    if action_repeat >= 2 and k_idx + 1 < n:
        v1 = int(stream[k_idx + 1])
        act1, valid1 = frame_to_action_projected(v1, s_int, mask_list)
        if (v1 & 0x1F0) != 0 and (v0 & 0x1F0) == 0 and valid1:
            return int(act1), bool(valid1)
    return int(act0), bool(valid0)


class ReplayWorker:
    """Manages one ``th155.exe`` slot in ``menu.replay_select`` for lockstep rollout."""

    def __init__(
        self,
        slot: int = 0,
        period_us: int = 1800,
        action_repeat: int = 2,
        reward_cfg: Optional[dict] = None,
        game_dir: Optional[str | Path] = None,
    ):
        self.slot = int(slot)
        self.period_us = int(period_us)
        self.action_repeat = int(action_repeat)
        self.reward_cfg = reward_cfg or {}
        self.game_dir = Path(game_dir) if game_dir else Path(find_game_dir())
        self.cur_rep_path = self.game_dir / "replay" / "current.rep"
        self.obs_builder = ObsBuilder()

        self.pid: Optional[int] = None
        self.game: Optional[Th155Game] = None
        self.menu: Optional[MenuDriver] = None
        self.shm: Optional[BridgeShm] = None

    def close(self, kill: bool = True) -> None:
        if self.shm is not None:
            try:
                self.shm.disable_lockstep()
            except Exception:
                pass
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None
        if self.game is not None:
            try:
                self.game.p.k32.CloseHandle(self.game.p.h)
            except Exception:
                pass
        self.menu = None
        self.game = None
        if kill:
            try:
                pid = self.pid or slot_pid(self.slot)
                if pid is not None:
                    kill_instance(pid)
            except Exception:
                pass
        self.pid = None

    def _proc_alive(self) -> bool:
        if self.game is None or self.shm is None or self.pid is None:
            return False
        code = wt.DWORD(0)
        if not self.game.p.k32.GetExitCodeProcess(self.game.p.h, ctypes.byref(code)):
            return False
        return int(code.value) == 259

    def is_alive(self) -> bool:
        return self._proc_alive() and slot_pid(self.slot) == self.pid

    def _replay_select_suspended(self) -> bool:
        if self.game is None:
            return False
        try:
            susp_sa = self.game.get_path("menu", "replay_select", "suspend")
            if susp_sa is None:
                return False
            return bool(self.game.r.value(susp_sa))
        except Exception:
            return False

    def _in_replay_select(self) -> bool:
        """Return True iff ``menu.replay_select`` is initialized, non-empty, and NOT suspended."""
        if self.game is None:
            return False
        try:
            if self._replay_select_suspended():
                return False
            page_sa = self.game.get_path("menu", "replay_select", "page")
            if page_sa is None:
                return False
            t, v = self.game.r.obj(page_sa)
            if (t & 0xFFFFFF) != 0x40:
                return False
            els = self.game.r.array(v)
            return len(els) > 0
        except Exception:
            return False

    def boot(
        self,
        seed_rep_bytes: bytes,
        launch_lock: Optional[threading.Lock] = None,
        timeout: float = 45.0,
    ) -> None:
        """Launch ``th155.exe`` on ``self.slot``, patch bridge, and enter ``menu.replay_select``."""
        self.close(kill=True)
        self.cur_rep_path.parent.mkdir(parents=True, exist_ok=True)
        if launch_lock is not None:
            with launch_lock:
                self.cur_rep_path.write_bytes(seed_rep_bytes)
        else:
            self.cur_rep_path.write_bytes(seed_rep_bytes)

        pid = launch_instance(slot=self.slot, timeout=timeout, period_us=self.period_us)
        self.pid = int(pid)

        t0 = time.time()
        last_err = None
        while time.time() - t0 < timeout:
            try:
                self.game = Th155Game(writable=True, verify_version=False, pid=self.pid)
                self.menu = MenuDriver(self.game)
                self.shm = BridgeShm.open(self.pid, timeout=10.0)
                break
            except Exception as e:
                last_err = e
                time.sleep(0.25)
        if self.game is None or self.menu is None or self.shm is None:
            raise RuntimeError(f"[slot {self.slot}] failed to attach to pid={self.pid}: {last_err}")

        patch_bridge_for_lockstep_replay(self.game.p)
        self.shm.enable_lockstep(timeout_ms=0)

        # Wait for Title Menu to be ready, then enter item 5 ("replay") with a tight hold pulse
        self.menu.wait_title_ready(timeout=20.0)
        t_nav = time.time()
        while time.time() - t_nav < timeout:
            if self._in_replay_select():
                break
            dev = self.menu.input_all()
            if dev:
                t_hold = time.time()
                while time.time() - t_hold < 0.04:
                    self.menu.set_cursor_val("menu", "title", "cursor", value=5)
                    self.game.p.write_i32(dev + 0x0C, 1)
                self.game.p.write_i32(dev + 0x0C, 0)
            # Wait up to 1.2s for the fade into menu.replay_select without pressing b0 again
            t_fade = time.time()
            while time.time() - t_fade < 1.2:
                if self._in_replay_select():
                    break
                time.sleep(0.02)

        if not self._in_replay_select():
            raise RuntimeError(f"[slot {self.slot}] timed out navigating to menu.replay_select")
        time.sleep(0.15)

    def start_current_replay(
        self,
        raw_bytes: bytes,
        launch_lock: Optional[threading.Lock] = None,
        timeout: float = 10.0,
    ) -> bool:
        """Write ``raw_bytes`` to ``replay/current.rep`` and trigger ``::replay.Load``."""
        assert self.game is not None and self.menu is not None and self.shm is not None

        # Wait until menu.replay_select is active and not suspended
        t_ready = time.time()
        while time.time() - t_ready < 8.0:
            if self._in_replay_select() and int(self.shm.s.attached) == 0:
                break
            time.sleep(0.02)

        def _do_trigger() -> bool:
            self.cur_rep_path.write_bytes(raw_bytes)
            t0 = time.time()
            while time.time() - t0 < timeout:
                if int(self.shm.s.attached) == 1 or self._replay_select_suspended():
                    dev = self.menu.input_all()
                    if dev:
                        self.game.p.write_i32(dev + 0x0C, 0)
                    return True
                dev = self.menu.input_all()
                if dev:
                    t_hold = time.time()
                    while time.time() - t_hold < 0.035:
                        self.menu.set_cursor_val("menu", "replay_select", "cursor_page", value=0)
                        self.menu.set_cursor_val("menu", "replay_select", "cursor", value=0)
                        self.game.p.write_i32(dev + 0x0C, 1)
                    self.game.p.write_i32(dev + 0x0C, 0)
                # Poll for 0.12s to let ::replay.Load + BeginVS fade run
                t_check = time.time()
                while time.time() - t_check < 0.12:
                    if int(self.shm.s.attached) == 1 or self._replay_select_suspended():
                        return True
                    time.sleep(0.01)
            return int(self.shm.s.attached) == 1 or self._replay_select_suspended()

        if launch_lock is not None:
            with launch_lock:
                ok = _do_trigger()
        else:
            ok = _do_trigger()

        if not ok:
            return False

        # Wait outside launch_lock until BridgeThread attaches to ::battle
        t_att = time.time()
        while time.time() - t_att < timeout:
            if int(self.shm.s.attached) == 1:
                return True
            time.sleep(0.01)
        return int(self.shm.s.attached) == 1

    def rollout_replay(
        self,
        rep_file: str,
        raw_bytes: bytes,
        teams: Sequence[int],
        launch_lock: Optional[threading.Lock] = None,
    ) -> Tuple[Dict[int, Dict[str, np.ndarray]], dict]:
        """Roll out one ``.rep`` file in lockstep and extract 30Hz trajectories for ``teams``."""
        assert self.shm is not None
        rep = parse_bytes(raw_bytes, source=rep_file)
        teams = sorted({int(t) for t in teams})
        n_frames = len(rep.streams[0])

        t_start = time.perf_counter()
        self.shm.enable_lockstep(timeout_ms=0)

        if not self.start_current_replay(raw_bytes, launch_lock=launch_lock, timeout=10.0):
            raise RuntimeError(f"[slot {self.slot}] failed to start replay {rep_file}")

        # Wait for first fighting frame (battle_state == 8)
        t_wait = time.time()
        while time.time() - t_wait < 12.0:
            if not self._proc_alive():
                raise RuntimeError(f"[slot {self.slot}] th155.exe died before round 1 in {rep_file}")
            if int(self.shm.s.attached) == 1 and int(self.shm.s.battle_state) == 8:
                break
            time.sleep(0.002)
        else:
            raise RuntimeError(f"[slot {self.slot}] timed out waiting for battle_state==8 in {rep_file}")

        st_k = self.shm.read_state()
        reward_calcs = {tm: RewardCalculator(self.reward_cfg) for tm in teams}
        prev_snaps = {tm: snapshot(st_k) for tm in teams}
        prev_actions = {tm: 0 for tm in teams}
        for tm in teams:
            reward_calcs[tm].reset_combat()

        obs_keys = (
            "self", "opp", "global", "proj", "proj_mask",
            "motion_self", "motion_opp", "action_mask",
        )
        buffers: Dict[int, Dict[str, list]] = {
            tm: {k: [] for k in obs_keys + ("actions", "bc_valid", "rewards", "dones")}
            for tm in teams
        }

        round_summaries = []
        last_step_wall = time.time()

        while True:
            if not self._proc_alive():
                raise RuntimeError(f"[slot {self.slot}] th155.exe crashed during {rep_file}")
            if time.time() - last_step_wall > 20.0:
                raise RuntimeError(f"[slot {self.slot}] rollout stalled in {rep_file}")

            k = int(st_k["frame"])
            cur_round = int(st_k["round"])
            st_teams = st_k["teams"]

            step_obs = {}
            step_act = {}
            step_valid = {}
            for tm in teams:
                obs_t = self.obs_builder.build(
                    st_k, controlled_team=tm, prev_action=prev_actions[tm]
                )
                me = active_actor(st_teams[tm])
                op = active_actor(st_teams[1 - tm])
                s_int = 1 if facing_sign(me, op) >= 0 else -1
                mask_list = list(obs_t["action_mask"])

                act_t, valid_t = select_window_action(
                    rep.streams[tm], k, s_int, mask_list, action_repeat=self.action_repeat
                )
                step_obs[tm] = obs_t
                step_act[tm] = act_t
                step_valid[tm] = valid_t

            # Advance up to action_repeat frames while in fighting state (battle_state == 8)
            for _ in range(self.action_repeat):
                if int(self.shm.s.battle_state) != 8 or int(self.shm.s.attached) == 0:
                    break
                self.shm.advance(1, timeout=0.25)

            last_step_wall = time.time()
            st_next = self.shm.read_state()
            next_state = int(st_next.get("battle_state", 0))
            next_round = int(st_next.get("round", cur_round))
            attached_now = int(st_next.get("attached", 0))
            next_frame = int(st_next.get("frame", k))

            round_ended = (
                (next_state != 8)
                or (next_round != cur_round)
                or (attached_now == 0)
                or (next_frame >= n_frames - 2)
            )

            for tm in teams:
                r_t, _comp_t, done_t = reward_calcs[tm].compute(
                    prev_snaps[tm], st_next, team=tm, action=step_act[tm]
                )
                if round_ended:
                    done_t = True
                buf = buffers[tm]
                for ok in obs_keys:
                    buf[ok].append(step_obs[tm][ok])
                buf["actions"].append(step_act[tm])
                buf["bc_valid"].append(step_valid[tm])
                buf["rewards"].append(float(r_t))
                buf["dones"].append(bool(done_t))

                prev_snaps[tm] = snapshot(st_next)
                prev_actions[tm] = step_act[tm]

            if round_ended:
                round_summaries.append({
                    "round": cur_round,
                    "end_frame": next_frame,
                    "end_state": next_state,
                    "winner": int(st_next.get("winner", 0)),
                    "terminal_rewards": {
                        tm: round(float(buffers[tm]["rewards"][-1]), 4) for tm in teams
                    },
                })
                t_trans = time.time()
                started_next = False
                while time.time() - t_trans < 15.0:
                    if not self._proc_alive():
                        raise RuntimeError(
                            f"[slot {self.slot}] th155.exe died in transition of {rep_file}"
                        )
                    att = int(self.shm.s.attached)
                    bs = int(self.shm.s.battle_state)
                    rd = int(self.shm.s.round)
                    fr = int(self.shm.s.frame)
                    if att == 0:
                        break
                    if bs == 8 and rd > cur_round:
                        started_next = True
                        break
                    if fr >= n_frames - 2 and bs in (32, 64):
                        time.sleep(0.01)
                        if int(self.shm.s.attached) == 0:
                            break
                    time.sleep(0.003)

                if started_next:
                    st_k = self.shm.read_state()
                    for tm in teams:
                        prev_snaps[tm] = snapshot(st_k)
                        reward_calcs[tm].reset_combat()
                        prev_actions[tm] = 0
                    last_step_wall = time.time()
                    continue
                else:
                    # Wait for match exit back to menu.replay_select (attached == 0 and not suspended)
                    t_ret = time.time()
                    while time.time() - t_ret < 12.0:
                        if not self._proc_alive():
                            raise RuntimeError(
                                f"[slot {self.slot}] th155.exe died exiting {rep_file}"
                            )
                        if int(self.shm.s.attached) == 0 and self._in_replay_select():
                            break
                        time.sleep(0.02)
                    time.sleep(0.10)
                    break
            else:
                st_k = st_next

        elapsed = time.perf_counter() - t_start
        trajs: Dict[int, Dict[str, np.ndarray]] = {}
        for tm in teams:
            buf = buffers[tm]
            if not buf["actions"]:
                raise RuntimeError(f"[slot {self.slot}] empty trajectory for {rep_file} team={tm}")
            buf["dones"][-1] = True
            traj = {
                "self": np.asarray(buf["self"], dtype=np.float32),
                "opp": np.asarray(buf["opp"], dtype=np.float32),
                "global": np.asarray(buf["global"], dtype=np.float32),
                "proj": np.asarray(buf["proj"], dtype=np.float32),
                "proj_mask": np.asarray(buf["proj_mask"], dtype=bool),
                "motion_self": np.asarray(buf["motion_self"], dtype=np.int64),
                "motion_opp": np.asarray(buf["motion_opp"], dtype=np.int64),
                "action_mask": np.asarray(buf["action_mask"], dtype=bool),
                "actions": np.asarray(buf["actions"], dtype=np.int64),
                "bc_valid": np.asarray(buf["bc_valid"], dtype=bool),
                "rewards": np.asarray(buf["rewards"], dtype=np.float32),
                "dones": np.asarray(buf["dones"], dtype=bool),
                "source_file": np.asarray(rep_file),
                "team": np.asarray(int(tm), dtype=np.int32),
            }
            trajs[tm] = traj

        meta = {
            "file": rep_file,
            "teams": teams,
            "n_frames": n_frames,
            "steps": len(buffers[teams[0]]["actions"]),
            "rounds": len(round_summaries),
            "round_summaries": round_summaries,
            "elapsed_sec": round(elapsed, 3),
            "bc_valid_rate": {
                tm: round(float(trajs[tm]["bc_valid"].mean()), 6) for tm in teams
            },
            "reward_sum": {
                tm: round(float(trajs[tm]["rewards"].sum()), 4) for tm in teams
            },
        }
        return trajs, meta
