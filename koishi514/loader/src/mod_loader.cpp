#include "mod_loader.h"
#include <stdio.h>
#include <shlwapi.h>
#include <vector>
#include <string>
#include <unordered_set>
#include <unordered_map>
#include <algorithm>

#pragma comment(lib, "shlwapi.lib")

struct ModInfo {
    std::string id;
    std::string name;
    std::string author;
    std::string version;
    std::string description;
    std::string path;
    std::string dataPath;
    bool hasModIni = false;
    bool enabled = false;
    int priority = 100;
};

#pragma pack(push, 1)
struct RbNode {
    RbNode* left;    // +0x00
    RbNode* parent;  // +0x04
    RbNode* right;   // +0x08
    char color;      // +0x0C
    char isnil;      // +0x0D
    char pad[2];     // +0x0E
    uint32_t key;    // +0x10
};
#pragma pack(pop)

static uintptr_t g_base = 0;
static std::string g_gameDir;
static std::string g_logPath;
static CRITICAL_SECTION g_logLock;
static bool g_logLockInited = false;
static bool g_rbtree_flipped = false;

// Virtual File System mapping: "data/actor/koishi.nut" -> absolute disk path in mod folder
static std::unordered_map<std::string, std::string> g_vfs;
static std::unordered_set<uint32_t> g_target_hashes;

// Function pointers for hooked Win32 File APIs
typedef HANDLE(WINAPI* PFN_CreateFileA)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef HANDLE(WINAPI* PFN_CreateFileW)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef DWORD(WINAPI* PFN_GetFileAttributesA)(LPCSTR);
typedef DWORD(WINAPI* PFN_GetFileAttributesW)(LPCWSTR);

static PFN_CreateFileA g_realCreateFileA = NULL;
static PFN_CreateFileW g_realCreateFileW = NULL;
static PFN_GetFileAttributesA g_realGetFileAttributesA = NULL;
static PFN_GetFileAttributesW g_realGetFileAttributesW = NULL;

void Log(const char* fmt, ...) {
    if (!g_logLockInited) return;
    EnterCriticalSection(&g_logLock);

    SYSTEMTIME st;
    GetLocalTime(&st);

    char timeBuf[64];
    snprintf(timeBuf, sizeof(timeBuf), "[%04d-%02d-%02d %02d:%02d:%02d.%03d] ",
             st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);

    char msgBuf[2048];
    va_list args;
    va_start(args, fmt);
    vsnprintf(msgBuf, sizeof(msgBuf), fmt, args);
    va_end(args);

    OutputDebugStringA(timeBuf);
    OutputDebugStringA(msgBuf);

    printf("%s%s", timeBuf, msgBuf);

    if (!g_logPath.empty()) {
        FILE* fp = fopen(g_logPath.c_str(), "a");
        if (fp) {
            fputs(timeBuf, fp);
            fputs(msgBuf, fp);
            fclose(fp);
        }
    }

    LeaveCriticalSection(&g_logLock);
}

static std::string NormalizePath(const char* p) {
    if (!p) return "";
    std::string s(p);
    for (char& c : s) {
        if (c == '\\') c = '/';
        else c = (char)tolower((unsigned char)c);
    }
    return s;
}

static uint32_t CalcPakHash(const std::string& path) {
    uint32_t h = 0x811C9DC5;
    for (char c : path) {
        if (c == '/') c = '\\';
        c = (char)tolower((unsigned char)c);
        h = (h ^ (uint8_t)c) * 0x01000193;
    }
    return (uint32_t)(0 - (int32_t)h);
}

static void TraverseDirectory(const std::string& baseDir, const std::string& relPrefix, std::vector<std::string>& files) {
    std::string searchPattern = baseDir + "\\*";
    WIN32_FIND_DATAA fd;
    HANDLE hFind = FindFirstFileA(searchPattern.c_str(), &fd);
    if (hFind == INVALID_HANDLE_VALUE) return;

    do {
        if (strcmp(fd.cFileName, ".") == 0 || strcmp(fd.cFileName, "..") == 0)
            continue;

        std::string fullPath = baseDir + "\\" + fd.cFileName;
        std::string relPath = relPrefix.empty() ? fd.cFileName : (relPrefix + "/" + fd.cFileName);

        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            TraverseDirectory(fullPath, relPath, files);
        } else {
            files.push_back(relPath);
        }
    } while (FindNextFileA(hFind, &fd));

    FindClose(hFind);
}

static std::string ResolveVirtualPathA(LPCSTR lpFileName) {
    if (!lpFileName || !lpFileName[0]) return "";

    std::string s(lpFileName);
    for (char& c : s) {
        if (c == '\\') c = '/';
        else c = (char)tolower((unsigned char)c);
    }

    size_t pos = s.find("data/");
    if (pos != std::string::npos) {
        std::string rel = s.substr(pos);
        auto it = g_vfs.find(rel);
        if (it != g_vfs.end()) {
            return it->second;
        }
    }
    return "";
}

static std::wstring ResolveVirtualPathW(LPCWSTR lpFileName) {
    if (!lpFileName || !lpFileName[0]) return L"";
    char mb[MAX_PATH * 2] = { 0 };
    WideCharToMultiByte(CP_ACP, 0, lpFileName, -1, mb, sizeof(mb), NULL, NULL);
    std::string redA = ResolveVirtualPathA(mb);
    if (redA.empty()) return L"";
    wchar_t wb[MAX_PATH * 2] = { 0 };
    MultiByteToWideChar(CP_ACP, 0, redA.c_str(), -1, wb, MAX_PATH * 2);
    return std::wstring(wb);
}

static HANDLE WINAPI HookedCreateFileA(
    LPCSTR lpFileName,
    DWORD dwDesiredAccess,
    DWORD dwShareMode,
    LPSECURITY_ATTRIBUTES lpSecurityAttributes,
    DWORD dwCreationDisposition,
    DWORD dwFlagsAndAttributes,
    HANDLE hTemplateFile
) {
    std::string redirected = ResolveVirtualPathA(lpFileName);
    LPCSTR targetPath = redirected.empty() ? lpFileName : redirected.c_str();
    if (!redirected.empty()) {
        Log("[VFS] Redirect CreateFileA: %s -> %s\n", lpFileName, targetPath);
    }
    return g_realCreateFileA(targetPath, dwDesiredAccess, dwShareMode,
                             lpSecurityAttributes, dwCreationDisposition,
                             dwFlagsAndAttributes, hTemplateFile);
}

static HANDLE WINAPI HookedCreateFileW(
    LPCWSTR lpFileName,
    DWORD dwDesiredAccess,
    DWORD dwShareMode,
    LPSECURITY_ATTRIBUTES lpSecurityAttributes,
    DWORD dwCreationDisposition,
    DWORD dwFlagsAndAttributes,
    HANDLE hTemplateFile
) {
    std::wstring redirected = ResolveVirtualPathW(lpFileName);
    LPCWSTR targetPath = redirected.empty() ? lpFileName : redirected.c_str();
    return g_realCreateFileW(targetPath, dwDesiredAccess, dwShareMode,
                             lpSecurityAttributes, dwCreationDisposition,
                             dwFlagsAndAttributes, hTemplateFile);
}

static DWORD WINAPI HookedGetFileAttributesA(LPCSTR lpFileName) {
    std::string redirected = ResolveVirtualPathA(lpFileName);
    LPCSTR targetPath = redirected.empty() ? lpFileName : redirected.c_str();
    return g_realGetFileAttributesA(targetPath);
}

static DWORD WINAPI HookedGetFileAttributesW(LPCWSTR lpFileName) {
    std::wstring redirected = ResolveVirtualPathW(lpFileName);
    LPCWSTR targetPath = redirected.empty() ? lpFileName : redirected.c_str();
    return g_realGetFileAttributesW(targetPath);
}

template<typename T>
static bool HookIAT(uintptr_t iat_addr, T hook_func, T& orig_func) {
    if (IsBadReadPtr((void*)iat_addr, sizeof(void*))) return false;
    DWORD oldProtect = 0;
    if (!VirtualProtect((void*)iat_addr, sizeof(void*), PAGE_READWRITE, &oldProtect))
        return false;
    orig_func = *(T*)iat_addr;
    *(void**)iat_addr = (void*)hook_func;
    VirtualProtect((void*)iat_addr, sizeof(void*), oldProtect, &oldProtect);
    FlushInstructionCache(GetCurrentProcess(), (void*)iat_addr, sizeof(void*));
    return true;
}

static void TraverseAndFlipRbTree(RbNode* head, const std::unordered_set<uint32_t>& target_keys, int& count) {
    if (!head) return;
    RbNode* root = head->parent;
    if (!root || root == head) return;

    std::vector<RbNode*> stack;
    std::unordered_set<RbNode*> visited;
    stack.push_back(root);

    while (!stack.empty()) {
        RbNode* node = stack.back();
        stack.pop_back();

        if (!node || node == head || visited.count(node))
            continue;
        visited.insert(node);

        if (node->isnil)
            continue;

        if (target_keys.count(node->key)) {
            uint32_t old_key = node->key;
            node->key ^= 1;
            count++;
            Log("[RbTree] Flipped node 0x%p key: 0x%08X -> 0x%08X\n", (void*)node, old_key, node->key);
        }

        if (node->left && node->left != head && !visited.count(node->left))
            stack.push_back(node->left);
        if (node->right && node->right != head && !visited.count(node->right))
            stack.push_back(node->right);
    }
}

static bool TryFlipRbTree() {
    if (g_rbtree_flipped) return true;

    uintptr_t pkg_mgr_ptr_addr = g_base + 0x4DAD18;
    if (IsBadReadPtr((void*)pkg_mgr_ptr_addr, sizeof(uintptr_t))) return false;

    uintptr_t pkg_mgr = *(uintptr_t*)pkg_mgr_ptr_addr;
    if (!pkg_mgr || IsBadReadPtr((void*)pkg_mgr, 0x30)) return false;

    uint32_t node_count = *(uint32_t*)(pkg_mgr + 0x24);
    if (node_count < 18000) return false;

    RbNode* head = *(RbNode**)(pkg_mgr + 0x20);
    if (!head || IsBadReadPtr(head, sizeof(RbNode))) return false;

    int flipped = 0;
    TraverseAndFlipRbTree(head, g_target_hashes, flipped);

    g_rbtree_flipped = true;
    Log("[RbTree] Successfully flipped %d keys in PackageManager tree (total %u nodes)!\n",
        flipped, node_count);
    return true;
}

static DWORD WINAPI RbTreeWatchThread(LPVOID) {
    Log("[WatchThread] Started background watcher for PackageManager...\n");
    for (int i = 0; i < 30000; i++) {
        if (TryFlipRbTree()) {
            break;
        }
        Sleep(1);
    }
    Log("[WatchThread] Exited (flipped=%d).\n", g_rbtree_flipped ? 1 : 0);
    return 0;
}

void InitModLoader(HMODULE hSelf) {
    InitializeCriticalSection(&g_logLock);
    g_logLockInited = true;

    g_base = (uintptr_t)GetModuleHandleA(NULL);

    char exePath[MAX_PATH];
    GetModuleFileNameA(NULL, exePath, MAX_PATH);
    char* lastSlash = strrchr(exePath, '\\');
    if (lastSlash) {
        *lastSlash = '\0';
        g_gameDir = exePath;
    } else {
        g_gameDir = ".";
    }

    char iniPath[MAX_PATH];
    snprintf(iniPath, sizeof(iniPath), "%s\\mod_loader.ini", g_gameDir.c_str());

    // Optional real-time console window
    int consoleEnabled = GetPrivateProfileIntA("ModLoader", "Console", 0, iniPath);
    if (consoleEnabled) {
        AllocConsole();
        SetConsoleTitleA("TH155 Mod Loader Console (BepInEx-Style)");
        FILE* fpOut;
        freopen_s(&fpOut, "CONOUT$", "w", stdout);
        freopen_s(&fpOut, "CONOUT$", "w", stderr);
    }

    char logFileName[MAX_PATH] = "mod_loader.log";
    GetPrivateProfileStringA("ModLoader", "LogFile", "mod_loader.log", logFileName, MAX_PATH, iniPath);

    char logFullPath[MAX_PATH];
    snprintf(logFullPath, sizeof(logFullPath), "%s\\%s", g_gameDir.c_str(), logFileName);
    g_logPath = logFullPath;

    FILE* fp = fopen(g_logPath.c_str(), "w");
    if (fp) fclose(fp);

    Log("=================================================================\n");
    Log("  TH155 BepInEx-Style Native Mod Loader (winmm.dll proxy)\n");
    Log("  Target: Touhou 15.5 Antinomy of Common Flowers (v1.21b)\n");
    Log("=================================================================\n");
    Log("[Init] Game Base: 0x%08X, Game Dir: %s\n", g_base, g_gameDir.c_str());

    int masterEnabled = GetPrivateProfileIntA("ModLoader", "Enabled", 1, iniPath);
    if (!masterEnabled) {
        Log("[Init] ModLoader is DISABLED in mod_loader.ini (Enabled=0). Exiting.\n");
        return;
    }

    char modsDirRel[MAX_PATH] = "mods";
    GetPrivateProfileStringA("ModLoader", "ModsDir", "mods", modsDirRel, MAX_PATH, iniPath);
    std::string fullModsDir = g_gameDir + "\\" + modsDirRel;
    Log("[Init] Scanning mods directory: %s\n", fullModsDir.c_str());

    // 1. Discover all mod subdirectories in mods/
    std::vector<ModInfo> discoveredMods;
    std::string searchPattern = fullModsDir + "\\*";
    WIN32_FIND_DATAA fd;
    HANDLE hFind = FindFirstFileA(searchPattern.c_str(), &fd);
    if (hFind != INVALID_HANDLE_VALUE) {
        do {
            if (strcmp(fd.cFileName, ".") == 0 || strcmp(fd.cFileName, "..") == 0)
                continue;
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))
                continue;

            std::string modFolderName = fd.cFileName;
            std::string modPath = fullModsDir + "\\" + modFolderName;

            // Locate data directory
            std::string dataDir = modPath + "\\data";
            if (!PathFileExistsA(dataDir.c_str())) {
                std::string altData = modPath + "\\mod\\data";
                if (PathFileExistsA(altData.c_str())) {
                    dataDir = altData;
                } else {
                    continue; // Skip folders with no data/
                }
            }

            ModInfo mod;
            mod.id = modFolderName;
            mod.path = modPath;
            mod.dataPath = dataDir;

            bool folderDisabled = false;
            if (modFolderName.size() > 9 && modFolderName.rfind(".disabled") == modFolderName.size() - 9) {
                folderDisabled = true;
                mod.id = modFolderName.substr(0, modFolderName.size() - 9);
            }

            std::string modIni = modPath + "\\mod.ini";
            mod.hasModIni = PathFileExistsA(modIni.c_str());

            char nameBuf[256] = {0}, authBuf[256] = {0}, verBuf[64] = {0}, descBuf[1024] = {0};
            if (mod.hasModIni) {
                GetPrivateProfileStringA("Mod", "Name", mod.id.c_str(), nameBuf, sizeof(nameBuf), modIni.c_str());
                GetPrivateProfileStringA("Mod", "Author", "Unknown", authBuf, sizeof(authBuf), modIni.c_str());
                GetPrivateProfileStringA("Mod", "Version", "1.0.0", verBuf, sizeof(verBuf), modIni.c_str());
                GetPrivateProfileStringA("Mod", "Description", "", descBuf, sizeof(descBuf), modIni.c_str());
                int iniEnabled = GetPrivateProfileIntA("Mod", "Enabled", 1, modIni.c_str());
                int iniPriority = GetPrivateProfileIntA("Mod", "Priority", 100, modIni.c_str());

                mod.name = nameBuf;
                mod.author = authBuf;
                mod.version = verBuf;
                mod.description = descBuf;
                mod.priority = iniPriority;
                mod.enabled = (iniEnabled != 0) && !folderDisabled;
            } else {
                mod.name = mod.id;
                mod.author = "Unknown";
                mod.version = "1.0.0";
                mod.description = "Legacy / Unregistered mod folder";
                mod.priority = 50;
                mod.enabled = false; // Folders without mod.ini default to disabled unless explicitly enabled in mod_loader.ini
            }

            // Check override in mod_loader.ini [Mods]
            int overrideVal = GetPrivateProfileIntA("Mods", mod.id.c_str(), -1, iniPath);
            if (overrideVal != -1) {
                mod.enabled = (overrideVal != 0);
            }

            discoveredMods.push_back(mod);
        } while (FindNextFileA(hFind, &fd));
        FindClose(hFind);
    }

    Log("[ModLoader] Discovered %zu mods:\n", discoveredMods.size());
    std::vector<ModInfo> enabledMods;
    for (const auto& m : discoveredMods) {
        Log("   [%s] %-18s (Priority=%3d) - %s (v%s by %s)\n",
            m.enabled ? "ENABLED " : "DISABLED",
            m.id.c_str(),
            m.priority,
            m.name.c_str(),
            m.version.c_str(),
            m.author.c_str());
        if (m.enabled) {
            enabledMods.push_back(m);
        }
    }

    // 2. Sort enabled mods by priority DESCENDING (highest priority first)
    std::sort(enabledMods.begin(), enabledMods.end(), [](const ModInfo& a, const ModInfo& b) {
        if (a.priority != b.priority)
            return a.priority > b.priority;
        return a.id < b.id;
    });

    // 3. Build Virtual File System (VFS) map
    for (const auto& mod : enabledMods) {
        std::vector<std::string> files;
        TraverseDirectory(mod.dataPath, "", files);
        int added = 0;
        int overridden = 0;
        for (const auto& f : files) {
            std::string rel = "data/" + NormalizePath(f.c_str());
            std::string fullPath = mod.dataPath + "\\" + f;
            for (char& c : fullPath) {
                if (c == '/') c = '\\';
            }

            if (g_vfs.find(rel) == g_vfs.end()) {
                g_vfs[rel] = fullPath;
                g_target_hashes.insert(CalcPakHash(rel));
                added++;
            } else {
                overridden++;
            }
        }
        Log("[VFS] Loaded mod '%s': %d files added%s\n",
            mod.id.c_str(), added,
            overridden > 0 ? (", " + std::to_string(overridden) + " files overridden by higher priority mod").c_str() : "");
    }

    Log("[VFS] Total active redirected files: %zu (pak target hashes: %zu)\n",
        g_vfs.size(), g_target_hashes.size());

    // 4. Install IAT Hooks in th155.exe
    HookIAT(g_base + 0x3881E0, HookedCreateFileA, g_realCreateFileA);
    HookIAT(g_base + 0x3881E4, HookedGetFileAttributesA, g_realGetFileAttributesA);
    HookIAT(g_base + 0x3880C0, HookedCreateFileW, g_realCreateFileW);
    HookIAT(g_base + 0x3882E0, HookedGetFileAttributesW, g_realGetFileAttributesW);
    Log("[IAT] Hooked CreateFileA, GetFileAttributesA, CreateFileW, GetFileAttributesW successfully.\n");

    DWORD oldProtect = 0;

    // 5. Bypass Tasofro DLL signature / whitelist check (anti-cheat)
    // VerifyModuleSignature (RVA 0x12F7F0) -> B0 01 C3 (mov al, 1; ret)
    uintptr_t verify_addr = g_base + 0x12F7F0;
    uint8_t mov1_patch[3] = { 0xB0, 0x01, 0xC3 };
    if (VirtualProtect((void*)verify_addr, sizeof(mov1_patch), PAGE_EXECUTE_READWRITE, &oldProtect)) {
        memcpy((void*)verify_addr, mov1_patch, sizeof(mov1_patch));
        VirtualProtect((void*)verify_addr, sizeof(mov1_patch), oldProtect, &oldProtect);
        FlushInstructionCache(GetCurrentProcess(), (void*)verify_addr, sizeof(mov1_patch));
        Log("[Patch] VerifyModuleSignature patched at 0x%08X to 'mov al, 1; ret'\n", verify_addr);
    }

    // 6. Patch GateCheck (RVA 0x026120) -> B0 01 C2 08 00 (mov al, 1; ret 8)
    uintptr_t gate_addr = g_base + 0x026120;
    uint8_t gate_patch[5] = { 0xB0, 0x01, 0xC2, 0x08, 0x00 };
    if (VirtualProtect((void*)gate_addr, sizeof(gate_patch), PAGE_EXECUTE_READWRITE, &oldProtect)) {
        memcpy((void*)gate_addr, gate_patch, sizeof(gate_patch));
        VirtualProtect((void*)gate_addr, sizeof(gate_patch), oldProtect, &oldProtect);
        FlushInstructionCache(GetCurrentProcess(), (void*)gate_addr, sizeof(gate_patch));
        Log("[Patch] GateCheck patched at 0x%08X to 'mov al, 1; ret 8'\n", gate_addr);
    }

    // 7. Fix Matrix::Vec3Transform bug (RVA 0x0422A6)
    // In th155.exe sub_4422A0:
    // Original: 8D 45 F4 (lea eax, [ebp-0Ch]) - passes local stack var_C to sub_448A10,
    // which was destroyed on return without copying back to pos argument [ebp+8].
    // Fixed:    8B 45 08 (mov eax, [ebp+8])  - directly writes transformed Vector3 to pos argument!
    uintptr_t vec3_addr = g_base + 0x0422A6;
    uint8_t vec3_patch[3] = { 0x8B, 0x45, 0x08 };
    if (VirtualProtect((void*)vec3_addr, sizeof(vec3_patch), PAGE_EXECUTE_READWRITE, &oldProtect)) {
        memcpy((void*)vec3_addr, vec3_patch, sizeof(vec3_patch));
        VirtualProtect((void*)vec3_addr, sizeof(vec3_patch), oldProtect, &oldProtect);
        FlushInstructionCache(GetCurrentProcess(), (void*)vec3_addr, sizeof(vec3_patch));
        Log("[Patch] Matrix::Vec3Transform fixed at 0x%08X to 'mov eax, [ebp+8]'\n", vec3_addr);
    }

    // 8. Background watcher thread for PackageManager RbTree flip
    HANDLE hThread = CreateThread(NULL, 0, RbTreeWatchThread, NULL, 0, NULL);
    if (hThread) {
        CloseHandle(hThread);
    }

    Log("[Init] ModLoader initialized successfully. VFS active with %zu files.\n", g_vfs.size());
}

void ShutdownModLoader() {
    Log("[Shutdown] ModLoader shutting down.\n");
    if (g_logLockInited) {
        DeleteCriticalSection(&g_logLock);
        g_logLockInited = false;
    }
}
