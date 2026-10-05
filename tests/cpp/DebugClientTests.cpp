#include "../../src/Runtime/launcher/DebugClient.h"
#include <iostream>
#include <fstream>
#include <mutex>
#include <cstring>

namespace {
void require(bool result, const char* message) { if (!result) throw std::runtime_error(message); }
t7::fs::path executable() {
    std::wstring path(32768, L'\0');
    const auto length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    require(length && length < path.size(), "synthetic executable identity"); path.resize(length);
    return path;
}
DWORD launchFixture(const t7::fs::path& path) {
    std::wstring command = L"\"" + path.wstring() + L"\"";
    STARTUPINFOW startup{}; startup.cb = sizeof(startup); PROCESS_INFORMATION process{};
    require(CreateProcessW(path.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
                           nullptr, path.parent_path().c_str(), &startup, &process), "fixture child creation");
    const auto state = WaitForSingleObject(process.hProcess, 10000);
    if (state != WAIT_OBJECT_0) TerminateProcess(process.hProcess, 1);
    DWORD code = 0; const auto observed = GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    require(state == WAIT_OBJECT_0 && observed, "fixture child exit");
    return code;
}
void copyGuiFixture(const t7::fs::path& source, const t7::fs::path& destination) {
    auto bytes = t7::readFile(source);
    IMAGE_DOS_HEADER dos{}; std::memcpy(&dos, bytes.data(), sizeof(dos));
    const auto offset = static_cast<size_t>(dos.e_lfanew) + offsetof(IMAGE_NT_HEADERS64, OptionalHeader)
        + offsetof(IMAGE_OPTIONAL_HEADER64, Subsystem);
    require(offset + 2 <= bytes.size(), "fixture subsystem bounds");
    // Match the GUI client without launching a console host alongside the fixture.
    const WORD subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI;
    std::memcpy(bytes.data() + offset, &subsystem, sizeof(subsystem));
    std::ofstream output(destination, std::ios::binary);
    output.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
    require(output.good(), "debug GUI fixture write");
}
void verifyChildren(const t7::fs::path& original) {
    const auto root = t7::fs::temp_directory_path() / (L"t7-debug-tree-" + std::to_wstring(GetCurrentProcessId())
                                                    + L"-" + std::to_wstring(GetTickCount64()));
    require(t7::fs::create_directory(root), "fresh debug tree directory");
    const auto cleanup = [&] {
        require(t7::fs::canonical(root).parent_path() == t7::fs::canonical(t7::fs::temp_directory_path()),
                "debug fixture cleanup escaped temporary directory");
        t7::fs::remove_all(root);
    };
    try {
        for (const auto* mode : {"allow", "unknown", "hash", "path", "stop"}) {
            const auto directory = root / mode;
            require(t7::fs::create_directory(directory), "fresh debug tree case");
            for (const auto* name : {"T7.DebugParent.exe", "T7.DebugChild.exe", "T7.DebugLeaf.exe"})
                copyGuiFixture(original, directory / name);
            const auto hash = t7::fileHash(directory / "T7.DebugParent.exe");
            std::vector<t7::ChildImageRule> children{{directory / "T7.DebugChild.exe", hash},
                                                    {directory / "T7.DebugLeaf.exe", hash}};
            const std::string scenario = mode;
            if (scenario == "unknown") children.clear();
            if (scenario == "hash") children[0].sha256.assign(64, '0');
            if (scenario == "path") children[0].executable = root / "T7.DebugChild.exe";
            if (scenario == "stop") std::ofstream(directory / "hold-leaf.txt").put('1');
            t7::DebugClient client;
            std::vector<HANDLE> observedChildren;
            std::mutex observations;
            std::string error;
            try {
                client.start(directory / "T7.DebugParent.exe", hash, {}, [](HANDLE, HANDLE) {},
                    [&](const std::string& line) {
                        const auto marker = line.find("Verified client child process; PID=");
                        if (marker != std::string::npos) {
                            const auto pid = static_cast<DWORD>(std::stoul(line.substr(marker + 35)));
                            const auto handle = OpenProcess(SYNCHRONIZE, FALSE, pid);
                            require(handle != nullptr, "verified child observation");
                            std::lock_guard<std::mutex> lock(observations); observedChildren.push_back(handle);
                        }
                    }, {}, children);
                if (scenario == "stop") {
                    const auto deadline = GetTickCount64() + 10000;
                    while (!t7::fs::exists(directory / "leaf-started.txt") && GetTickCount64() < deadline) {
                        client.check(); Sleep(10);
                    }
                    require(t7::fs::exists(directory / "leaf-started.txt"), "leaf readiness");
                } else require(WaitForSingleObject(client.process(), 15000) == WAIT_OBJECT_0, "debug tree completion");
                client.stop(); client.check();
            } catch (const std::exception& failure) { error = failure.what(); client.stop(); }
            const bool allowed = scenario == "allow" || scenario == "stop";
            bool childrenStopped = true;
            for (const auto handle : observedChildren) {
                childrenStopped = WaitForSingleObject(handle, 0) == WAIT_OBJECT_0 && childrenStopped;
                require(CloseHandle(handle), "observed child handle cleanup");
            }
            if (!childrenStopped) throw std::runtime_error("owned child left running in " + scenario);
            if (error.empty() != allowed) throw std::runtime_error("unexpected child identity decision for " + scenario + ": " + error);
            if (!allowed) require(error.find("unverified client child process") != std::string::npos
                                  && error.find("t7.debugchild.exe") != std::string::npos, "wrong child rejection reason");
            require(t7::fs::exists(directory / "child-started.txt") == allowed, "unverified child executed user code");
            require(t7::fs::exists(directory / "leaf-started.txt") == allowed, "descendant execution coverage");
            DWORD exitCode = 0;
            require(GetExitCodeProcess(client.process(), &exitCode) && exitCode == (scenario == "allow" ? 41u : 1u),
                    "owned parent exit code");
            require(observedChildren.size() == (allowed ? 2u : 0u), "verified child event coverage");
        }
        cleanup();
    } catch (...) { cleanup(); throw; }
}
void verifyUnhandledException(const t7::fs::path& original) {
    const auto root = t7::fs::temp_directory_path() / (L"t7-debug-fault-" + std::to_wstring(GetCurrentProcessId())
                                                    + L"-" + std::to_wstring(GetTickCount64()));
    require(t7::fs::create_directory(root), "fresh debug fault directory");
    const auto path = root / "T7.DebugFault.exe";
    const auto cleanup = [&] {
        require(t7::fs::canonical(root).parent_path() == t7::fs::canonical(t7::fs::temp_directory_path()),
                "debug fault cleanup escaped temporary directory");
        t7::fs::remove_all(root);
    };
    try {
        copyGuiFixture(original, path);
        t7::DebugClient client;
        std::vector<std::string> lines;
        client.start(path, t7::fileHash(path), {}, [](HANDLE, HANDLE) {},
                     [&](const std::string& line) { lines.push_back(line); });
        require(WaitForSingleObject(client.process(), 10000) == WAIT_OBJECT_0, "faulting fixture did not exit");
        client.stop();
        std::string error;
        try { client.check(); } catch (const std::runtime_error& failure) { error = failure.what(); }
        require(error.find("unhandled client exception") != std::string::npos, "primary fault was lost");
        require(error.find("module=") != std::string::npos && error.find("rva=0x") != std::string::npos,
                "unhandled exception lost its module-relative location");
        bool context = false, stack = false;
        for (const auto& line : lines) {
            context = context || line.find("Client fault context;") != std::string::npos;
            stack = stack || line.find("Client fault stack candidate;") != std::string::npos;
        }
        require(context && stack, "unhandled exception omitted bounded thread diagnostics");
        cleanup();
    } catch (...) { cleanup(); throw; }
}
}
int runDebugClientFixture() {
    const auto path = executable(); const auto name = path.filename().wstring();
    if (name == L"T7.DebugFault.exe") { RaiseException(0xE0007107, EXCEPTION_NONCONTINUABLE, 0, nullptr); return 1; }
    if (name == L"T7.DebugParent.exe") return launchFixture(path.parent_path() / "T7.DebugChild.exe") == 42 ? 41 : 1;
    if (name == L"T7.DebugChild.exe") {
        std::ofstream(path.parent_path() / "child-started.txt").put('1');
        return launchFixture(path.parent_path() / "T7.DebugLeaf.exe") == 43 ? 42 : 1;
    }
    if (name == L"T7.DebugLeaf.exe") {
        std::ofstream(path.parent_path() / "leaf-started.txt").put('1');
        if (t7::fs::exists(path.parent_path() / "hold-leaf.txt")) Sleep(10000);
        return 43;
    }
    return -1;
}
bool verifyDebugClient() {
    try {
        const auto path = executable();
        const auto hash = t7::fileHash(path);
        for (bool cancel : {true, false}) {
            t7::DebugClient client; bool prepared = false; std::string primaryError;
            try {
                client.start(path, hash, {}, [&](HANDLE, HANDLE) {
                    prepared = true;
                    // This owned copy of the test executable never resumes.
                    throw std::runtime_error("synthetic preparation failure");
                }, {}, [cancel] { return cancel; });
            } catch (const std::runtime_error& error) { primaryError = error.what(); }
            require(!primaryError.empty() && prepared != cancel, "preparation cancellation order");
            client.stop();
            if (cancel) require(!client.process(), "cancelled preparation created a process");
            else require(client.process() && WaitForSingleObject(client.process(), 0) == WAIT_OBJECT_0,
                         "failed preparation left an owned process running");
            bool retained = false;
            try { client.check(); } catch (const std::runtime_error& error) { retained = primaryError == error.what(); }
            require(retained, "primary error lost after successful cleanup");
        }
        verifyChildren(path);
        verifyUnhandledException(path);
        std::cout << "Debug client preparation, child identity and owned tree cleanup cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Debug client test failure: " << error.what() << '\n'; return false;
    }
}
