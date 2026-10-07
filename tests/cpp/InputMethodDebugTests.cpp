#include "../../src/Runtime/launcher/DebugClient.h"
#include <algorithm>
#include <cstring>
#include <cwctype>
#include <fstream>
#include <iostream>
#include <mutex>

namespace {
void require(bool result, const char* message) { if (!result) throw std::runtime_error(message); }
t7::fs::path executable() {
    wchar_t path[32768];
    const auto length = GetModuleFileNameW(nullptr, path, static_cast<DWORD>(std::size(path)));
    require(length && length < std::size(path), "IME fixture executable path");
    return std::wstring(path, length);
}
void copyFixture(const t7::fs::path& source, const t7::fs::path& destination) {
    auto bytes = t7::readFile(source);
    IMAGE_DOS_HEADER dos{}; std::memcpy(&dos, bytes.data(), sizeof(dos));
    const auto offset = static_cast<size_t>(dos.e_lfanew) + offsetof(IMAGE_NT_HEADERS64, OptionalHeader)
        + offsetof(IMAGE_OPTIONAL_HEADER64, Subsystem);
    require(offset + sizeof(WORD) <= bytes.size(), "IME fixture subsystem bounds");
    const WORD subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI;
    std::memcpy(bytes.data() + offset, &subsystem, sizeof(subsystem));
    std::ofstream file(destination, std::ios::binary);
    file.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    require(file.good(), "IME fixture image write");
}
HANDLE launch(const t7::fs::path& path) {
    auto command = L"\"" + path.wstring() + L"\"";
    STARTUPINFOW startup{}; startup.cb = sizeof(startup); PROCESS_INFORMATION process{};
    require(CreateProcessW(path.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
        nullptr, path.parent_path().c_str(), &startup, &process), "IME fixture child creation");
    require(CloseHandle(process.hThread), "IME fixture primary thread close");
    return process.hProcess;
}
void awaitExit(HANDLE process) {
    const auto state = WaitForSingleObject(process, 15000);
    if (state != WAIT_OBJECT_0) TerminateProcess(process, 1);
    require(CloseHandle(process), "IME fixture process close");
    require(state == WAIT_OBJECT_0, "IME fixture child timeout");
}
void loadModule(const t7::fs::path& directory, const wchar_t* name, bool unload = false) {
    const auto module = LoadLibraryExW((directory / name).c_str(), nullptr,
                                      LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    require(module != nullptr, "IME fixture DLL load");
    if (unload) require(FreeLibrary(module), "IME fixture DLL unload");
}
void marker(const t7::fs::path& path) {
    std::ofstream output(path); output.put('1');
    require(output.good(), "IME fixture marker write");
}
}

int runInputMethodDebugFixture() {
    const auto path = executable(); const auto directory = path.parent_path(); const auto name = path.filename().wstring();
    if (name != L"T7.ImeParent.exe" && name != L"T7.ImeHelper.exe" && name != L"T7.ImeLeaf.exe"
        && name != L"T7.ImeUnowned.exe") return -1;
    std::string mode; std::ifstream(directory / "mode.txt") >> mode;
    if (name == L"T7.ImeUnowned.exe") { Sleep(20000); return 0; }
    if (name == L"T7.ImeLeaf.exe") {
        marker(directory / "leaf-started.txt");
        if (mode == "stop" || mode == "parent-exit") Sleep(20000);
        return 43;
    }
    if (name == L"T7.ImeHelper.exe") {
        marker(directory / "helper-started.txt");
        if (mode == "fault" || mode == "verified-fault") {
            RaiseException(0xE0007108, EXCEPTION_NONCONTINUABLE, 0, nullptr); return 1;
        }
        if (mode == "nonzero") return 7;
        if (mode == "forbidden") loadModule(directory, L"tenslx.dll");
        if (mode == "skip-adaptation" || mode == "verified-adaptation") loadModule(directory, L"T7.HelperLibrary.dll");
        if (mode == "verified-load") loadModule(directory, L"T7.RegisteredIme.dll");
        awaitExit(launch(directory / "T7.ImeLeaf.exe"));
        return 42;
    }
    if (mode != "not-loaded" && mode != "before-load" && mode != "verified-load")
        loadModule(directory, L"T7.RegisteredIme.dll", mode == "unload");
    const auto helper = launch(directory / "T7.ImeHelper.exe");
    if (mode == "parent-exit") {
        const auto deadline = GetTickCount64() + 10000;
        while (!t7::fs::exists(directory / "leaf-started.txt") && GetTickCount64() < deadline) Sleep(10);
        require(t7::fs::exists(directory / "leaf-started.txt"), "IME descendant readiness");
        require(CloseHandle(helper), "IME parent detached wait handle close");
    } else awaitExit(helper);
    if (mode == "before-load") loadModule(directory, L"T7.RegisteredIme.dll");
    marker(directory / "parent-survived.txt");
    return 41;
}

bool verifyInputMethodDebugClient() {
    const auto root = t7::fs::temp_directory_path() / (L"t7-ime-debug-" + std::to_wstring(GetCurrentProcessId())
                                                    + L"-" + std::to_wstring(GetTickCount64()));
    const auto cleanup = [&] {
        require(t7::fs::canonical(root).parent_path() == t7::fs::canonical(t7::fs::temp_directory_path()),
                "IME fixture cleanup escaped temporary directory");
        t7::fs::remove_all(root);
    };
    try {
        require(t7::fs::create_directory(root), "fresh IME fixture directory");
        wchar_t system[32768];
        const auto length = GetSystemDirectoryW(system, static_cast<UINT>(std::size(system)));
        require(length && length < std::size(system), "IME fixture system directory");
        const auto original = executable();
        for (const std::string mode : {"allow", "unload", "not-loaded", "before-load", "unregistered", "module-path", "case-path",
                                      "registry-error", "fault", "nonzero", "known-hash", "known-path", "forbidden", "skip-adaptation",
                                      "verified-load", "verified-fault", "verified-adaptation", "stop", "parent-exit"}) {
            const auto directory = root / mode;
            require(t7::fs::create_directory(directory), "fresh IME case directory");
            for (const auto* name : {"T7.ImeParent.exe", "T7.ImeHelper.exe", "T7.ImeLeaf.exe", "T7.ImeUnowned.exe"})
                copyFixture(original, directory / name);
            for (const auto* name : {"T7.RegisteredIme.dll", "T7.HelperLibrary.dll", "tenslx.dll"})
                t7::fs::copy_file(t7::fs::path(system) / "version.dll", directory / name);
            std::ofstream(directory / "mode.txt") << mode;
            const auto hash = t7::fileHash(directory / "T7.ImeParent.exe");
            std::vector<t7::ChildImageRule> children;
            if (mode == "known-hash" || mode == "known-path" || mode.rfind("verified-", 0) == 0)
                children.push_back({mode == "known-path" ? root / "T7.ImeHelper.exe" : directory / "T7.ImeHelper.exe",
                                    mode == "known-hash" ? std::string(64, '0') : hash});
            std::vector<t7::DllEntryRule> rules;
            if (mode == "skip-adaptation" || mode == "verified-adaptation")
                rules.push_back({L"T7.HelperLibrary.dll", std::string(64, '0'), 0x1000, {0x90},
                                 [](uint32_t) { return std::vector<t7::MemoryPatch>{}; }});
            t7::DebugClient client([directory, mode]() -> std::vector<t7::InputMethodRegistration> {
                if (mode == "registry-error") throw std::runtime_error("synthetic registry unavailable");
                auto module = ((mode == "module-path" ? directory.parent_path() : directory)
                    / (mode == "unregistered" ? "OtherIme.dll" : "T7.RegisteredIme.dll")).wstring();
                if (mode == "case-path") std::transform(module.begin(), module.end(), module.begin(), [](wchar_t value) {
                    return static_cast<wchar_t>(std::towupper(value));
                });
                return {{module, "TSF/fixture/x64"}};
            });
            std::vector<HANDLE> observed;
            std::vector<std::string> logs, warnings;
            std::mutex mutex;
            const auto log = [&](const std::string& line) {
                std::lock_guard<std::mutex> lock(mutex); logs.push_back(line);
                if (line.find("Compatible client child process; PID=") == 0
                    || line.find("Verified client child process; PID=") == 0) {
                    const auto pid = static_cast<DWORD>(std::stoul(line.substr(line.find("PID=") + 4)));
                    const auto handle = OpenProcess(SYNCHRONIZE, FALSE, pid);
                    require(handle != nullptr, "IME child observation handle"); observed.push_back(handle);
                }
            };
            std::string error;
            HANDLE unowned = mode == "stop" ? launch(directory / "T7.ImeUnowned.exe") : nullptr;
            try {
                client.start(directory / "T7.ImeParent.exe", hash, rules, [](HANDLE, HANDLE) {}, log, {}, children, {},
                    [&](const std::string& line) { std::lock_guard<std::mutex> lock(mutex); warnings.push_back(line); });
                if (mode == "stop") {
                    const auto deadline = GetTickCount64() + 10000;
                    while (!t7::fs::exists(directory / "leaf-started.txt") && GetTickCount64() < deadline) {
                        client.check(); Sleep(10);
                    }
                    require(t7::fs::exists(directory / "leaf-started.txt"), "compatible descendant readiness");
                    require(WaitForSingleObject(client.process(), 0) == WAIT_TIMEOUT, "parent stopped before request");
                } else require(WaitForSingleObject(client.process(), 15000) == WAIT_OBJECT_0, "IME case completion");
                client.stop(); client.check();
            } catch (const std::exception& failure) { error = failure.what(); client.stop(); }
            bool childrenStopped = true;
            for (const auto handle : observed) {
                childrenStopped = WaitForSingleObject(handle, 0) == WAIT_OBJECT_0 && childrenStopped;
                require(CloseHandle(handle), "IME child observation cleanup");
            }
            if (unowned) {
                const auto stillRunning = WaitForSingleObject(unowned, 0) == WAIT_TIMEOUT;
                const auto terminated = TerminateProcess(unowned, 0);
                awaitExit(unowned);
                require(stillRunning && terminated, "unowned process affected by game cleanup");
            }
            const bool rejected = mode == "not-loaded" || mode == "before-load" || mode == "unregistered"
                || mode == "registry-error" || mode == "known-hash" || mode == "known-path" || mode == "forbidden"
                || mode == "module-path" || mode == "verified-fault" || mode == "verified-adaptation";
            if (error.empty() == rejected) throw std::runtime_error("IME case " + mode + ": " + (error.empty() ? "unexpected acceptance" : error));
            if (rejected) {
                const auto reason = mode == "forbidden" ? "TP module reached loader"
                    : mode == "verified-fault" ? "unhandled client exception"
                    : mode == "verified-adaptation" ? "required DLL identity/address mismatch" : "unverified client child process";
                require(error.find(reason) != std::string::npos, "wrong IME case rejection reason");
            }
            require(childrenStopped, "compatible process left running");
            DWORD exitCode = 0;
            require(GetExitCodeProcess(client.process(), &exitCode) && exitCode == (rejected || mode == "stop" ? 1u : 41u),
                    "IME case parent exit status");
            require(t7::fs::exists(directory / "parent-survived.txt") == (!rejected && mode != "stop"), "helper failure reached parent");
            const auto activations = std::count_if(logs.begin(), logs.end(), [](const auto& line) {
                return line.find("Input method compatibility enabled;") == 0;
            });
            require(activations == (mode != "not-loaded" && mode != "before-load" && mode != "unregistered"
                                   && mode != "module-path" && mode != "registry-error" ? 1 : 0),
                    "registration observation/activation mismatch");
            if (mode == "fault" || mode == "nonzero" || mode == "registry-error") {
                const auto reason = mode == "fault" ? "terminating only compatible child"
                    : mode == "nonzero" ? "code=7; role=CompatibleChild" : "Input method registry unavailable";
                require(std::any_of(warnings.begin(), warnings.end(), [&](const auto& line) {
                    return line.find(reason) != std::string::npos;
                }), "IME failure warning missing");
            }
            for (const auto& line : logs)
                require(line.find(t7::utf8(directory.wstring())) == std::string::npos, "IME log contains personal directory");
            if (mode == "known-hash" || mode == "known-path")
                require(!t7::fs::exists(directory / "helper-started.txt"), "known component bypassed its identity check");
        }
        cleanup();
        std::cout << "Input method child compatibility, fault isolation and owned cleanup cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Input method debug test failure: " << error.what() << '\n';
        if (t7::fs::exists(root)) cleanup();
        return false;
    }
}
