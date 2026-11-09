@echo off
rem ===========================================================================
rem  Build th155_rl_bridge.dll (x86 / 32-bit), no Windows SDK headers, no CRT.
rem    1) cl  : compile the single .cpp (only project headers)
rem    2) lib : generate the kernel32 import library from kernel32.def
rem    3) link: /DLL /NODEFAULTLIB minimal DLL
rem  Requires Visual Studio 2022 BuildTools (x86 toolset).
rem ===========================================================================
setlocal
cd /d "%~dp0"

set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat"
if not exist "%VCVARS%" (
  echo [x] vcvars32.bat not found: "%VCVARS%"
  exit /b 1
)
if not defined VCINSTALLDIR call "%VCVARS%" >nul

echo [1/3] generating offsets header ...
python "..\..\scripts\gen_offsets_header.py" || exit /b 1

echo [2/3] compiling ...
cl /nologo /c /O2 /GS- /GR- /EHs-c- /utf-8 /D_WIN32 /DWIN32 /Fo:th155_rl_bridge.obj th155_rl_bridge.cpp || exit /b 1

echo [3/4] linking (no import lib, no CRT) ...
link /nologo /DLL /NODEFAULTLIB /ENTRY:DllMain /MACHINE:X86 /OUT:th155_rl_bridge.dll /MAP:th155_rl_bridge.map th155_rl_bridge.obj || exit /b 1

echo BUILD_OK
endlocal
