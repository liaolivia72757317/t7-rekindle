#pragma once
#include "../core/Common.h"
#include <tlhelp32.h>

namespace t7 {
struct ModuleLocation {
    uintptr_t base = 0;
    DWORD error = ERROR_SUCCESS;
};

inline ModuleLocation findImageBase(DWORD pid, const fs::path& image) {
    auto normalized = fs::absolute(image).lexically_normal().make_preferred();
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid);
    if (snapshot == INVALID_HANDLE_VALUE) return {0, GetLastError()};
    MODULEENTRY32W entry{}; entry.dwSize = sizeof(entry);
    if (!Module32FirstW(snapshot, &entry)) {
        auto error = GetLastError(); CloseHandle(snapshot); return {0, error};
    }
    ModuleLocation result{0, ERROR_MOD_NOT_FOUND};
    do {
        if (!_wcsicmp(entry.szExePath, normalized.c_str())) {
            result = {reinterpret_cast<uintptr_t>(entry.modBaseAddr), ERROR_SUCCESS}; break;
        }
    } while (Module32NextW(snapshot, &entry));
    CloseHandle(snapshot); return result;
}
}
