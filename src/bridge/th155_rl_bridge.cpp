// =============================================================================
// th155_rl_bridge.dll  —  《东方凭依华》 th155.exe v1.21b 强化学习桥接 DLL
//
//  职责
//    1) 每帧把游戏内部状态(双方队伍/角色/弹幕/计时器/COM 难度)写入共享内存
//       "Local\th155_rl_bridge_v1_<pid>",Python 侧 <0.1ms 即可读到完整状态;
//       可选帧锁步(ctl_lockstep):每帧边界阻塞游戏,等 Python 放行;
//    2) 从共享内存读取动作,在 Manbow::InputMulti::Update 返回之后写入设备
//       状态 —— 与游戏同帧、无竞态的输入注入点(内置 AI 也是通过 command.rsv_*
//       在同样的时机驱动角色);
//    3) 支持瞬间重置:练习模式下同时按下 b0~b3 触发 battle.PracticeRestart()。
//
//  设计约束
//    * 不依赖 CRT / Windows SDK:Win32 声明全部内联,链接 /NODEFAULTLIB,
//      只需要 kernel32 导入库(见 kernel32.def + build.bat);
//    * 只做内存读写与一处 5 字节 inline hook,不修改游戏文件;
//    * 所有地址来自 configs/offsets.yaml(脚本对象图按名字解析,原生结构用 RVA)。
// =============================================================================

#include "protocol.h"
#include "offsets_generated.h"

typedef unsigned char  u8;
typedef unsigned int   u32;
typedef int            i32;
typedef unsigned long  ulong32;
typedef void*          HANDLE;
typedef int            BOOL;

// ---------------------------------------------------------------------------
// Win32 依赖:完全不使用导入库。通过 PEB 找到 kernel32.dll 基址,
// 再解析其导出表拿到所需 API 的函数指针(与 shellcode 常用做法一致)。
// ---------------------------------------------------------------------------
typedef HANDLE (__stdcall *PFN_CreateFileMappingW)(HANDLE, void*, unsigned int, unsigned int, unsigned int, const wchar_t*);
typedef void*  (__stdcall *PFN_MapViewOfFile)(HANDLE, unsigned int, unsigned int, unsigned int, unsigned long);
typedef HANDLE (__stdcall *PFN_CreateThread)(void*, unsigned long, unsigned int(__stdcall*)(void*), void*, unsigned int, unsigned int*);
typedef void   (__stdcall *PFN_Sleep)(unsigned int);
typedef unsigned int (__stdcall *PFN_GetTickCount)(void);
typedef void*  (__stdcall *PFN_VirtualAlloc)(void*, unsigned long, unsigned int, unsigned int);
typedef int    (__stdcall *PFN_VirtualProtect)(void*, unsigned long, unsigned int, unsigned int*);
typedef int    (__stdcall *PFN_FlushInstructionCache)(HANDLE, const void*, unsigned long);
typedef HANDLE (__stdcall *PFN_GetCurrentProcess)(void);
typedef unsigned long (__stdcall *PFN_VirtualQuery)(const void*, void*, unsigned long);
typedef int    (__stdcall *PFN_ReadProcessMemory)(HANDLE, const void*, void*, unsigned long, unsigned long*);
typedef HANDLE (__stdcall *PFN_CreateEventW)(void*, BOOL, BOOL, const wchar_t*);
typedef BOOL   (__stdcall *PFN_SetEvent)(HANDLE);
typedef unsigned int (__stdcall *PFN_WaitForSingleObject)(HANDLE, unsigned int);
typedef BOOL   (__stdcall *PFN_QueryPerformanceCounter)(long long*);
typedef BOOL   (__stdcall *PFN_QueryPerformanceFrequency)(long long*);

static PFN_CreateFileMappingW      pCreateFileMappingW;
static PFN_MapViewOfFile           pMapViewOfFile;
static PFN_CreateThread            pCreateThread;
static PFN_Sleep                   pSleep;
static PFN_GetTickCount            pGetTickCount;
static PFN_VirtualAlloc            pVirtualAlloc;
static PFN_VirtualProtect          pVirtualProtect;
static PFN_FlushInstructionCache   pFlushInstructionCache;
static PFN_GetCurrentProcess       pGetCurrentProcess;
static PFN_VirtualQuery            pVirtualQuery;
static PFN_ReadProcessMemory       pReadProcessMemory;
/* 帧锁步用(v19)。不并入 InitImports 的成功判定:缺了只是不支持锁步。 */
static PFN_CreateEventW            pCreateEventW;
static PFN_SetEvent                pSetEvent;
static PFN_WaitForSingleObject     pWaitForSingleObject;
static PFN_QueryPerformanceCounter pQueryPerformanceCounter;
static u32                         g_qpcPerUs = 0;   /* QPC 计数/微秒;0 = 不可用 */
static u32                         g_selfProc = 0;   /* 伪句柄 -1 = 当前进程 */

static u32 g_base = 0;
static u32 g_is_game = 0;   /* 1 = 宿主是 th155.exe(否则只做自检,不碰游戏结构) */
static volatile u32 g_status = 0;   /* 自检位图,见 Th155BridgeStatus() */
/* 调试用可远程读取的中间量(地址见 th155_rl_bridge.map) */
volatile u32 g_dbg_peb = 0, g_dbg_ldr = 0, g_dbg_head = 0, g_dbg_first = 0;
volatile u32 g_dbg_n = 0, g_dbg_k32 = 0, g_dbg_exe = 0, g_dbg_namelen = 0;
volatile u32 g_dbg_name0 = 0, g_dbg_name1 = 0, g_dbg_name2 = 0, g_dbg_fn = 0;
volatile u32 g_dbg_loop = 0, g_dbg_gbase = 0, g_dbg_hookbytes = 0, g_dbg_hookres = 0;
volatile u32 g_dbg_rg_stage = 0;
volatile u32 g_dbg_vm = 0, g_dbg_root = 0, g_dbg_rnodes = 0, g_dbg_rnum = 0;
volatile u32 g_dbg_k0tag = 0, g_dbg_k0val = 0, g_dbg_k0ch = 0, g_dbg_v0tag = 0;

#define PAGE_EXECUTE_READWRITE 0x40u
#define PAGE_READWRITE         0x04u
#define FILE_MAP_ALL_ACCESS    0x000F001Fu
#define MEM_COMMIT_RESERVE     0x3000u

// ---------------------------------------------------------------------------
// 基础内存访问
// ---------------------------------------------------------------------------
static inline u32 rd32(u32 a) { return *(volatile u32*)a; }
static inline unsigned short rd16(u32 a) { return *(volatile unsigned short*)a; }
static inline void wr32(u32 a, u32 v) { *(volatile u32*)a = v; }
static inline float rdf(u32 a) { return *(volatile float*)a; }
static inline u32 rawtag(u32 t) { return t & O_RAW_TYPE_MASK; }
/* SQObjectPtr.tag 的低 24 位才是类型;全 tag 还带引用/弱引用等高位 */
#define RAW_INT       0x02u
#define RAW_FLOAT     0x04u
#define RAW_BOOL      0x08u
#define RAW_STRING    0x10u
#define RAW_TABLE     0x20u
#define RAW_ARRAY     0x40u
#define RAW_CLOSURE   0x100u
#define RAW_INSTANCE  0x8000u
static inline u32 as_u32(void* p) { return (u32)p; }

// 用 volatile 逐字节清零,避免编译器把它折叠成 CRT 的 memset
static void memzero(void* dst, unsigned int n) {
    volatile unsigned char* p = (volatile unsigned char*)dst;
    for (unsigned int i = 0; i < n; ++i) p[i] = 0;
}

/* 宿主 pid:TEB+0x20 = ClientId.UniqueProcess(x86)。不用导入 GetCurrentProcessId。 */
static u32 CurrentPid(void) {
    u32 p = 0;
    __asm {
        mov eax, fs:[0x20]
        mov p, eax
    }
    return p;
}

/* out = prefix + 十进制 pid。逐字 volatile 写,避免被折叠成 CRT memcpy。 */
static void BuildName(wchar_t* out, u32 cap, const wchar_t* prefix, u32 pid) {
    volatile wchar_t* o = out;
    u32 n = 0;
    while (prefix[n] && n + 12 < cap) { o[n] = prefix[n]; ++n; }
    wchar_t tmp[12];
    u32 k = 0;
    do { tmp[k++] = (wchar_t)(L'0' + pid % 10); pid /= 10; } while (pid && k < 11);
    while (k) o[n++] = tmp[--k];
    o[n] = 0;
}

/* 主线程(帧边界同步发布)与 BridgeThread(解析/发布)共用字段缓存、Resolved,
   这些都不是线程安全的,必须互斥。持锁区只做内存读写,不等待任何别的线程。 */
extern "C" long __cdecl _InterlockedCompareExchange(long volatile*, long, long);
extern "C" long __cdecl _InterlockedExchange(long volatile*, long);
#pragma intrinsic(_InterlockedCompareExchange, _InterlockedExchange)
static volatile long g_lock = 0;
static void Lock(void) {
    while (_InterlockedCompareExchange(&g_lock, 1, 0) != 0) pSleep(0);
}
static void Unlock(void) { _InterlockedExchange(&g_lock, 0); }

static int str_eq(const char* a, const char* b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

// ---------------------------------------------------------------------------
// kernel32 导出解析(含 forwarder 处理:Win7+ 的 kernel32 大多转发到 kernelbase)
// ---------------------------------------------------------------------------
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

// 读取 LDR_DATA_TABLE_ENTRY 的 BaseDllName -> 小写 ASCII,去掉 ".dll" 后缀
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

// 按名字找模块基址(名字可带或不带 .dll,大小写不敏感)
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

static u32 GetExportDepth(u32 moduleBase, const char* name, u32 depth);

static u32 GetExport(u32 moduleBase, const char* name) {
    return GetExportDepth(moduleBase, name, 0);
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
    u32 funcs = moduleBase + rd32(ed + 0x1C);   /* AddressOfFunctions */
    u32 names = moduleBase + rd32(ed + 0x20);   /* AddressOfNames */
    u32 ords = moduleBase + rd32(ed + 0x24);    /* AddressOfNameOrdinals */
    if (numNames > 0x10000) return 0;
    for (u32 i = 0; i < numNames; ++i) {
        u32 nameRva = rd32(names + i * 4);
        if (!nameRva) continue;
        if (!str_eq((const char*)(moduleBase + nameRva), name)) continue;
        unsigned short ord = rd16(ords + i * 2);
        u32 fnRva = rd32(funcs + ord * 4);
        if (!fnRva) return 0;
        if (fnRva >= expRva && fnRva < expRva + expSize) {
            /* forwarder: "MODULE.Function" */
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

static int InitImports(void) {
    u32 k32 = FindModuleByName("kernel32");
    if (!k32) return 0;
    g_status |= 0x02;
    g_base = FindModuleByName("th155.exe");
    g_is_game = g_base ? 1 : 0;
    if (!g_base) {
        /* 宿主不是 th155.exe(自检模式):退回到模块链表第一项,但不做任何游戏内存访问 */
        u32 peb = 0;
        __asm {
            mov eax, fs:[0x30]
            mov peb, eax
        }
        u32 ldr = rd32(peb + 0x0C);
        u32 head = ldr + 0x14;
        g_base = rd32(rd32(head) - 0x08 + 0x18);
    }
    u32 fnMask = 0;
    pCreateFileMappingW    = (PFN_CreateFileMappingW)GetExport(k32, "CreateFileMappingW");
    if (pCreateFileMappingW) fnMask |= 0x001;
    pMapViewOfFile         = (PFN_MapViewOfFile)GetExport(k32, "MapViewOfFile");
    if (pMapViewOfFile) fnMask |= 0x002;
    pCreateThread          = (PFN_CreateThread)GetExport(k32, "CreateThread");
    if (pCreateThread) fnMask |= 0x004;
    pSleep                 = (PFN_Sleep)GetExport(k32, "Sleep");
    if (pSleep) fnMask |= 0x008;
    pGetTickCount          = (PFN_GetTickCount)GetExport(k32, "GetTickCount");
    if (pGetTickCount) fnMask |= 0x010;
    pVirtualAlloc          = (PFN_VirtualAlloc)GetExport(k32, "VirtualAlloc");
    if (pVirtualAlloc) fnMask |= 0x020;
    pVirtualProtect        = (PFN_VirtualProtect)GetExport(k32, "VirtualProtect");
    if (pVirtualProtect) fnMask |= 0x040;
    pFlushInstructionCache = (PFN_FlushInstructionCache)GetExport(k32, "FlushInstructionCache");
    if (pFlushInstructionCache) fnMask |= 0x080;
    pGetCurrentProcess     = (PFN_GetCurrentProcess)GetExport(k32, "GetCurrentProcess");
    if (pGetCurrentProcess) fnMask |= 0x100;
    /* VirtualQuery 位于 kernel32,用于全堆扫描定位 Actor2D */
    pVirtualQuery          = (PFN_VirtualQuery)GetExport(k32, "VirtualQuery");
    /* ReadProcessMemory:堆扫描必须用它做**缓冲读取**,不能裸解引用。
       裸解引用与 VirtualQuery 之间存在 TOCTOU 窗口 —— 游戏线程释放/重映射
       区域会让 rd32(p) 直接 AV(实测崩在 0xc0000005 / ScanStep 的 vtable 比较)。 */
    pReadProcessMemory     = (PFN_ReadProcessMemory)GetExport(k32, "ReadProcessMemory");
    pCreateEventW          = (PFN_CreateEventW)GetExport(k32, "CreateEventW");
    pSetEvent              = (PFN_SetEvent)GetExport(k32, "SetEvent");
    pWaitForSingleObject   = (PFN_WaitForSingleObject)GetExport(k32, "WaitForSingleObject");
    pQueryPerformanceCounter = (PFN_QueryPerformanceCounter)GetExport(k32, "QueryPerformanceCounter");
    {
        PFN_QueryPerformanceFrequency qpf =
            (PFN_QueryPerformanceFrequency)GetExport(k32, "QueryPerformanceFrequency");
        long long freq = 0;
        if (qpf && qpf(&freq) && freq > 0 && (freq >> 32) == 0)
            g_qpcPerUs = (u32)freq / 1000000u;
    }
    g_selfProc             = 0xFFFFFFFFu;    /* GetCurrentProcess() 伪句柄 */
    g_dbg_fn = fnMask;
    if (fnMask == 0x1FF) {
        g_status |= 0x04;
        return 1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Squirrel 对象图导航
// ---------------------------------------------------------------------------
/* ---- 字段槽缓存 -------------------------------------------------------
 * 每次 table_slot/inst_slot 都要线性扫描整个成员表(每项 3~4 次 RPM),
 * 发布一帧要查 ~40 个字段 -> 上万次 RPM,实测只有 2Hz。
 * 这里按 (容器地址, 名字指针) 缓存槽地址;ResolveGraph 每次重解析时清空,
 * 因此缓存寿命 <= 250ms,不会因为实例被回收而失效。
 */
#define FIELD_CACHE_SIZE 512
typedef struct { u32 owner; const char* name; u32 slot; } FieldCacheEntry;
static FieldCacheEntry g_fieldCache[FIELD_CACHE_SIZE];
static u32 g_fieldCacheN = 0;

static void field_cache_clear(void) { g_fieldCacheN = 0; }

static u32 field_cache_find(u32 owner, const char* name) {
    for (u32 i = 0; i < g_fieldCacheN; ++i)
        if (g_fieldCache[i].owner == owner && g_fieldCache[i].name == name)
            return g_fieldCache[i].slot;
    return 0;
}

static void field_cache_add(u32 owner, const char* name, u32 slot) {
    if (g_fieldCacheN < FIELD_CACHE_SIZE) {
        g_fieldCache[g_fieldCacheN].owner = owner;
        g_fieldCache[g_fieldCacheN].name = name;
        g_fieldCache[g_fieldCacheN].slot = slot;
        ++g_fieldCacheN;
    }
}

static int table_get(u32 table, const char* name, u32* outTag, u32* outVal) {
    if (!table) return 0;
    u32 nodes = rd32(table + O_TABLE_NODES);
    u32 num = rd32(table + O_TABLE_NUMNODES);
    if (!nodes || !num || num > 0x100000) return 0;
    for (u32 i = 0; i < num; ++i) {
        u32 n = nodes + i * O_TABLE_NODE_STRIDE;
        if (rawtag(rd32(n + O_TABLE_NODE_KTAG)) != RAW_STRING) continue;
        u32 kptr = rd32(n + O_TABLE_NODE_KEY);
        if (!kptr) continue;
        if (str_eq((const char*)(kptr + O_STR_DATA), name)) {
            *outTag = rd32(n + O_TABLE_NODE_VTAG);
            *outVal = rd32(n + O_TABLE_NODE_VALUE);
            return 1;
        }
    }
    return 0;
}

// 表查找:返回 value 的 SQObject 地址(表的 value 就是节点起始处)
static u32 table_slot_uncached(u32 table, const char* name) {
    if (!table) return 0;
    u32 nodes = rd32(table + O_TABLE_NODES);
    u32 num = rd32(table + O_TABLE_NUMNODES);
    if (!nodes || !num || num > 0x100000) return 0;
    for (u32 i = 0; i < num; ++i) {
        u32 n = nodes + i * O_TABLE_NODE_STRIDE;
        if (rawtag(rd32(n + O_TABLE_NODE_KTAG)) != RAW_STRING) continue;
        u32 k = rd32(n + O_TABLE_NODE_KEY);
        if (k && str_eq((const char*)(k + O_STR_DATA), name)) return n;
    }
    return 0;
}

static u32 table_slot(u32 table, const char* name) {
    if (!table) return 0;
    u32 hit = field_cache_find(table, name);
    if (hit) return hit;
    u32 slot = table_slot_uncached(table, name);
    field_cache_add(table, name, slot);
    return slot;
}

static i32 table_int(u32 table, const char* name) {
    u32 s = table_slot(table, name);
    if (!s) return 0;
    u32 t = rd32(s);
    if (rawtag(t) == RAW_INT || rawtag(t) == RAW_BOOL) return (i32)rd32(s + O_OBJ_VALUE);
    if (rawtag(t) == RAW_FLOAT) return (i32)rdf(s + O_OBJ_VALUE);
    return 0;
}

static u32 table_inst(u32 table, const char* name) {
    u32 s = table_slot(table, name);
    if (!s || rawtag(rd32(s)) != RAW_INSTANCE) return 0;
    return rd32(s + O_OBJ_VALUE);
}

static u32 inst_slot_uncached(u32 inst, const char* name) {
    if (!inst) return 0;
    u32 cls = rd32(inst + O_INST_CLASS);
    if (!cls) return 0;
    u32 tag, val;
    if (!table_get(rd32(cls + O_CLASS_MEMBERS), name, &tag, &val)) return 0;
    if (rawtag(tag) != RAW_INT) return 0;
    if (!(val & O_MEMBER_FIELD_FLAG)) return 0;
    return inst + O_INST_VALUES + (val & 0x00FFFFFF) * O_INST_STRIDE;
}

static u32 inst_slot(u32 inst, const char* name) {
    if (!inst) return 0;
    u32 hit = field_cache_find(inst, name);
    if (hit) return hit;
    u32 slot = inst_slot_uncached(inst, name);
    field_cache_add(inst, name, slot);
    return slot;
}

static u32 inst_field_inst(u32 inst, const char* name) {
    u32 slot = inst_slot(inst, name);
    if (!slot || rawtag(rd32(slot)) != RAW_INSTANCE) return 0;
    return rd32(slot + O_OBJ_VALUE);
}

static i32 inst_field_int(u32 inst, const char* name) {
    u32 slot = inst_slot(inst, name);
    if (!slot) return 0;
    u32 t = rd32(slot);
    if (rawtag(t) == RAW_INT || rawtag(t) == RAW_BOOL) return (i32)rd32(slot + O_OBJ_VALUE);
    if (rawtag(t) == RAW_FLOAT) return (i32)rdf(slot + O_OBJ_VALUE);
    return 0;
}

static float inst_field_float(u32 inst, const char* name) {
    u32 slot = inst_slot(inst, name);
    if (!slot) return 0.0f;
    u32 t = rd32(slot);
    if (rawtag(t) == RAW_FLOAT) return rdf(slot + O_OBJ_VALUE);
    if (rawtag(t) == RAW_INT) return (float)(i32)rd32(slot + O_OBJ_VALUE);
    return 0.0f;
}

static void inst_set_int(u32 inst, const char* name, i32 v) {
    u32 slot = inst_slot(inst, name);
    if (!slot) return;
    wr32(slot, O_TAG_INT);   /* 写入完整 tag(不是 raw) */
    wr32(slot + O_OBJ_VALUE, (u32)v);
}

static u32 inst_field_str(u32 inst, const char* name, char* out, u32 cap) {
    u32 slot = inst_slot(inst, name);
    if (!slot || rawtag(rd32(slot)) != RAW_STRING) return 0;
    u32 sp = rd32(slot + O_OBJ_VALUE);
    if (!sp) return 0;
    u32 len = rd32(sp + O_STR_LENGTH);
    const char* s = (const char*)(sp + O_STR_DATA);
    u32 i = 0;
    for (; i + 1 < cap && i < len && s[i]; ++i) out[i] = s[i];
    out[i] = 0;
    return i;
}

static inline u32 inst_native(u32 inst) { return inst ? rd32(inst + O_INST_USERPTR) : 0; }

// 无 CRT 链接时编译器/链接器需要的符号
extern "C" int _fltused = 0;
extern "C" void __load_config_used(void) {}

// ---------------------------------------------------------------------------
// 全局状态
// ---------------------------------------------------------------------------
static Th155Shared* g_shm = 0;
static u32          g_trampoline = 0;
static u32          g_retEax = 0;
static u32          g_hookOk = 0;

static u32 g_dev[2] = { 0, 0 };
static u32 g_talkDev = 0;
/* ---- 1 帧回合重置 ------------------------------------------------------
 * 游戏每帧从 `battle` 表取 `battleUpdate` 闭包并调用。把该槽临时换成
 * `battle.RoundReset` 闭包,游戏下一帧就会执行真正的回合重置(血量/计时器/
 * 弹幕/站位全部归位),再下一帧还原,于是重置只花 1 帧而不是自然 KO 的 11 秒。
 * 实测两个闭包参数个数兼容(直接交换不会抛 Sqrat 异常)。
 */
static u32 g_updSlot = 0;          /* battle.battleUpdate 的 SQObjectPtr 槽 */
static u32 g_updObj[2] = { 0, 0 }; /* 原闭包 tag/ptr */
static u32 g_rstObj[2] = { 0, 0 }; /* RoundReset 闭包 tag/ptr */
static u32 g_fastResetPending = 0; /* 1 = 本帧已换入,下一帧还原 */
static u32 g_shotPhase = 0;
static u32 g_lastResolve = 0;
static u32 g_lastFrameChange = 0;
static u32 g_prevLoopStart = 0;
static u32 g_lastFrame = 0xFFFFFFFFu;
static u32 g_resetFrames = 0;
static u32 g_lastPublishTick = 0;

static volatile i32 g_injX = 0, g_injY = 0;
static volatile i32 g_injK[6] = { 0, 0, 0, 0, 0, 0 };
static volatile u32 g_injEnable = 0;
static volatile u32 g_injTeam = 0;
static volatile u32 g_talkInject = 0;

/* ---- 帧锁步(v19) ------------------------------------------------------
 * 帧边界 = 本帧第一次对战设备(g_dev[0]/g_dev[1])的 InputMulti::Update。
 * sub_40E1A0 先扫一遍输入设备(@0x40E21B),再 `inc [O_FRAME_COUNTER_RVA]`
 * (@0x40E21D),之后才跑本帧逻辑;所以计数值变化后的第一次设备更新时,
 * 上一帧逻辑已经跑完、本帧逻辑还没开始,在这里发布状态并阻塞游戏主线程。 */
static u32    g_lsLastCounter = 0xFFFFFFFFu;
static u32    g_lsStalled = 0;        /* 1 = 等放行超时,暂停阻塞直到 ctl_step_seq 变化 */
static u32    g_lsStalledSeq = 0;
static u32    g_lsLastPubTick = 0;    /* 主线程最近一次锁步发布(GetTickCount) */
static HANDLE g_evState = 0, g_evStep = 0;
static void LockstepBoundary(void);

// ---------------------------------------------------------------------------
// 输入注入 hook: Manbow::InputMulti::Update
// ---------------------------------------------------------------------------
static void __stdcall OnInputUpdated(void* self) {
    u32 dev = as_u32(self);
    if (!g_shm || !dev) return;
    if (dev == g_dev[0] || dev == g_dev[1]) {
        u32 fc = rd32(g_base + O_FRAME_COUNTER_RVA);
        if (fc != g_lsLastCounter) {
            g_lsLastCounter = fc;
            LockstepBoundary();       /* 必须在本帧注入之前:放行后才拿到本帧动作 */
        }
    }
    if (g_injEnable && dev == g_dev[g_injTeam & 1]) {
        wr32(dev + O_INPUT_X, (u32)g_injX);
        wr32(dev + O_INPUT_Y, (u32)g_injY);
        wr32(dev + O_INPUT_B0, (u32)g_injK[0]);
        wr32(dev + O_INPUT_B1, (u32)g_injK[1]);
        wr32(dev + O_INPUT_B2, (u32)g_injK[2]);
        wr32(dev + O_INPUT_B3, (u32)g_injK[3]);
        wr32(dev + O_INPUT_B4, (u32)g_injK[4]);
        wr32(dev + O_INPUT_B5, (u32)g_injK[5]);
    }
    if (g_resetFrames && dev == g_dev[0]) {
        wr32(dev + O_INPUT_B0, 1);
        wr32(dev + O_INPUT_B1, 1);
        wr32(dev + O_INPUT_B2, 1);
        wr32(dev + O_INPUT_B3, 1);
        if (--g_resetFrames == 0) g_shm->ctl_reset_ack++;
    }
    if (g_talkInject && dev == g_talkDev) {
        wr32(dev + O_INPUT_B0, 1);
    }
    if (dev == g_dev[0] && g_shm) {
        if (g_fastResetPending) {                 /* 下一帧还原 */
            g_fastResetPending = 0;
            wr32(g_updSlot, g_updObj[0]);
            wr32(g_updSlot + 4, g_updObj[1]);
            g_shm->ctl_fast_reset_ack++;
        } else if (g_shm->ctl_fast_reset) {       /* 本帧换入 RoundReset */
            g_shm->ctl_fast_reset = 0;
            if (g_updSlot && g_rstObj[0]) {
                wr32(g_updSlot, g_rstObj[0]);
                wr32(g_updSlot + 4, g_rstObj[1]);
                g_fastResetPending = 1;
            }
        }
    }
    g_shm->ctl_ack++;
}

extern "C" __declspec(naked) void InputUpdateDetour(void) {
    __asm {
        push ecx
        pushad
        mov  eax, g_trampoline
        call eax
        mov  g_retEax, eax
        popad
        pop  ecx
        push ecx
        call OnInputUpdated
        mov  eax, g_retEax
        ret
    }
}

static u32 InstallInputHook(u32 base) {
    u32 fn = base + O_INPUT_UPDATE_RVA;
    const u8* p = (const u8*)fn;
    g_dbg_hookbytes = ((u32)p[0]) | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
    if (!(p[0] == 0x55 && p[1] == 0x8B && p[2] == 0xEC && p[3] == 0x51 && p[4] == 0x56)) {
        g_dbg_hookres = 1;
        return 0;
    }
    u32 tramp = as_u32(pVirtualAlloc(0, 64, MEM_COMMIT_RESERVE, PAGE_EXECUTE_READWRITE));
    if (!tramp) return 0;
    u8* t = (u8*)tramp;
    for (u32 i = 0; i < 5; ++i) t[i] = p[i];
    t[5] = 0xE9;
    *(u32*)(t + 6) = (fn + 5) - (tramp + 10);
    g_trampoline = tramp;

    u32 old = 0;
    if (!pVirtualProtect((void*)fn, 8, PAGE_EXECUTE_READWRITE, &old)) { g_dbg_hookres = 3; return 0; }
    u8* w = (u8*)fn;
    w[0] = 0xE9;
    *(u32*)(w + 1) = as_u32(&InputUpdateDetour) - (fn + 5);
    pVirtualProtect((void*)fn, 8, old, &old);
    pFlushInstructionCache(pGetCurrentProcess(), (void*)fn, 8);
    g_dbg_hookres = 0x99;
    return 1;
}

// ---------------------------------------------------------------------------
// 对象图解析
// ---------------------------------------------------------------------------
/* 玩家 native 对象地址缓存。用于把"玩家本体"从弹幕容器里剔除。
   实测 group_player 45 条里只有 4 条是玩家(motion 恰好落进弹幕区间),
   不剔除就会把 40x110 的玩家判定框当成弹幕发出去。 */
static u32 g_playerNat[4] = {0, 0, 0, 0};
static int g_playerNatN = 0;

static int IsPlayerNative(u32 proc) {
    for (int i = 0; i < g_playerNatN; ++i)
        if (g_playerNat[i] && g_playerNat[i] == proc) return 1;
    return 0;
}

static int g_battleIsTable = 1;   /* ::battle 在 th155 中是 table(不是 instance) */

static u32 battle_slot(u32 battle, const char* name) {
    return g_battleIsTable ? table_slot(battle, name) : inst_slot(battle, name);
}

static i32 battle_int(u32 battle, const char* name) {
    if (g_battleIsTable) return table_int(battle, name);
    return inst_field_int(battle, name);
}

/* 按 battle 的真实类型取得 (tag, value)。实例/表两种形态都支持。 */
static int battle_get(u32 battle, const char* name, u32* outTag, u32* outVal) {
    u32 slot = battle_slot(battle, name);
    if (!slot) return 0;
    *outTag = rd32(slot);
    *outVal = rd32(slot + O_OBJ_VALUE);
    return 1;
}

struct Resolved {
    u32 battle;
    u32 team[2];
    u32 player[2][2];
    u32 input[2];
    u32 talk;
    int ok;
};

/* BridgeThread 解析、主线程(锁步)与 BridgeThread 发布共用,访问必须持 g_lock。 */
static Resolved g_rs;

static u32 g_vm = 0, g_root = 0;

static int ResolveGraph(Resolved* rs) {
    u32 i;
    memzero(rs, sizeof(Resolved));
    if (!g_vm) {
        g_vm = rd32(g_base + O_VM_GLOBAL_RVA);
        if (!g_vm) { g_dbg_rg_stage = 1; return 0; }
        if (rawtag(rd32(g_vm + O_VM_ROOTTABLE)) != RAW_TABLE) { g_dbg_rg_stage = 2; return 0; }
        g_root = rd32(g_vm + O_VM_ROOTTABLE + O_OBJ_VALUE);
        if (!g_root) { g_dbg_rg_stage = 3; return 0; }
        g_dbg_rg_stage = 4;
        g_dbg_vm = g_vm;
        g_dbg_root = g_root;
        g_dbg_rnodes = rd32(g_root + O_TABLE_NODES);
        g_dbg_rnum = rd32(g_root + O_TABLE_NUMNODES);
        if (g_dbg_rnodes && g_dbg_rnum) {
            u32 n0 = g_dbg_rnodes;
            g_dbg_k0tag = rd32(n0 + O_TABLE_NODE_KTAG);
            g_dbg_k0val = rd32(n0 + O_TABLE_NODE_KEY);
            g_dbg_v0tag = rd32(n0 + O_TABLE_NODE_VTAG);
            if (g_dbg_k0val) g_dbg_k0ch = rd32(g_dbg_k0val + O_STR_DATA);
        }
    }
    u32 tag = 0, val = 0;
    if (!table_get(g_root, "battle", &tag, &val)) { g_dbg_rg_stage = 5; return 0; }
    if (rawtag(tag) != RAW_TABLE && rawtag(tag) != RAW_INSTANCE) { g_dbg_rg_stage = 6; return 0; }
    g_battleIsTable = (rawtag(tag) == RAW_TABLE);
    rs->battle = val;

    /* 1 帧重置用到的两个闭包槽(闭包对象本身很少变,解析时缓存) */
    {
        u32 up = battle_slot(rs->battle, "battleUpdate");
        u32 rp = battle_slot(rs->battle, "RoundReset");
        if (up && rp && rawtag(rd32(up)) == RAW_CLOSURE && rawtag(rd32(rp)) == RAW_CLOSURE) {
            g_updSlot = up;
            g_updObj[0] = rd32(up);
            g_updObj[1] = rd32(up + 4);
            g_rstObj[0] = rd32(rp);
            g_rstObj[1] = rd32(rp + 4);
        } else {
            g_updSlot = 0;
        }
    }

    u32 ts = battle_slot(rs->battle, "team");
    if (!ts || rawtag(rd32(ts)) != RAW_ARRAY) { g_dbg_rg_stage = 7; return 0; }
    u32 vals = rd32(rd32(ts + O_OBJ_VALUE) + O_ARRAY_VALUES);
    u32 size = rd32(rd32(ts + O_OBJ_VALUE) + O_ARRAY_SIZE);
    if (!vals || size < 2) { g_dbg_rg_stage = 8; return 0; }
    for (i = 0; i < 2; ++i) {
        u32 slot = vals + i * 8;
        if (rawtag(rd32(slot)) != RAW_INSTANCE) return 0;
        rs->team[i] = rd32(slot + O_OBJ_VALUE);
        rs->player[i][0] = inst_field_inst(rs->team[i], "master");
        rs->player[i][1] = inst_field_inst(rs->team[i], "slave");
        rs->input[i] = inst_field_inst(rs->team[i], "input");
        /* 缓存玩家 native 地址,供弹幕采集剔除 */
        g_playerNat[i * 2 + 0] = rs->player[i][0] ? inst_native(rs->player[i][0]) : 0;
        g_playerNat[i * 2 + 1] = rs->player[i][1] ? inst_native(rs->player[i][1]) : 0;
    }
    g_playerNatN = 4;
    if (table_get(g_root, "input_talk", &tag, &val) && rawtag(tag) == RAW_INSTANCE)
        rs->talk = inst_native(val);
    rs->ok = 1;
    g_dbg_rg_stage = 0x10;
    return 1;
}

// ---------------------------------------------------------------------------
// 弹幕与判定框采集
//
// 历史教训(三轮实测):
//   1. `player.shot_actor` 侵入式链表恒空(sentinel 自循环) -> 废弃;
//   2. battle.group_player / group_effect 里 motion∈[6000,6999] 的对象是
//      **远处的**弹幕,掉血时距我方 AABB 500~1200px,AABB 从不相接 -> 不是伤害源;
//   3. 真正打中玩家的是 motion 2008/2009/2010,它们**不属于任何 battle 容器**,
//      只有全内存扫 vtable=0x8e7a38 才看得到。
//
// 所以这里两条路都走:
//   A. 遍历 group_player / group_effect(便宜,覆盖绝大多数演出弹幕);
//   B. 扫描已提交内存里的 vtable==0x8e7a38(贵,但能抓到 2008-2010 这种孤儿),
//      用去重表避免重复,并按 1/8 频率刷新以控制开销。
//
// 归属判据(实测): battle.team[i].callback_group_shot 给了每队的弹幕组号
//   team0 = 16, team1 = 32;  Actor2D.callbackGroup(0x84) == 该值即属于该队。
//   玩家本体 callbackGroup = 1 / 2。
//
// 判定框: Actor2D 的 left/top/right/bottom(0x1C/0x20/0x24/0x28) 就是碰撞 AABB。
//   玩家实测 = (x±20, y-60 .. y+50) 即 40x110;
//   弹幕的 AABB 退化成点(宽高 0),用中心点做点-矩形距离判定。
//   注意: `hitLeft/Top/Right/Bottom`(0x8C..0x98) 在所有对象上**恒为 0**,
//   是死字段,不要用。
// ---------------------------------------------------------------------------
#define ACTOR_GROUP_COUNT  0x10u
#define ACTOR_GROUP_BEGIN  0x14u
#define ACTOR_GROUP_END    0x18u
#define ACTOR_GROUP_MAX    2048
/* 曾经的过滤是 motion∈[2000,6999]。实测 28908 帧:该区间里只出现了
   2009(真弹幕)与 6050(演出特效)两种,而 1000..1999 的近身攻击动作
   被整段丢弃(player_check.nut:82 的 IsAttack 认为 motion>=1000 就是攻击)。
   因此下限降到 1000。注意 is_attack 只是 IsAttack 的 motion 区间分级,
   它**区分不了**攻击与 6030/6050 这类特效(那些同样 >=1000),
   真正的 flagAttack 在帧数据里,不在 Actor2D 上暴露。 */
/* 弹幕 motion 区间。实测容器里真实弹幕的 motion 是 **190 / 213**, 不是
   1000+。旧下限 1000 把它们全部丢弃, 只留下 2009(近身)与 6010/6030/6050
   (特效), 这正是"弹幕数看起来很多但都是特效"的根因。
   190/213 的对象有真实坐标与速度(轨迹遍布全屏, 含屏外负坐标),
   是货真价实的弹幕; 6010/6030/6050 是演出特效, 用 atk_lv 区分不了,
   但它们本来就 >=1000, 保留在范围内由上层按需过滤。 */
#define SHOT_MOTION_MIN    100
#define SHOT_MOTION_MAX    6999
#define ACTOR2D_VT         0x008e7a38u   /* 未重定位的 vtable 常量 */
#define SHOT_DEDUP         256

static u32 g_shotSeen[SHOT_DEDUP];
static u32 g_shotSeenN;
static u32 g_cbGroupShot[2];     /* [team] -> callback_group_shot */
static u32 g_cbGroupMask[2];     /* [team] -> callback_mask_shot  */

static int ShotSeen(u32 p) {
    for (u32 i = 0; i < g_shotSeenN; ++i)
        if (g_shotSeen[i] == p) return 1;
    if (g_shotSeenN < SHOT_DEDUP) g_shotSeen[g_shotSeenN++] = p;
    return 0;
}

/* 读 team[i].callback_group_shot,建立 cbg -> team 映射。 */
static void ResolveShotGroups(u32 battle) {
    u32 i;
    g_cbGroupShot[0] = g_cbGroupShot[1] = 0;
    g_cbGroupMask[0] = g_cbGroupMask[1] = 0;
    u32 ts = battle_slot(battle, "team");
    if (!ts || rawtag(rd32(ts)) != RAW_ARRAY) return;
    u32 arr = rd32(ts + O_OBJ_VALUE);
    if (!arr) return;
    u32 vals = rd32(arr + O_ARRAY_VALUES);
    u32 size = rd32(arr + O_ARRAY_SIZE);
    if (!vals || size < 2) return;
    for (i = 0; i < 2; ++i) {
        u32 slot = vals + i * 8;
        if (rawtag(rd32(slot)) != RAW_INSTANCE) continue;
        u32 inst = rd32(slot + O_OBJ_VALUE);
        if (!inst) continue;
        g_cbGroupShot[i] = (u32)inst_field_int(inst, "callback_group_shot");
        g_cbGroupMask[i] = (u32)inst_field_int(inst, "callback_mask_shot");
    }
}

/* 由 Actor2D 的 callbackGroup 反查所属队伍;0/1 = 队号,-1 = 未知(中立/特效)。
   实测: 真实弹幕的 callbackGroup 大量为 0(池化对象未设置组), 只有少数
   带 16/32。因此 cbg 查不到时退回**轨迹推断**: 弹幕朝哪个方向飞, 就归属
   哪一方 —— 弹幕总是从施法者朝对手飞。这比直接判 -1 有用得多。 */
static i32 OwnerOfActor(u32 proc) {
    i32 cbg = (i32)rd32(proc + O_ACTOR_CBGROUP);
    if (cbg != 0) {
        if (g_cbGroupShot[0] && (u32)cbg == g_cbGroupShot[0]) return 0;
        if (g_cbGroupShot[1] && (u32)cbg == g_cbGroupShot[1]) return 1;
    }
    /* 不做轨迹推断。
       曾用"水平速度方向 + 施法者左右位置"兜底, 实测**准确率只有 61.9%**
       (255058 个 cb_group∈{16,32} 的真值样本, 几乎等于瞎猜)。
       原因是弹幕有追踪/反弹/纵向/环绕等大量非直线轨迹, 方向与发射方无关。
       判错方向很危险: obs 里 own_flag 把 owner==self_team 标 1(当作己方免威胁),
       把敌弹误标成己方会直接丢掉威胁信号。因此查不到组号时一律返回 -1
       (未知即按敌对处理)。 */
    return -1;
}

/* 把单个 Actor2D 写进输出(含判定框与归属)。
   调用方必须先确保 proc 及 anim 可读(HeapScan 路径用 IsReadable 验证)。 */
static int IsReadable(u32 addr, u32 size);
/* 发布路径专用的可读判定(带单帧区域缓存),定义见 IsReadable 之后。 */
static int PubReadable(u32 addr, u32 size);

/* ---- 判定尺寸缓存:(motion, key_take) -> 半宽/半高 ----
   动机:弹幕的攻击框向量 (anim+0x84) **不是每帧都非空**。实测真弹幕里
   直接解出体积的帧只占 16.8%,其余帧 first==last 即向量为空,体积整帧丢失、
   box 退化成点 —— 观测里"这发弹幕有多大"这一档信息就没了。
   尺寸来自帧数据 Take Data 的静态配置,与实例地址无关,所以按该键缓存:
   某帧解出过一次,后续空向量帧沿用。
   **不用 actor 指针做键**:对象池会复用地址,不同弹幕会串尺寸。

   **多值键必须封存**(实测 .scratch/probe_multivalue.py):
   (motion, take) 并不总是唯一决定尺寸。2008/take0 有 100/45/50/80 四种
   (最大一类只占 50%),3000/take1 有三种,3010/take1 有两种 ——
   总样本加权后 last-write-wins 会**错标 6.4%**;单看 2008/take0 更是 50%。
   所以一旦观察到同一键出现第二种尺寸,就把该键标为 ambiguous 并
   **永不回填**(宁可退回退化点,也不给出错误体积)。
   注:样本 >= 30 的稳定键(2008/take6 的 36x36、6030/take1 的 60x60、
   2009/take5 的 36x36、6050/take1 的 90x240)全部单值,缓存仍是主力来源。 */
#define SHOT_SIZE_CACHE 128
static struct {
    i32 motion, take;
    float hw, hh;
    float hw_lo, hh_lo;     /* 观测到的最小值 */
    float hw_hi, hh_hi;     /* 观测到的最大值 */
    u32 used;
    u32 frozen;             /* 1 = 尺寸随状态变化,禁止回填 */
    i32 is_beam;
    float beam_w, beam_l;
} g_sizeCache[SHOT_SIZE_CACHE];
static u32 g_sizeCacheNext;

/* 尺寸"稳定"的判据。
   实测同一 (motion,take) 下尺寸有两种完全不同的行为:
     1. 抖动:motion 2009/take0 出现 85/86/87 三档,只差 1px ——
        这是框边缘的浮点/整数取整误差,**必须视为同一个值**,否则缓存
        立刻判为不稳定而全体失效(实测覆盖率会从 84% 崩到 29%)。
     2. 真变化:motion 2009/take0 的另一部分样本从 50 长到 87,
        是充能/生长类实体,尺寸随帧推进单调变化 —— 这种**不能**缓存,
        否则空向量帧会被标上某个固定尺寸,等于编造信息。
   用"极差 / 中值"做相对门控区分二者:1px 在 87px 上是 1.1%,
   50->87 是 74%。阈值取 15%(相对),对应 87px 上约 13px 的容差。 */
#define SHOT_SIZE_REL_TOL 0.15f
#define SHOT_SIZE_ABS_TOL 0.05f    /* 同值判定:亚像素浮点抖动 */

static int ShotSizeLookup(i32 motion, i32 take, float* hw, float* hh,
                          int* is_beam, float* beam_w, float* beam_l) {
    for (u32 i = 0; i < SHOT_SIZE_CACHE; ++i) {
        if (g_sizeCache[i].used && g_sizeCache[i].motion == motion
            && g_sizeCache[i].take == take) {
            if (g_sizeCache[i].frozen) return 0;      /* 不可信:放弃回填 */
            *hw = g_sizeCache[i].hw;
            *hh = g_sizeCache[i].hh;
            if (is_beam) *is_beam = g_sizeCache[i].is_beam;
            if (beam_w) *beam_w = g_sizeCache[i].beam_w;
            if (beam_l) *beam_l = g_sizeCache[i].beam_l;
            return 1;
        }
    }
    return 0;
}

static void ShotSizeStore(i32 motion, i32 take, float hw, float hh,
                          int is_beam, float beam_w, float beam_l) {
    u32 i;
    for (i = 0; i < SHOT_SIZE_CACHE; ++i) {
        if (g_sizeCache[i].used && g_sizeCache[i].motion == motion
            && g_sizeCache[i].take == take) {
            if (g_sizeCache[i].frozen) return;        /* 已封存,不再更新 */
            float dh = g_sizeCache[i].hw - hw;
            float dk = g_sizeCache[i].hh - hh;
            if (dh < 0.0f) dh = -dh;
            if (dk < 0.0f) dk = -dk;
            if (dh <= SHOT_SIZE_ABS_TOL && dk <= SHOT_SIZE_ABS_TOL) return; /* 同值 */

            /* 记录极差,判断这次差异是抖动还是真变化。 */
            if (hw < g_sizeCache[i].hw_lo) g_sizeCache[i].hw_lo = hw;
            if (hw > g_sizeCache[i].hw_hi) g_sizeCache[i].hw_hi = hw;
            if (hh < g_sizeCache[i].hh_lo) g_sizeCache[i].hh_lo = hh;
            if (hh > g_sizeCache[i].hh_hi) g_sizeCache[i].hh_hi = hh;
            float rw = (g_sizeCache[i].hw_hi - g_sizeCache[i].hw_lo)
                       / (g_sizeCache[i].hw_hi > 1.0f ? g_sizeCache[i].hw_hi : 1.0f);
            float rh = (g_sizeCache[i].hh_hi - g_sizeCache[i].hh_lo)
                       / (g_sizeCache[i].hh_hi > 1.0f ? g_sizeCache[i].hh_hi : 1.0f);
            if (rw > SHOT_SIZE_REL_TOL || rh > SHOT_SIZE_REL_TOL) {
                g_sizeCache[i].frozen = 1;            /* 真变化 -> 封存 */
                return;
            }
            /* 抖动范围内:取较大者。低估判定框会让策略误判"这里是安全的",
               后果比高估严重得多,所以统一取上界。 */
            if (hw > g_sizeCache[i].hw) g_sizeCache[i].hw = hw;
            if (hh > g_sizeCache[i].hh) g_sizeCache[i].hh = hh;
            g_sizeCache[i].is_beam = is_beam;
            g_sizeCache[i].beam_w = beam_w;
            g_sizeCache[i].beam_l = beam_l;
            return;
        }
    }
    /* 环形替换:满了就覆盖最早的一条,不淘汰检查,几百条弹幕足够用。 */
    u32 slot = g_sizeCacheNext++ % SHOT_SIZE_CACHE;
    g_sizeCache[slot].motion = motion;
    g_sizeCache[slot].take = take;
    g_sizeCache[slot].hw = hw;
    g_sizeCache[slot].hh = hh;
    g_sizeCache[slot].hw_lo = g_sizeCache[slot].hw_hi = hw;
    g_sizeCache[slot].hh_lo = g_sizeCache[slot].hh_hi = hh;
    g_sizeCache[slot].used = 1;
    g_sizeCache[slot].frozen = 0;
    g_sizeCache[slot].is_beam = is_beam;
    g_sizeCache[slot].beam_w = beam_w;
    g_sizeCache[slot].beam_l = beam_l;
}

static inline void sincos_f32(float rad, float* s, float* c) {
    __asm {
        fld dword ptr [rad]
        fsincos
        mov eax, dword ptr [c]
        fstp dword ptr [eax]
        mov eax, dword ptr [s]
        fstp dword ptr [eax]
    }
}

static inline float atan2_f32(float y, float x) {
    float res;
    __asm {
        fld dword ptr [y]
        fld dword ptr [x]
        fpatan
        fstp dword ptr [res]
    }
    return res;
}

/* 从 anim+0x84 的攻击框向量解出半宽/半高与激光几何(含 margin 与 localScaling)。
   返回 1 = 解出,0 = 该帧向量为空或不可解。 */
static int ShotSizeFromAnim(u32 anim, float* hw, float* hh,
                            float* cx, float* cy,
                            float* r00, float* r01, float* r10, float* r11,
                            int* is_beam, float* beam_w, float* beam_l) {
    if (!anim || !PubReadable(anim, 0x90)) return 0;
    u32 first = rd32(anim + 0x84u);
    u32 last  = rd32(anim + 0x88u);
    if (!first || !last || last <= first || (last - first) > 0x80u) return 0;
    if (!PubReadable(first, 8)) return 0;
    u32 box_obj = rd32(first);
    if (!box_obj || !PubReadable(box_obj, 0x10)) return 0;
    u32 col_obj = rd32(box_obj + 0x0cu);
    if (!col_obj || !PubReadable(col_obj, 0xd0)) return 0;
    u32 shape = rd32(col_obj + 0xccu);
    if (!shape || !PubReadable(shape, 0x38)) return 0;
    float a = rdf(shape + 0x20u);
    float b = rdf(shape + 0x24u);
    float mg = rdf(shape + 0x30u);
    float sx = rdf(shape + 0x10u);
    float sy = rdf(shape + 0x14u);
    if (sx > 0.001f && sx < 100.0f) a *= sx;
    if (sy > 0.001f && sy < 100.0f) b *= sy;
    if (!(a > 0.0f && b > 0.0f && a < 5000.0f && b < 5000.0f)) return 0;
    *hw = a + mg;
    *hh = b + mg;
    if (cx && cy) {
        *cx = rdf(col_obj + 0x40u);
        *cy = rdf(col_obj + 0x44u);
    }
    if (r00 && r01 && r10 && r11) {
        *r00 = rdf(col_obj + 0x0cu);
        *r01 = rdf(col_obj + 0x10u);
        *r10 = rdf(col_obj + 0x1cu);
        *r11 = rdf(col_obj + 0x20u);
    }
    float max_dim = (*hw > *hh) ? *hw : *hh;
    float min_dim = (*hw < *hh) ? *hw : *hh;
    if (is_beam && beam_w && beam_l) {
        if (max_dim >= 3.0f * min_dim || max_dim >= 150.0f) {
            *is_beam = 1;
        } else {
            *is_beam = 0;
        }
        *beam_w = min_dim * 2.0f;
        *beam_l = max_dim * 2.0f;
    }
    return 1;
}

/* ---- 近战攻击判定框 ----
   链路:anim+0x84 向量 -> ContactBoxItem -> +0x0C = btCollisionObject
         -> +0x0C/+0x10 = 框中心的世界坐标
         -> +0xCC = btBox2dShape -> +0x20/+0x24 半宽高, +0x30 margin
   实测向量里**每两个元素一个有效**(偶数位是真实框,奇数位恒为 (0,0) 占位),
   一次攻击最多出现 4 个有效框(motion 1210 / 3040)。
   玩家采样里只有 12.6% 的帧有非空向量 —— 攻击框只在出手帧存在,属正常。 */
static int ShotSizeFromShape(u32 shape, float* hw, float* hh) {
    if (!shape || !PubReadable(shape, 0x38)) return 0;
    float a = rdf(shape + 0x20u);
    float b = rdf(shape + 0x24u);
    float mg = rdf(shape + 0x30u);
    if (!(a > 0.0f && b > 0.0f && a < 3000.0f && b < 3000.0f)) return 0;
    *hw = a + mg;
    *hh = b + mg;
    return 1;
}

static void CollectAtkBoxes(u32 anim, Th155ActorState* out) {
    u32 i;
    out->atk_box_n = 0;
    for (i = 0; i < 4; ++i) {
        out->atk_box_x[i] = out->atk_box_y[i] = 0.0f;
        out->atk_box_hw[i] = out->atk_box_hh[i] = 0.0f;
    }
    if (!anim || !PubReadable(anim, 0x90)) return;
    u32 first = rd32(anim + 0x84u);
    u32 last  = rd32(anim + 0x88u);
    if (!first || !last || last <= first || (last - first) > 0x200u) return;
    u32 count = (last - first) / 4u;
    if (count > 16u) count = 16u;
    /* 必须先验证整段向量可读再逐元素读:first/last 是游戏堆指针,
       非零不代表可读(可能指向已释放或不可访问的页)。
       缺少这一行会让桥接在游戏进程内直接崩溃 ——
       实测 ShotSizeFromAnim 一直有这道保护,这里漏了。 */
    if (!PubReadable(first, count * 4u)) return;
    int n = 0;
    for (u32 k = 0; k < count && n < 4; ++k) {
        u32 box_obj = rd32(first + k * 4u);
        if (!box_obj || !PubReadable(box_obj, 0x10)) continue;
        u32 col_obj = rd32(box_obj + 0x0cu);
        if (!col_obj || !PubReadable(col_obj, 0xd0)) continue;
        float hw = 0.0f, hh = 0.0f;
        if (!ShotSizeFromShape(rd32(col_obj + 0xccu), &hw, &hh)) continue;
        /* 占位元素的尺寸是野值:它们共用同一个 col_obj(实测 co=0b6dcc9c),
           shape 指针却指向别处,解出 -4.7e34 这种数。ShotSizeFromShape 的
           上界 3000 只在 a/b 一个是 NaN 时拦住,野值仍可能是正常符号。
           这里再加一道"框必须落在角色附近的合理范围"的校验:
           攻击框的世界坐标必然在场景内(0..1280 / 0..720)并允许少量外延。 */
        {
            float bx = rdf(col_obj + 0x40u);
            float by = rdf(col_obj + 0x44u);
            if (!(bx > -600.0f && bx < 1880.0f && by > -400.0f && by < 1120.0f))
                continue;
            if (!(hw > 0.5f && hw < 600.0f && hh > 0.5f && hh < 600.0f)) continue;
        }
        /* 世界坐标在 btTransform 的 m_origin = co + 0x40 / 0x44。
           **不是** co + 0x0C / 0x10 —— 那里是 3x3 基矩阵:
           +0x0C..+0x38 依次是 m_basis 的 9 个 float(单位阵特征值是
           0/1 交替,曾误读成 (0,-1) 这种"坐标"),+0x3C 是填充,
           m_origin 从 +0x40 起。实测 co+0x40/0x44 = (1303.0, 268.7)
           与角色坐标吻合。 */
        out->atk_box_x[n]  = rdf(col_obj + 0x40u);
        out->atk_box_y[n]  = rdf(col_obj + 0x44u);
        out->atk_box_hw[n] = hw;
        out->atk_box_hh[n] = hh;
        ++n;
    }
    out->atk_box_n = n;
}

static void EmitActor(u32 proc, Th155ShotState* s) {
    s->src = -1;
    s->x  = rdf(proc + O_ACTOR_X);
    s->y  = rdf(proc + O_ACTOR_Y);
    s->vx = rdf(proc + O_ACTOR_VX);
    s->vy = rdf(proc + O_ACTOR_VY);
    s->dir = rdf(proc + O_ACTOR_DIR);
    s->id = (i32)rd32(proc + O_ACTOR_ID);
    u32 anim = rd32(proc + O_ACTOR_ANIM);
    s->motion = anim ? (i32)rd32(anim + O_ANIM_MOTION) : -1;
    /* 判定框(AABB) */
    s->box_l = rdf(proc + O_ACTOR_BOX_L);
    s->box_t = rdf(proc + O_ACTOR_BOX_T);
    s->box_r = rdf(proc + O_ACTOR_BOX_R);
    s->box_b = rdf(proc + O_ACTOR_BOX_B);

    s->is_beam = 0;
    s->beam_w = 0.0f;
    s->beam_l = 0.0f;
    s->beam_angle = 0.0f;

    /* 从 Actor2D 读取 rz 角度 (proc + 0x64) */
    if (PubReadable(proc, 0x70)) {
        s->beam_angle = rdf(proc + O_ACTOR_RZ);
    }

    /* Bullet Physics 2D: 弹幕与激光的判定尺寸解析 */
    if (anim) {
        i32 take = (i32)rd32(anim + O_ANIM_TAKE);
        float hw = 0.0f, hh = 0.0f;
        float cx = s->x, cy = s->y;
        float r00 = 1.0f, r01 = 0.0f, r10 = 0.0f, r11 = 1.0f;
        int is_bm = 0;
        float bw = 0.0f, bl = 0.0f;

        int got = ShotSizeFromAnim(anim, &hw, &hh, &cx, &cy, &r00, &r01, &r10, &r11,
                                   &is_bm, &bw, &bl);
        if (got) {
            ShotSizeStore(s->motion, take, hw, hh, is_bm, bw, bl);
        } else {
            ShotSizeLookup(s->motion, take, &hw, &hh, &is_bm, &bw, &bl);
        }

        /* 结合 motion 识别特有激光(如魔理沙 2018/2019/2509/4009/4019 等) */
        if (s->motion == 2018 || s->motion == 2019 || s->motion == 2509 ||
            s->motion == 4009 || s->motion == 4019) {
            is_bm = 1;
            if (bw <= 0.0f) bw = (s->motion == 4009) ? 104.0f : 30.0f;
            if (bl <= 0.0f) bl = (s->motion == 4009) ? 1729.0f : 1280.0f;
            if (hw <= 0.0f || hh <= 0.0f) {
                hw = bl * 0.5f;
                hh = bw * 0.5f;
            }
        }

        if (s->beam_angle == 0.0f) {
            if (got && (r01 != 0.0f || r10 != 0.0f || r00 < 0.0f)) {
                s->beam_angle = atan2_f32(r10, r00);
            } else if (s->dir < 0.0f) {
                s->beam_angle = 3.14159265f;
            }
        }

        s->is_beam = is_bm;
        s->beam_w = bw;
        s->beam_l = bl;

        /* 计算有效 AABB */
        if (hw > 0.0f && hh > 0.0f) {
            if (got && (cx > -1000.0f && cx < 3000.0f && cy > -1000.0f && cy < 2000.0f)) {
                /* Bullet 已给出真实世界中心 cx, cy 与旋转矩阵 */
                float abs_r00 = (r00 >= 0.0f) ? r00 : -r00;
                float abs_r01 = (r01 >= 0.0f) ? r01 : -r01;
                float abs_r10 = (r10 >= 0.0f) ? r10 : -r10;
                float abs_r11 = (r11 >= 0.0f) ? r11 : -r11;
                float dx = hw, dy = hh;
                float rot_sum = abs_r00 + abs_r01;
                if (rot_sum > 0.5f && rot_sum < 2.0f) {
                    dx = abs_r00 * hw + abs_r01 * hh;
                    dy = abs_r10 * hw + abs_r11 * hh;
                }
                s->box_l = cx - dx;
                s->box_r = cx + dx;
                s->box_t = cy - dy;
                s->box_b = cy + dy;
            } else if (is_bm) {
                /* 射线/激光: 发射原点在 s->x, s->y, 向 beam_angle 延伸 bl 长度 */
                float sin_a = 0.0f, cos_a = 1.0f;
                sincos_f32(s->beam_angle, &sin_a, &cos_a);
                float half_l = bl * 0.5f;
                float half_w = bw * 0.5f;
                float center_x = s->x + half_l * cos_a;
                float center_y = s->y + half_l * sin_a;
                float abs_c = (cos_a >= 0.0f) ? cos_a : -cos_a;
                float abs_s = (sin_a >= 0.0f) ? sin_a : -sin_a;
                float dx = abs_c * half_l + abs_s * half_w;
                float dy = abs_s * half_l + abs_c * half_w;
                s->box_l = center_x - dx;
                s->box_r = center_x + dx;
                s->box_t = center_y - dy;
                s->box_b = center_y + dy;
            } else {
                /* 常规点弹幕正交包围盒 */
                s->box_l = s->x - hw;
                s->box_r = s->x + hw;
                s->box_t = s->y - hh;
                s->box_b = s->y + hh;
            }
        }
    }
    s->cb_group = (i32)rd32(proc + O_ACTOR_CBGROUP);
    s->cb_mask  = (i32)rd32(proc + O_ACTOR_CBMASK);
    s->owner = OwnerOfActor(proc);
    s->key_take = anim ? (i32)rd32(anim + O_ANIM_TAKE) : -1;
    s->key_frame = anim ? (i32)rd32(anim + O_ANIM_FRAME) : -1;
    s->is_attack = th155_is_attack(s->motion);
    /* 弹幕的攻击等级 **不可采**。
       曾用 "扫 0x40..0xc0 找第一个 INSTANCE 引用" 的办法取 temp_atk_data,
       实测那是错的:
         - 命中的实例就是**施法者本人**(探针: +0x74 -> master),
         - 所以所有弹幕的 atk_lv 恒等于该玩家的攻击等级
           (实测 91.2% 的弹幕 atk_lv 恰好等于某个 actor 的 atk_lv,
            且同一 motion 的弹幕 atk_lv 完全相同,与弹幕种类无关),
         - 弹幕 native 对象上**没有**指向自身 SQ 实例的引用
           (20 个 proc × 0x00..0x200 全扫,命中的全是玩家实例)。
       因此这里保持 0, 由上层用 is_attack(按 motion 分级)与几何关系判断。 */
    (void)0;
} 

/* 伪弹幕判据:**没有任何一方认领它**。
   实测 motion 190 占弹幕样本的 58%(11779/约20000),它的 cb_mask=0、
   owner=-1、cb_group=0,且**从未**出现过一个非零判定框(hasbox=0/11779),
   速度非零所以能通过休眠壳子过滤。它既不是攻击源、也不属于任何队伍、
   更没有可碰到的东西,留在观测里只是把 16 个 MAX_PROJ 名额占满。
   同时 cb_group=0 也意味着它不在 callback_group_shot(16/32)里。
   判据取 owner<0 且 cb_group==0 且 cb_mask==0 三者同时成立:
   真实的 2008/2009/6030 至少满足 cb_mask!=0 或 cb_group in {16,32}。 */
static int IsPhantomShot(u32 proc) {
    if ((i32)rd32(proc + O_ACTOR_CBMASK) != 0) return 0;
    i32 grp = (i32)rd32(proc + O_ACTOR_CBGROUP);
    if (grp != 0) return 0;
    return OwnerOfActor(proc) < 0;
}

static u32 CollectFromGroup(u32 battle, const char* name,
                            Th155ShotState* out, u32 maxOut, u32 srcTag) {
    if (!battle || !maxOut) return 0;
    u32 slot = battle_slot(battle, name);
    if (!slot || rawtag(rd32(slot)) != RAW_INSTANCE) return 0;
    u32 inst = rd32(slot + O_OBJ_VALUE);
    if (!inst) return 0;
    u32 up = inst_native(inst);
    if (!up) return 0;
    u32 begin = rd32(up + ACTOR_GROUP_BEGIN);
    u32 end   = rd32(up + ACTOR_GROUP_END);
    if (!begin || !end || end <= begin) return 0;
    u32 total = (end - begin) / 4;
    if (total > ACTOR_GROUP_MAX) total = ACTOR_GROUP_MAX;

    u32 n = 0;
    for (u32 i = 0; i < total && n < maxOut; ++i) {
        u32 proc = rd32(begin + i * 4);
        if (!proc) continue;
        u32 anim = rd32(proc + O_ACTOR_ANIM);
        if (!anim) continue;
        i32 mo = (i32)rd32(anim + O_ANIM_MOTION);
        /* 容器里的演出弹幕 motion=6030;而真正命中的 2008/2009/2010
           不在容器中(见下方堆扫描),两者都收。 */
        if (mo < SHOT_MOTION_MIN || mo > SHOT_MOTION_MAX) continue;
        /* 容器里混有玩家本体。**必须排除玩家,而不是只留归组**:
           实测玩家 cb_group = 1/2(team+1), 弹幕 cb_group = 16/32
           (callback_group_shot)。旧逻辑"只保留 cbg 等于 shot 组"恰好
           把所有弹幕滤掉、只留下玩家, 导致:
             - 弹幕 AABB 全 0(玩家才是 40x110, 但玩家被当弹幕发了)
             - 弹幕 owner 恒为 team1(容器只含一方玩家)
           现在改为: 玩家 native 对象直接跳过, 其余照收。 */
        if (IsPlayerNative(proc)) continue;
        /* 剔除对象池里的休眠/残影: 静止且无活跃判定框的对象。
           允许具有活跃判定框或激光/光束 motion 的静态对象通过(如持续激光/Master Spark)。 */
        {
            float svx = rdf(proc + O_ACTOR_VX);
            float svy = rdf(proc + O_ACTOR_VY);
            if (svx == 0.0f && svy == 0.0f) {
                int is_laser_or_active = 0;
                if (mo == 2018 || mo == 2019 || mo == 2509 || mo == 4009 || mo == 4019) {
                    is_laser_or_active = 1;
                } else if (anim && PubReadable(anim, 0x8c)) {
                    u32 f = rd32(anim + 0x84u);
                    u32 l = rd32(anim + 0x88u);
                    if (f && l > f) is_laser_or_active = 1;
                }
                if (!is_laser_or_active) continue;
            }
        }
        /* 伪弹幕(motion 190 之类)直接丢弃:它占样本 58% 却永远没有判定框。 */
        if (IsPhantomShot(proc)) continue;
        /* 登记去重。**必须检查返回值**:组向量里同一个 proc 会出现多次
           (实测一帧 16 条里 id=139 重复 9 次、id=136 重复 4 次),
           旧代码调用 ShotSeen 却丢弃返回值,等于没去重,
           导致 5 个真实弹幕占满 16 个 MAX_PROJ 名额,观测全是重复行。 */
        if (ShotSeen(proc)) continue;
        EmitActor(proc, &out[n]);
        out[n].src = (i32)srcTag;
        ++n;
    }
    return n;
}

/* 32 位 MEMORY_BASIC_INFORMATION = 7 个 u32(28 字节),**没有 PartitionId**
   (PartitionId 是 64 位版本才有的字段)。原始字实测:
     w0=0x00010000 (Base)  w3=0x00490000 (RegionSize)
     w4=0x00010000 (State = MEM_FREE)  w5=Protect
   所以布局是: [0]Base [1]AllocationBase [2]AllocationProtect
              [3]RegionSize [4]State [5]Protect [6]Type
   历史 bug:旧代码用 mbi[1] 当 RegionSize(其实是 AllocationBase,空区为 0),
   于是 `if (!rsize) break` 第一次就断,堆扫描全程是死代码。 */
#define MBI_U32   7u
#define MBI_BASE  0u
#define MBI_SIZE  3u
#define MBI_STATE 4u
#define MBI_PROT  5u
#define MBI_TYPE  6u
#define MEM_COMMIT_    0x1000u
#define MEM_PRIVATE_   0x20000u
#define PAGE_GUARD_    0x100u
#define PAGE_NOACCESS_ 0x001u

/* 干净区间的可读性判定。
   /NODEFAULTLIB 下 SEH 需要 CRT 的 __except_handler3,链不进来,
   所以用 VirtualQuery 提前判定:目标区间必须整体落在同一个 MEM_COMMIT
   且非 NOACCESS/GUARD 的区域里。这样后续裸解引用不会 AV。 */
static int IsReadable(u32 addr, u32 size) {
    if (!pVirtualQuery || !addr) return 0;
    u32 mbi[MBI_U32];
    if (!pVirtualQuery((void*)addr, mbi, sizeof(mbi))) return 0;
    u32 base  = mbi[MBI_BASE];
    u32 rsize = mbi[MBI_SIZE];
    u32 state = mbi[MBI_STATE];
    u32 prot  = mbi[MBI_PROT];
    if (state != MEM_COMMIT_) return 0;
    if (prot & (PAGE_GUARD_ | PAGE_NOACCESS_)) return 0;
    if (addr < base || addr + size < addr) return 0;
    if (addr + size > base + rsize) return 0;
    return 1;
}

/* ---- 发布路径的单帧可读区域缓存 ----
   实测(4 实例训练中对游戏主线程做外部栈采样):主线程 52% 的时间在
   IsReadable 的 VirtualQuery 里,锁步一帧的同步发布 ls_pub_us 为 7~17ms。
   发布一帧要验证几百个对象指针(group_effect 里静止对象逐个查 anim),
   它们落在少数几个堆区域里。这里记住本次发布已确认可读的整块区域
   [base, base+size):只缓存"可读"结论,不可读一律重查;每次 PublishState
   开头清空,寿命不超过一次发布。判定结果与 IsReadable 相同(区间必须整体
   落在同一个可读区域内),TOCTOU 窗口与原来的"先查后读"同一量级。
   只给发布路径用:PublishState 的两个调用方都持 g_lock;ScanThread 不持锁,
   所以 HeapActorValid/PoolEntryLive 仍直接走 IsReadable。 */
#define PUB_REGION_CACHE 16u
static u32 g_pubRegBase[PUB_REGION_CACHE], g_pubRegEnd[PUB_REGION_CACHE];
static u32 g_pubRegN = 0, g_pubRegNext = 0;

static void PubReadableReset(void) { g_pubRegN = 0; g_pubRegNext = 0; }

static int PubReadable(u32 addr, u32 size) {
    if (!addr) return 0;
    u32 end = addr + size;
    if (end < addr) return 0;
    for (u32 i = 0; i < g_pubRegN; ++i)
        if (addr >= g_pubRegBase[i] && end <= g_pubRegEnd[i]) return 1;
    if (!pVirtualQuery) return 0;
    u32 mbi[MBI_U32];
    if (!pVirtualQuery((void*)addr, mbi, sizeof(mbi))) return 0;
    u32 base  = mbi[MBI_BASE];
    u32 rend  = base + mbi[MBI_SIZE];
    if (mbi[MBI_STATE] != MEM_COMMIT_) return 0;
    if (mbi[MBI_PROT] & (PAGE_GUARD_ | PAGE_NOACCESS_)) return 0;
    if (addr < base || end > rend || rend < base) return 0;
    u32 slot = g_pubRegNext++ % PUB_REGION_CACHE;
    g_pubRegBase[slot] = base;
    g_pubRegEnd[slot] = rend;
    if (g_pubRegN < PUB_REGION_CACHE) ++g_pubRegN;
    return 1;
}

/* 安全读原语(定义在文件后部,堆扫描与池子校验都要用)。
   ReadProcessMemory 对不可读页返回失败而不是抛异常,是堆扫描唯一允许的读路径。 */
static u32 SafeReadBuf(u32 addr, u32 bytes);
static int SafeRead32(u32 addr, u32* out);
static int SafeReadF(u32 addr, float* out);

/* 堆扫描的接受判据。
   已释放对象的堆块会留下 vtable 残值,直接按 vtable 扫会混入大量垃圾
   (实测单个采样帧里 cbg=0 的"对象"有 200+ 个)。这里三重过滤:
     1) 坐标必须是有限值且落在场地附近 —— 垃圾块的 x/y 是随机 float;
     2) callbackGroup 必须等于某队的 callback_group_shot(16/32) —— 这是
        归属判据本身,顺带排掉玩家本体(cbg=1/2)和中性特效(cbg=0);
     3) 只在成功解析出 animation 指针时才算数。 */
static int HeapActorValid(u32 proc) {
    if (!g_cbGroupShot[0] && !g_cbGroupShot[1]) return 0;
    if (!IsReadable(proc, 0x100)) return 0;
    u32 rawCbg = 0, anim = 0;
    if (!SafeRead32(proc + O_ACTOR_CBGROUP, &rawCbg)) return 0;
    i32 cbg = (i32)rawCbg;
    if ((u32)cbg != g_cbGroupShot[0] && (u32)cbg != g_cbGroupShot[1]) return 0;
    if (!SafeRead32(proc + O_ACTOR_ANIM, &anim)) return 0;
    if (!anim) return 0;
    u32 animMo = 0;
    if (!SafeRead32(anim + O_ANIM_MOTION, &animMo)) return 0;
    float x = 0.0f, y = 0.0f;
    if (!SafeReadF(proc + O_ACTOR_X, &x)) return 0;
    if (!SafeReadF(proc + O_ACTOR_Y, &y)) return 0;
    /* 场地约 1280x720,留足边界容差;NaN 比较恒假,天然被排除 */
    if (x != x || y != y) return 0;
    if (x < -4096.0f || x > 8192.0f) return 0;
    if (y < -4096.0f || y > 8192.0f) return 0;
    return 1;
}

/* 扫描堆,找所有 vtable==ACTOR2D_VT 的对象,把弹幕补进 out。
   只扫 MEM_PRIVATE(堆/栈),跳过 exe 映像与映射文件,省掉大半开销。 */
static u32 g_dbgRegions, g_dbgVt, g_dbgEmit;
static u32 g_dbgVtTotal;         /* 累计命中数(不随单次 ScanStep 重置) */
static u32 g_dbgMbiRaw[8], g_dbgFirst;
static u32 g_dbgScanCursor, g_dbgScanPass;
static u32 g_regionCursor = 0;

/* ---- 堆扫描:发现池 + 预算游标 ----------------------------------------
 * 朴素实现要遍历 568MB(1.42 亿次比较),单次几百毫秒,而发布线程每 2ms
 * 循环一次 —— 线程被彻底拖死(实测 loop 冻结在 48097)。分批扫描又会漏掉
 * 未扫到的弹幕,导致同一发弹幕时有时无。
 *
 * 最终方案:**持久对象池**
 *   1. 发现阶段:维护持久游标,每次调用只扫 SCAN_BUDGET 字节,途中发现的
 *      Actor2D 指针写进池子。一个全堆周期约 24 次调用,不必一帧扫完。
 *   2. 发布阶段:每次调用只重新读池子里的指针(几十个,~微秒级),
 *      校验 vtable/交接组/坐标仍然有效,失效的踢出池子。
 * 这样每帧都能拿到"上次扫到过的全部存活弹幕",而扫描成本被摊平。
 * -------------------------------------------------------------------- */
#define SCAN_BUDGET   (6u * 1024u * 1024u)     /* 已废弃:改用时间预算 SCAN_SLICE_MS */
#define SCAN_SLICE_MS 4u                       /* 扫描线程单次连续工作的毫秒上限 */

#define SCAN_MAX_REG  128u                     /* 单次扫描区域数上限 */
#define SCAN_MAX_REGION_BYTES (256u * 1024u)   /* 单区域内单次扫描字节上限 */
#define POOL_MAX      512u

static u32  g_scanCursor = 0x10000u;
static u32  g_scanPass   = 0;
static u32  g_pool[POOL_MAX];
static u32  g_poolN = 0;

/* 发布线程只读的快照。写者是后台扫描线程,用 seqlock(奇=写入中)保护:
   写者在 x86 上对 volatile 的写不会互相重排,读者两次读 seq 一致即认为完整。
   万一读到撕裂值也没关系 —— 使用前一律先 IsReadable + vtable 校验。 */
static u32          g_snap[POOL_MAX];
static volatile u32 g_snapN = 0;
static volatile u32 g_snapSeq = 0;
static u32          g_rdBuf[POOL_MAX];      /* 发布线程私有,避免快照被中途改写 */

static int PoolHas(u32 p) {
    for (u32 i = 0; i < g_poolN; ++i) if (g_pool[i] == p) return 1;
    return 0;
}

static void PoolAdd(u32 p) {
    if (g_poolN < POOL_MAX && !PoolHas(p)) g_pool[g_poolN++] = p;
}

/* 把主池子发布成快照(仅扫描线程调用)。 */
static void PublishPool(void) {
    u32 n = g_poolN;
    ++g_snapSeq;                            /* 变奇:写入中 */
    /* 用 volatile 指针逐条写:普通数组复制会被 /O2 向量化成 memcpy,
       /NODEFAULTLIB 下没有 CRT 提供它(链接期 LNK2019)。 */
    volatile u32* dst = g_snap;
    for (u32 i = 0; i < n; ++i) dst[i] = g_pool[i];
    g_snapN = n;                            /* 先放数据,后放计数 */
    ++g_snapSeq;                            /* 变偶:稳定 */
}

/* 读取一份一致快照到 g_rdBuf,返回条数(仅发布线程调用)。 */
static u32 ReadPoolSnapshot(void) {
    volatile u32* src = g_snap;
    for (u32 attempt = 0; attempt < 4; ++attempt) {
        u32 s1 = g_snapSeq;
        if (s1 & 1u) continue;
        u32 n = g_snapN;
        if (n > POOL_MAX) n = POOL_MAX;
        for (u32 i = 0; i < n; ++i) g_rdBuf[i] = src[i];
        if (g_snapSeq == s1) return n;
    }
    return 0;                               /* 连续冲突:本帧不报堆弹幕,下帧再试 */
}

/* ---- 安全批量读 --------------------------------------------------------
 * 堆扫描**不能**用裸指针读:VirtualQuery 与 rd32 之间存在 TOCTOU 窗口,
 * 游戏线程在两者之间释放/重映射页会让 rd32 直接 AV。
 * 实测崩溃点:0xc0000005 @ ScanStep 的 `mov eax,[edx]; cmp eax,ACTOR2D_VT`
 * (dmp0 字节 8b 02 / 3d 38 7a 8e 00 / 75 62)。
 * ReadProcessMemory 对不可读页返回失败码而不是抛异常,所以用它做唯一读路径。
 *
 * 返回实际读到的字节数(0 = 整块不可读)。
 */
#define SCAN_BUF_BYTES (64u * 1024u)
static u32 g_scanBuf[SCAN_BUF_BYTES / 4];

static u32 SafeReadBuf(u32 addr, u32 bytes) {
    if (!pReadProcessMemory || !addr || !bytes) return 0;
    if (bytes > SCAN_BUF_BYTES) bytes = SCAN_BUF_BYTES;
    unsigned long got = 0;
    if (!pReadProcessMemory((HANDLE)g_selfProc, (const void*)addr,
                            g_scanBuf, bytes, &got))
        return 0;
    return (u32)got;
}

/* 单个 u32 的安全读(池子条目校验用)。 */
static int SafeRead32(u32 addr, u32* out) {
    if (!pReadProcessMemory || !addr || !out) return 0;
    unsigned long got = 0;
    if (!pReadProcessMemory((HANDLE)g_selfProc, (const void*)addr,
                            out, 4, &got) || got != 4)
        return 0;
    return 1;
}

/* 安全读一个 float(候选对象字段校验用)。 */
static int SafeReadF(u32 addr, float* out) {
    u32 raw = 0;
    if (!SafeRead32(addr, &raw)) return 0;
    *out = *(float*)&raw;
    return 1;
}

/* 池子中 p 是否仍是一个存活的敌方弹幕;是则返回 1。 */
static int PoolEntryLive(u32 p) {
    u32 vt = 0;
    if (!p || !IsReadable(p, 0x100)) return 0;
    if (!SafeRead32(p, &vt) || vt != ACTOR2D_VT) return 0;
    return HeapActorValid(p);
}

/* 发现阶段:按**时间片**在独立线程上推进游标。
   历史坑(两次,都记在案):
     1. 扫描原本在发布路径上 → `dbg_pub_ms` 800~1640ms,`shm.frame` 冻结,
        `action_repeat=4` 被拉成 48~98 帧/步;
     2. 修完又加过"慢区永久拉黑":首次触碰某区的页面本身就慢(6~11ms/64KB),
        而那时 `vt` 必然是 0,于是**真正装着 actor 的区域被永久排除**,`emit` 归零。
   所以现在:扫描独占线程、按时间片让出 CPU、**不做任何区域级拉黑**。
   慢区的代价(整轮多花几秒)远小于丢弹幕的代价。 */
static void ScanStep(void) {
    if (!pVirtualQuery || !pGetTickCount) return;
    g_dbgRegions = 0;
    g_dbgVt = 0;
    g_dbgScanCursor = g_scanCursor;
    u32 t0 = pGetTickCount();
    u32 addr = g_scanCursor;
    if (addr < 0x10000u) addr = 0x10000u;
    u32 regs = 0;

    while (addr < 0x7FFE0000u && regs < SCAN_MAX_REG) {
        if (pGetTickCount() - t0 >= SCAN_SLICE_MS) break;
        u32 mbi[MBI_U32];
        memzero(mbi, sizeof(mbi));
        if (!pVirtualQuery((void*)addr, mbi, sizeof(mbi)))
            break;
        u32 base  = mbi[MBI_BASE];
        u32 rsize = mbi[MBI_SIZE];
        u32 state = mbi[MBI_STATE];
        u32 prot  = mbi[MBI_PROT];
        u32 type  = mbi[MBI_TYPE];
        if (!rsize) break;
        ++regs;
        ++g_dbgRegions;

        if (state == MEM_COMMIT_ && type == MEM_PRIVATE_
            && rsize >= 0x1000u
            && !(prot & (PAGE_GUARD_ | PAGE_NOACCESS_))) {
            u32 lim = base + rsize;
            u32 start = base;
            if (g_regionCursor > base && g_regionCursor < lim)
                start = g_regionCursor;
            u32 chunkEnd = start + SCAN_MAX_REGION_BYTES;
            if (chunkEnd > lim) chunkEnd = lim;

            u32 p = start;
            /* 分块 + ReadProcessMemory 扫描:一次读 SCAN_BUF_BYTES,
               再在本地缓冲里找 vtable。裸解引用会在区域被释放时 AV。 */
            while (p + 4 <= chunkEnd) {
                u32 want = chunkEnd - p;
                if (want > SCAN_BUF_BYTES) want = SCAN_BUF_BYTES;
                u32 got = SafeReadBuf(p, want);
                if (!got) {
                    /* 整块读不到:跳过这一块,继续下一块(不要 break,
                       后面可能还有可读页)。 */
                    p += want;
                    continue;
                }
                u32 n = got / 4;
                for (u32 i = 0; i < n; ++i) {
                    if (g_scanBuf[i] != ACTOR2D_VT) continue;
                    u32 cand = p + i * 4;
                    ++g_dbgVt;
                    if (g_poolN >= POOL_MAX) break;
                    if (PoolHas(cand)) continue;
                    if (!HeapActorValid(cand)) continue;
                    PoolAdd(cand);
                }
                p += n * 4;
                if (n * 4 < want) p += (want - n * 4);   /* 读到一半:跳过剩余 */
            }

            /* 本区域还有剩余:游标停在 base,下次从 g_regionCursor 继续。 */
            if (chunkEnd < lim) {
                g_regionCursor = chunkEnd;
                g_scanCursor = base;
                g_dbgScanCursor = base;
                PublishPool();
                return;
            }
            g_regionCursor = 0;
        }

        u32 next = base + rsize;
        if (next <= addr) break;
        addr = next;
    }

    if (addr >= 0x7FFE0000u) {
        g_scanCursor = 0x10000u;
        ++g_scanPass;
        /* 一轮扫完:压掉池子里已死/已不再是弹幕的条目。
           不压缩的话 POOL_MAX 会被历史指针占满,新弹幕再也进不来。 */
        u32 w = 0;
        for (u32 i = 0; i < g_poolN; ++i) {
            if (PoolEntryLive(g_pool[i])) g_pool[w++] = g_pool[i];
        }
        g_poolN = w;
    } else {
        g_scanCursor = addr;
    }
    PublishPool();
}

/* 发布阶段:只重读**快照**里的指针,输出存活弹幕。
   这里绝不推进扫描游标 —— 否则又会把发布线程拖住。 */
static u32 CollectFromHeap(Th155ShotState* out, u32 maxOut, u32 startN) {
    g_dbgEmit = 0;
    u32 n = startN;
    u32 poolN = ReadPoolSnapshot();
    for (u32 i = 0; i < poolN && n < maxOut; ++i) {
        u32 p = g_rdBuf[i];
        if (!PoolEntryLive(p)) continue;
        i32 mo = (i32)rd32(rd32(p + O_ACTOR_ANIM) + O_ANIM_MOTION);
        if (mo < SHOT_MOTION_MIN || mo > SHOT_MOTION_MAX) continue;
        if (ShotSeen(p)) continue;
        if (IsPlayerNative(p)) continue;
        if (IsPhantomShot(p)) continue;   /* 同上: 无主/无判定框的不是弹幕 */
        {   /* 同上: 剔除静止且无活跃判定框的休眠/残影对象(允许持续激光通过) */
            float hvx = 0.0f, hvy = 0.0f;
            if (!SafeReadF(p + O_ACTOR_VX, &hvx)) continue;
            if (!SafeReadF(p + O_ACTOR_VY, &hvy)) continue;
            if (hvx == 0.0f && hvy == 0.0f) {
                int is_laser_or_active = 0;
                if (mo == 2018 || mo == 2019 || mo == 2509 || mo == 4009 || mo == 4019) {
                    is_laser_or_active = 1;
                } else {
                    u32 anim = rd32(p + O_ACTOR_ANIM);
                    if (anim && PubReadable(anim, 0x8c)) {
                        u32 f = rd32(anim + 0x84u);
                        u32 l = rd32(anim + 0x88u);
                        if (f && l > f) is_laser_or_active = 1;
                    }
                }
                if (!is_laser_or_active) continue;
            }
        }
        EmitActor(p, &out[n]);
        out[n].src = 2;
        ++n;
        ++g_dbgEmit;
    }
    return n;
}

static u32 CollectShots(u32 battle, Th155ShotState* out, u32 maxOut) {
    if (!battle || !maxOut) return 0;
    ResolveShotGroups(battle);
    g_shotSeenN = 0;
    u32 n = CollectFromGroup(battle, "group_player", out, maxOut, 0);
    if (n < maxOut)
        n += CollectFromGroup(battle, "group_effect", out + n, maxOut - n, 1);
    /* 堆扫描结果:只读后台线程发布的快照指针(几十条,微秒级)。
       真正的扫描在 ScanThread 里,按时间片推进。 */
    n = CollectFromHeap(out, maxOut, n);
    return n;
}

// ---------------------------------------------------------------------------
// 状态发布
// ---------------------------------------------------------------------------
static void PublishActor(Th155ActorState* out, u32 playerInst) {
    memzero(out, sizeof(Th155ActorState));
    if (!playerInst) return;
    u32 nat = inst_native(playerInst);
    if (!nat) return;
    out->x = rdf(nat + O_ACTOR_X);
    out->y = rdf(nat + O_ACTOR_Y);
    out->vx = rdf(nat + O_ACTOR_VX);
    out->vy = rdf(nat + O_ACTOR_VY);
    out->dir = rdf(nat + O_ACTOR_DIR);
    u32 anim = rd32(nat + O_ACTOR_ANIM);
    if (anim) {
        out->motion = (i32)rd32(anim + O_ANIM_MOTION);
        out->key_take = (i32)rd32(anim + O_ANIM_TAKE);
        out->key_frame = (i32)rd32(anim + O_ANIM_FRAME);
    }
    out->hit_stop = inst_field_int(playerInst, "hitStopTime");
    out->damage_stop = inst_field_int(playerInst, "damageStopTime");
    out->hit_state = inst_field_int(playerInst, "hit_state");
    out->guard_state = inst_field_int(playerInst, "guard_state");
    /* dash 无敌:dashCount>0 期间实测掉血率为非 dash 的 1/68(见 docs §17.9)。
       `player.invin` 在 dash 期间**恒为 0**,不能用它判断无敌。 */
    out->dash_count = inst_field_int(playerInst, "dashCount");
    out->graze_state = inst_field_int(playerInst, "graze_state");
    /* 受击框 AABB:实测 40x110,即 (x±20, y-60..y+50),随位置移动但尺寸恒定 */
    out->box_l = rdf(nat + O_ACTOR_BOX_L);
    out->box_t = rdf(nat + O_ACTOR_BOX_T);
    out->box_r = rdf(nat + O_ACTOR_BOX_R);
    out->box_b = rdf(nat + O_ACTOR_BOX_B);
    out->cb_group = (i32)rd32(nat + O_ACTOR_CBGROUP);
    /* ---- 攻击/受击判定 ----
       这些全是**实例字段**(在 1101 个成员里实测存在且随 motion/keyFrame
       变化),不是 native 偏移,所以走 inst_field_int。
       `is_attack` 用 motion 走 th155_is_attack(),与原生 IsAttack() 同口径 ——
       实测掉血帧攻击方 motion 为 0/1/2/1000/1212/1500/1600/1720/2000/3031,
       旧的 "motion>=3000 才算攻击" 会漏掉其中 8 个。 */
    out->atk_id = inst_field_int(playerInst, "atk_id");
    out->hit_count = inst_field_int(playerInst, "hitCount");
    out->hit_result = inst_field_int(playerInst, "hitResult");
    /* 帧级攻击数据:temp_atk_data 是一个子实例,不是 actor 自己的字段。
       之前只查 actor 上的同名 null 值,所以误判成"不可达"。 */
    {
        u32 atk = inst_field_inst(playerInst, "temp_atk_data");
        if (atk) {
            out->atk_lv = inst_field_int(atk, "attackLV");
            out->atk_cancel = inst_field_int(atk, "cancelCount");
            out->atk_guard_break = inst_field_int(atk, "disableGuard");
        }
    }
    out->hit_back_flag = inst_field_int(playerInst, "hitBackFlag");
    /* 命中位掩码。实测掉血帧 160/161 = bit14(16384),普通期是 0/bit23/bit14+26。
       这是"这一帧挨了什么"的直接依据,旧版判它死字段是采样窗口不对。 */
    out->hit_id = inst_field_int(playerInst, "hit_id");
    out->is_attack = th155_is_attack(out->motion);
    /* 硬直/破防/受身。同上,全部是实例字段。 */
    out->endure_count = inst_field_int(playerInst, "endureCount");
    out->endure_max = inst_field_int(playerInst, "endureMax");
    out->stan_count = inst_field_int(playerInst, "stanCount");
    out->recover = inst_field_int(playerInst, "recover");
    out->center_stop = inst_field_int(playerInst, "centerStop");
    out->gravity = inst_field_float(playerInst, "baseGravity");
    out->slide_speed = inst_field_float(playerInst, "baseSlideSpeed");
    out->atk_range = inst_field_int(playerInst, "atkRange");
    out->target_dist = inst_field_float(playerInst, "targetDist");
    /* 近战攻击框:见 CollectAtkBoxes 的链路说明。 */
    CollectAtkBoxes(anim, out);
}

static void PublishState(Resolved* rs) {
    Th155Shared* s = g_shm;
    u32 i;
    /* seqlock:发布期间 seq 必为奇数。Python 侧据此丢弃撕裂快照。
       以前是先写数据后 seq++,读者可能在字段写入中途读到
       "motion=3910 但 is_attack=0" 这种自相矛盾的组合。 */
    s->seq++;
    PubReadableReset();
    s->frame = (u32)battle_int(rs->battle, "count");
    s->battle_state = battle_int(rs->battle, "state");
    s->time = battle_int(rs->battle, "time");
    s->time_unit = battle_int(rs->battle, "time_unit");
    s->round = battle_int(rs->battle, "round");
    s->winner = battle_int(rs->battle, "winner");
    s->match_num = battle_int(rs->battle, "match_num");
    s->is_time_stop = battle_int(rs->battle, "is_time_stop");
    s->time_stop_count = battle_int(rs->battle, "time_stop_count");
    s->slow_count = battle_int(rs->battle, "slow_count");
    s->demo_count = battle_int(rs->battle, "demoCount");
    s->corner_right_actor = battle_int(rs->battle, "corner_right_actor");
    s->enable_time_up = battle_int(rs->battle, "enableTimeUp");
    s->enable_contact_test = battle_int(rs->battle, "enable_contact_test");
    s->enable_time_count = battle_int(rs->battle, "enableTimeCount");
    {
        /* 走 battle_slot 而不是裸 table_slot: ::battle 可能是 instance
           (g_battleIsTable 在解析时确定), 裸调 table_slot 会把实例的
           O_INST_USERPTR 当 O_TABLE_NODES 读 -> nodes 恒 0 -> 恒判 0。 */
        u32 slot = battle_slot(rs->battle, "PracticeRestart");
        u32 pt = 0, pv = 0;
        s->is_practice = (slot && battle_get(rs->battle, "PracticeRestart", &pt, &pv) &&
                          rawtag(pt) == RAW_CLOSURE) ? 1 : 0;
    }
    for (i = 0; i < 2; ++i) {
        u32 t = rs->team[i];
        s->life[i] = inst_field_int(t, "life");
        s->life_max[i] = inst_field_int(t, "life_max");
        s->regain[i] = inst_field_int(t, "regain_life");
        s->sp[i] = inst_field_int(t, "sp");
        s->sp_max[i] = inst_field_int(t, "sp_max");
        s->mp[i] = inst_field_int(t, "mp");
        s->op[i] = inst_field_int(t, "op");
        s->spell_active[i] = inst_field_int(t, "spell_active");
        s->spell_use_count[i] = inst_field_int(t, "spell_use_count");
        s->combo_count[i] = inst_field_int(t, "combo_count");
        s->slave_ban[i] = inst_field_int(t, "slave_ban");
        s->combo_damage[i] = inst_field_int(t, "combo_damage");
        s->combo_stun[i] = inst_field_int(t, "combo_stun");
        s->damage_scale[i] = inst_field_float(t, "damage_scale");
        inst_field_str(t, "master_name", s->master_name[i], 24);
        inst_field_str(t, "slave_name", s->slave_name[i], 24);
        s->com_difficulty[i] = inst_field_int(rs->player[i][0], "com_difficulty");
        s->com_level[i] = inst_field_int(rs->player[i][0], "com_level");
        {
            /* current 是**实例**字段(不是 table 字段)。曾误用 table_slot/
               table_get 读实例: table_slot 读 O_TABLE_NODES(0x20), 而实例在
               0x20 是 O_INST_USERPTR, 于是 nodes 恒 0 -> 永远返回 0 ->
               current 恒 0, 无论换没换人。用 inst_field_inst 读。 */
            u32 curInst = inst_field_inst(t, "current");
            if (curInst == rs->player[i][0])
                s->current[i] = 0;
            else if (curInst == rs->player[i][1])
                s->current[i] = 1;
            else
                s->current[i] = -1;   /* 解析失败: 不静默当成 master */
        }
        /* op = 怪奇(Occult)ゲージ, 上限 op_stop_max(通常 2000)。
           player_input.nut: 换人需 op >= 1000 且 slave_ban == 0;
           符卡/LastWord 需 op >= 2000 且 sp >= sp_max2。 */
        s->tag_ready[i] = (s->slave_ban[i] == 0 && s->op[i] >= 1000) ? 1 : 0;
        s->spell_ready[i] = (s->op[i] >= 2000 &&
                             s->sp[i] >= inst_field_int(t, "sp_max2")) ? 1 : 0;
        PublishActor(&s->actors[i * 2 + 0], rs->player[i][0]);
        PublishActor(&s->actors[i * 2 + 1], rs->player[i][1]);
    }
    /* 弹幕:容器遍历 + 池子快照,两者都是微秒级(扫描已在独立线程),
       所以每帧都刷新。之前按 1/4 频率刷新是因为扫描在发布路径上;
       现在没有那个成本,反而应该每帧刷 —— 15Hz 决策下 4 帧才更新一次弹幕,
       意味着策略看到的弹幕位置最多滞后 4 帧(约 67ms)。 */
    {
        g_shotPhase = 0;
        s->shot_count = CollectShots(rs->battle, s->shots, TH155_MAX_SHOTS);
    }
    s->attached = 1;
    s->seq++;                      /* 偶数 = 快照完整可读 */
}

/* 无 CRT:x86 上 64 位乘除要 __allmul/__alldiv,这里全部用 32 位。
   QPC 计数取低 32 位,差值按无符号回绕;g_qpcPerUs = 频率/1e6(Win10+ 恒为 10)。 */
static u32 Qpc(void) {
    long long t = 0;
    if (pQueryPerformanceCounter) pQueryPerformanceCounter(&t);
    return (u32)t;
}

static u32 QpcUs(u32 t0, u32 t1) {
    return g_qpcPerUs ? (t1 - t0) / g_qpcPerUs : 0;
}

/* 输入 hook 里、主线程上调用。见 g_lsLastCounter 处的说明。 */
static void LockstepBoundary(void) {
    Th155Shared* s = g_shm;
    if (!s->ctl_lockstep) {
        g_lsStalled = 0;
        return;
    }
    u32 t0 = Qpc();
    Lock();
    int ok = g_rs.ok;
    if (ok) {
        PublishState(&g_rs);
        g_lastFrame = s->frame;
        g_lastFrameChange = pGetTickCount();
        g_lastPublishTick = g_lastFrameChange;
        g_lsLastPubTick = g_lastFrameChange;
    }
    Unlock();
    if (!ok) return;
    u32 t1 = Qpc();
    s->ls_pub_us = QpcUs(t0, t1);
    s->ls_frame = rd32(g_base + O_FRAME_COUNTER_RVA);
    u32 seq = s->ls_seq + 1;
    s->ls_seq = seq;
    if (g_evState) pSetEvent(g_evState);
    if (s->battle_state != 8) return;              /* 演出/菜单:只发布不阻塞 */
    if (g_lsStalled) {
        if (s->ctl_step_seq == g_lsStalledSeq) return;   /* Python 还没回来 */
        g_lsStalled = 0;
    }
    u32 limitUs = s->ctl_lockstep_timeout_ms * 1000u;
    for (;;) {
        if ((i32)(s->ctl_step_seq - seq) >= 0) break;          /* 放行 */
        if (!s->ctl_lockstep) break;
        if (limitUs && QpcUs(t1, Qpc()) > limitUs) {
            s->ls_timeouts++;
            g_lsStalled = 1;
            g_lsStalledSeq = s->ctl_step_seq;
            break;
        }
        if (g_evStep && pWaitForSingleObject) pWaitForSingleObject(g_evStep, 1);
        else pSleep(0);
    }
    s->ls_wait_us = QpcUs(t1, Qpc());
    /* 放行后立刻取本帧动作(BridgeThread 每 2ms 才拷一次,等它就晚一帧) */
    g_injEnable = s->ctl_input_enable;
    g_injTeam = s->ctl_team;
    g_injX = s->ctl_x;
    g_injY = s->ctl_y;
    g_injK[0] = s->ctl_k[0];
    g_injK[1] = s->ctl_k[1];
    g_injK[2] = s->ctl_k[2];
    g_injK[3] = s->ctl_k[3];
    g_injK[4] = s->ctl_k[4];
    g_injK[5] = s->ctl_k[5];
}

// ---------------------------------------------------------------------------
// 后台线程
// ---------------------------------------------------------------------------
/* 独立的堆扫描线程。
   历史:扫描原本在 BridgeThread 的发布路径里,每 4 帧一次。实测单次
   800~1640ms(`dbg_pub_ms`),期间 `shm.frame` 完全不刷新 —— 训练侧的
   `_wait_frames` 只能干等,一个 action_repeat=4 的步会被拉长到 48~98 帧,
   action_repeat 的语义被彻底破坏。根因不是"扫描太慢",而是"扫描在发布路径上"。
   现在扫描独占一个线程,发布线程只读 seqlock 快照。 */
static u32 __stdcall ScanThread(void* arg) {
    (void)arg;
    for (;;) {
        pSleep(1);
        if (!g_shm || !g_hookOk) continue;      /* 对象图就绪前扫了也没用 */
        /* 每轮只做 SCAN_SLICE_MS 的活,然后让出 40ms:
           慢区一次页错误就可能吃掉几十 ms,必须留出间隙让游戏主线程跑。
           一个全堆周期(约 370MB 私有内存)因此被摊成几秒,可接受。 */
        ScanStep();
        pSleep(40);
    }
}

static u32 __stdcall BridgeThread(void* arg) {
    (void)arg;
    Resolved& rs = g_rs;
    rs.ok = 0;
    for (;;) {
        pSleep(2);
        g_dbg_loop++;
        g_dbg_gbase = g_base;
        if (!g_shm) continue;
        {
            u32 tLoop = pGetTickCount();
            g_shm->dbg_loop_count++;
            if (g_prevLoopStart) g_shm->dbg_loop_ms = tLoop - g_prevLoopStart;
            g_prevLoopStart = tLoop;
        }
        {
            if (!g_hookOk && g_base && g_is_game) {
                g_hookOk = InstallInputHook(g_base);
                if (g_hookOk) g_status |= 0x40;
            }
            /* 对象图解析要遍历整条指针链(每次几百次 RPM,约 0.5s),
               只在必要时做:解析失败 / 帧号长时间不动(指针失效) / 每 5s 兜底。 */
            Lock();
            {
                u32 now = pGetTickCount();
                int need = !rs.ok;
                if (!need && now - g_lastResolve > 5000) need = 1;
                if (!need && now - g_lastFrameChange > 1500) need = 1;
                if (need) {
                    u32 t0 = pGetTickCount();
                    field_cache_clear();
                    rs.ok = ResolveGraph(&rs);
                    g_lastResolve = now;
                    g_lastFrameChange = now;
                    g_shm->dbg_resolve_ms = pGetTickCount() - t0;
                g_shm->dbg_fn_mask     = g_dbg_fn;
                g_shm->dbg_heap_regions = g_dbgRegions;
                g_shm->dbg_heap_vt      = g_dbgVt;
                g_shm->dbg_heap_emit    = g_dbgEmit;
                /* 诊断:首个 MBI 原始字 + 扫描游标/周期数。保留供后续排查。 */
                for (u32 i = 0; i < 8; ++i) g_shm->dbg_mbi[i] = g_dbgMbiRaw[i];
                g_shm->dbg_mbi[6] = g_dbgScanCursor;
                g_shm->dbg_mbi[7] = g_scanPass;
                }
            }
            if (rs.ok) {
                u32 tPub0 = pGetTickCount();
                g_status |= 0x80;
                g_dev[0] = inst_native(rs.input[0]);   /* hook 里拿到的是原生 InputMulti* */
                g_dev[1] = inst_native(rs.input[1]);
                g_talkDev = rs.talk;
                g_injEnable = g_shm->ctl_input_enable;
                g_injTeam = g_shm->ctl_team;
                g_injX = g_shm->ctl_x;
                g_injY = g_shm->ctl_y;
                g_injK[0] = g_shm->ctl_k[0];
                g_injK[1] = g_shm->ctl_k[1];
                g_injK[2] = g_shm->ctl_k[2];
                g_injK[3] = g_shm->ctl_k[3];
                g_injK[4] = g_shm->ctl_k[4];
                g_injK[5] = g_shm->ctl_k[5];
                g_talkInject = g_shm->ctl_talk;
                if (g_shm->ctl_reset) {
                    g_shm->ctl_reset = 0;
                    g_resetFrames = 12;
                }
                if (g_shm->ctl_difficulty_dirty) {
                    u32 tag = 0, val = 0;
                    g_shm->ctl_difficulty_dirty = 0;
                    /* 原代码 table_slot(val, "practice") 里 val 从未赋值,
                       恒为 0 -> table_slot(0,...) 恒返回 0 -> 难度设置从未生效。
                       practice 配置在 root table 里。 */
                    u32 cfgSlot = 0;
                    if (g_root && table_get(g_root, "practice", &tag, &val) &&
                        rawtag(tag) == RAW_TABLE)
                        cfgSlot = val;
                    u32 cfg = 0;
                    if (cfgSlot && rawtag(rd32(cfgSlot)) == RAW_TABLE) cfg = rd32(cfgSlot + O_OBJ_VALUE);
                    if (cfg) {
                        i32 lv = g_shm->ctl_difficulty;
                        u32 ds = table_slot(cfg, "difficulty");
                        if (ds && lv >= 0 && lv <= 4) { wr32(ds, O_TAG_INT); wr32(ds + O_OBJ_VALUE, (u32)lv); }
                        u32 ps = table_slot(cfg, "player2");
                        if (ps && g_shm->ctl_player2 >= 0) { wr32(ps, O_TAG_INT); wr32(ps + O_OBJ_VALUE, (u32)g_shm->ctl_player2); }
                    }
                }
                u32 f = (u32)battle_int(rs.battle, "count");
                /* 锁步时主线程每帧自己发布;这里只在它停发(菜单/放行超时)时兜底 */
                int mainPub = g_shm->ctl_lockstep && pGetTickCount() - g_lsLastPubTick < 200;
                if (f != g_lastFrame && !mainPub) {
                    g_lastFrame = f;
                    g_lastFrameChange = pGetTickCount();
                    g_lastPublishTick = pGetTickCount();
                    PublishState(&rs);
                    g_shm->dbg_pub_ms = pGetTickCount() - tPub0;
                } else if (!mainPub && pGetTickCount() - g_lastPublishTick > 500) {
                    g_lastPublishTick = pGetTickCount();
                    PublishState(&rs);
                    g_shm->dbg_pub_ms = pGetTickCount() - tPub0;
                }
            } else {
                /* 对象图未解析(标题菜单/换场/刚启动):必须**主动标记离线**,
                   否则共享内存会永远停在上一场战斗的最后一份快照上。
                   实测后果:游戏回到菜单后 Python 仍读到
                   "frame=61822 state=8",误判成画面停住并反复重启。
                   这里把 attached 清 0、frame 清 0,读者据此判定"当前不在战斗中"。 */
                if (g_shm->attached || g_shm->frame) {
                    g_shm->seq++;
                    g_shm->attached = 0;
                    g_shm->frame = 0;
                    g_shm->battle_state = 0;
                    g_shm->shot_count = 0;
                    g_shm->seq++;
                }
            }
            Unlock();
        }
    }
    return 0;
}

// 自检探针:返回状态位图(可由宿主/注入器远程调用)
extern "C" __declspec(dllexport) u32 __stdcall Th155BridgeStatus(void) {
    return g_status;
}

extern "C" __declspec(dllexport) u32 __stdcall Th155BridgeMagic(void) {
    return TH155_SHM_MAGIC;
}

// ---------------------------------------------------------------------------
extern "C" BOOL __stdcall DllMain(HANDLE hModule, u32 reason, void* reserved) {
    (void)hModule; (void)reserved;
    if (reason == 1) {
        g_status |= 0x01;
        if (!InitImports()) return 1;
        /* 每个游戏实例一份映射/事件(名字带宿主 pid),多实例并行时互不覆盖 */
        u32 pid = CurrentPid();
        wchar_t name[96];
        BuildName(name, 96, TH155_SHM_PREFIX, pid);
        HANDLE h = pCreateFileMappingW((HANDLE)(long)-1, 0, PAGE_READWRITE, 0,
                                      (u32)sizeof(Th155Shared), name);
        if (pCreateEventW) {
            BuildName(name, 96, TH155_STATE_PREFIX, pid);
            g_evState = pCreateEventW(0, 0, 0, name);   /* 自动复位,初始无信号 */
            BuildName(name, 96, TH155_STEP_PREFIX, pid);
            g_evStep = pCreateEventW(0, 0, 0, name);
        }
        if (h) {
            g_status |= 0x08;
            g_shm = (Th155Shared*)pMapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(Th155Shared));
            if (g_shm) g_shm->struct_size = (u32)sizeof(Th155Shared);
            if (g_shm) {
                /* **必须整段清零**。共享内存是跨进程持久的:
                   上一个游戏实例退出后映射仍然存在,新实例挂到的是
                   **旧实例留下的快照**。以前只重置 magic/version/attached/
                   seq/ctl_* 这几项,frame/battle_state/time/life/actors 全部
                   原样继承 —— 实测后果:新游戏还停在标题菜单时,
                   Python 读到的是上一局的 "frame=61822 state=8" 冻结快照,
                   训练侧据此判定"画面停住"并反复重启游戏,形成死循环。
                   清零后未进战斗时读到的是全 0(合法的"离线"状态)。 */
                memzero(g_shm, (unsigned int)sizeof(Th155Shared));
                g_shm->magic = TH155_SHM_MAGIC;
                g_shm->version = TH155_SHM_VERSION;
                g_shm->struct_size = (u32)sizeof(Th155Shared);
                g_shm->attached = 0;
                g_shm->seq = 0;
                g_shm->ctl_reset = 0;
                g_shm->ctl_input_enable = 0;
                g_shm->ctl_player2 = -1;
                g_shm->ctl_difficulty = -1;
                g_shm->ls_pid = pid;
                g_status |= 0x10;
            }
        }
        u32 tid = 0;
        if (pCreateThread(0, 0, BridgeThread, 0, 0, &tid)) g_status |= 0x20;
        u32 tid2 = 0;
        if (pCreateThread(0, 0, ScanThread, 0, 0, &tid2)) g_status |= 0x100;
    }
    return 1;
}
