@echo off
rem 编译到备用文件名:主进程已注入 th155_rl_bridge.dll 时它被锁住,无法原地覆盖。
rem 产出的 th155_rl_bridge_next.dll 需在游戏退出后改名替换(见 docs)。
setlocal
cd /d "%~dp0"
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat"
if not exist "%VCVARS%" (
  echo [x] vcvars32.bat not found
  exit /b 1
)
if not defined VCINSTALLDIR call "%VCVARS%" >nul
python "..\..\scripts\gen_offsets_header.py" || exit /b 1
cl /nologo /c /O2 /GS- /GR- /EHs-c- /utf-8 /D_WIN32 /DWIN32 /Fo:th155_rl_bridge_next.obj th155_rl_bridge.cpp || exit /b 1
link /nologo /DLL /NODEFAULTLIB /ENTRY:DllMain /MACHINE:X86 ^
  /OUT:th155_rl_bridge_next.dll /MAP:th155_rl_bridge_next.map th155_rl_bridge_next.obj || exit /b 1
echo BUILD_OK
endlocal
