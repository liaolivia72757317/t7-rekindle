#include "../../src/Runtime/bridge/Session.h"
#include <fstream>
#include <iostream>

namespace {
using namespace t7r;
using namespace t7r::bridge;
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }

bool waitUntil(const std::function<bool()>& predicate, unsigned timeoutMs = 2000) {
    const auto deadline = GetTickCount64() + timeoutMs;
    do {
        if (predicate()) return true;
        Sleep(5);
    } while (GetTickCount64() < deadline);
    return false;
}

struct Fixture {
    std::atomic<bool> alive{false};
    std::atomic<unsigned> stops{0};
    std::shared_ptr<Session> session;
    fs::path root;
    explicit Fixture(const fs::path& path) : root(path) {}
    Bootstrap::TestAdapter adapter() {
        Bootstrap::TestAdapter result;
        result.launch = [this](const fs::path&, const Config&, const DiagnosticLogCallback&,
            const std::function<bool()>&, const std::function<void()>&) { alive = true; };
        result.running = [this] { return alive.load(); };
        result.stop = [this] { alive = false; ++stops; };
        return result;
    }
    void start(Bootstrap::TestAdapter adapter, std::function<void()> afterOperation = {}) {
        session = Session::createForTest(utf8((root / "package").wstring()), std::move(adapter), std::move(afterOperation));
        uint64_t operation = 0;
        require(session->submit(T7RNB_OPERATION_START, utf8((root / "client/Bin").wstring()), operation,
            "Player", false, {1, 2, 3}) == T7RNB_OK, "monitor test start rejected");
    }
    Snapshot snapshot() {
        Snapshot result; session->snapshot(result); return result;
    }
    std::string logs() {
        uint64_t cursor = 0; uint32_t required = 0, flags = 0;
        std::vector<uint8_t> bytes(1024 * 1024);
        require(session->readLogs(cursor, bytes.data(), static_cast<uint32_t>(bytes.size()), required, flags) == T7RNB_OK,
            "monitor log read failed");
        return std::string(bytes.begin(), bytes.begin() + required);
    }
    void stop() {
        uint64_t id = 0;
        require(session->submit(T7RNB_OPERATION_STOP, {}, id) == T7RNB_OK, "monitor stop rejected");
        require(waitUntil([&] { const auto s = snapshot(); return s.state == T7RNB_STATE_IDLE && !s.operation; }),
            "monitor stop did not complete");
    }
    ~Fixture() {
        if (session) {
            session->requestClose();
            session->waitForWorkerForTest(std::chrono::seconds(10));
        }
    }
};

void windowProbe(const fs::path& root, bool persistent) {
    Fixture fixture(root);
    std::atomic<unsigned> probes{0};
    auto adapter = fixture.adapter();
    adapter.windowClosed = [&] {
        if (++probes <= 3 || persistent) throw std::runtime_error("synthetic window probe failure");
        return false;
    };
    fixture.start(std::move(adapter));
    require(waitUntil([&] { return probes >= 4; }), "window probe was not retried");
    require(fixture.snapshot().state == T7RNB_STATE_RUNNING && fixture.alive, "transient probe ended the game");
    if (persistent) {
        require(waitUntil([&] { return fixture.snapshot().state == T7RNB_STATE_FAILED; }, 6000), "window retry has no deadline");
        require(!fixture.alive && fixture.logs().find("probe failed for 5 seconds") != std::string::npos,
            "window deadline lost its cause");
    } else fixture.stop();
}

void workerFailure(const fs::path& root, bool standard, bool failCleanup = false) {
    std::atomic<bool> injected{false};
    Fixture fixture(root);
    auto adapter = fixture.adapter();
    adapter.stop = [&] {
        if (++fixture.stops == 1 && failCleanup) throw std::runtime_error("synthetic worker cleanup failure");
        fixture.alive = false;
    };
    fixture.start(std::move(adapter), [standard, &injected] {
        if (injected.exchange(true)) return;
        if (standard) throw std::runtime_error("synthetic worker failure");
        throw 7;
    });
    require(waitUntil([&] { const auto s = fixture.snapshot(); return s.phase == (failCleanup ? "failed-cleaning" : "failed")
        && s.errorCode == (failCleanup ? T7RNB_ERROR_CLEANUP : T7RNB_INTERNAL_ERROR)
        && fixture.logs().find("session worker failed:") != std::string::npos && fixture.stops == 1; }), "worker failure was not published");
    const auto state = fixture.snapshot();
    require(state.state == (failCleanup ? T7RNB_STATE_FAILED_CLEANING : T7RNB_STATE_FAILED)
        && state.errorCode == (failCleanup ? T7RNB_ERROR_CLEANUP : T7RNB_INTERNAL_ERROR)
        && ((state.flags & 1) != 0) == !failCleanup,
        "outer worker exception lost terminal failure state");
    require(fixture.stops == 1 && fixture.alive == failCleanup, "failed worker cleanup repeated");
    require(fixture.logs().find(standard ? "synthetic worker failure" : "non-standard native exception") != std::string::npos,
        "worker failure cause missing");
    if (failCleanup) require(fixture.logs().find("synthetic worker cleanup failure") != std::string::npos,
        "worker cleanup lost the secondary failure");
    fixture.stop();
}

void captureFailure(const fs::path& root, bool invalidPath) {
    const auto capture = invalidPath ? fs::path(L"relative-capture.jsonl") : root / "capture.jsonl";
    require(SetEnvironmentVariableW(L"T7R_BATTLE_CAPTURE_PATH", capture.c_str()), "capture environment setup");
    Fixture fixture(root);
    std::atomic<unsigned> polls{0};
    auto adapter = fixture.adapter();
    adapter.pollWorld = [&](WorldSnapshot& world) {
        ++polls;
        world.contacts.resize(8193);
        return true;
    };
    fixture.start(std::move(adapter));
    require(waitUntil([&] { return fixture.logs().find("battle capture disabled:") != std::string::npos; }),
        "capture failure was not reported");
    require(waitUntil([&] { return fixture.snapshot().state == T7RNB_STATE_RUNNING; }), "capture failure stopped startup");
    Sleep(150);
    require(fixture.alive && fixture.stops == 0 && (invalidPath || polls == 1), "capture failure reached game lifecycle");
    fixture.stop();
    if (!invalidPath) {
        std::ifstream stream(capture);
        const std::string data((std::istreambuf_iterator<char>(stream)), {});
        require(data.find("\"complete\":false") != std::string::npos, "disabled capture claimed complete coverage");
    }
}
}

bool verifySessionMonitoring() {
    const auto root = t7r::fs::temp_directory_path() / (L"t7r-monitor-tests-" + std::to_wstring(GetCurrentProcessId())
        + L"-" + std::to_wstring(GetTickCount64()));
    wchar_t previous[32768]{};
    const auto length = GetEnvironmentVariableW(L"T7R_BATTLE_CAPTURE_PATH", previous, static_cast<DWORD>(std::size(previous)));
    const auto cleanup = [&] {
        SetEnvironmentVariableW(L"T7R_BATTLE_CAPTURE_PATH", length ? previous : nullptr);
        require(t7r::fs::canonical(root).parent_path() == t7r::fs::canonical(t7r::fs::temp_directory_path()), "monitor cleanup root");
        t7r::fs::remove_all(root);
    };
    try {
        require(length < std::size(previous), "capture environment too long");
        t7r::fs::create_directories(root / "package");
        t7r::fs::create_directories(root / "client/Bin");
        SetEnvironmentVariableW(L"T7R_BATTLE_CAPTURE_PATH", nullptr);
        windowProbe(root, false); windowProbe(root, true);
        workerFailure(root, true); workerFailure(root, false); workerFailure(root, true, true);
        captureFailure(root, true); captureFailure(root, false);
        cleanup();
        return true;
    } catch (const std::exception& error) {
        std::cerr << "session monitoring: " << error.what() << '\n';
        cleanup();
        return false;
    }
}
