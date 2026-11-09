#include "proxy_winmm.h"
#include "mod_loader.h"

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    switch (ul_reason_for_call) {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hModule);
        InitRealWinmm();
        InitModLoader(hModule);
        break;
    case DLL_PROCESS_DETACH:
        ShutdownModLoader();
        break;
    }
    return TRUE;
}
