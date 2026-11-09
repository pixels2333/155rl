@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat" >nul 2>&1
dumpbin /symbols kernel32.lib > syms.txt 2>&1
echo done
