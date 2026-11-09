@echo off
chcp 65001 >nul
title 东方凭依华 - 古明地恋心绮楼符卡Mod一键启动器
echo ========================================================
echo   东方凭依华 (TH15.5) - 古明地恋心绮楼 (TH13.5) 符卡 Mod
echo ========================================================
echo.
echo [*] 正在启动游戏并应用内存补丁...
python "%~dp0apply_koishi514_mod.py"
if %errorlevel% neq 0 (
    echo.
    echo [!] 启动或注入失败，请检查 Python 环境及是否有杀毒软件拦截。
) else (
    echo.
    echo [√] Mod 已成功激活！请直接在游戏窗口中畅玩。
)
echo.
pause
