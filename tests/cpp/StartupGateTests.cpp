#include "StartupGateFixture.h"
#include "../../src/Runtime/launcher/StartupGate.h"
#include "../../src/Runtime/launcher/DebugClient.h"
#include "../../src/Runtime/launcher/EndpointStorage.h"
#include <atomic>
#include <cstring>
#include <functional>
#include <iostream>

namespace {
using namespace startupFixture;
struct Options {
    bool gate = true, initialized = false, peer = false, pendingPeer = false, badSignature = false, badObject = false;
    uint32_t delay = 0;
};
class Run {
public:
    std::atomic<unsigned> hits{0};
    std::atomic<bool> pending{false}, allowPending{false};
    t7::StartupGate gate;
    t7::DebugClient client;
    ~Run() { allowPending.store(true); }
    void start(const t7::fs::path& path, const Options& options = {}) {
        const auto patch = initializationPatch();
        client.start(path, t7::fileHash(path), {}, [&](HANDLE process, HANDLE) {
            t7::applyRemotePatches(process, BASE, {patch});
            writeWord(process, CONTROL + InitializationSuccess, options.initialized ? 1u : 0u);
            writeWord(process, CONTROL + Delay, options.delay);
            writeWord(process, CONTROL + Peer, options.peer ? 1u : 0u);
            writeWord(process, CONTROL + PeerReady, options.pendingPeer ? 0u : 1u);
            if (options.badObject) writeWord(process, NETWORK, 0);
            if (options.badSignature) t7::writeClientMemory(process, SELECTOR, {0x90});
            if (options.gate) gate.install(process, BASE);
        }, {}, {}, {}, [this, options](DWORD threadId, uintptr_t address) {
            if (address == SELECTOR && options.pendingPeer && hits.load() == 1) {
                pending.store(true);
                const auto deadline = GetTickCount64() + 5000;
                while (!allowPending.load() && GetTickCount64() < deadline) Sleep(1);
                require(allowPending.load(), "pending breakpoint test coordination timed out");
            }
            if (!options.gate || !gate.handleBreakpoint(threadId, address)) return false;
            ++hits; return true;
        });
    }
    void wait(const std::function<bool()>& condition, const char* message) {
        const auto deadline = GetTickCount64() + 10000;
        for (;;) {
            client.check();
            if (condition()) return;
            require(GetTickCount64() < deadline, message);
            Sleep(2);
        }
    }
    void held(unsigned count = 1) {
        wait([&] { return hits.load() == count; }, "startup gate was not reached");
        require(gate.object() == NETWORK, "startup gate captured the wrong object");
        require(readWord(client.process(), CONTROL + Selections) == 0, "client selected before publication");
        require(readWord(client.process(), CONTROL + DialogCalls) == 0, "startup displayed an ANSI dialog");
        require(readWord(client.process(), CONTROL + StackValid) == 1, "initialization stack imbalance");
        require(readWord(client.process(), CONTROL + RegisterValid) == 1, "initialization changed nonvolatile registers");
        require(readWord(client.process(), CONTROL + InitializationResult) == 1, "initialization did not return success");
    }
    void stop() {
        allowPending.store(true);
        client.stop();
        require(WaitForSingleObject(client.process(), 0) == WAIT_OBJECT_0, "startup cleanup left a process running");
        gate.clear();
    }
    void finish(unsigned count = 1) {
        wait([&] { return readWord(client.process(), CONTROL + Selections) == count; }, "startup gate did not release all callers");
        require(readWord(client.process(), CONTROL + Error) == 0, "startup fixture worker creation failed");
        require(t7::readClientMemory(client.process(), SELECTOR, 1) == t7::Bytes{0x55}, "selector instruction was not restored");
        writeWord(client.process(), CONTROL + Exit, 1);
        require(WaitForSingleObject(client.process(), 10000) == WAIT_OBJECT_0, "startup fixture did not exit");
        stop(); client.check();
        DWORD exitCode = 1;
        require(GetExitCodeProcess(client.process(), &exitCode) && exitCode == 0, "startup fixture failed after release");
    }
};
template<class Operation> void rejects(Operation operation, const char* expected) {
    std::string failure;
    try { operation(); } catch (const std::exception& error) { failure = error.what(); }
    if (failure.find(expected) == std::string::npos)
        throw std::runtime_error(std::string("expected '") + expected + "', got '" + failure + "'");
}
template<class T> void writeBlock(HANDLE process, uint32_t address, const T& value) {
    const auto bytes = reinterpret_cast<const unsigned char*>(&value);
    t7::writeClientMemory(process, address, t7::Bytes(bytes, bytes + sizeof(value)));
}
void publish(Run& run) {
    const auto process = run.client.process();
    const auto allocator = t7::clientEndpointAllocator(process, BASE);
    t7::Config config; config.ports[0] = 26101; config.ports[1] = 26102; config.ports[2] = 26103;
    const auto parity = readWord(process, NETWORK + 0x1C) & 1;
    const auto addresses = t7::allocateEndpointStorage(process, allocator, config, parity);
    const auto layout = t7::makeEndpointLayout(config, addresses, parity);
    writeBlock(process, addresses.records, layout.records); writeBlock(process, addresses.group, layout.group);
    writeBlock(process, addresses.candidates, layout.candidates);
    const uint32_t groups[]{addresses.group, addresses.group + 12, addresses.group + 12};
    const uint32_t candidates[]{addresses.candidates, addresses.candidates + 56, addresses.candidates + 56};
    writeBlock(process, NETWORK + 0x8C, groups); writeBlock(process, NETWORK + 0x78, candidates);
    require(readWord(process, CONTROL + Selections) == 0, "allocator worker released server selection early");
}
void success(const t7::fs::path& path, unsigned expectedCodePage, bool initialized = false, unsigned delay = 0) {
    Run run; Options options; options.initialized = initialized; options.delay = delay;
    run.start(path, options); run.held();
    require(readWord(run.client.process(), CONTROL + CodePage) == expectedCodePage, "fixture code page was not activated");
    require(readWord(run.client.process(), NETWORK + 0x1C) == (initialized ? 10001u : 10000u),
            "original successful initialization branch changed");
    publish(run); run.gate.release(); run.finish();
}
void concurrentCalls(const t7::fs::path& path, bool pending) {
    Run run; Options options; options.peer = true; options.pendingPeer = pending;
    run.start(path, options); run.held(pending ? 1u : 2u);
    publish(run);
    if (pending) {
        writeWord(run.client.process(), CONTROL + PeerReady, 1);
        run.wait([&] { return run.pending.load(); }, "second breakpoint was not queued");
    }
    run.gate.release(); run.allowPending.store(true); run.finish(2);
    require(run.hits.load() == 2, "concurrent breakpoint handling coverage");
}
void failures(const t7::fs::path& path) {
    {
        Run run; Options options; options.gate = false;
        run.start(path, options);
        require(WaitForSingleObject(run.client.process(), 10000) == WAIT_OBJECT_0, "empty-list regression did not fault");
        run.stop();
        rejects([&] { run.client.check(); }, "unhandled client exception code=3221225620");
    }
    {
        Run run; Options options; options.badSignature = true;
        rejects([&] { run.start(path, options); }, "startup selector signature mismatch");
        run.stop();
    }
    {
        Run run; Options options; options.badObject = true;
        run.start(path, options);
        require(WaitForSingleObject(run.client.process(), 10000) == WAIT_OBJECT_0, "wrong object did not stop startup");
        run.stop(); rejects([&] { run.client.check(); }, "startup gate network object mismatch");
    }
    for (const auto* kind : {"empty", "owner", "vtable", "vector", "instruction", "allocate"}) {
        Run run; run.start(path); run.held(); const auto process = run.client.process();
        const std::string scenario = kind;
        if (scenario == "allocate") {
            writeWord(process, CONTROL + AllocationFailure, 1);
            rejects([&] { publish(run); }, "client endpoint allocation failed");
        } else {
            if (scenario != "empty") publish(run);
            if (scenario == "owner") writeWord(process, SERVICE, NETWORK + 4);
            if (scenario == "vtable") writeWord(process, NETWORK, VTABLE + 4);
            if (scenario == "vector") writeWord(process, NETWORK + 0x90, 0);
            if (scenario == "instruction") t7::writeClientMemory(process, SELECTOR + 1, {0x90});
            const auto* expected = scenario == "owner" || scenario == "vtable" ? "network object mismatch"
                : scenario == "instruction" ? "breakpoint signature changed" : "requires published endpoint vectors";
            rejects([&] { run.gate.release(); }, expected);
        }
        require(readWord(process, CONTROL + Selections) == 0, "failed startup was released");
        run.stop();
    }
    // Cancel/timeout cleanup before arrival, while held, and after publication.
    for (unsigned stage = 0; stage < 3; ++stage) {
        Run run; Options options; options.delay = stage == 0 ? 30000u : 0u;
        run.start(path, options);
        if (stage) run.held();
        if (stage == 2) publish(run);
        rejects([&] { run.gate.clear(); }, "requires owned client termination");
        require(readWord(run.client.process(), CONTROL + Selections) == 0, "cancelled startup advanced");
        run.stop(); run.client.check();
    }
}
bool supportsLocaleManifest() {
    using Query = LONG(WINAPI*)(OSVERSIONINFOW*);
    const auto symbol = GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "RtlGetVersion");
    Query query = nullptr; static_assert(sizeof(query) == sizeof(symbol)); std::memcpy(&query, &symbol, sizeof(query));
    OSVERSIONINFOW version{}; version.dwOSVersionInfoSize = sizeof(version);
    require(query && query(&version) == 0, "startup fixture OS version query");
    return version.dwBuildNumber >= 22000;
}
}
bool verifyStartupGate() {
    const auto root = t7::fs::temp_directory_path() / (L"t7-startup-" + std::to_wstring(GetCurrentProcessId())
                                                    + L"-" + std::to_wstring(GetTickCount64()));
    const auto cleanup = [&] {
        require(t7::fs::canonical(root).parent_path() == t7::fs::canonical(t7::fs::temp_directory_path()),
                "startup fixture cleanup boundary");
        t7::fs::remove_all(root);
    };
    try {
        require(t7::fs::create_directory(root), "fresh startup fixture directory");
        const auto path = create(root, "default");
        const auto originalHash = t7::fileHash(path);
        success(path, GetACP()); success(path, GetACP(), true); success(path, GetACP(), false, 150);
        concurrentCalls(path, false); concurrentCalls(path, true); failures(path);
        success(create(root, "utf8", "UTF-8"), CP_UTF8);
        if (supportsLocaleManifest()) {
            success(create(root, "gbk", "zh-CN"), 936);
            success(create(root, "western", "en-US"), 1252);
            std::cout << "Startup gate code pages 936, 65001 and 1252 passed\n";
        } else std::cout << "Startup gate default/UTF-8 passed; locale manifests require Windows 11\n";
        require(t7::fileHash(path) == originalHash, "startup gate changed the disk image");
        cleanup();
        std::cout << "Startup gate initialization, allocation, concurrency, queued events and failure cleanup cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Startup gate test failure: " << error.what() << '\n';
        try { if (t7::fs::exists(root)) cleanup(); }
        catch (const std::exception& cleanupError) { std::cerr << "Startup fixture cleanup: " << cleanupError.what() << '\n'; }
        return false;
    }
}
