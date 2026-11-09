@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat" >nul 2>&1
lib /nologo /def:k2.def /machine:x86 /out:k2.lib
dumpbin /symbols k2.lib > syms2.txt 2>&1
echo done
