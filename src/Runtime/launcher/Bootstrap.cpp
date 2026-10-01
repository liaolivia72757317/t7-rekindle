#include "Bootstrap.h"
#include "ModuleLookup.h"
#include "ProcessCleanup.h"
#include "EndpointLayout.h"
#include "ClientPath.h"
#include "MovementOverlay.h"
#include <d3d9.h>
#include <sstream>
#include <iomanip>
#include <utility>

namespace t7 {
namespace {
const char* CLIENT_HASH = "3c205c7efaf1956bc2c458b1073e5273a9fef29e3e5ee18418f93f418eeaa5c8";
const char* PROTOCOL_HASH = "432ea9dd64d7b90af5fed323e52ce33d0a035a3b13cf4ac75a050f0611776d18";
constexpr uintptr_t SERVICE_RVA = 0x024396CC, VTABLE_RVA = 0x01734E64, STARTUP_RVA = 0x000890B1;
const unsigned char SIGNATURE[] = {0xE8,0x8A,0x04,0,0,0x84,0xC0,0x75,0x18,0x6A,0,0x68,0xA0,0xCF,0xB2,1,
    0x68,0x9C,0x4F,0xB3,1,0x6A,0,0xFF,0x15,0x90,0x6A,0xAF,1,0x32,0xC0,0x5E,0xC3};
std::string hexValue(uintptr_t value) {
    std::ostringstream out; out << "0x" << std::hex << std::uppercase << value; return out.str();
}
class SuspendedProcess {
    using Operation = LONG (NTAPI*)(HANDLE);
    HANDLE process_;
    Operation resume_ = nullptr;
    bool suspended_ = false;
public:
    explicit SuspendedProcess(HANDLE process) : process_(process) {
        auto module = GetModuleHandleW(L"ntdll.dll");
        auto first = GetProcAddress(module, "NtSuspendProcess"), second = GetProcAddress(module, "NtResumeProcess");
        Operation suspend = nullptr;
        static_assert(sizeof(first) == sizeof(suspend), "function pointer size mismatch");
        memcpy(&suspend, &first, sizeof(suspend)); memcpy(&resume_, &second, sizeof(resume_));
        if (!suspend || !resume_ || suspend(process_) < 0) throw std::runtime_error("owned process suspension failed");
        suspended_ = true;
    }
    void resume() {
        if (suspended_ && resume_(process_) < 0) throw std::runtime_error("owned process resume failed");
        suspended_ = false;
    }
    ~SuspendedProcess() {
        // On a failing transaction launch() terminates this owned process.
        if (suspended_) resume_(process_);
    }
};
void read(HANDLE process, uintptr_t address, void* destination, size_t size) {
    SIZE_T actual = 0;
    if (!ReadProcessMemory(process, reinterpret_cast<void*>(address), destination, size, &actual)) {
        auto error = GetLastError();
        throw std::runtime_error("ReadProcessMemory address=" + hexValue(address) + " error=" + std::to_string(error));
    }
    if (actual != size) throw std::runtime_error("short process read at " + hexValue(address));
}
void write(HANDLE process, uintptr_t address, const void* source, size_t size) {
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(address), source, size, &actual)) {
        auto error = GetLastError();
        throw std::runtime_error("WriteProcessMemory address=" + hexValue(address) + " error=" + std::to_string(error));
    }
    if (actual != size) throw std::runtime_error("short process write at " + hexValue(address));
}
struct Prompt { DWORD pid; HWND window; };
BOOL CALLBACK findPrompt(HWND window, LPARAM argument) {
    auto search = reinterpret_cast<Prompt*>(argument); DWORD pid = 0; GetWindowThreadProcessId(window, &pid);
    wchar_t text[128]{}; GetWindowTextW(window, text, 128);
    if (pid == search->pid && IsWindowVisible(window) && !wcscmp(text, L"提示")) { search->window = window; return FALSE; }
    return TRUE;
}
void verifyGraphics() {
    auto d3d = Direct3DCreate9(D3D_SDK_VERSION);
    if (!d3d) throw std::runtime_error("host D3D9 unavailable");
    D3DCAPS9 caps{}; auto hr = d3d->GetDeviceCaps(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, &caps); d3d->Release();
    if (FAILED(hr) || caps.PixelShaderVersion < D3DPS_VERSION(3,0) || caps.VertexShaderVersion < D3DVS_VERSION(3,0))
        throw std::runtime_error("host D3D9 capability gate failed");
}
void inject(HANDLE process, uintptr_t base, const Config& config, const std::function<void(std::string)>& log) {
    SuspendedProcess suspended(process);
    uint32_t object = 0, vtable = 0, fields[3]{}, servers[3]{}, selectorSalt = 0;
    read(process, base + SERVICE_RVA, &object, 4);
    if (!object) throw std::runtime_error("NetworkService not initialized");
    read(process, object, &vtable, 4);
    if (vtable != base + VTABLE_RVA) throw std::runtime_error("NetworkService vtable mismatch");
    read(process, object + 0x8C, fields, sizeof(fields)); read(process, object + 0x78, servers, sizeof(servers));
    read(process, object + 0x1C, &selectorSalt, sizeof(selectorSalt));
    if (fields[0] != fields[1] || fields[1] != fields[2] || servers[0] != servers[1] || servers[1] != servers[2])
        throw std::runtime_error("server vectors must be empty");
    // Retain the hash-bound layout guard; the parser itself is not executed.
    Bytes parser(0x8848E - 0x88270); read(process, base + 0x88270, parser.data(), parser.size());
    if (sha256(parser) != "b55a282ee9fcd56ab08ff3d9eb0189e2d82d84c87dc7ec0d2bb1d1d931510eae")
        throw std::runtime_error("client endpoint parser identity mismatch");
    if (log) log("Direct endpoint layout verified; no parser thread will be created");
    void* storage = VirtualAllocEx(process, nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!storage) throw std::runtime_error(errorText("endpoint data allocation"));
    auto address = reinterpret_cast<uintptr_t>(storage);
    bool publicationAttempted = false;
    try {
        if (address > UINT32_MAX) throw std::runtime_error("endpoint allocation outside x86 address range");
        auto layout = makeEndpointLayout(config, static_cast<uint32_t>(address), selectorSalt & 1);
        write(process, address, &layout, sizeof(layout));
        EndpointLayout checked{}; read(process, address, &checked, sizeof(checked));
        if (memcmp(&layout, &checked, sizeof(layout))) throw std::runtime_error("endpoint data readback mismatch");
        uint32_t begin = static_cast<uint32_t>(address + offsetof(EndpointLayout, group));
        uint32_t published[]{begin, begin + sizeof(layout.group), begin + sizeof(layout.group)};
        uint32_t candidates = static_cast<uint32_t>(address + offsetof(EndpointLayout, candidates));
        uint32_t selected[]{candidates, candidates + sizeof(layout.candidates), candidates + sizeof(layout.candidates)};
        publicationAttempted = true;
        write(process, object + 0x8C, published, sizeof(published));
        write(process, object + 0x78, selected, sizeof(selected));
        uint32_t current[3]{};
        read(process, object + 0x8C, current, sizeof(current));
        if (memcmp(published, current, sizeof(published))) throw std::runtime_error("published vector readback mismatch");
        read(process, object + 0x78, current, sizeof(current));
        if (memcmp(selected, current, sizeof(selected))) throw std::runtime_error("selected candidate vector readback mismatch");
    } catch (...) {
        cleanupAndRethrow(std::current_exception(), [&] {
            if (publicationAttempted) {
                uint32_t current[3]{}; write(process, object + 0x8C, fields, sizeof(fields));
                read(process, object + 0x8C, current, sizeof(current));
                if (memcmp(fields, current, sizeof(fields))) throw std::runtime_error("endpoint publication rollback failed");
                write(process, object + 0x78, servers, sizeof(servers));
                read(process, object + 0x78, current, sizeof(current));
                if (memcmp(servers, current, sizeof(servers))) throw std::runtime_error("candidate publication rollback failed");
            }
            if (!VirtualFreeEx(process, storage, 0, MEM_RELEASE)) throw std::runtime_error(errorText("unpublished endpoint cleanup"));
        });
    }
    suspended.resume();
    // Borrowed fixture data stays valid until the owned process exits.
    if (log) {
        log("Direct endpoint vector published and read back; data retained for process lifetime at " + hexValue(address));
        log("Fixed same-host selection published: candidates=2; parity=" + std::to_string(selectorSalt & 1) +
            "; attempt0=" + config.advertisedAddress + ":" + std::to_string(config.ports[0]) +
            "; attempt1=" + config.advertisedAddress + ":" + std::to_string(config.ports[1]));
    }
}
}
Bootstrap::Bootstrap(TestAdapter adapter) : testAdapter_(std::move(adapter)) {}
Bootstrap::~Bootstrap() {
    if (testAdapter_.launch) {
        if (testRunning_) {
            try { stop(); } catch (...) {}
        }
        return;
    }
    try { movementOverlay_.rollback(); } catch (...) {}
    if (process_) CloseHandle(process_);
    if (job_) CloseHandle(job_);
}
bool Bootstrap::running() const {
    if (testAdapter_.launch) {
        return testAdapter_.running ? testAdapter_.running() : testRunning_;
    }
    if (!process_) return false;
    auto state = WaitForSingleObject(process_, 0);
    if (state == WAIT_FAILED) throw std::runtime_error(errorText("owned client status wait"));
    return state == WAIT_TIMEOUT;
}
DWORD Bootstrap::exitCode() const {
    if (testAdapter_.launch) return testAdapter_.exitCode ? testAdapter_.exitCode() : 0;
    DWORD code = 0;
    if (!process_ || !GetExitCodeProcess(process_, &code)) throw std::runtime_error(errorText("owned client exit code"));
    return code;
}
void Bootstrap::check(const fs::path& directory, const Config& config, const std::function<void(std::string)>& log) {
    validateConfig(config);
    if (testAdapter_.check) {
        testAdapter_.check(directory, config, log);
        return;
    }
    validateClientDirectory(directory);
    if (fileHash(directory / "TieJiClient.exe") != CLIENT_HASH || fileHash(directory / "ProtocalHandler.dll") != PROTOCOL_HASH)
        throw std::runtime_error("unsupported client/protocol baseline");
    if (fs::exists(directory / "TesSafe.sys") && log)
        log("WARNING: 发现 Bin/TesSafe.sys 文件，仅记录文件存在，不代表驱动已加载。启动器不安装或加载驱动；客户端自身行为未由本检查验证。");
    verifyGraphics();
}
void Bootstrap::launch(const fs::path& directory, const Config& config, const std::function<void(std::string)>& log,
                       const std::function<bool()>& cancelled, const std::function<void()>& adapting) {
    if (running()) throw std::runtime_error("this launcher already owns a running client");
    if (testAdapter_.launch) {
        validateConfig(config);
        if (cancelled && cancelled()) throw std::runtime_error("client launch cancelled");
        testAdapter_.launch(directory, config, log, cancelled, adapting);
        testRunning_ = true;
        return;
    }
    if (process_) {
        // A previous client may have exited naturally.  Clear the overlay's
        // ownership record before reusing this Bootstrap instance.
        movementOverlay_.rollback();
        CloseHandle(process_);
        process_ = nullptr;
    }
    if (job_) { CloseHandle(job_); job_ = nullptr; }
    if (cancelled && cancelled()) throw std::runtime_error("client launch cancelled");
    check(directory, config, log);
    if (cancelled && cancelled()) throw std::runtime_error("client launch cancelled");
    if (log) log("Baseline and D3D9 preflight passed; host/VM and network deployment are user-selected");
    auto image = (directory / "TieJiClient.exe").make_preferred();
    std::wstring command = L"\"" + image.wstring() + L"\"";
    STARTUPINFOW startup{}; startup.cb = sizeof(startup); PROCESS_INFORMATION info{};
    if (!CreateProcessW(image.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_SUSPENDED, nullptr, directory.c_str(), &startup, &info))
        throw std::runtime_error(errorText("CreateProcessW"));
    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    if (!job) {
        TerminateProcess(info.hProcess, 1); CloseHandle(info.hThread); CloseHandle(info.hProcess);
        throw std::runtime_error(errorText("CreateJobObjectW"));
    }
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))
        || !SetHandleInformation(job, HANDLE_FLAG_INHERIT, 0)
        || !AssignProcessToJobObject(job, info.hProcess)) {
        auto error = GetLastError();
        TerminateProcess(info.hProcess, 1); WaitForSingleObject(info.hProcess, 5000);
        CloseHandle(info.hThread); CloseHandle(info.hProcess); CloseHandle(job);
        throw std::runtime_error("owned client Job Object setup failed error=" + std::to_string(error));
    }
    process_ = info.hProcess; job_ = job; pid_ = info.dwProcessId;
    try {
        uintptr_t overlayBase = 0;
        const auto overlayDeadline = GetTickCount64() + 5000;
        while (GetTickCount64() < overlayDeadline) {
            if (cancelled && cancelled()) throw std::runtime_error("client launch cancelled");
            overlayBase = findImageBase(pid_, image).base;
            if (overlayBase) break;
            Sleep(25);
        }
        if (!overlayBase) throw std::runtime_error("client image unavailable for movement overlay");
        movementOverlay_.install(process_, overlayBase, log);
    } catch (...) {
        stop();
        throw;
    }
    if (ResumeThread(info.hThread) == static_cast<DWORD>(-1)) {
        auto error = GetLastError(); CloseHandle(info.hThread);
        stop(); throw std::runtime_error("owned client resume failed error=" + std::to_string(error));
    }
    CloseHandle(info.hThread);
    try {
        if (adapting) adapting();
        if (log) log("Started owned client PID=" + std::to_string(pid_));
        uintptr_t base = 0; bool patched = false; auto deadline = GetTickCount64() + 20000;
        std::string stage = "module-lookup";
        DWORD lastError = ERROR_SUCCESS; bool moduleLogged = false;
        unsigned char observed[sizeof(SIGNATURE)]{}; SIZE_T observedCount = 0;
        while (running() && GetTickCount64() < deadline) {
            if (cancelled && cancelled()) throw std::runtime_error("client launch cancelled");
            auto module = findImageBase(pid_, image); base = module.base; lastError = module.error;
            stage = "module-lookup"; observedCount = 0;
            if (base) {
                if (!moduleLogged) {
                    if (log) log("Main module resolved; imageBase=" + hexValue(base) + "; startupRva=" + hexValue(STARTUP_RVA));
                    moduleLogged = true;
                }
                stage = "startup-memory-read";
                if (!ReadProcessMemory(process_, reinterpret_cast<void*>(base + STARTUP_RVA), observed, sizeof(observed), &observedCount))
                    lastError = GetLastError();
                else if (observedCount != sizeof(observed)) lastError = ERROR_PARTIAL_COPY;
                else { stage = "startup-signature-mismatch"; lastError = ERROR_SUCCESS; }
            }
            if (base && !lastError && observedCount == sizeof(observed) && !memcmp(observed, SIGNATURE, sizeof(observed))) {
                SuspendedProcess suspended(process_);
                unsigned char bytes[sizeof(SIGNATURE)]{};
                read(process_, base + STARTUP_RVA, bytes, sizeof(bytes));
                if (memcmp(bytes, SIGNATURE, sizeof(bytes))) throw std::runtime_error("startup signature changed before patch");
                DWORD old = 0; auto address = reinterpret_cast<void*>(base + STARTUP_RVA + 29);
                if (!VirtualProtectEx(process_, address, 2, PAGE_EXECUTE_READWRITE, &old)) throw std::runtime_error("startup protection failed");
                const unsigned char patch[]{0xB0, 1}; write(process_, reinterpret_cast<uintptr_t>(address), patch, 2);
                DWORD ignored = 0;
                if (!FlushInstructionCache(process_, address, 2) || !VirtualProtectEx(process_, address, 2, old, &ignored))
                    throw std::runtime_error("startup protection restore failed");
                unsigned char checked[2]; read(process_, reinterpret_cast<uintptr_t>(address), checked, 2);
                if (memcmp(patch, checked, 2)) throw std::runtime_error("startup readback mismatch");
                suspended.resume();
                patched = true; break;
            }
            Sleep(50);
        }
        if (!patched) {
            std::ostringstream details;
            details << "startup adaptation failed; stage=" << stage << "; imageBase=" << hexValue(base)
                << "; win32Error=" << lastError << "; bytesRead=" << observedCount;
            if (!running()) {
                DWORD exitCode = 0;
                if (GetExitCodeProcess(process_, &exitCode)) details << "; clientExitCode=" << exitCode;
            } else details << "; timeoutMs=20000";
            if (stage == "startup-signature-mismatch") {
                details << "; observed=";
                for (auto byte : observed) details << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(byte);
            }
            throw std::runtime_error(details.str());
        }
        if (log) log("Startup adapted; waiting for network object");
        Prompt prompt{pid_, nullptr}; deadline = GetTickCount64() + 60000; bool ready = false;
        while (running() && GetTickCount64() < deadline) {
            if (cancelled && cancelled()) throw std::runtime_error("client launch cancelled");
            EnumWindows(findPrompt, reinterpret_cast<LPARAM>(&prompt)); uint32_t object = 0; SIZE_T actual = 0;
            if (prompt.window && ReadProcessMemory(process_, reinterpret_cast<void*>(base + SERVICE_RVA), &object, 4, &actual) && actual == 4 && object) { ready = true; break; }
            Sleep(50);
        }
        if (!ready) {
            DWORD exitCode = 0;
            auto state = WaitForSingleObject(process_, 0);
            if (state == WAIT_OBJECT_0 && GetExitCodeProcess(process_, &exitCode))
                throw std::runtime_error("client exited before network readiness; exitCode=" + hexValue(exitCode));
            throw std::runtime_error("startup prompt/network readiness failed; promptPresent=" + std::to_string(prompt.window != nullptr));
        }
        if (log) log("Network object ready; beginning endpoint injection");
        inject(process_, base, config, log);
        if (!PostMessageW(prompt.window, WM_KEYDOWN, VK_RETURN, 1) || !PostMessageW(prompt.window, WM_KEYUP, VK_RETURN, 1))
            throw std::runtime_error("target prompt input failed");
        if (log) log("Endpoints verified; waiting for the game to connect to the configured server");
    } catch (...) { cleanupAndRethrow(std::current_exception(), [this] { stop(); }); }
}
void Bootstrap::stop() {
    if (testAdapter_.launch) {
        if (testRunning_ && testAdapter_.stop) testAdapter_.stop();
        testRunning_ = false;
        return;
    }
    std::string overlayError;
    try { movementOverlay_.rollback(); }
    catch (const std::exception& error) { overlayError = error.what(); }
    if (process_) {
        stopOwnedProcess(process_);
        // If rollback failed while the client was live, process termination
        // makes the remote page unreachable; clear the stale local record.
        if (movementOverlay_.installed()) {
            try { movementOverlay_.rollback(); } catch (...) {}
        }
    }
    if (process_) CloseHandle(process_);
    process_ = nullptr; pid_ = 0;
    if (job_) CloseHandle(job_);
    job_ = nullptr;
    if (!overlayError.empty()) throw std::runtime_error(overlayError);
}
}
