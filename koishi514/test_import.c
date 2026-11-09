#include <windows.h>
#include <stdio.h>
#pragma comment(lib, "winmm.lib")

int main() {
    printf("Starting test_import...\n");
    timeBeginPeriod(1);
    printf("timeGetTime: %lu\n", timeGetTime());
    timeEndPeriod(1);
    printf("Done!\n");
    return 0;
}
