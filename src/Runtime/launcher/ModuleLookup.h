#pragma once
#include "../core/Common.h"
#include <psapi.h>

namespace t7 {
struct ModuleLocation {
    uintptr_t base = 0;
    DWORD error = ERROR_SUCCESS;
};

inline ModuleLocation findMappedImageBase(HANDLE process, const fs::path& image) {
    auto normalized = fs::absolute(image).lexically_normal().make_preferred();
    std::wstring processImage(32768, L'\0');
    DWORD length = static_cast<DWORD>(processImage.size());
    if (!QueryFullProcessImageNameW(process, 0, processImage.data(), &length)) return {0, GetLastError()};
    if (_wcsicmp(processImage.c_str(), normalized.c_str())) return {0, ERROR_MOD_NOT_FOUND};
    length = static_cast<DWORD>(processImage.size());
    if (!QueryFullProcessImageNameW(process, PROCESS_NAME_NATIVE, processImage.data(), &length))
        return {0, GetLastError()};

    // Image mappings exist before the suspended primary thread initializes
    // the loader lists used by Toolhelp module snapshots.
    std::wstring mappedImage(32768, L'\0');
    uintptr_t address = 0;
    for (;;) {
        MEMORY_BASIC_INFORMATION region{};
        if (!VirtualQueryEx(process, reinterpret_cast<void*>(address), &region, sizeof(region))) {
            const auto error = GetLastError();
            return {0, error == ERROR_INVALID_PARAMETER ? ERROR_MOD_NOT_FOUND : error};
        }
        if (region.Type == MEM_IMAGE && region.BaseAddress == region.AllocationBase) {
            const auto mappedLength = GetMappedFileNameW(process, region.AllocationBase, mappedImage.data(),
                                                        static_cast<DWORD>(mappedImage.size()));
            if (!mappedLength) return {0, GetLastError()};
            if (mappedLength >= mappedImage.size()) return {0, ERROR_INSUFFICIENT_BUFFER};
            if (!_wcsicmp(mappedImage.c_str(), processImage.c_str()))
                return {reinterpret_cast<uintptr_t>(region.AllocationBase), ERROR_SUCCESS};
        }
        const auto next = reinterpret_cast<uintptr_t>(region.BaseAddress) + region.RegionSize;
        if (next <= address) return {0, ERROR_MOD_NOT_FOUND};
        address = next;
    }
}

inline ModuleLocation findImageBase(DWORD pid, const fs::path& image) {
    HANDLE process = OpenProcess(PROCESS_QUERY_INFORMATION, FALSE, pid ? pid : GetCurrentProcessId());
    if (!process) return {0, GetLastError()};
    try {
        const auto result = findMappedImageBase(process, image);
        CloseHandle(process);
        return result;
    } catch (...) {
        CloseHandle(process);
        throw;
    }
}
}
