@echo off
setlocal
cd /d "%~dp0"
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat"
if not exist "%VCVARS%" ( echo [x] vcvars missing & exit /b 1 )
if not defined VCINSTALLDIR call "%VCVARS%" >nul
cl /nologo /c /O2 /GS- /GR- /EHs-c- /utf-8 /Fo:test_host.obj test_host.cpp || exit /b 1
link /nologo /NODEFAULTLIB /ENTRY:entry /SUBSYSTEM:CONSOLE /MACHINE:X86 /OUT:test_host.exe test_host.obj || exit /b 1
echo HOST_BUILD_OK
endlocal
