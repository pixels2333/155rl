"""端到端:启动游戏 -> 注入 DLL -> 进入对战 -> 运行桥接验证。

默认使用 VS COM 模式:练习模式(player2=3)会触发游戏自身的
std::bad_function_call 崩溃,详见 docs/re_notes.md §6.1。

用法:
    python scripts/e2e.py                       # 重启游戏,VS COM 难度1
    python scripts/e2e.py --mode practice       # 练习模式(player2=0 站桩靶)
    python scripts/e2e.py --difficulty 2 --no-restart
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from th155 import Th155Game            # noqa: E402
from th155.menu import MenuDriver      # noqa: E402
import game_session                    # noqa: E402


def ensure_battle(g: Th155Game, mode: str = "practice", difficulty: int = 1) -> bool:
    m = MenuDriver(g)
    st = g.read_state(with_shots=False)
    if st.battle_state in (2, 4, 8):
        return True
    # 关键:新启动的游戏从未收到真实按键 -> input_all.GetLastDevice() = -2,
    # 于是 character_select 无法创建输入设备。写 last_device = -1(键盘)即可。
    m.seed_last_device()
    if mode == "practice":
        print("[w] 练习模式在自动化下不稳定(数秒后 std::bad_function_call),"
              "仅用于调试;训练请用默认的 vs_com")
        from th155.ops import BattleOps
        BattleOps(g).set_practice_config(player2=0, difficulty=difficulty)
        m.select_title_item("practice")
        time.sleep(0.8)
        m.seed_last_device()
        return m.advance_select(timeout=90)
    return m.enter_vs_com(difficulty)


def main() -> int:
    restart = "--no-restart" not in sys.argv
    mode = "vs_com"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    difficulty = 1
    if "--difficulty" in sys.argv:
        difficulty = int(sys.argv[sys.argv.index("--difficulty") + 1])
    if restart:
        game_session.kill()
        game_session.start(inject_dll=False)
    pid = game_session.start(inject_dll=False) if not restart else None
    from bridge.inject import inject
    from th155.mem import find_pid
    from bridge.shm import BridgeShm
    pid = find_pid("th155.exe")
    try:
        hmod = inject(pid, str(game_session.DLL))
        print(f"[ok] DLL 注入 hmod={hmod:#x}")
    except Exception as e:
        print(f"[w] 注入跳过({e})")

    g = Th155Game(writable=True)
    t0 = time.time()
    ok = ensure_battle(g, mode, difficulty)
    print(f"[{'ok' if ok else 'x'}] 进入 {mode} 对战 ({time.time()-t0:.1f}s) "
          f"state={g.battle_value('state')}")
    if not ok:
        return 1
    time.sleep(1.0)
    return __import__("subprocess").call(
        [sys.executable, str(ROOT / "scripts" / "verify_bridge.py")])


if __name__ == "__main__":
    raise SystemExit(main())
