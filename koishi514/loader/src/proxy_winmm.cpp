#include "proxy_winmm.h"
#include <stdio.h>

HMODULE g_hRealWinmm = NULL;
FARPROC g_pfn[193] = {0};

void InitRealWinmm() {
    if (g_hRealWinmm) return;
    char sysDir[MAX_PATH];
    UINT len = GetSystemDirectoryA(sysDir, MAX_PATH);
    if (len > 0) {
        strcat_s(sysDir, sizeof(sysDir), "\\winmm.dll");
        g_hRealWinmm = LoadLibraryA(sysDir);
    }
    if (!g_hRealWinmm) {
        g_hRealWinmm = LoadLibraryA("C:\\Windows\\System32\\winmm.dll");
    }
    if (!g_hRealWinmm) return;

    g_pfn[0] = GetProcAddress(g_hRealWinmm, MAKEINTRESOURCEA(2));
    g_pfn[1] = GetProcAddress(g_hRealWinmm, "mciExecute");
    g_pfn[2] = GetProcAddress(g_hRealWinmm, "CloseDriver");
    g_pfn[3] = GetProcAddress(g_hRealWinmm, "DefDriverProc");
    g_pfn[4] = GetProcAddress(g_hRealWinmm, "DriverCallback");
    g_pfn[5] = GetProcAddress(g_hRealWinmm, "DrvGetModuleHandle");
    g_pfn[6] = GetProcAddress(g_hRealWinmm, "GetDriverModuleHandle");
    g_pfn[7] = GetProcAddress(g_hRealWinmm, "NotifyCallbackData");
    g_pfn[8] = GetProcAddress(g_hRealWinmm, "OpenDriver");
    g_pfn[9] = GetProcAddress(g_hRealWinmm, "PlaySound");
    g_pfn[10] = GetProcAddress(g_hRealWinmm, "PlaySoundA");
    g_pfn[11] = GetProcAddress(g_hRealWinmm, "PlaySoundW");
    g_pfn[12] = GetProcAddress(g_hRealWinmm, "SendDriverMessage");
    g_pfn[13] = GetProcAddress(g_hRealWinmm, "WOW32DriverCallback");
    g_pfn[14] = GetProcAddress(g_hRealWinmm, "WOW32ResolveMultiMediaHandle");
    g_pfn[15] = GetProcAddress(g_hRealWinmm, "WOWAppExit");
    g_pfn[16] = GetProcAddress(g_hRealWinmm, "aux32Message");
    g_pfn[17] = GetProcAddress(g_hRealWinmm, "auxGetDevCapsA");
    g_pfn[18] = GetProcAddress(g_hRealWinmm, "auxGetDevCapsW");
    g_pfn[19] = GetProcAddress(g_hRealWinmm, "auxGetNumDevs");
    g_pfn[20] = GetProcAddress(g_hRealWinmm, "auxGetVolume");
    g_pfn[21] = GetProcAddress(g_hRealWinmm, "auxOutMessage");
    g_pfn[22] = GetProcAddress(g_hRealWinmm, "auxSetVolume");
    g_pfn[23] = GetProcAddress(g_hRealWinmm, "joy32Message");
    g_pfn[24] = GetProcAddress(g_hRealWinmm, "joyConfigChanged");
    g_pfn[25] = GetProcAddress(g_hRealWinmm, "joyGetDevCapsA");
    g_pfn[26] = GetProcAddress(g_hRealWinmm, "joyGetDevCapsW");
    g_pfn[27] = GetProcAddress(g_hRealWinmm, "joyGetNumDevs");
    g_pfn[28] = GetProcAddress(g_hRealWinmm, "joyGetPos");
    g_pfn[29] = GetProcAddress(g_hRealWinmm, "joyGetPosEx");
    g_pfn[30] = GetProcAddress(g_hRealWinmm, "joyGetThreshold");
    g_pfn[31] = GetProcAddress(g_hRealWinmm, "joyReleaseCapture");
    g_pfn[32] = GetProcAddress(g_hRealWinmm, "joySetCapture");
    g_pfn[33] = GetProcAddress(g_hRealWinmm, "joySetThreshold");
    g_pfn[34] = GetProcAddress(g_hRealWinmm, "mci32Message");
    g_pfn[35] = GetProcAddress(g_hRealWinmm, "mciDriverNotify");
    g_pfn[36] = GetProcAddress(g_hRealWinmm, "mciDriverYield");
    g_pfn[37] = GetProcAddress(g_hRealWinmm, "mciFreeCommandResource");
    g_pfn[38] = GetProcAddress(g_hRealWinmm, "mciGetCreatorTask");
    g_pfn[39] = GetProcAddress(g_hRealWinmm, "mciGetDeviceIDA");
    g_pfn[40] = GetProcAddress(g_hRealWinmm, "mciGetDeviceIDFromElementIDA");
    g_pfn[41] = GetProcAddress(g_hRealWinmm, "mciGetDeviceIDFromElementIDW");
    g_pfn[42] = GetProcAddress(g_hRealWinmm, "mciGetDeviceIDW");
    g_pfn[43] = GetProcAddress(g_hRealWinmm, "mciGetDriverData");
    g_pfn[44] = GetProcAddress(g_hRealWinmm, "mciGetErrorStringA");
    g_pfn[45] = GetProcAddress(g_hRealWinmm, "mciGetErrorStringW");
    g_pfn[46] = GetProcAddress(g_hRealWinmm, "mciGetYieldProc");
    g_pfn[47] = GetProcAddress(g_hRealWinmm, "mciLoadCommandResource");
    g_pfn[48] = GetProcAddress(g_hRealWinmm, "mciSendCommandA");
    g_pfn[49] = GetProcAddress(g_hRealWinmm, "mciSendCommandW");
    g_pfn[50] = GetProcAddress(g_hRealWinmm, "mciSendStringA");
    g_pfn[51] = GetProcAddress(g_hRealWinmm, "mciSendStringW");
    g_pfn[52] = GetProcAddress(g_hRealWinmm, "mciSetDriverData");
    g_pfn[53] = GetProcAddress(g_hRealWinmm, "mciSetYieldProc");
    g_pfn[54] = GetProcAddress(g_hRealWinmm, "mid32Message");
    g_pfn[55] = GetProcAddress(g_hRealWinmm, "midiConnect");
    g_pfn[56] = GetProcAddress(g_hRealWinmm, "midiDisconnect");
    g_pfn[57] = GetProcAddress(g_hRealWinmm, "midiInAddBuffer");
    g_pfn[58] = GetProcAddress(g_hRealWinmm, "midiInClose");
    g_pfn[59] = GetProcAddress(g_hRealWinmm, "midiInGetDevCapsA");
    g_pfn[60] = GetProcAddress(g_hRealWinmm, "midiInGetDevCapsW");
    g_pfn[61] = GetProcAddress(g_hRealWinmm, "midiInGetErrorTextA");
    g_pfn[62] = GetProcAddress(g_hRealWinmm, "midiInGetErrorTextW");
    g_pfn[63] = GetProcAddress(g_hRealWinmm, "midiInGetID");
    g_pfn[64] = GetProcAddress(g_hRealWinmm, "midiInGetNumDevs");
    g_pfn[65] = GetProcAddress(g_hRealWinmm, "midiInMessage");
    g_pfn[66] = GetProcAddress(g_hRealWinmm, "midiInOpen");
    g_pfn[67] = GetProcAddress(g_hRealWinmm, "midiInPrepareHeader");
    g_pfn[68] = GetProcAddress(g_hRealWinmm, "midiInReset");
    g_pfn[69] = GetProcAddress(g_hRealWinmm, "midiInStart");
    g_pfn[70] = GetProcAddress(g_hRealWinmm, "midiInStop");
    g_pfn[71] = GetProcAddress(g_hRealWinmm, "midiInUnprepareHeader");
    g_pfn[72] = GetProcAddress(g_hRealWinmm, "midiOutCacheDrumPatches");
    g_pfn[73] = GetProcAddress(g_hRealWinmm, "midiOutCachePatches");
    g_pfn[74] = GetProcAddress(g_hRealWinmm, "midiOutClose");
    g_pfn[75] = GetProcAddress(g_hRealWinmm, "midiOutGetDevCapsA");
    g_pfn[76] = GetProcAddress(g_hRealWinmm, "midiOutGetDevCapsW");
    g_pfn[77] = GetProcAddress(g_hRealWinmm, "midiOutGetErrorTextA");
    g_pfn[78] = GetProcAddress(g_hRealWinmm, "midiOutGetErrorTextW");
    g_pfn[79] = GetProcAddress(g_hRealWinmm, "midiOutGetID");
    g_pfn[80] = GetProcAddress(g_hRealWinmm, "midiOutGetNumDevs");
    g_pfn[81] = GetProcAddress(g_hRealWinmm, "midiOutGetVolume");
    g_pfn[82] = GetProcAddress(g_hRealWinmm, "midiOutLongMsg");
    g_pfn[83] = GetProcAddress(g_hRealWinmm, "midiOutMessage");
    g_pfn[84] = GetProcAddress(g_hRealWinmm, "midiOutOpen");
    g_pfn[85] = GetProcAddress(g_hRealWinmm, "midiOutPrepareHeader");
    g_pfn[86] = GetProcAddress(g_hRealWinmm, "midiOutReset");
    g_pfn[87] = GetProcAddress(g_hRealWinmm, "midiOutSetVolume");
    g_pfn[88] = GetProcAddress(g_hRealWinmm, "midiOutShortMsg");
    g_pfn[89] = GetProcAddress(g_hRealWinmm, "midiOutUnprepareHeader");
    g_pfn[90] = GetProcAddress(g_hRealWinmm, "midiStreamClose");
    g_pfn[91] = GetProcAddress(g_hRealWinmm, "midiStreamOpen");
    g_pfn[92] = GetProcAddress(g_hRealWinmm, "midiStreamOut");
    g_pfn[93] = GetProcAddress(g_hRealWinmm, "midiStreamPause");
    g_pfn[94] = GetProcAddress(g_hRealWinmm, "midiStreamPosition");
    g_pfn[95] = GetProcAddress(g_hRealWinmm, "midiStreamProperty");
    g_pfn[96] = GetProcAddress(g_hRealWinmm, "midiStreamRestart");
    g_pfn[97] = GetProcAddress(g_hRealWinmm, "midiStreamStop");
    g_pfn[98] = GetProcAddress(g_hRealWinmm, "mixerClose");
    g_pfn[99] = GetProcAddress(g_hRealWinmm, "mixerGetControlDetailsA");
    g_pfn[100] = GetProcAddress(g_hRealWinmm, "mixerGetControlDetailsW");
    g_pfn[101] = GetProcAddress(g_hRealWinmm, "mixerGetDevCapsA");
    g_pfn[102] = GetProcAddress(g_hRealWinmm, "mixerGetDevCapsW");
    g_pfn[103] = GetProcAddress(g_hRealWinmm, "mixerGetID");
    g_pfn[104] = GetProcAddress(g_hRealWinmm, "mixerGetLineControlsA");
    g_pfn[105] = GetProcAddress(g_hRealWinmm, "mixerGetLineControlsW");
    g_pfn[106] = GetProcAddress(g_hRealWinmm, "mixerGetLineInfoA");
    g_pfn[107] = GetProcAddress(g_hRealWinmm, "mixerGetLineInfoW");
    g_pfn[108] = GetProcAddress(g_hRealWinmm, "mixerGetNumDevs");
    g_pfn[109] = GetProcAddress(g_hRealWinmm, "mixerMessage");
    g_pfn[110] = GetProcAddress(g_hRealWinmm, "mixerOpen");
    g_pfn[111] = GetProcAddress(g_hRealWinmm, "mixerSetControlDetails");
    g_pfn[112] = GetProcAddress(g_hRealWinmm, "mmDrvInstall");
    g_pfn[113] = GetProcAddress(g_hRealWinmm, "mmGetCurrentTask");
    g_pfn[114] = GetProcAddress(g_hRealWinmm, "mmTaskBlock");
    g_pfn[115] = GetProcAddress(g_hRealWinmm, "mmTaskCreate");
    g_pfn[116] = GetProcAddress(g_hRealWinmm, "mmTaskSignal");
    g_pfn[117] = GetProcAddress(g_hRealWinmm, "mmTaskYield");
    g_pfn[118] = GetProcAddress(g_hRealWinmm, "mmioAdvance");
    g_pfn[119] = GetProcAddress(g_hRealWinmm, "mmioAscend");
    g_pfn[120] = GetProcAddress(g_hRealWinmm, "mmioClose");
    g_pfn[121] = GetProcAddress(g_hRealWinmm, "mmioCreateChunk");
    g_pfn[122] = GetProcAddress(g_hRealWinmm, "mmioDescend");
    g_pfn[123] = GetProcAddress(g_hRealWinmm, "mmioFlush");
    g_pfn[124] = GetProcAddress(g_hRealWinmm, "mmioGetInfo");
    g_pfn[125] = GetProcAddress(g_hRealWinmm, "mmioInstallIOProcA");
    g_pfn[126] = GetProcAddress(g_hRealWinmm, "mmioInstallIOProcW");
    g_pfn[127] = GetProcAddress(g_hRealWinmm, "mmioOpenA");
    g_pfn[128] = GetProcAddress(g_hRealWinmm, "mmioOpenW");
    g_pfn[129] = GetProcAddress(g_hRealWinmm, "mmioRead");
    g_pfn[130] = GetProcAddress(g_hRealWinmm, "mmioRenameA");
    g_pfn[131] = GetProcAddress(g_hRealWinmm, "mmioRenameW");
    g_pfn[132] = GetProcAddress(g_hRealWinmm, "mmioSeek");
    g_pfn[133] = GetProcAddress(g_hRealWinmm, "mmioSendMessage");
    g_pfn[134] = GetProcAddress(g_hRealWinmm, "mmioSetBuffer");
    g_pfn[135] = GetProcAddress(g_hRealWinmm, "mmioSetInfo");
    g_pfn[136] = GetProcAddress(g_hRealWinmm, "mmioStringToFOURCCA");
    g_pfn[137] = GetProcAddress(g_hRealWinmm, "mmioStringToFOURCCW");
    g_pfn[138] = GetProcAddress(g_hRealWinmm, "mmioWrite");
    g_pfn[139] = GetProcAddress(g_hRealWinmm, "mmsystemGetVersion");
    g_pfn[140] = GetProcAddress(g_hRealWinmm, "mod32Message");
    g_pfn[141] = GetProcAddress(g_hRealWinmm, "mxd32Message");
    g_pfn[142] = GetProcAddress(g_hRealWinmm, "sndPlaySoundA");
    g_pfn[143] = GetProcAddress(g_hRealWinmm, "sndPlaySoundW");
    g_pfn[144] = GetProcAddress(g_hRealWinmm, "tid32Message");
    g_pfn[145] = GetProcAddress(g_hRealWinmm, "timeBeginPeriod");
    g_pfn[146] = GetProcAddress(g_hRealWinmm, "timeEndPeriod");
    g_pfn[147] = GetProcAddress(g_hRealWinmm, "timeGetDevCaps");
    g_pfn[148] = GetProcAddress(g_hRealWinmm, "timeGetSystemTime");
    g_pfn[149] = GetProcAddress(g_hRealWinmm, "timeGetTime");
    g_pfn[150] = GetProcAddress(g_hRealWinmm, "timeKillEvent");
    g_pfn[151] = GetProcAddress(g_hRealWinmm, "timeSetEvent");
    g_pfn[152] = GetProcAddress(g_hRealWinmm, "waveInAddBuffer");
    g_pfn[153] = GetProcAddress(g_hRealWinmm, "waveInClose");
    g_pfn[154] = GetProcAddress(g_hRealWinmm, "waveInGetDevCapsA");
    g_pfn[155] = GetProcAddress(g_hRealWinmm, "waveInGetDevCapsW");
    g_pfn[156] = GetProcAddress(g_hRealWinmm, "waveInGetErrorTextA");
    g_pfn[157] = GetProcAddress(g_hRealWinmm, "waveInGetErrorTextW");
    g_pfn[158] = GetProcAddress(g_hRealWinmm, "waveInGetID");
    g_pfn[159] = GetProcAddress(g_hRealWinmm, "waveInGetNumDevs");
    g_pfn[160] = GetProcAddress(g_hRealWinmm, "waveInGetPosition");
    g_pfn[161] = GetProcAddress(g_hRealWinmm, "waveInMessage");
    g_pfn[162] = GetProcAddress(g_hRealWinmm, "waveInOpen");
    g_pfn[163] = GetProcAddress(g_hRealWinmm, "waveInPrepareHeader");
    g_pfn[164] = GetProcAddress(g_hRealWinmm, "waveInReset");
    g_pfn[165] = GetProcAddress(g_hRealWinmm, "waveInStart");
    g_pfn[166] = GetProcAddress(g_hRealWinmm, "waveInStop");
    g_pfn[167] = GetProcAddress(g_hRealWinmm, "waveInUnprepareHeader");
    g_pfn[168] = GetProcAddress(g_hRealWinmm, "waveOutBreakLoop");
    g_pfn[169] = GetProcAddress(g_hRealWinmm, "waveOutClose");
    g_pfn[170] = GetProcAddress(g_hRealWinmm, "waveOutGetDevCapsA");
    g_pfn[171] = GetProcAddress(g_hRealWinmm, "waveOutGetDevCapsW");
    g_pfn[172] = GetProcAddress(g_hRealWinmm, "waveOutGetErrorTextA");
    g_pfn[173] = GetProcAddress(g_hRealWinmm, "waveOutGetErrorTextW");
    g_pfn[174] = GetProcAddress(g_hRealWinmm, "waveOutGetID");
    g_pfn[175] = GetProcAddress(g_hRealWinmm, "waveOutGetNumDevs");
    g_pfn[176] = GetProcAddress(g_hRealWinmm, "waveOutGetPitch");
    g_pfn[177] = GetProcAddress(g_hRealWinmm, "waveOutGetPlaybackRate");
    g_pfn[178] = GetProcAddress(g_hRealWinmm, "waveOutGetPosition");
    g_pfn[179] = GetProcAddress(g_hRealWinmm, "waveOutGetVolume");
    g_pfn[180] = GetProcAddress(g_hRealWinmm, "waveOutMessage");
    g_pfn[181] = GetProcAddress(g_hRealWinmm, "waveOutOpen");
    g_pfn[182] = GetProcAddress(g_hRealWinmm, "waveOutPause");
    g_pfn[183] = GetProcAddress(g_hRealWinmm, "waveOutPrepareHeader");
    g_pfn[184] = GetProcAddress(g_hRealWinmm, "waveOutReset");
    g_pfn[185] = GetProcAddress(g_hRealWinmm, "waveOutRestart");
    g_pfn[186] = GetProcAddress(g_hRealWinmm, "waveOutSetPitch");
    g_pfn[187] = GetProcAddress(g_hRealWinmm, "waveOutSetPlaybackRate");
    g_pfn[188] = GetProcAddress(g_hRealWinmm, "waveOutSetVolume");
    g_pfn[189] = GetProcAddress(g_hRealWinmm, "waveOutUnprepareHeader");
    g_pfn[190] = GetProcAddress(g_hRealWinmm, "waveOutWrite");
    g_pfn[191] = GetProcAddress(g_hRealWinmm, "wid32Message");
    g_pfn[192] = GetProcAddress(g_hRealWinmm, "wod32Message");
}

extern "C" {
__declspec(naked) void Proxy_0() { __asm jmp dword ptr [g_pfn + 0 * 4] }
__declspec(naked) void Proxy_1() { __asm jmp dword ptr [g_pfn + 1 * 4] }
__declspec(naked) void Proxy_2() { __asm jmp dword ptr [g_pfn + 2 * 4] }
__declspec(naked) void Proxy_3() { __asm jmp dword ptr [g_pfn + 3 * 4] }
__declspec(naked) void Proxy_4() { __asm jmp dword ptr [g_pfn + 4 * 4] }
__declspec(naked) void Proxy_5() { __asm jmp dword ptr [g_pfn + 5 * 4] }
__declspec(naked) void Proxy_6() { __asm jmp dword ptr [g_pfn + 6 * 4] }
__declspec(naked) void Proxy_7() { __asm jmp dword ptr [g_pfn + 7 * 4] }
__declspec(naked) void Proxy_8() { __asm jmp dword ptr [g_pfn + 8 * 4] }
__declspec(naked) void Proxy_9() { __asm jmp dword ptr [g_pfn + 9 * 4] }
__declspec(naked) void Proxy_10() { __asm jmp dword ptr [g_pfn + 10 * 4] }
__declspec(naked) void Proxy_11() { __asm jmp dword ptr [g_pfn + 11 * 4] }
__declspec(naked) void Proxy_12() { __asm jmp dword ptr [g_pfn + 12 * 4] }
__declspec(naked) void Proxy_13() { __asm jmp dword ptr [g_pfn + 13 * 4] }
__declspec(naked) void Proxy_14() { __asm jmp dword ptr [g_pfn + 14 * 4] }
__declspec(naked) void Proxy_15() { __asm jmp dword ptr [g_pfn + 15 * 4] }
__declspec(naked) void Proxy_16() { __asm jmp dword ptr [g_pfn + 16 * 4] }
__declspec(naked) void Proxy_17() { __asm jmp dword ptr [g_pfn + 17 * 4] }
__declspec(naked) void Proxy_18() { __asm jmp dword ptr [g_pfn + 18 * 4] }
__declspec(naked) void Proxy_19() { __asm jmp dword ptr [g_pfn + 19 * 4] }
__declspec(naked) void Proxy_20() { __asm jmp dword ptr [g_pfn + 20 * 4] }
__declspec(naked) void Proxy_21() { __asm jmp dword ptr [g_pfn + 21 * 4] }
__declspec(naked) void Proxy_22() { __asm jmp dword ptr [g_pfn + 22 * 4] }
__declspec(naked) void Proxy_23() { __asm jmp dword ptr [g_pfn + 23 * 4] }
__declspec(naked) void Proxy_24() { __asm jmp dword ptr [g_pfn + 24 * 4] }
__declspec(naked) void Proxy_25() { __asm jmp dword ptr [g_pfn + 25 * 4] }
__declspec(naked) void Proxy_26() { __asm jmp dword ptr [g_pfn + 26 * 4] }
__declspec(naked) void Proxy_27() { __asm jmp dword ptr [g_pfn + 27 * 4] }
__declspec(naked) void Proxy_28() { __asm jmp dword ptr [g_pfn + 28 * 4] }
__declspec(naked) void Proxy_29() { __asm jmp dword ptr [g_pfn + 29 * 4] }
__declspec(naked) void Proxy_30() { __asm jmp dword ptr [g_pfn + 30 * 4] }
__declspec(naked) void Proxy_31() { __asm jmp dword ptr [g_pfn + 31 * 4] }
__declspec(naked) void Proxy_32() { __asm jmp dword ptr [g_pfn + 32 * 4] }
__declspec(naked) void Proxy_33() { __asm jmp dword ptr [g_pfn + 33 * 4] }
__declspec(naked) void Proxy_34() { __asm jmp dword ptr [g_pfn + 34 * 4] }
__declspec(naked) void Proxy_35() { __asm jmp dword ptr [g_pfn + 35 * 4] }
__declspec(naked) void Proxy_36() { __asm jmp dword ptr [g_pfn + 36 * 4] }
__declspec(naked) void Proxy_37() { __asm jmp dword ptr [g_pfn + 37 * 4] }
__declspec(naked) void Proxy_38() { __asm jmp dword ptr [g_pfn + 38 * 4] }
__declspec(naked) void Proxy_39() { __asm jmp dword ptr [g_pfn + 39 * 4] }
__declspec(naked) void Proxy_40() { __asm jmp dword ptr [g_pfn + 40 * 4] }
__declspec(naked) void Proxy_41() { __asm jmp dword ptr [g_pfn + 41 * 4] }
__declspec(naked) void Proxy_42() { __asm jmp dword ptr [g_pfn + 42 * 4] }
__declspec(naked) void Proxy_43() { __asm jmp dword ptr [g_pfn + 43 * 4] }
__declspec(naked) void Proxy_44() { __asm jmp dword ptr [g_pfn + 44 * 4] }
__declspec(naked) void Proxy_45() { __asm jmp dword ptr [g_pfn + 45 * 4] }
__declspec(naked) void Proxy_46() { __asm jmp dword ptr [g_pfn + 46 * 4] }
__declspec(naked) void Proxy_47() { __asm jmp dword ptr [g_pfn + 47 * 4] }
__declspec(naked) void Proxy_48() { __asm jmp dword ptr [g_pfn + 48 * 4] }
__declspec(naked) void Proxy_49() { __asm jmp dword ptr [g_pfn + 49 * 4] }
__declspec(naked) void Proxy_50() { __asm jmp dword ptr [g_pfn + 50 * 4] }
__declspec(naked) void Proxy_51() { __asm jmp dword ptr [g_pfn + 51 * 4] }
__declspec(naked) void Proxy_52() { __asm jmp dword ptr [g_pfn + 52 * 4] }
__declspec(naked) void Proxy_53() { __asm jmp dword ptr [g_pfn + 53 * 4] }
__declspec(naked) void Proxy_54() { __asm jmp dword ptr [g_pfn + 54 * 4] }
__declspec(naked) void Proxy_55() { __asm jmp dword ptr [g_pfn + 55 * 4] }
__declspec(naked) void Proxy_56() { __asm jmp dword ptr [g_pfn + 56 * 4] }
__declspec(naked) void Proxy_57() { __asm jmp dword ptr [g_pfn + 57 * 4] }
__declspec(naked) void Proxy_58() { __asm jmp dword ptr [g_pfn + 58 * 4] }
__declspec(naked) void Proxy_59() { __asm jmp dword ptr [g_pfn + 59 * 4] }
__declspec(naked) void Proxy_60() { __asm jmp dword ptr [g_pfn + 60 * 4] }
__declspec(naked) void Proxy_61() { __asm jmp dword ptr [g_pfn + 61 * 4] }
__declspec(naked) void Proxy_62() { __asm jmp dword ptr [g_pfn + 62 * 4] }
__declspec(naked) void Proxy_63() { __asm jmp dword ptr [g_pfn + 63 * 4] }
__declspec(naked) void Proxy_64() { __asm jmp dword ptr [g_pfn + 64 * 4] }
__declspec(naked) void Proxy_65() { __asm jmp dword ptr [g_pfn + 65 * 4] }
__declspec(naked) void Proxy_66() { __asm jmp dword ptr [g_pfn + 66 * 4] }
__declspec(naked) void Proxy_67() { __asm jmp dword ptr [g_pfn + 67 * 4] }
__declspec(naked) void Proxy_68() { __asm jmp dword ptr [g_pfn + 68 * 4] }
__declspec(naked) void Proxy_69() { __asm jmp dword ptr [g_pfn + 69 * 4] }
__declspec(naked) void Proxy_70() { __asm jmp dword ptr [g_pfn + 70 * 4] }
__declspec(naked) void Proxy_71() { __asm jmp dword ptr [g_pfn + 71 * 4] }
__declspec(naked) void Proxy_72() { __asm jmp dword ptr [g_pfn + 72 * 4] }
__declspec(naked) void Proxy_73() { __asm jmp dword ptr [g_pfn + 73 * 4] }
__declspec(naked) void Proxy_74() { __asm jmp dword ptr [g_pfn + 74 * 4] }
__declspec(naked) void Proxy_75() { __asm jmp dword ptr [g_pfn + 75 * 4] }
__declspec(naked) void Proxy_76() { __asm jmp dword ptr [g_pfn + 76 * 4] }
__declspec(naked) void Proxy_77() { __asm jmp dword ptr [g_pfn + 77 * 4] }
__declspec(naked) void Proxy_78() { __asm jmp dword ptr [g_pfn + 78 * 4] }
__declspec(naked) void Proxy_79() { __asm jmp dword ptr [g_pfn + 79 * 4] }
__declspec(naked) void Proxy_80() { __asm jmp dword ptr [g_pfn + 80 * 4] }
__declspec(naked) void Proxy_81() { __asm jmp dword ptr [g_pfn + 81 * 4] }
__declspec(naked) void Proxy_82() { __asm jmp dword ptr [g_pfn + 82 * 4] }
__declspec(naked) void Proxy_83() { __asm jmp dword ptr [g_pfn + 83 * 4] }
__declspec(naked) void Proxy_84() { __asm jmp dword ptr [g_pfn + 84 * 4] }
__declspec(naked) void Proxy_85() { __asm jmp dword ptr [g_pfn + 85 * 4] }
__declspec(naked) void Proxy_86() { __asm jmp dword ptr [g_pfn + 86 * 4] }
__declspec(naked) void Proxy_87() { __asm jmp dword ptr [g_pfn + 87 * 4] }
__declspec(naked) void Proxy_88() { __asm jmp dword ptr [g_pfn + 88 * 4] }
__declspec(naked) void Proxy_89() { __asm jmp dword ptr [g_pfn + 89 * 4] }
__declspec(naked) void Proxy_90() { __asm jmp dword ptr [g_pfn + 90 * 4] }
__declspec(naked) void Proxy_91() { __asm jmp dword ptr [g_pfn + 91 * 4] }
__declspec(naked) void Proxy_92() { __asm jmp dword ptr [g_pfn + 92 * 4] }
__declspec(naked) void Proxy_93() { __asm jmp dword ptr [g_pfn + 93 * 4] }
__declspec(naked) void Proxy_94() { __asm jmp dword ptr [g_pfn + 94 * 4] }
__declspec(naked) void Proxy_95() { __asm jmp dword ptr [g_pfn + 95 * 4] }
__declspec(naked) void Proxy_96() { __asm jmp dword ptr [g_pfn + 96 * 4] }
__declspec(naked) void Proxy_97() { __asm jmp dword ptr [g_pfn + 97 * 4] }
__declspec(naked) void Proxy_98() { __asm jmp dword ptr [g_pfn + 98 * 4] }
__declspec(naked) void Proxy_99() { __asm jmp dword ptr [g_pfn + 99 * 4] }
__declspec(naked) void Proxy_100() { __asm jmp dword ptr [g_pfn + 100 * 4] }
__declspec(naked) void Proxy_101() { __asm jmp dword ptr [g_pfn + 101 * 4] }
__declspec(naked) void Proxy_102() { __asm jmp dword ptr [g_pfn + 102 * 4] }
__declspec(naked) void Proxy_103() { __asm jmp dword ptr [g_pfn + 103 * 4] }
__declspec(naked) void Proxy_104() { __asm jmp dword ptr [g_pfn + 104 * 4] }
__declspec(naked) void Proxy_105() { __asm jmp dword ptr [g_pfn + 105 * 4] }
__declspec(naked) void Proxy_106() { __asm jmp dword ptr [g_pfn + 106 * 4] }
__declspec(naked) void Proxy_107() { __asm jmp dword ptr [g_pfn + 107 * 4] }
__declspec(naked) void Proxy_108() { __asm jmp dword ptr [g_pfn + 108 * 4] }
__declspec(naked) void Proxy_109() { __asm jmp dword ptr [g_pfn + 109 * 4] }
__declspec(naked) void Proxy_110() { __asm jmp dword ptr [g_pfn + 110 * 4] }
__declspec(naked) void Proxy_111() { __asm jmp dword ptr [g_pfn + 111 * 4] }
__declspec(naked) void Proxy_112() { __asm jmp dword ptr [g_pfn + 112 * 4] }
__declspec(naked) void Proxy_113() { __asm jmp dword ptr [g_pfn + 113 * 4] }
__declspec(naked) void Proxy_114() { __asm jmp dword ptr [g_pfn + 114 * 4] }
__declspec(naked) void Proxy_115() { __asm jmp dword ptr [g_pfn + 115 * 4] }
__declspec(naked) void Proxy_116() { __asm jmp dword ptr [g_pfn + 116 * 4] }
__declspec(naked) void Proxy_117() { __asm jmp dword ptr [g_pfn + 117 * 4] }
__declspec(naked) void Proxy_118() { __asm jmp dword ptr [g_pfn + 118 * 4] }
__declspec(naked) void Proxy_119() { __asm jmp dword ptr [g_pfn + 119 * 4] }
__declspec(naked) void Proxy_120() { __asm jmp dword ptr [g_pfn + 120 * 4] }
__declspec(naked) void Proxy_121() { __asm jmp dword ptr [g_pfn + 121 * 4] }
__declspec(naked) void Proxy_122() { __asm jmp dword ptr [g_pfn + 122 * 4] }
__declspec(naked) void Proxy_123() { __asm jmp dword ptr [g_pfn + 123 * 4] }
__declspec(naked) void Proxy_124() { __asm jmp dword ptr [g_pfn + 124 * 4] }
__declspec(naked) void Proxy_125() { __asm jmp dword ptr [g_pfn + 125 * 4] }
__declspec(naked) void Proxy_126() { __asm jmp dword ptr [g_pfn + 126 * 4] }
__declspec(naked) void Proxy_127() { __asm jmp dword ptr [g_pfn + 127 * 4] }
__declspec(naked) void Proxy_128() { __asm jmp dword ptr [g_pfn + 128 * 4] }
__declspec(naked) void Proxy_129() { __asm jmp dword ptr [g_pfn + 129 * 4] }
__declspec(naked) void Proxy_130() { __asm jmp dword ptr [g_pfn + 130 * 4] }
__declspec(naked) void Proxy_131() { __asm jmp dword ptr [g_pfn + 131 * 4] }
__declspec(naked) void Proxy_132() { __asm jmp dword ptr [g_pfn + 132 * 4] }
__declspec(naked) void Proxy_133() { __asm jmp dword ptr [g_pfn + 133 * 4] }
__declspec(naked) void Proxy_134() { __asm jmp dword ptr [g_pfn + 134 * 4] }
__declspec(naked) void Proxy_135() { __asm jmp dword ptr [g_pfn + 135 * 4] }
__declspec(naked) void Proxy_136() { __asm jmp dword ptr [g_pfn + 136 * 4] }
__declspec(naked) void Proxy_137() { __asm jmp dword ptr [g_pfn + 137 * 4] }
__declspec(naked) void Proxy_138() { __asm jmp dword ptr [g_pfn + 138 * 4] }
__declspec(naked) void Proxy_139() { __asm jmp dword ptr [g_pfn + 139 * 4] }
__declspec(naked) void Proxy_140() { __asm jmp dword ptr [g_pfn + 140 * 4] }
__declspec(naked) void Proxy_141() { __asm jmp dword ptr [g_pfn + 141 * 4] }
__declspec(naked) void Proxy_142() { __asm jmp dword ptr [g_pfn + 142 * 4] }
__declspec(naked) void Proxy_143() { __asm jmp dword ptr [g_pfn + 143 * 4] }
__declspec(naked) void Proxy_144() { __asm jmp dword ptr [g_pfn + 144 * 4] }
__declspec(naked) void Proxy_145() { __asm jmp dword ptr [g_pfn + 145 * 4] }
__declspec(naked) void Proxy_146() { __asm jmp dword ptr [g_pfn + 146 * 4] }
__declspec(naked) void Proxy_147() { __asm jmp dword ptr [g_pfn + 147 * 4] }
__declspec(naked) void Proxy_148() { __asm jmp dword ptr [g_pfn + 148 * 4] }
__declspec(naked) void Proxy_149() { __asm jmp dword ptr [g_pfn + 149 * 4] }
__declspec(naked) void Proxy_150() { __asm jmp dword ptr [g_pfn + 150 * 4] }
__declspec(naked) void Proxy_151() { __asm jmp dword ptr [g_pfn + 151 * 4] }
__declspec(naked) void Proxy_152() { __asm jmp dword ptr [g_pfn + 152 * 4] }
__declspec(naked) void Proxy_153() { __asm jmp dword ptr [g_pfn + 153 * 4] }
__declspec(naked) void Proxy_154() { __asm jmp dword ptr [g_pfn + 154 * 4] }
__declspec(naked) void Proxy_155() { __asm jmp dword ptr [g_pfn + 155 * 4] }
__declspec(naked) void Proxy_156() { __asm jmp dword ptr [g_pfn + 156 * 4] }
__declspec(naked) void Proxy_157() { __asm jmp dword ptr [g_pfn + 157 * 4] }
__declspec(naked) void Proxy_158() { __asm jmp dword ptr [g_pfn + 158 * 4] }
__declspec(naked) void Proxy_159() { __asm jmp dword ptr [g_pfn + 159 * 4] }
__declspec(naked) void Proxy_160() { __asm jmp dword ptr [g_pfn + 160 * 4] }
__declspec(naked) void Proxy_161() { __asm jmp dword ptr [g_pfn + 161 * 4] }
__declspec(naked) void Proxy_162() { __asm jmp dword ptr [g_pfn + 162 * 4] }
__declspec(naked) void Proxy_163() { __asm jmp dword ptr [g_pfn + 163 * 4] }
__declspec(naked) void Proxy_164() { __asm jmp dword ptr [g_pfn + 164 * 4] }
__declspec(naked) void Proxy_165() { __asm jmp dword ptr [g_pfn + 165 * 4] }
__declspec(naked) void Proxy_166() { __asm jmp dword ptr [g_pfn + 166 * 4] }
__declspec(naked) void Proxy_167() { __asm jmp dword ptr [g_pfn + 167 * 4] }
__declspec(naked) void Proxy_168() { __asm jmp dword ptr [g_pfn + 168 * 4] }
__declspec(naked) void Proxy_169() { __asm jmp dword ptr [g_pfn + 169 * 4] }
__declspec(naked) void Proxy_170() { __asm jmp dword ptr [g_pfn + 170 * 4] }
__declspec(naked) void Proxy_171() { __asm jmp dword ptr [g_pfn + 171 * 4] }
__declspec(naked) void Proxy_172() { __asm jmp dword ptr [g_pfn + 172 * 4] }
__declspec(naked) void Proxy_173() { __asm jmp dword ptr [g_pfn + 173 * 4] }
__declspec(naked) void Proxy_174() { __asm jmp dword ptr [g_pfn + 174 * 4] }
__declspec(naked) void Proxy_175() { __asm jmp dword ptr [g_pfn + 175 * 4] }
__declspec(naked) void Proxy_176() { __asm jmp dword ptr [g_pfn + 176 * 4] }
__declspec(naked) void Proxy_177() { __asm jmp dword ptr [g_pfn + 177 * 4] }
__declspec(naked) void Proxy_178() { __asm jmp dword ptr [g_pfn + 178 * 4] }
__declspec(naked) void Proxy_179() { __asm jmp dword ptr [g_pfn + 179 * 4] }
__declspec(naked) void Proxy_180() { __asm jmp dword ptr [g_pfn + 180 * 4] }
__declspec(naked) void Proxy_181() { __asm jmp dword ptr [g_pfn + 181 * 4] }
__declspec(naked) void Proxy_182() { __asm jmp dword ptr [g_pfn + 182 * 4] }
__declspec(naked) void Proxy_183() { __asm jmp dword ptr [g_pfn + 183 * 4] }
__declspec(naked) void Proxy_184() { __asm jmp dword ptr [g_pfn + 184 * 4] }
__declspec(naked) void Proxy_185() { __asm jmp dword ptr [g_pfn + 185 * 4] }
__declspec(naked) void Proxy_186() { __asm jmp dword ptr [g_pfn + 186 * 4] }
__declspec(naked) void Proxy_187() { __asm jmp dword ptr [g_pfn + 187 * 4] }
__declspec(naked) void Proxy_188() { __asm jmp dword ptr [g_pfn + 188 * 4] }
__declspec(naked) void Proxy_189() { __asm jmp dword ptr [g_pfn + 189 * 4] }
__declspec(naked) void Proxy_190() { __asm jmp dword ptr [g_pfn + 190 * 4] }
__declspec(naked) void Proxy_191() { __asm jmp dword ptr [g_pfn + 191 * 4] }
__declspec(naked) void Proxy_192() { __asm jmp dword ptr [g_pfn + 192 * 4] }
}
