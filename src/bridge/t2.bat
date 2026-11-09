@echo off
set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat"
echo VCINSTALLDIR=[%VCINSTALLDIR%]
call "%VCVARS%"
echo after call VCINSTALLDIR=[%VCINSTALLDIR%]
echo LIB=[%LIB%]
