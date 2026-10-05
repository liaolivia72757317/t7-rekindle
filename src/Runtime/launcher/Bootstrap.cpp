#include "Bootstrap.h"
#include "ModuleLookup.h"
#include "ProcessCleanup.h"
#include "EndpointStorage.h"
#include "ClientPath.h"
#include "MovementOverlay.h"
#include <d3d9.h>
#include <sstream>
#include <utility>

namespace t7 {
namespace {
const char* CLIENT_HASH = "3c205c7efaf1956bc2c458b1073e5273a9fef29e3e5ee18418f93f418eeaa5c8";
const char* PROTOCOL_HASH = "432ea9dd64d7b90af5fed323e52ce33d0a035a3b13cf4ac75a050f0611776d18";
const char* WEB_HELPER_HASH = "6d6232bddd6374abdcda65c726b5ecb007bd410dca04e125097d2b59449b0b47";
constexpr uintptr_t SERVICE_RVA = 0x024396CC, VTABLE_RVA = 0x01734E64;
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
    const auto allocator = clientEndpointAllocator(process, static_cast<uint32_t>(base));
    suspended.resume();
    const auto addresses = allocateEndpointStorage(process, allocator, config, selectorSalt & 1);
    bool unpublished = true;
    try {
        const auto layout = makeEndpointLayout(config, addresses, selectorSalt & 1);
        const auto writeBlock = [&](uint32_t address, const void* data, size_t size) {
            write(process, address, data, size);
            const auto checked = readClientMemory(process, address, size);
            if (memcmp(data, checked.data(), size)) throw std::runtime_error("endpoint data readback mismatch");
        };
        writeBlock(addresses.records, layout.records, sizeof(layout.records));
        writeBlock(addresses.group, layout.group, sizeof(layout.group));
        writeBlock(addresses.candidates, layout.candidates, sizeof(layout.candidates));
        for (size_t i = 0; i < 4; ++i) {
            if (addresses.descriptors[i]) writeBlock(addresses.descriptors[i], layout.descriptors[i], sizeof(layout.descriptors[i]));
        }
        SuspendedProcess publication(process);
        uint32_t currentObject = 0, currentVtable = 0, currentSalt = 0, current[3]{}, currentServers[3]{};
        read(process, base + SERVICE_RVA, &currentObject, 4);
        read(process, object, &currentVtable, 4);
        read(process, object + 0x1C, &currentSalt, 4);
        read(process, object + 0x8C, current, sizeof(current));
        read(process, object + 0x78, currentServers, sizeof(currentServers));
        if (currentObject != object || currentVtable != vtable || currentSalt != selectorSalt
            || memcmp(fields, current, sizeof(fields)) || memcmp(servers, currentServers, sizeof(servers)))
            throw std::runtime_error("endpoint owner changed during allocation");
        const auto begin = addresses.group;
        uint32_t published[]{begin, begin + sizeof(layout.group), begin + sizeof(layout.group)};
        const auto candidates = addresses.candidates;
        uint32_t selected[]{candidates, candidates + sizeof(layout.candidates), candidates + sizeof(layout.candidates)};
        try {
            unpublished = false;
            write(process, object + 0x8C, published, sizeof(published));
            write(process, object + 0x78, selected, sizeof(selected));
            read(process, object + 0x8C, current, sizeof(current));
            if (memcmp(published, current, sizeof(published))) throw std::runtime_error("published vector readback mismatch");
            read(process, object + 0x78, current, sizeof(current));
            if (memcmp(selected, current, sizeof(selected))) throw std::runtime_error("selected candidate vector readback mismatch");
        } catch (...) {
            cleanupAndRethrow(std::current_exception(), [&] {
                uint32_t restored[3]{}; write(process, object + 0x8C, fields, sizeof(fields));
                read(process, object + 0x8C, restored, sizeof(restored));
                if (memcmp(fields, restored, sizeof(fields))) throw std::runtime_error("endpoint publication rollback failed");
                write(process, object + 0x78, servers, sizeof(servers));
                read(process, object + 0x78, restored, sizeof(restored));
                if (memcmp(servers, restored, sizeof(servers))) throw std::runtime_error("candidate publication rollback failed");
                unpublished = true;
            });
        }
        publication.resume();
    } catch (...) {
        cleanupAndRethrow(std::current_exception(), [&] {
            if (unpublished) freeEndpointStorage(process, allocator, addresses);
        });
    }
    if (log) {
        log("Direct endpoint vectors published and read back; allocation ownership transferred to client");
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
    try { stop(); } catch (...) {}
}
bool Bootstrap::running() const {
    if (testAdapter_.launch) {
        return testAdapter_.running ? testAdapter_.running() : testRunning_;
    }
    if (!process_) return false;
    if (debugClient_) debugClient_->check();
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
    if (fileHash(directory / "TieJiClient.exe") != CLIENT_HASH || fileHash(directory / "ProtocalHandler.dll") != PROTOCOL_HASH
        || fileHash(directory / "TenProxy.dll") != TEN_PROXY_SHA256
        || fileHash(directory / "TieJiWebHelper.exe") != WEB_HELPER_HASH)
        throw std::runtime_error("unsupported client/protocol baseline");
    if (fs::exists(directory / "TesSafe.sys") && log)
        log("Bin/TesSafe.sys 保持原样；客户端适配仅在本次进程内存中进行。");
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
    if (process_ || debugClient_) stop();
    if (cancelled && cancelled()) throw std::runtime_error("client launch cancelled");
    check(directory, config, log);
    if (adapting) adapting();
    auto image = recoverClientImage(readFile(directory / "TieJiClient.exe"), cancelled);
    applyMemoryPatches(image.bytes, clientMemoryPatches(image.bytes));
    const auto helper = readFile(directory / "TenProxy.dll");
    tenProxyMemoryPatches(helper, 0x10000000);
    const DllEntryRule rule{L"TenProxy.dll", TEN_PROXY_SHA256, TEN_PROXY_ENTRY_RVA,
                           {0x55,0x8B,0xEC,0x53,0x8B,0x5D,0x08},
                           [helper](uint32_t base) { return tenProxyMemoryPatches(helper, base); }};
    const auto executable = (directory / "TieJiClient.exe").make_preferred();
    debugClient_ = std::make_unique<DebugClient>();
    const auto base = static_cast<uintptr_t>(image.imageBase);
    try {
        debugClient_->start(executable, CLIENT_HASH, {rule}, [&](HANDLE process, HANDLE thread) {
            const auto module = findMappedImageBase(process, executable);
            if (module.base != base) throw std::runtime_error("client mapped image identity mismatch before preparation");
            installClientImage(process, thread, image, cancelled);
            movementOverlay_.install(process, base, log, config.skipStartupAnimation);
            if (log) log("Client code, imports, TP paths and movement adapted before first ResumeThread; disk binaries unchanged");
        }, log, cancelled, {{fs::absolute(directory / "TieJiWebHelper.exe"), WEB_HELPER_HASH}},
        [this](DWORD threadId, uintptr_t address) { return movementOverlay_.handleBreakpoint(threadId, address); });
        process_ = debugClient_->process(); pid_ = debugClient_->pid();
        if (log) log("Prepared owned client PID=" + std::to_string(pid_) + "; waiting for network object");
        auto deadline = GetTickCount64() + 60000;
        Prompt prompt{pid_, nullptr}; bool ready = false;
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
    std::string cleanupError;
    if (debugClient_) {
        try { debugClient_->stop(); }
        catch (const std::exception& error) { cleanupError = error.what(); }
    }
    try { movementOverlay_.rollback(); }
    catch (const std::exception& error) {
        if (!cleanupError.empty()) cleanupError += "; ";
        cleanupError += error.what();
    }
    debugClient_.reset(); process_ = nullptr; pid_ = 0;
    if (!cleanupError.empty()) throw std::runtime_error(cleanupError);
}
}
