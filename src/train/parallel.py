"""多环境并行采样:每个 env 一个 spawn 子进程(worker),学习者同步收集 rollout。

设计见 docs/multi_instance_feasibility.md §6:
  * worker 持有自己的 env 与策略副本,每次 update 前由学习者广播权重;
    对局与 LSTM hidden 在 worker 内跨 update 连续,用现有 `collect()` 采样。
  * env 由 EnvProvider 提供:`make(slot)`、`recover(slot, env, why)`、`close(slot, env)`。
    OfflineProvider 用合成环境(测试),GameProvider 按实例契约拉起游戏。
  * 容错:worker 内 env 异常走 `provider.recover`;worker 进程死掉/超时按原 slot 重启;
    同一 slot 连续失败超过 `max_failures` 才报错退出。
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
from multiprocessing.connection import wait as _mp_wait
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import torch

import train.train_ppo as tp
from agents.ppo_agent import PolicyNet
from train.eval import MatchRecord, play_matches


# ---------------------------------------------------------------------------
# EnvProvider
# ---------------------------------------------------------------------------
class OfflineProvider:
    """合成环境(`_OfflineEnv`),每个 slot 用不同随机种子。只用于测试/冒烟。"""

    supports_difficulty = False

    def __init__(self, max_episode_steps: int = 60, action_repeat: int = 2, seed: int = 0):
        self.max_episode_steps = int(max_episode_steps)
        self.action_repeat = int(action_repeat)
        self.seed = int(seed)

    def make(self, slot: int):
        env = tp._OfflineEnv(max_episode_steps=self.max_episode_steps,
                             action_repeat=self.action_repeat, seed=self.seed + int(slot))
        obs, _info = env.reset()
        return env, obs

    def recover(self, slot: int, env, why: str):
        self.close(slot, env)
        return self.make(slot)

    def close(self, slot: int, env) -> None:
        env.close()

    def apply_difficulty(self, slot: int, env, level: int):
        return env, None

    def release(self, slot: int) -> None:
        """学习者侧清理已死 worker 留下的资源;合成环境没有外部资源。"""


class _MenuLock:
    """跨进程命名互斥量。实例启动/恢复/切难度都要驱动菜单,并行驱动会失败(§5 第 5 条)。

    持锁进程被杀时 Windows 把互斥量标记为 abandoned,下一个等待者照常拿到(WAIT_ABANDONED)。
    """

    NAME = "Local\\th155_rl_menu_lock"

    def __enter__(self):
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.CreateMutexW(None, False, self.NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        got = k32.WaitForSingleObject(handle, 0xFFFFFFFF)       # INFINITE
        if got not in (0x0, 0x80):                              # WAIT_OBJECT_0 / WAIT_ABANDONED
            err = ctypes.get_last_error()
            k32.CloseHandle(handle)
            raise ctypes.WinError(err)
        self._k32, self._handle = k32, handle
        return self

    def __exit__(self, *exc) -> bool:
        self._k32.ReleaseMutex(self._handle)
        self._k32.CloseHandle(self._handle)
        return False


class GameProvider:
    """实机实例:按 slot 启动独立游戏进程(`th155.instances`),env 绑定该实例的 pid。"""

    supports_difficulty = True

    def __init__(self, cfg: dict, action_repeat: Optional[int] = None):
        self.cfg = cfg
        self.action_repeat = action_repeat

    def make(self, slot: int):
        from th155.instances import kill_instance, launch_instance, slot_pid
        with _MenuLock():
            pid = launch_instance(slot, period_us=tp.frame_period_us(self.cfg))
            env = None
            try:
                env = tp.build_env(self.cfg, self.action_repeat, pid=pid)
                env, (obs, _info) = tp.reset_with_restart(self.cfg, env, self.action_repeat,
                                                          f"worker {slot} 启动", slot=slot)
            except Exception:
                if env is not None:
                    try:
                        env.close()
                    except Exception:               # noqa: BLE001
                        pass
                # reset_with_restart 可能已按 slot 重启过实例:按 slot 查当前 pid 再结束。
                owner = slot_pid(slot)
                if owner is not None:
                    kill_instance(owner)
                raise
        return env, obs

    def recover(self, slot: int, env, why: str):
        with _MenuLock():
            env, (obs, _info) = tp._recover_game(self.cfg, env, self.action_repeat, why, slot=slot)
        return env, obs

    def close(self, slot: int, env) -> None:
        from th155.instances import kill_instance
        pid = getattr(env, "pid", None)
        try:
            env.close()
        finally:
            if pid is not None:
                kill_instance(pid)

    def apply_difficulty(self, slot: int, env, level: int):
        with _MenuLock():
            return tp._apply_difficulty(self.cfg, env, int(level), tp._curriculum_mode(self.cfg),
                                        tp._difficulty_switch(self.cfg), self.action_repeat,
                                        slot=slot)

    def release(self, slot: int) -> None:
        from th155.instances import kill_instance, slot_pid
        pid = slot_pid(slot)
        if pid is not None:
            kill_instance(pid)


# ---------------------------------------------------------------------------
# worker 进程
# ---------------------------------------------------------------------------
def state_to_numpy(net: torch.nn.Module) -> Dict[str, np.ndarray]:
    """权重转成 numpy 再过管道:避开 torch 张量走共享内存句柄的跨进程语义。"""
    return {k: v.detach().cpu().numpy() for k, v in net.state_dict().items()}


def _load_np(net: torch.nn.Module, sd: Dict[str, np.ndarray]) -> None:
    net.load_state_dict({k: torch.from_numpy(np.asarray(v)) for k, v in sd.items()})


def _fresh_ep_state() -> dict:
    return {"return": 0.0, "steps": 0, "damage": 0.0, "tags": 0, "comp": {}}


class _Worker:
    def __init__(self, slot: int, cfg: dict, provider, ref_state: Optional[dict]):
        self.slot = int(slot)
        self.provider = provider
        net_cfg = cfg.get("network") or {}
        self.net = PolicyNet(net_cfg)
        self.base_temperature = float(self.net.sampling_temperature)
        self.base_min_p = float(getattr(self.net, "min_p", 0.0))
        self.base_top_k = int(getattr(self.net, "top_k", 0))
        self.ref_net = None
        if ref_state is not None:
            self.ref_net = PolicyNet(net_cfg)
            _load_np(self.ref_net, ref_state)
            self.ref_net.eval()
            for p in self.ref_net.parameters():
                p.requires_grad_(False)
        self.overrides: Dict[str, float] = {}
        self.stall = 0
        # 最终回复之前的进度消息(评估逐局回传);_worker_main 接到管道上。
        self.emit: Callable[[str, object], None] = lambda _kind, _data: None
        self.env, self.obs = provider.make(self.slot)
        self.reset_episode()

    def reset_episode(self) -> None:
        self.hidden = self.net.init_hidden(1)
        self.ref_hidden = self.ref_net.init_hidden(1) if self.ref_net is not None else None
        self.ep_state = _fresh_ep_state()

    def _apply_overrides(self) -> None:
        if self.overrides:
            try:
                self.env.reward_calc.C.update(self.overrides)
            except Exception:                       # noqa: BLE001 - 合成环境没有 reward_calc
                pass

    def recover(self, why: str) -> None:
        self.env, self.obs = self.provider.recover(self.slot, self.env, why)
        self._apply_overrides()
        self.reset_episode()

    def collect(self, p: dict) -> dict:
        _load_np(self.net, p["state"])
        if p.get("overrides") is not None:
            self.overrides = dict(p["overrides"])
            self._apply_overrides()
        stats = tp.Stats()
        try:
            roll, (obs, hidden, ep_state), infer_ms, elapsed = tp.collect(
                self.net, self.env, int(p["n_steps"]), self.hidden, stats, self.obs,
                ep_state=self.ep_state, stop_on_done=bool(p["stop_on_done"]),
                max_steps=p["max_steps"], ref_net=self.ref_net, ref_hidden=self.ref_hidden)
        except Exception as exc:                    # noqa: BLE001 - env 异常一律走 recover
            err = f"{type(exc).__name__}: {exc}"
            print(f"[w] worker {self.slot} 采样失败({err}),恢复 env", flush=True)
            self.recover("采样失败后")
            return {"ok": False, "error": err, "episodes": stats.episodes}
        self.obs, self.hidden, self.ep_state = obs, hidden, ep_state
        if self.ref_net is not None:
            self.ref_hidden = ep_state.get("ref_hidden", self.ref_hidden)
        steps = len(roll.actions)
        fps = steps / max(elapsed, 1e-9)
        # 与单环境同一判据:连续两次 fps<5 视为游戏状态机卡死,丢弃本批并恢复 env。
        self.stall = self.stall + 1 if fps < 5.0 else 0
        if self.stall >= 2:
            print(f"[w] worker {self.slot} 采样停顿(连续 {self.stall} 次 fps<5,"
                  f"本次 {fps:.1f}),丢弃本批并恢复 env", flush=True)
            self.stall = 0
            self.recover("采样停顿")
            return {"ok": False, "error": "stall", "episodes": []}
        return {"ok": True, "roll": roll, "episodes": stats.episodes, "fps": fps,
                "infer_ms": infer_ms, "elapsed": elapsed, "steps": steps}

    def evaluate(self, p: dict) -> dict:
        """逐局打完就回传 ("eval_rec", rec):学习者的超时按"两局之间"计,
        worker 中途失效时已打完的局也不丢。最终回复只带完成局数。"""
        _load_np(self.net, p["state"])
        self.net.sampling_temperature = float(p.get("temperature", self.base_temperature))
        self.net.min_p = float(p.get("min_p", self.base_min_p))
        self.net.top_k = int(p.get("top_k", self.base_top_k))
        done = 0
        self.net.eval()

        def _on_record(_i: int, rec: MatchRecord) -> None:
            nonlocal done
            done += 1
            self.emit("eval_rec", rec)

        try:
            play_matches(self.net, self.env, int(p["episodes"]), com_level=int(p["com_level"]),
                         on_record=_on_record, deterministic=bool(p["deterministic"]))
            ok, err = True, None
        except Exception as exc:                    # noqa: BLE001
            ok, err = False, f"{type(exc).__name__}: {exc}"
            print(f"[w] worker {self.slot} 评估失败({err}),恢复 env", flush=True)
        finally:
            self.net.train()
            self.net.sampling_temperature = self.base_temperature
            self.net.min_p = self.base_min_p
            self.net.top_k = self.base_top_k
        if not ok:
            self.recover("评估失败后")
        else:
            # 评估复用训练 env:回到干净的训练状态(同单环境路径)。
            try:
                self.obs, _info = self.env.reset()
                self.reset_episode()
            except Exception as exc:                # noqa: BLE001
                print(f"[w] worker {self.slot} 评估后 reset 失败({exc}),恢复 env", flush=True)
                self.recover("评估后")
        return {"ok": ok, "error": err, "done": done}

    def difficulty(self, level: int) -> dict:
        self.env, obs = self.provider.apply_difficulty(self.slot, self.env, int(level))
        if obs is not None:
            self.obs = obs
            self.reset_episode()
        self._apply_overrides()
        return {"ok": True}


def _worker_main(slot: int, conn, cfg: dict, provider, ref_state: Optional[dict],
                 seed: int) -> None:
    torch.set_num_threads(1)
    torch.manual_seed(seed)
    np.random.seed(seed % (2 ** 32))
    try:
        w = _Worker(slot, cfg, provider, ref_state)
    except Exception as exc:                        # noqa: BLE001
        conn.send(("ready", {"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return
    w.emit = lambda kind, data: conn.send((kind, data))
    conn.send(("ready", {"ok": True, "pid": os.getpid()}))
    handlers = {"collect": w.collect, "eval": w.evaluate, "difficulty": w.difficulty}
    try:
        while True:
            try:
                cmd, payload = conn.recv()
            except (EOFError, OSError):             # 学习者已退出
                break
            if cmd == "close":
                break
            try:
                res = handlers[cmd](payload)
            except Exception as exc:                # noqa: BLE001 - recover 失败会在这里抛出
                err = f"{type(exc).__name__}: {exc}"
                print(f"[w] worker {slot} 执行 {cmd} 失败({err})", flush=True)
                w.recover(f"{cmd} 失败后")
                res = {"ok": False, "error": err, "episodes": [], "done": 0}
            conn.send((cmd, res))
    finally:
        try:
            provider.close(slot, w.env)
        finally:
            try:
                conn.send(("closed", None))
            except Exception:                       # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# 学习者侧
# ---------------------------------------------------------------------------
class _Slot:
    def __init__(self, proc, conn, gen: int):
        self.proc = proc
        self.conn = conn
        self.gen = gen
        self.pid: Optional[int] = None


def split_episodes(total: int, n: int) -> List[int]:
    """把 total 局尽量均分给 n 个 worker(前 total%n 个多 1 局)。"""
    base, extra = divmod(int(total), int(n))
    return [base + (1 if i < extra else 0) for i in range(int(n))]


def merge_episodes(stats: "tp.Stats", results: List[Optional[dict]]) -> None:
    """按 slot 顺序把各 worker 新完成的对局并入学习者的 Stats(采样失败的 worker
    在失败前打完的对局也计入,与单环境 collect 抛异常时的口径一致)。"""
    for res in results:
        for ep in (res or {}).get("episodes") or []:
            stats.episodes.append(ep)
            if ep["result"] == "win":
                stats.wins += 1
            elif ep["result"] == "lose":
                stats.losses += 1
            else:
                stats.draws += 1


class WorkerPool:
    """N 个采样 worker。所有请求都是"广播 -> 同步等全部回复"。"""

    def __init__(self, cfg: dict, provider, n: int, *, seed: int = 0,
                 ref_state: Optional[dict] = None, timeout: float = 900.0,
                 max_failures: int = 5, verbose: bool = True):
        self.cfg = cfg
        self.provider = provider
        self.n = int(n)
        self.seed = int(seed)
        self.ref_state = ref_state
        self.timeout = float(timeout)
        self.max_failures = int(max_failures)
        self.verbose = verbose
        self.ctx = mp.get_context("spawn")
        self.slots: List[Optional[_Slot]] = [None] * self.n
        self.failures = [0] * self.n
        self._gens = [-1] * self.n
        self._closed = False

    # -- 生命周期 ------------------------------------------------------------
    def start(self) -> "WorkerPool":
        for s in range(self.n):
            self._spawn(s)
        ready = self._gather(list(range(self.n)), "ready", self.timeout)
        for s in range(self.n):
            if ready.get(s) is not None and ready[s].get("ok"):
                self._mark_ready(s, ready[s])
            else:
                self._revive(s, (ready.get(s) or {}).get("error", "启动超时/进程退出"))
        return self

    def _spawn(self, s: int) -> None:
        self._gens[s] += 1
        gen = self._gens[s]
        parent, child = self.ctx.Pipe()
        proc = self.ctx.Process(
            target=_worker_main, name=f"th155-worker{s}", daemon=True,
            args=(s, child, self.cfg, self.provider, self.ref_state,
                  self.seed + 1000 * (s + 1) + gen))
        proc.start()
        child.close()
        self.slots[s] = _Slot(proc, parent, gen)

    def _mark_ready(self, s: int, info: dict) -> None:
        self.slots[s].pid = info.get("pid")
        if self.verbose:
            print(f"[i] worker {s} 就绪(pid={self.slots[s].pid}, 第 {self.slots[s].gen} 代)",
                  flush=True)

    def _fail(self, s: int, why: str) -> None:
        self.failures[s] += 1
        if self.failures[s] > self.max_failures:
            raise RuntimeError(f"worker {s} 连续失败 {self.failures[s]} 次"
                               f"(上限 {self.max_failures}),最后一次:{why}")

    def _kill(self, s: int) -> None:
        w = self.slots[s]
        if w is None:
            return
        if w.proc.is_alive():
            w.proc.kill()
        w.proc.join(10)
        w.conn.close()
        self.slots[s] = None
        self.provider.release(s)

    def _revive(self, s: int, why: str) -> None:
        """杀掉(若还活着)并按原 slot 重启 worker,直到就绪或连续失败超过上限。"""
        while True:
            print(f"[w] worker {s} 失效({why}),按原 slot 重启", flush=True)
            self._fail(s, why)
            self._kill(s)
            self._spawn(s)
            got = self._gather([s], "ready", self.timeout).get(s)
            if got is not None and got.get("ok"):
                self._mark_ready(s, got)
                return
            why = (got or {}).get("error", "启动超时/进程退出")

    def close(self) -> None:
        """关闭全部 worker 及其 env/实例。重复调用无副作用。"""
        if self._closed:
            return
        self._closed = True
        live = [s for s in range(self.n) if self.slots[s] is not None]
        for s in live:
            try:
                self.slots[s].conn.send(("close", None))
            except Exception:                       # noqa: BLE001
                pass
        self._gather(live, "closed", 120.0, revive=False)
        for s in live:
            w = self.slots[s]
            w.proc.join(10)
            if w.proc.is_alive():
                self._kill(s)
            else:
                w.conn.close()
                self.slots[s] = None

    # -- 通信 ----------------------------------------------------------------
    def _send_all(self, msg: tuple, slots: List[int]) -> Tuple[List[int], List[int]]:
        sent, dead = [], []
        for s in slots:
            try:
                self.slots[s].conn.send(msg)
                sent.append(s)
            except Exception:                       # noqa: BLE001 - 管道已断 = 进程已死
                dead.append(s)
        return sent, dead

    def _gather(self, slots: List[int], kind: str, timeout: float, revive: bool = False,
                progress: Optional[Tuple[str, Callable[[int, object], None]]] = None
                ) -> Dict[int, Optional[dict]]:
        """等 slots 的 `kind` 回复;超时/进程退出记 None。revive=True 时随后按 slot 重启。

        `progress=(pkind, fn)`:最终回复前 worker 可发任意条 (pkind, data),每条调
        fn(slot, data) 并把该 slot 的计时清零 —— timeout 是每个 slot "两条消息之间"的上限。
        """
        out: Dict[int, Optional[dict]] = {}
        pkind, pfn = progress if progress is not None else (None, None)
        start = time.monotonic()
        deadline = {s: start + timeout for s in slots}
        while deadline:
            now = time.monotonic()
            for s in [s for s, d in deadline.items() if d <= now]:
                del deadline[s]
                out[s] = None
            if not deadline:
                break
            watch = {}
            for s in deadline:
                watch[self.slots[s].conn] = s
                watch[self.slots[s].proc.sentinel] = s
            for obj in _mp_wait(list(watch), min(deadline.values()) - now):
                s = watch[obj]
                if s not in deadline:
                    continue
                w = self.slots[s]
                broken = False
                try:
                    msg = w.conn.recv() if w.conn.poll() else None
                except (EOFError, OSError):         # 对端已关闭:进程正在退出或已退出
                    msg, broken = None, True
                if msg is None and not broken and w.proc.is_alive():
                    continue                        # 哨兵误报:进程还在,继续等
                if msg is not None and pkind is not None and msg[0] == pkind:
                    pfn(s, msg[1])
                    deadline[s] = time.monotonic() + timeout
                    continue
                del deadline[s]
                out[s] = msg[1] if (msg is not None and msg[0] == kind) else None
        if revive:
            for s in slots:
                if out.get(s) is None:
                    self._revive(s, f"{kind} 无回复(超时或进程退出)")
        return out

    # -- 请求 ----------------------------------------------------------------
    def collect(self, net: torch.nn.Module, n_steps: int, stop_on_done: bool = False,
                max_steps: Optional[int] = None, overrides: Optional[dict] = None
                ) -> Tuple[List[Optional[dict]], float]:
        """全部 worker 各采 n_steps;返回 (按 slot 排列的结果, 墙钟秒)。失败的 slot 为 None。"""
        payload = {"state": state_to_numpy(net), "n_steps": int(n_steps),
                   "stop_on_done": bool(stop_on_done), "max_steps": max_steps,
                   "overrides": overrides}
        t0 = time.perf_counter()
        sent, dead = self._send_all(("collect", payload), list(range(self.n)))
        got = self._gather(sent, "collect", self.timeout)
        wall = time.perf_counter() - t0
        results: List[Optional[dict]] = [None] * self.n
        for s in range(self.n):
            res = got.get(s)
            if res is None:
                if s not in dead:
                    dead.append(s)
                continue
            results[s] = res
            if res.get("ok"):
                self.failures[s] = 0
            else:
                self._fail(s, str(res.get("error")))
        for s in sorted(dead):
            self._revive(s, "采样无回复(超时或进程退出)")
        return results, wall

    def evaluate(self, net: torch.nn.Module, episodes: int, com_level: int = 0,
                 deterministic: bool = True, temperature: Optional[float] = None,
                 on_record: Optional[Callable[[int, MatchRecord], None]] = None,
                 rounds: int = 4) -> List[MatchRecord]:
        """把 episodes 局分给各 worker;缺的局(worker 失败/死掉)最多再派 rounds-1 轮。

        worker 逐局回传记录,`worker_timeout` 只约束"两局之间"的间隔;失效 worker 已打完的局照常计入。
        `on_record(i, rec)` 按到达顺序实时调用;返回列表按"轮次 -> slot"顺序合并,
        保证同样的输入得到同样的汇总顺序。
        """
        state = state_to_numpy(net)
        records: List[MatchRecord] = []
        for _round in range(int(rounds)):
            missing = int(episodes) - len(records)
            if missing <= 0:
                break
            quota = split_episodes(missing, self.n)
            todo = [s for s in range(self.n) if quota[s] > 0]
            sent = []
            for s in todo:
                payload = {"state": state, "episodes": quota[s], "com_level": int(com_level),
                           "deterministic": bool(deterministic)}
                if temperature is not None:
                    payload["temperature"] = float(temperature)
                if getattr(net, "min_p", 0.0) > 0.0:
                    payload["min_p"] = float(net.min_p)
                if getattr(net, "top_k", 0) > 0:
                    payload["top_k"] = int(net.top_k)
                sent += self._send_all(("eval", payload), [s])[0]
            streamed: Dict[int, List[MatchRecord]] = {s: [] for s in todo}
            seen = len(records)

            def _arrive(s: int, rec: MatchRecord) -> None:
                nonlocal seen
                streamed[s].append(rec)
                if on_record is not None:
                    on_record(seen, rec)
                seen += 1

            got = self._gather(sent, "eval", self.timeout, progress=("eval_rec", _arrive))
            for s in todo:
                records.extend(streamed[s])
                if got.get(s) is None:
                    self._revive(s, "评估无回复(超时或进程退出)")
        if len(records) < int(episodes):
            print(f"[w] 分布式评估只完成 {len(records)}/{episodes} 局", flush=True)
        return records

    def apply_difficulty(self, level: int) -> None:
        """全部 worker 切到新难度;重启的 worker 也按新难度起 env。"""
        self.cfg.setdefault("env", {})["difficulty"] = int(level)
        sent, dead = self._send_all(("difficulty", int(level)), list(range(self.n)))
        got = self._gather(sent, "difficulty", self.timeout)
        for s in range(self.n):
            if got.get(s) is None and s not in dead:
                dead.append(s)
        for s in sorted(dead):
            self._revive(s, "切难度无回复(超时或进程退出)")


def evaluate_parallel(cfg: dict, model_sd: Dict[str, torch.Tensor], episodes: int,
                      num_envs: int, provider, *, com_level: int, deterministic: bool,
                      temperature: Optional[float] = None,
                      on_record: Optional[Callable[[int, MatchRecord], None]] = None
                      ) -> List[MatchRecord]:
    """独立评估入口:起 num_envs 个 worker 分摊 episodes 局,结束时关闭全部实例。"""
    net = PolicyNet(cfg.get("network") or {})
    tp.load_weights(net, model_sd, verbose=True)
    tcfg = cfg.get("train") or {}
    pool = WorkerPool(cfg, provider, num_envs, seed=int(tcfg.get("seed", 0)),
                      timeout=float(tcfg.get("worker_timeout", 900.0)),
                      max_failures=int(tcfg.get("worker_max_failures", 5)))
    try:
        pool.start()
        return pool.evaluate(net, episodes, com_level=com_level, deterministic=deterministic,
                             temperature=temperature, on_record=on_record)
    finally:
        pool.close()
