import ctypes
from ctypes import wintypes
import os

DEBUG_PROCESS = 0x00000001
INFINITE = 0xFFFFFFFF

class EXCEPTION_RECORD(ctypes.Structure):
    pass
EXCEPTION_RECORD._fields_ = [
    ('ExceptionCode', wintypes.DWORD),
    ('ExceptionFlags', wintypes.DWORD),
    ('ExceptionRecord', ctypes.POINTER(EXCEPTION_RECORD)),
    ('ExceptionAddress', ctypes.c_void_p),
    ('NumberParameters', wintypes.DWORD),
    ('ExceptionInformation', ctypes.c_ulong * 15),
]

class EXCEPTION_DEBUG_INFO(ctypes.Structure):
    _fields_ = [
        ('ExceptionRecord', EXCEPTION_RECORD),
        ('dwFirstChance', wintypes.DWORD),
    ]

class DEBUG_EVENT_UNION(ctypes.Union):
    _fields_ = [
        ('Exception', EXCEPTION_DEBUG_INFO),
        ('pad', ctypes.c_byte * 160),
    ]

class DEBUG_EVENT(ctypes.Structure):
    _fields_ = [
        ('dwDebugEventCode', wintypes.DWORD),
        ('dwProcessId', wintypes.DWORD),
        ('dwThreadId', wintypes.DWORD),
        ('u', DEBUG_EVENT_UNION),
    ]

class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('hProcess', wintypes.HANDLE),
        ('hThread', wintypes.HANDLE),
        ('dwProcessId', wintypes.DWORD),
        ('dwThreadId', wintypes.DWORD),
    ]

class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD),
        ('lpReserved', wintypes.LPWSTR),
        ('lpDesktop', wintypes.LPWSTR),
        ('lpTitle', wintypes.LPWSTR),
        ('dwX', wintypes.DWORD),
        ('dwY', wintypes.DWORD),
        ('dwXSize', wintypes.DWORD),
        ('dwYSize', wintypes.DWORD),
        ('dwXCountChars', wintypes.DWORD),
        ('dwYCountChars', wintypes.DWORD),
        ('dwFillAttribute', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('wShowWindow', wintypes.WORD),
        ('cbReserved2', wintypes.WORD),
        ('lpReserved2', ctypes.c_void_p),
        ('hStdInput', wintypes.HANDLE),
        ('hStdOutput', wintypes.HANDLE),
        ('hStdError', wintypes.HANDLE),
    ]

def debug_game():
    game_dir = r'D:\[th15.5] 东方凭依华（英文版+日文版）'
    exe_path = os.path.join(game_dir, 'th155.exe')

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()

    print(f'Starting {exe_path} under debugger...')
    success = ctypes.windll.kernel32.CreateProcessW(
        exe_path, None, None, None, False,
        DEBUG_PROCESS, None, game_dir,
        ctypes.byref(si), ctypes.byref(pi)
    )
    if not success:
        print('CreateProcessW failed:', ctypes.GetLastError())
        return

    dbg = DEBUG_EVENT()
    DBG_CONTINUE = 0x00010002
    DBG_EXCEPTION_NOT_HANDLED = 0x80010001
    
    first_chance_count = 0
    while True:
        if not ctypes.windll.kernel32.WaitForDebugEvent(ctypes.byref(dbg), 3000):
            print('WaitForDebugEvent timed out (game running stably?)')
            break

        code = dbg.dwDebugEventCode
        continue_status = DBG_CONTINUE

        if code == 1: # EXCEPTION_DEBUG_EVENT
            ex = dbg.u.Exception.ExceptionRecord
            first_chance = dbg.u.Exception.dwFirstChance
            exc_code = ex.ExceptionCode
            exc_addr = ex.ExceptionAddress
            if exc_code not in (0x80000003, 0x4000001F, 0xE06D7363): # Ignore breakpoints and C++ throw
                print(f'>>> EXCEPTION: 0x{exc_code:08X} at 0x{exc_addr:08X} (FirstChance={first_chance})')
                mod_name = (ctypes.c_wchar * 512)()
                ctypes.windll.psapi.GetMappedFileNameW(pi.hProcess, exc_addr, mod_name, 512)
                print('    Module path:', mod_name.value)
                
                # Check thread registers
                hTh = ctypes.windll.kernel32.OpenThread(0x001F03FF, False, dbg.dwThreadId)
                if hTh:
                    class WOW64_CONTEXT(ctypes.Structure):
                        _fields_ = [
                            ('ContextFlags', wintypes.DWORD),
                            ('Dr0', wintypes.DWORD), ('Dr1', wintypes.DWORD), ('Dr2', wintypes.DWORD),
                            ('Dr3', wintypes.DWORD), ('Dr6', wintypes.DWORD), ('Dr7', wintypes.DWORD),
                            ('FloatSave', ctypes.c_byte * 112),
                            ('SegGs', wintypes.DWORD), ('SegFs', wintypes.DWORD),
                            ('SegEs', wintypes.DWORD), ('SegDs', wintypes.DWORD),
                            ('Edi', wintypes.DWORD), ('Esi', wintypes.DWORD),
                            ('Ebx', wintypes.DWORD), ('Edx', wintypes.DWORD),
                            ('Ecx', wintypes.DWORD), ('Eax', wintypes.DWORD),
                            ('Ebp', wintypes.DWORD), ('Eip', wintypes.DWORD),
                            ('SegCs', wintypes.DWORD), ('EFlags', wintypes.DWORD),
                            ('Esp', wintypes.DWORD), ('SegSs', wintypes.DWORD),
                            ('ExtendedRegisters', ctypes.c_byte * 512),
                        ]
                    ctx = WOW64_CONTEXT()
                    ctx.ContextFlags = 0x10007 # WOW64_CONTEXT_FULL
                    ctypes.windll.kernel32.Wow64GetThreadContext(hTh, ctypes.byref(ctx))
                    print(f'    EAX=0x{ctx.Eax:08X} ECX=0x{ctx.Ecx:08X} EDX=0x{ctx.Edx:08X} EBX=0x{ctx.Ebx:08X}')
                    print(f'    ESP=0x{ctx.Esp:08X} EBP=0x{ctx.Ebp:08X} ESI=0x{ctx.Esi:08X} EDI=0x{ctx.Edi:08X} EIP=0x{ctx.Eip:08X}')
                    
                    sbuf = (ctypes.c_uint32 * 8)()
                    read = ctypes.c_size_t()
                    ctypes.windll.kernel32.ReadProcessMemory(pi.hProcess, ctx.Esp, sbuf, 32, ctypes.byref(read))
                    print('    Stack [ESP..]:', [f'0x{v:08X}' for v in sbuf])

                    ebp_buf = (ctypes.c_uint32 * 8)()
                    ctypes.windll.kernel32.ReadProcessMemory(pi.hProcess, ctx.Ebp, ebp_buf, 32, ctypes.byref(read))
                    print('    Stack [EBP..]:')
                    for idx, val in enumerate(ebp_buf):
                        smod = (ctypes.c_wchar * 256)()
                        ctypes.windll.psapi.GetMappedFileNameW(pi.hProcess, val, mod_name, 256)
                        smod_name = os.path.basename(mod_name.value) if mod_name.value else ''
                        print(f'      [EBP+{idx*4:02X}] 0x{val:08X} {smod_name}')
                    ctypes.windll.kernel32.CloseHandle(hTh)

                if not first_chance:
                    print(f'FATAL UNHANDLED EXCEPTION: 0x{exc_code:08X} at 0x{exc_addr:08X}')
                    break
                else:
                    first_chance_count += 1
                    continue_status = DBG_EXCEPTION_NOT_HANDLED
            else:
                continue_status = DBG_CONTINUE
        elif code == 5: # EXIT_PROCESS_DEBUG_EVENT
            print('Process exited!')
            break

        ctypes.windll.kernel32.ContinueDebugEvent(dbg.dwProcessId, dbg.dwThreadId, continue_status)

    ctypes.windll.kernel32.DebugActiveProcessStop(pi.dwProcessId)
    ctypes.windll.kernel32.TerminateProcess(pi.hProcess, 0)
    ctypes.windll.kernel32.CloseHandle(pi.hProcess)
    ctypes.windll.kernel32.CloseHandle(pi.hThread)

if __name__ == '__main__':
    debug_game()
