#pragma once
#include <windows.h>
#include <string>
#include <unordered_set>

void InitModLoader(HMODULE hSelf);
void ShutdownModLoader();
void Log(const char* fmt, ...);
