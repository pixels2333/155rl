#include <windows.h>
#include <stdio.h>

int main() {
    printf("Testing LoadLibrary...\n");
    HMODULE hLocal = LoadLibraryA("winmm.dll");
    char localPath[MAX_PATH] = {0};
    GetModuleFileNameA(hLocal, localPath, MAX_PATH);
    printf("hLocal: 0x%p, Path: %s\n", (void*)hLocal, localPath);

    char sysDir[MAX_PATH];
    GetSystemDirectoryA(sysDir, MAX_PATH);
    strcat_s(sysDir, sizeof(sysDir), "\\winmm.dll");
    HMODULE hSys = LoadLibraryA(sysDir);
    char sysPath[MAX_PATH] = {0};
    GetModuleFileNameA(hSys, sysPath, MAX_PATH);
    printf("hSys: 0x%p, Path: %s\n", (void*)hSys, sysPath);
    printf("Equal? %s\n", (hLocal == hSys) ? "YES (BUG!)" : "NO (Different)");

    FARPROC pSysTime = GetProcAddress(hSys, "timeBeginPeriod");
    printf("pSysTime: 0x%p\n", (void*)pSysTime);

    typedef MMRESULT (WINAPI *fn_timeBeginPeriod)(UINT);
    fn_timeBeginPeriod my_timeBeginPeriod = (fn_timeBeginPeriod)GetProcAddress(hLocal, "timeBeginPeriod");
    printf("my_timeBeginPeriod export from hLocal: 0x%p\n", (void*)my_timeBeginPeriod);
    printf("Calling my_timeBeginPeriod(1)...\n");
    MMRESULT res = my_timeBeginPeriod(1);
    printf("Result: %u\n", res);

    typedef DWORD (WINAPI *fn_timeGetTime)();
    fn_timeGetTime my_timeGetTime = (fn_timeGetTime)GetProcAddress(hLocal, "timeGetTime");
    printf("my_timeGetTime export from hLocal: 0x%p\n", (void*)my_timeGetTime);
    printf("Calling my_timeGetTime()...\n");
    DWORD t = my_timeGetTime();
    printf("Result timeGetTime: %lu\n", t);
    return 0;
}
