import time
import subprocess
from pathlib import Path

LOG_PATH = Path("D:/programme/155rl/.scratch/train_60hz_coldstart.log")
OUT_LOG = Path("D:/programme/155rl/.scratch/ticket10_restart.log")

def log(msg):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}\n"
    print(line, end="", flush=True)
    with open(OUT_LOG, "a", encoding="utf-8") as f:
        f.write(line)

log("自动化等待监听启动: 等待 Update 765 最小单元作业 (30 局定标) 完成...")

while True:
    time.sleep(10)
    if not LOG_PATH.exists():
        continue
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-30:]
        text = "".join(lines)
        if "eval @ update 765" in text and "matches=30 decided=30" in text:
            # 找到匹配行
            for l in lines:
                if "eval @ update 765" in l and "matches=30" in l:
                    log(f"检测到 Update 765 定标完成: {l.strip()}")
            break
    except Exception as e:
        pass

log("最小作业单元已完结! 正在执行标准冷加载退出: 终止旧训练进程 (PID 13380) 与游戏进程 (PID 19028)...")
subprocess.run(["taskkill", "/F", "/PID", "13380"], capture_output=True)
subprocess.run(["taskkill", "/F", "/PID", "19028"], capture_output=True)
log("旧进程已干净终止! 看门狗进程 (PID 20372) 将在 5 秒内自动由最新 checkpoint 拉起全新冷加载进程...")
