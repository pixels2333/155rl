// =============================================================================
// test_host.exe — 32 位最小宿主,用于隔离验证 th155_rl_bridge.dll
//   用法: test_host.exe <dll 路径>
//   退出码 = DLL 的 Th155BridgeStatus() 位图:
//     0x01 DllMain 进入  0x02 找到 kernel32  0x04 全部 Win32 API 解析成功
//     0x08 共享内存已创建 0x10 视图已映射     0x20 线程已启动
//     0x40 inline hook 已安装                0x80 对象图已解析
//   与桥接 DLL 相同:不依赖 CRT/SDK,通过 PEB + 导出表(含 forwarder)取 API。
// =============================================================================
typedef unsigned char  u8;
typedef unsigned int   u32;
typedef unsigned long  ulong32;
typedef void*          HANDLE;

static inline u32 rd32(u32 a) { return *(volatile u32*)a; }
static inline unsigned short rd16(u32 a) { return *(volatile unsigned short*)a; }

static int str_eq(const char* a, const char* b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

static int str_eq_ci(const char* a, const char* b) {
    while (*a && *b) {
        char x = *a, y = *b;
        if (x >= 'A' && x <= 'Z') x = (char)(x - 'A' + 'a');
        if (y >= 'A' && y <= 'Z') y = (char)(y - 'A' + 'a');
        if (x != y) return 0;
        ++a; ++b;
    }
    return *a == *b;
}

static int ReadModuleName(u32 entry, char* out, u32 cap) {
    u32 buf = rd32(entry + 0x30);
    u32 len = rd16(entry + 0x2C);
    if (!buf || len < 2 || (len / 2) + 1 > cap) return 0;
    u32 n = len / 2, i;
    for (i = 0; i < n; ++i) {
        unsigned short c = rd16(buf + i * 2);
        if (c >= L'A' && c <= L'Z') c = (unsigned short)(c - L'A' + L'a');
        if (c > 127) c = (unsigned short)'?';
        out[i] = (char)c;
    }
    out[i] = 0;
    if (i > 4 && out[i - 4] == '.' && str_eq_ci(out + i - 3, "dll")) out[i - 4] = 0;
    return 1;
}

static u32 FindModuleByName(const char* name) {
    char want[64];
    u32 i = 0;
    for (; name[i] && i < 63; ++i) {
        char c = name[i];
        if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
        want[i] = c;
    }
    want[i] = 0;
    if (i > 4 && str_eq_ci(want + i - 4, ".dll")) want[i - 4] = 0;

    u32 peb = 0;
    __asm {
        mov eax, fs:[0x30]
        mov peb, eax
    }
    if (!peb) return 0;
    u32 ldr = rd32(peb + 0x0C);
    if (!ldr) return 0;
    u32 head = ldr + 0x14;
    u32 e = rd32(head);
    u32 n = 0;
    while (e && e != head && n < 256) {
        u32 entry = e - 0x08;
        char nm[64];
        if (ReadModuleName(entry, nm, sizeof(nm)) && str_eq(nm, want))
            return rd32(entry + 0x18);
        e = rd32(e);
        ++n;
    }
    return 0;
}

static u32 GetExportDepth(u32 moduleBase, const char* name, u32 depth) {
    if (!moduleBase || depth > 4) return 0;
    if (rd16(moduleBase) != 0x5A4D) return 0;   /* MZ */
    u32 pe = moduleBase + rd32(moduleBase + 0x3C);
    if (rd32(pe) != 0x00004550) return 0;
    u32 opt = pe + 0x18;
    u32 expRva = rd32(opt + 0x60);
    u32 expSize = rd32(opt + 0x64);
    if (!expRva) return 0;
    u32 ed = moduleBase + expRva;
    u32 numNames = rd32(ed + 0x18);
    u32 funcs = moduleBase + rd32(ed + 0x1C);
    u32 names = moduleBase + rd32(ed + 0x20);
    u32 ords = moduleBase + rd32(ed + 0x24);
    if (numNames > 0x10000) return 0;
    for (u32 i = 0; i < numNames; ++i) {
        u32 nameRva = rd32(names + i * 4);
        if (!nameRva) continue;
        if (!str_eq((const char*)(moduleBase + nameRva), name)) continue;
        unsigned short ord = rd16(ords + i * 2);
        u32 fnRva = rd32(funcs + ord * 4);
        if (!fnRva) return 0;
        if (fnRva >= expRva && fnRva < expRva + expSize) {
            const char* fwd = (const char*)(moduleBase + fnRva);
            u32 len = 0;
            while (fwd[len] && len < 200) ++len;
            u32 dot = len;
            while (dot > 0 && fwd[dot] != '.') --dot;
            if (dot == 0 || dot + 1 >= len) return 0;
            char mod[64], fn[64];
            u32 k = 0;
            for (u32 j = 0; j < dot && k < 63; ++j) mod[k++] = fwd[j];
            mod[k] = 0;
            k = 0;
            for (u32 j = dot + 1; j < len && k < 63; ++j) fn[k++] = fwd[j];
            fn[k] = 0;
            u32 mb = FindModuleByName(mod);
            return mb ? GetExportDepth(mb, fn, depth + 1) : 0;
        }
        return moduleBase + fnRva;
    }
    return 0;
}

static u32 GetExport(u32 base, const char* name) { return GetExportDepth(base, name, 0); }

typedef void* (__stdcall *PFN_LoadLibraryW)(const wchar_t*);
typedef u32   (__stdcall *PFN_GetLastError)(void);
typedef void  (__stdcall *PFN_ExitProcess)(u32);
typedef const wchar_t* (__stdcall *PFN_GetCommandLineW)(void);
typedef void* (__stdcall *PFN_GetProcAddress)(void*, const char*);
typedef void  (__stdcall *PFN_Sleep)(u32);
typedef u32   (__stdcall *PFN_Status)(void);

extern "C" int __stdcall entry(void) {
    u32 k32 = FindModuleByName("kernel32");
    if (!k32) return 0x10000002;
    PFN_LoadLibraryW pLoad = (PFN_LoadLibraryW)GetExport(k32, "LoadLibraryW");
    PFN_GetLastError pErr = (PFN_GetLastError)GetExport(k32, "GetLastError");
    PFN_ExitProcess pExit = (PFN_ExitProcess)GetExport(k32, "ExitProcess");
    PFN_GetCommandLineW pCmd = (PFN_GetCommandLineW)GetExport(k32, "GetCommandLineW");
    PFN_GetProcAddress pGPA = (PFN_GetProcAddress)GetExport(k32, "GetProcAddress");
    PFN_Sleep pSleep = (PFN_Sleep)GetExport(k32, "Sleep");
    if (!pLoad) return 0x10000013;
    if (!pCmd) return 0x10000014;
    if (!pExit) return 0x10000015;

    const wchar_t* p = pCmd();
    if (*p == L'"') { ++p; while (*p && *p != L'"') ++p; if (*p) ++p; }
    else { while (*p && *p != L' ') ++p; }
    while (*p == L' ') ++p;
    static wchar_t path[512];
    u32 i = 0;
    if (*p == L'"') {
        ++p;
        while (*p && *p != L'"' && i < 511) path[i++] = *p++;
    } else {
        while (*p && *p != L' ' && i < 511) path[i++] = *p++;
    }
    path[i] = 0;
    if (!i) return 0x10000005;

    void* h = pLoad(path);
    if (!h) {
        u32 err = pErr ? pErr() : 0;
        if (pExit) pExit(0x10000000u | (err & 0xFFFF));
        return (int)(0x10000000u | (err & 0xFFFF));
    }
    /* 状态由外部(scripts/bridge_debug.py / runhost.py)读取 DLL 内的 g_status,
       宿主这里只保证 DLL 成功加载并保持存活一小段时间。 */
    (void)pGPA;
    if (pSleep) pSleep(3000);
    if (pExit) pExit(0);
    return 0;
}
