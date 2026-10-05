#include "../../src/Runtime/launcher/ModuleLookup.h"
#include <iostream>

namespace {
bool verifyExecutable(HANDLE process, uintptr_t base, WORD machine) {
    IMAGE_DOS_HEADER dos{};
    SIZE_T actual = 0;
    if (!ReadProcessMemory(process, reinterpret_cast<void*>(base), &dos, sizeof(dos), &actual)
        || actual != sizeof(dos) || dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew <= 0) return false;
    DWORD signature = 0;
    const auto nt = base + static_cast<uintptr_t>(dos.e_lfanew);
    if (!ReadProcessMemory(process, reinterpret_cast<void*>(nt), &signature, sizeof(signature), &actual)
        || actual != sizeof(signature) || signature != IMAGE_NT_SIGNATURE) return false;
    IMAGE_FILE_HEADER header{};
    if (!ReadProcessMemory(process, reinterpret_cast<void*>(nt + sizeof(signature)), &header, sizeof(header), &actual)
        || actual != sizeof(header)) return false;
    return header.Machine == machine && (header.Characteristics & IMAGE_FILE_EXECUTABLE_IMAGE)
        && !(header.Characteristics & IMAGE_FILE_DLL);
}

bool verifySuspendedImage(const t7::fs::path& image, WORD machine) {
    std::wstring command = L"\"" + image.wstring() + L"\" /d /c exit 0";
    STARTUPINFOW startup{}; startup.cb = sizeof(startup);
    PROCESS_INFORMATION info{};
    if (!CreateProcessW(image.c_str(), command.data(), nullptr, nullptr, FALSE,
                        CREATE_SUSPENDED | CREATE_NO_WINDOW, nullptr, nullptr, &startup, &info)) {
        std::cerr << "suspended fixture creation failed; win32Error=" << GetLastError() << '\n';
        return false;
    }
    bool valid = false;
    try {
        const auto location = t7::findImageBase(info.dwProcessId, image);
        valid = location.base && location.error == ERROR_SUCCESS
            && verifyExecutable(info.hProcess, location.base, machine);
        if (!valid)
            std::cerr << "suspended image lookup failed; machine=" << machine
                      << "; win32Error=" << location.error << '\n';
        // Lookup must not run the client to initialize its loader list.
        if (ResumeThread(info.hThread) != 1 || WaitForSingleObject(info.hProcess, 5000) != WAIT_OBJECT_0)
            valid = false;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
    }
    if (WaitForSingleObject(info.hProcess, 0) != WAIT_OBJECT_0) {
        if (!TerminateProcess(info.hProcess, 1)) valid = false;
        if (WaitForSingleObject(info.hProcess, 5000) != WAIT_OBJECT_0) valid = false;
    }
    if (!CloseHandle(info.hThread)) valid = false;
    if (!CloseHandle(info.hProcess)) valid = false;
    return valid;
}
}

bool verifyModuleLookup() {
    wchar_t currentImage[32768]{};
    const auto currentLength = GetModuleFileNameW(nullptr, currentImage, static_cast<DWORD>(std::size(currentImage)));
    if (!currentLength || currentLength >= std::size(currentImage)) return false;
    const auto current = t7::findImageBase(GetCurrentProcessId(), currentImage);
    if (current.base != reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr)) || current.error != ERROR_SUCCESS) {
        std::cerr << "running image lookup failed\n";
        return false;
    }
    const auto mismatch = t7::findImageBase(GetCurrentProcessId(), t7::fs::path(currentImage).parent_path() / L"not-client.exe");
    if (mismatch.base || mismatch.error != ERROR_MOD_NOT_FOUND) return false;
    const auto invalid = t7::findImageBase(MAXDWORD, currentImage);
    if (invalid.base || invalid.error == ERROR_SUCCESS) return false;

    wchar_t nativeDirectory[MAX_PATH]{}, wow64Directory[MAX_PATH]{};
    const auto nativeLength = GetSystemDirectoryW(nativeDirectory, static_cast<UINT>(std::size(nativeDirectory)));
    const auto wow64Length = GetSystemWow64DirectoryW(wow64Directory, static_cast<UINT>(std::size(wow64Directory)));
    if (!nativeLength || nativeLength >= std::size(nativeDirectory)
        || !wow64Length || wow64Length >= std::size(wow64Directory)) return false;
    const bool native = verifySuspendedImage(t7::fs::path(nativeDirectory) / L"cmd.exe", IMAGE_FILE_MACHINE_AMD64);
    const bool wow64 = verifySuspendedImage(t7::fs::path(wow64Directory) / L"cmd.exe", IMAGE_FILE_MACHINE_I386);
    return native && wow64;
}
