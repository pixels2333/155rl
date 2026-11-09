"""实机验证 th155_rl_bridge.dll:瞬间重置 / 状态读取 / 输入注入 / COM 难度。

前置条件:游戏已启动、DLL 已注入(inject.py)、处于对战(练习或 VS COM)。

用法:
    python scripts/verify_bridge.py [--no-reset]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from bridge.shm import BridgeShm                    # noqa: E402
from th155 import Th155Game                         # noqa: E402
from th155.ops import BattleOps                     # noqa: E402


def hdr(title: str) -> None:
    print(f"\n=== {title} ===")


def reset_round(g: Th155Game, shm: BridgeShm) -> bool:
    """推进到下一个干净回合。

    练习模式:b0~b3 快捷键,1 帧完成。
    VS 模式:计时器归零触发回合结束,等游戏自己走完 64/32->128->4->8(约 8s)。
    """
    practice = bool(shm.read_state().get("is_practice"))
    if practice:
        ack0 = shm.s.ctl_reset_ack
        ok = shm.request_reset(timeout=2.0)
        time.sleep(0.6)
        print(f"  练习重置 ack={ok} ({ack0} -> {shm.s.ctl_reset_ack})")
        return ok
    # VS 模式没有安全的手动重置入口:计时器归零/手动跳演示都会触发游戏自身的
    # std::bad_function_call 崩溃或卡死在 state=32(见 docs/re_notes.md §6/§10)。
    # 这里只校验「对战正在进行、计时器在走」,回合切换交给自然 KO。
    st = shm.read_state()
    s1 = g.battle_value("state")
    t1 = g.battle_value("time")
    time.sleep(0.4)
    t2 = g.battle_value("time")
    ok = s1 == 8 and t1 is not None and t2 is not None and t2 < t1
    print(f"  VS 模式:不做手动重置(自然 KO 切换约 11s);state={s1} time {t1} -> {t2} "
          f"-> {'OK' if ok else 'FAIL'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-reset", action="store_true", help="跳过开局的回合重置")
    a = ap.parse_args()

    g = Th155Game(writable=True)
    shm = BridgeShm.open(timeout=3.0)
    # 等 DLL 发布第一个「活」快照(刚注入时共享内存还可能是空的)
    prev = shm.read_state()["frame"]
    t0 = time.time()
    while time.time() - t0 < 5.0:
        time.sleep(0.05)
        cur = shm.read_state()["frame"]
        if cur != prev:
            break
        prev = cur
    st = shm.read_state()
    if st["battle_state"] not in (2, 4, 8):
        print(f"[x] 当前不在对战中(state={st['battle_state']}),请先进入对战")
        return 2
    if st["battle_state"] != 8:
        # 开场倒计时/载入中:输入会被忽略,先等可操作回合
        print(f"[i] 等待可操作回合(当前 state={st['battle_state']}) ...")
        BattleOps(g).wait_next_round(timeout=30.0)
        st = shm.read_state()
        print(f"[i] state={st['battle_state']} time={st['time']}")
    print(f"[i] frame={st['frame']} state={st['battle_state']} "
          f"practice={st['is_practice']} "
          f"t0={st['teams'][0]['master_name']}+{st['teams'][0]['slave_name']} "
          f"t1={st['teams'][1]['master_name']}+{st['teams'][1]['slave_name']}")

    results = {}

    # 0) 先重开回合,保证双方满血/归位,后续注入测试环境干净
    if not a.no_reset:
        hdr("回合重置 / 对战状态")
        results["reset"] = reset_round(g, shm)
        print(f"  state={shm.read_state()['battle_state']}")

    # 1) 状态读取一致性(DLL 共享内存 vs Python 直读)
    hdr("状态读取一致性")
    f1 = shm.read_state()["frame"]
    time.sleep(0.3)
    f2 = shm.read_state()["frame"]
    print(f"  DLL 帧计数推进 {f1} -> {f2}")
    # 两次采样时刻不同(可能刚好挨打/跨回合),多采几次挑最一致的一对
    best = None
    for _ in range(8):
        py = g.read_state(with_shots=True)
        d0 = shm.read_state()
        score = (d0["battle_state"] == py.battle_state) \
            + (d0["teams"][0]["life"] == py.teams[0].life) \
            + (d0["teams"][1]["life"] == py.teams[1].life)
        if best is None or score > best[0]:
            best = (score, py, d0)
        if score == 3:
            break
        time.sleep(0.15)
    _, py, d0 = best
    checks = {
        "frame": (d0["frame"], py.frame, 60),
        "state": (d0["battle_state"], py.battle_state, 0),
        "time": (d0["time"], py.time, 60),
        "life0": (d0["teams"][0]["life"], py.teams[0].life, 0),
        "life1": (d0["teams"][1]["life"], py.teams[1].life, 0),
        "x0": (d0["teams"][0]["master"]["x"], py.teams[0].master.x, 40.0),
        "y0": (d0["teams"][0]["master"]["y"], py.teams[0].master.y, 40.0),
        "master0": (d0["teams"][0]["master_name"], py.teams[0].master_name, None),
    }
    for k, (x, y, tol) in checks.items():
        ok = (x == y) if tol is None else (abs(x - y) <= tol)
        print(f"  {k:10s} dll={x!r:>22} py={y!r:>22}  {'OK' if ok else 'MISMATCH'}")
        results[f"state_{k}"] = ok

    # 防止反复验证把比赛打满 2 胜(胜负对话无法自动推进)
    try:
        BattleOps(Th155Game(writable=True)).reset_win()
    except Exception:
        pass

    # 2) 输入注入:右移
    hdr("输入注入(1P 右移)")
    # x 是相对朝向的输入(+1 = 朝前):角色贴墙时"朝前"可能是朝墙,
    # 因此两个方向各试一次,任意一次产生位移即通过。
    # 角色可能正处在受击硬直/自身动作中(此时方向键无效),因此最多重试 4 轮。
    moved = 0.0
    detail = []
    for attempt in range(12):
        st_try = shm.read_state()
        a0 = st_try["teams"][0]["master"]
        if a0["hit_state"] != 0:
            time.sleep(0.4)            # 受击硬直中方向键无效,跳过
            continue
        for dx_in in (1, -1):
            x_before = shm.read_state()["teams"][0]["master"]["x"]
            shm.set_action(team=0, x=dx_in)
            time.sleep(0.7)
            x_after = shm.read_state()["teams"][0]["master"]["x"]
            shm.clear_action()
            time.sleep(0.2)
            detail.append(f"x={dx_in:+d}: {x_before:.1f}->{x_after:.1f} (dx={x_after - x_before:+.1f})")
            moved = max(moved, abs(x_after - x_before))
        if moved > 30:
            break
    print("  " + " | ".join(detail))
    results["inject_move"] = moved > 30
    time.sleep(0.2)

    # 3) 输入注入:攻击(改变 motion 或产生弹幕/飞行道具)
    hdr("输入注入(1P 攻击 b0)")
    detail = ""
    attack_ok = False
    for _ in range(6):
        before = shm.read_state()
        shm.set_action(team=0, b0=1)
        time.sleep(0.10)
        mid = shm.read_state()
        shm.set_action(team=0, b0=0)
        time.sleep(0.30)
        after = shm.read_state()
        m_before = before["teams"][0]["master"]["motion"]
        m_mid = mid["teams"][0]["master"]["motion"]
        m_after = after["teams"][0]["master"]["motion"]
        detail = (f"motion {m_before} -> {m_mid} -> {m_after}, "
                  f"shots {len(before['shots'])} -> {len(after['shots'])}")
        if len({m_before, m_mid, m_after}) > 1 or len(after["shots"]) > len(before["shots"]):
            attack_ok = True
            if after["shots"]:
                for idx, s in enumerate(after["shots"]):
                    w = s["box_r"] - s["box_l"]
                    h = s["box_b"] - s["box_t"]
                    print(f"    [SHOT #{idx}] motion={s['motion']}, pos=({s['x']:.1f}, {s['y']:.1f}), box=[{s['box_l']:.1f}, {s['box_t']:.1f}, {s['box_r']:.1f}, {s['box_b']:.1f}], size={w:.1f}x{h:.1f}")
            break
        time.sleep(0.25)
    print(f"  {detail}")
    results["inject_attack"] = attack_ok

    # 3.1) 输入注入:射击 (b1, 产生真实弹幕并验证 Bullet 判定尺寸)
    hdr("输入注入(1P 射击 b1 产生弹幕)")
    for attempt in range(12):
        shm.set_action(team=0, b1=1)
        time.sleep(0.12)
        shm.set_action(team=0, b1=0)
        time.sleep(0.35)
        st_shot = shm.read_state()
        if st_shot["shots"]:
            print(f"  [FOUND SHOTS] 成功捕获 {len(st_shot['shots'])} 个弹幕实体:")
            for idx, s in enumerate(st_shot["shots"]):
                w = s['box_r'] - s['box_l']
                h = s['box_b'] - s['box_t']
                print(f"    Shot #{idx}: motion={s['motion']}, pos=({s['x']:.1f}, {s['y']:.1f}), "
                      f"box=[{s['box_l']:.1f}, {s['box_t']:.1f}, {s['box_r']:.1f}, {s['box_b']:.1f}], "
                      f"size: {w:.1f} x {h:.1f}")
            break
        # 尝试 6B
        shm.set_action(team=0, b1=1, x=1)
        time.sleep(0.12)
        shm.set_action(team=0, b1=0, x=0)
        time.sleep(0.35)
        st_shot = shm.read_state()
        if st_shot["shots"]:
            print(f"  [FOUND SHOTS (6B)] 成功捕获 {len(st_shot['shots'])} 个弹幕实体:")
            for idx, s in enumerate(st_shot["shots"]):
                w = s['box_r'] - s['box_l']
                h = s['box_b'] - s['box_t']
                print(f"    Shot #{idx}: motion={s['motion']}, pos=({s['x']:.1f}, {s['y']:.1f}), "
                      f"box=[{s['box_l']:.1f}, {s['box_t']:.1f}, {s['box_r']:.1f}, {s['box_b']:.1f}], "
                      f"size: {w:.1f} x {h:.1f}")
            break

    # 4) COM:只读校验(运行期写 com_* 字段会崩,难度必须在对局前通过配置表决定)
    hdr("COM 难度(只读校验)")
    st3 = shm.read_state()
    t1 = st3["teams"][1]
    def val(*path):
        p = g.get_path(*path)
        return g.r.value(p) if p else None

    cfg_vs = val("config", "difficulty", "vs")
    print(f"  config.difficulty.vs={cfg_vs} | team1 com_difficulty={t1['com_difficulty']} "
          f"com_level={t1['com_level']} guard={t1.get('com_guard_rate')}")
    results["com_difficulty"] = t1["com_difficulty"] is not None

    hdr("结果")
    for k, v in results.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    shm.close()
    ok = all(results.values())
    print("\n[OK] 桥接 DLL 全部验证通过" if ok else "\n[FAIL] 存在未通过项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
