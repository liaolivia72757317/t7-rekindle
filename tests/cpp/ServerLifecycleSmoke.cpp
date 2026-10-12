#include "../../src/Runtime/server/Server.h"
#include "../../src/Runtime/server/Journal.h"
#include "../../src/Runtime/launcher/Bootstrap.h"
#include "../../src/Runtime/bridge/Session.h"
#include <winsock2.h>
#include <chrono>
#include <fstream>
#include <iostream>
#include <memory>
#include <thread>
#include <utility>

bool verifyJournalContracts(const t7::fs::path& fixtureRoot);
bool verifyServerEventOrder(const t7::fs::path& fixtureRoot);
bool verifyDiagnosticLogs(const t7::fs::path& fixtureRoot);
bool verifyFailureCleanup(const t7::fs::path& packageRoot, bool cancel);
bool verifyNamedSessionExit(const t7::fs::path& packageRoot, DWORD exitCode, bool normalExit);
bool verifyWindowCloseCleanup(const t7::fs::path& packageRoot, bool failCleanup);
bool verifyOperationPublication(const t7::fs::path& packageRoot, uint32_t expectedStatus);
bool verifyMovementOverlay();
bool verifyMovementHookExecution();
bool verifyGraphicsHookExecution();
bool verifyGraphicsSession(const t7::fs::path& packageRoot);
bool verifyResourcePreflight(const t7::fs::path& packageRoot);
bool verifyAudioSession(const t7::fs::path& packageRoot);

bool verifyLobbyNews();

namespace {
bool verifyJobAssignmentOrder() {
    wchar_t systemDirectory[MAX_PATH]{};
    auto length = GetSystemDirectoryW(systemDirectory, static_cast<UINT>(std::size(systemDirectory)));
    if (!length || length >= std::size(systemDirectory)) return false;
    std::wstring image(systemDirectory);
    image += L"\\cmd.exe";
    std::wstring command = L"\"" + image + L"\" /c exit 0";
    STARTUPINFOW startup{}; startup.cb = sizeof(startup);
    PROCESS_INFORMATION processInfo{};
    if (!CreateProcessW(image.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_SUSPENDED,
                        nullptr, nullptr, &startup, &processInfo)) return false;
    HANDLE job = CreateJobObjectW(nullptr, nullptr);
    if (!job) {
        TerminateProcess(processInfo.hProcess, 1); CloseHandle(processInfo.hThread); CloseHandle(processInfo.hProcess);
        return false;
    }
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    bool assigned = SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))
        && SetHandleInformation(job, HANDLE_FLAG_INHERIT, 0)
        && AssignProcessToJobObject(job, processInfo.hProcess)
        && WaitForSingleObject(processInfo.hProcess, 0) == WAIT_TIMEOUT;
    if (assigned) assigned = ResumeThread(processInfo.hThread) != static_cast<DWORD>(-1)
        && WaitForSingleObject(processInfo.hProcess, 5000) == WAIT_OBJECT_0;
    if (!assigned) TerminateProcess(processInfo.hProcess, 1);
    CloseHandle(processInfo.hThread); CloseHandle(processInfo.hProcess); CloseHandle(job);
    return assigned;
}

bool verifyFakeBootstrapAdapter() {
    bool checked = false, launched = false, stopped = false, adapted = false;
    t7::Bootstrap::TestAdapter adapter;
    adapter.check = [&checked](const t7::fs::path&, const t7::Config&, const t7::DiagnosticLogCallback&) {
        checked = true;
    };
    adapter.launch = [&launched](const t7::fs::path&, const t7::Config&, const t7::DiagnosticLogCallback&,
                                 const std::function<bool()>&, const std::function<void()>& adapting) {
        launched = true;
        if (adapting) adapting();
    };
    adapter.running = [&launched, &stopped] { return launched && !stopped; };
    adapter.stop = [&stopped] { stopped = true; };
    t7::Bootstrap bootstrap(std::move(adapter));
    t7::Config config;
    bootstrap.check(L"synthetic-client", config);
    bool cancelled = false;
    try {
        bootstrap.launch(L"synthetic-client", config, {}, [] { return true; }, {});
    } catch (const std::exception&) {
        cancelled = true;
    }
    if (!cancelled || launched) return false;
    bootstrap.launch(L"synthetic-client", config, {}, {}, [&adapted] { adapted = true; });
    if (!checked || !launched || !adapted || !bootstrap.running()) return false;
    bootstrap.stop();
    return stopped && !bootstrap.running();
}

std::string journalIndex(const t7::fs::path& root) {
    std::string result;
    std::error_code error;
    if (!t7::fs::exists(root, error)) return result;
    for (const auto& entry : t7::fs::recursive_directory_iterator(root, error)) {
        if (error) break;
        if (entry.is_regular_file(error) && entry.path().extension() == L".jsonl") {
            std::ifstream input(entry.path());
            result.assign((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
            if (!result.empty()) break;
        }
    }
    return result;
}

bool verifyWireCapture(const t7::fs::path& packageRoot, bool capture) {
    auto writableRoot = t7::fs::temp_directory_path() /
        (L"T7-Rekindle-wire-capture-" + std::to_wstring(GetCurrentProcessId()) +
         (capture ? L"-on" : L"-off"));
    std::error_code cleanup;
    t7::fs::remove_all(writableRoot, cleanup);
    t7::Config config;
    config.captureWire = capture;
    t7::Server server;
    server.start(config, packageRoot, writableRoot);
    SOCKET client = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (client == INVALID_SOCKET) {
        server.stop();
        t7::fs::remove_all(writableRoot, cleanup);
        return false;
    }
    sockaddr_in address{}; address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(server.boundConfig().ports[0]);
    bool connected = connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0;
    const unsigned char incomplete[] = {0x55, 0x0e, 0x05, 0x00};
    bool sent = connected && send(client, reinterpret_cast<const char*>(incomplete), sizeof(incomplete), 0)
        == static_cast<int>(sizeof(incomplete));
    Sleep(200);
    closesocket(client);
    server.stop();
    auto index = journalIndex(writableRoot);
    uintmax_t stored = 0;
    for (const auto& entry : t7::fs::recursive_directory_iterator(writableRoot))
        if (entry.is_regular_file() && entry.path().extension() == L".bin") stored += entry.file_size();
    t7::fs::remove_all(writableRoot, cleanup);
    if (!connected || !sent || index.find("\"direction\":\"C2S\"") == std::string::npos) return false;
    auto record = index.substr(index.find("\"direction\":\"C2S\""));
    record = record.substr(0, record.find('\n'));
    auto marker = record.find("\"wireLength\":");
    if (marker == std::string::npos) return false;
    marker += std::string("\"wireLength\":").size();
    auto end = record.find_first_not_of("0123456789", marker);
    size_t length = 0;
    try { length = static_cast<size_t>(std::stoull(record.substr(marker, end - marker))); }
    catch (...) { return false; }
    return length == sizeof(incomplete)
        && record.find(capture ? "\"captured\":true" : "\"captured\":false") != std::string::npos
        && (capture ? stored == sizeof(incomplete)
                    : stored == 0 && record.find("\"wireSha256\":null") != std::string::npos);
}

bool verifySyntheticAuth(const t7::fs::path& packageRoot) {
    auto writableRoot = t7::fs::temp_directory_path() /
        (L"T7-Rekindle-auth-test-" + std::to_wstring(GetCurrentProcessId()));
    std::error_code cleanup;
    t7::fs::remove_all(writableRoot, cleanup);
    t7::Config config;
    config.runtimeMovement = false; // Transport/world delivery does not require client VFS assets.
    t7::Server server;
    server.start(config, packageRoot, writableRoot);
    std::vector<SOCKET> clients;
    const unsigned char auth[29] = {
        0x55, 0x0e, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03,
        0x00, 0x00, 0x00, 0x00, 0x00
    };
    bool valid = true;
    for (auto port : server.boundConfig().ports) {
        SOCKET client = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (client == INVALID_SOCKET) { valid = false; break; }
        int timeout = 2000;
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));
        sockaddr_in address{}; address.sin_family = AF_INET;
        address.sin_addr.s_addr = htonl(INADDR_LOOPBACK); address.sin_port = htons(port);
        if (connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0
            || send(client, reinterpret_cast<const char*>(auth), 7, 0) != 7
            || send(client, reinterpret_cast<const char*>(auth + 7), sizeof(auth) - 7, 0) != sizeof(auth) - 7) {
            closesocket(client); valid = false; break;
        }
        unsigned char reply[48]{}; int received = 0;
        while (received < static_cast<int>(sizeof(reply))) {
            auto count = recv(client, reinterpret_cast<char*>(reply) + received,
                static_cast<int>(sizeof(reply)) - received, 0);
            if (count <= 0) break;
            received += count;
        }
        if (received != static_cast<int>(sizeof(reply)) || reply[0] != 0x55 || reply[1] != 0x0e) {
            closesocket(client); valid = false; break;
        }
        clients.push_back(client);
    }
    Sleep(100);
    auto snapshot = server.snapshot();
    size_t authenticated = 0;
    for (const auto& client : snapshot.clients) if (client.authenticated) ++authenticated;
    valid = valid && authenticated == clients.size() && clients.size() == 3;
    for (unsigned frame = 0; frame < 3; ++frame) {
        t7::WorldSnapshot world;
        world.ready = true; world.instanceStartedAt = 100; world.sampledAt = GetTickCount64();
        if (!server.needsWorld()) { valid = false; break; }
        server.updateWorld(world);
        const auto deadline = GetTickCount64() + 2000;
        while (!server.needsWorld() && GetTickCount64() < deadline) Sleep(1);
        const auto error = server.snapshot().error;
        if (!error.empty()) std::cerr << "world delivery error: " << error << '\n';
        valid = valid && server.needsWorld() && error.empty();
    }
    for (auto client : clients) closesocket(client);
    server.stop();
    auto index = journalIndex(writableRoot);
    size_t worldEvents = 0, worldAt = 0;
    while ((worldAt = index.find(" world command=0", worldAt)) != std::string::npos) { ++worldEvents; ++worldAt; }
    valid = valid && worldEvents == 3 && !server.needsWorld();
    if (worldEvents != 3) std::cerr << "world event count: " << worldEvents << '\n';
    auto outbound = index.find("\"direction\":\"S2C\"");
    if (outbound == std::string::npos) valid = false;
    else {
        auto record = index.substr(outbound, index.find('\n', outbound) - outbound);
        auto length = record.find("\"wireLength\":");
        valid = valid && record.find("\"captured\":false") != std::string::npos
            && record.find("\"wireSha256\":null") != std::string::npos
            && length != std::string::npos && std::stoull(record.substr(length + 13)) > 0;
    }
    t7::fs::remove_all(writableRoot, cleanup);
    return valid;
}

bool waitForOperation(const std::shared_ptr<t7::bridge::Session>& session,
                      uint64_t operationId, uint32_t expectedStatus) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (std::chrono::steady_clock::now() < deadline) {
        t7::bridge::Operation operation;
        if (session->operation(operationId, operation) != T7NB_OK) return false;
        if (operation.status == T7NB_OPERATION_SUCCEEDED
            || operation.status == T7NB_OPERATION_CANCELLED
            || operation.status == T7NB_OPERATION_FAILED) {
            return operation.status == expectedStatus;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return false;
}

bool verifySessionAssembly(const t7::fs::path& packageRoot) {
    struct FakeState {
        bool launched = false;
        bool stopped = false;
        bool clientRunning = false;
    };
    auto state = std::make_shared<FakeState>();
    t7::Bootstrap::TestAdapter adapter;
    adapter.launch = [state](
        const t7::fs::path&, const t7::Config& config,
        const t7::DiagnosticLogCallback& log,
        const std::function<bool()>& cancelled,
        const std::function<void()>& adapting) {
        if (!config.ports[0] || !config.ports[1] || !config.ports[2])
            throw std::runtime_error("fake adapter received unbound endpoint");
        if (cancelled && cancelled()) throw std::runtime_error("fake session launch cancelled");
        state->launched = true;
        state->clientRunning = true;
        if (adapting) adapting();
        if (log) log(t7::DiagnosticLogLevel::Info, "synthetic client adapter launched");
    };
    adapter.running = [state] { return state->clientRunning; };
    adapter.stop = [state] {
        state->stopped = true;
        state->clientRunning = false;
    };

    auto session = t7::bridge::Session::createForTest(
        t7::utf8(packageRoot.wstring()), std::move(adapter));
    const auto clientDirectory = t7::utf8(
        (packageRoot / "synthetic-client" / "Bin").wstring());
    uint64_t startId = 0;
    if (session->submit(T7NB_OPERATION_START, clientDirectory, startId) != T7NB_OK
        || !waitForOperation(session, startId, T7NB_OPERATION_SUCCEEDED)
        || !state->launched) {
        session->requestClose();
        session->waitForWorkerForTest(std::chrono::seconds(5));
        return false;
    }

    t7::bridge::Snapshot running;
    if (session->cancel(startId) != T7NB_NOT_READY || session->snapshot(running) != T7NB_OK
        || running.state != T7NB_STATE_RUNNING
        || (running.flags & 1u) != 0
        || !running.ports[0] || !running.ports[1] || !running.ports[2]
        || running.ports[0] == running.ports[1]
        || running.ports[1] == running.ports[2]) {
        session->requestClose();
        session->waitForWorkerForTest(std::chrono::seconds(5));
        return false;
    }

    uint64_t stopId = 0;
    if (session->submit(T7NB_OPERATION_STOP, {}, stopId) != T7NB_OK
        || !waitForOperation(session, stopId, T7NB_OPERATION_SUCCEEDED)
        || !state->stopped) {
        session->requestClose();
        session->waitForWorkerForTest(std::chrono::seconds(5));
        return false;
    }
    t7::bridge::Snapshot idle;
    const bool valid = session->snapshot(idle) == T7NB_OK
        && idle.state == T7NB_STATE_IDLE
        && (idle.flags & 1u) != 0
        && idle.ports[0] == 0 && idle.ports[1] == 0 && idle.ports[2] == 0;
    session->requestClose();
    return valid && session->waitForWorkerForTest(std::chrono::seconds(5));
}
}

int wmain(int argc, wchar_t** argv) {
    const auto dispatched = t7::detail::TimePoint{};
    const auto committed = dispatched + std::chrono::milliseconds(231);
    for (const auto* id : {"1:battle-action-phase", "1:bot-action-phase-20", "1:bot-release", "1:combat-hurt-2"}) {
        if (!t7::detail::animationTimer(id)
            || t7::detail::timerDeadline(id, dispatched, committed, 640) != committed + std::chrono::milliseconds(640)) {
            std::cerr << "AI callback time shortened a native animation deadline\n";
            return 1;
        }
    }
    for (const auto* id : {"", "1:actors-pulse", "1:actors-decision", "1:bot-think-20", "1:bot-move"}) {
        if (t7::detail::animationTimer(id)
            || t7::detail::timerDeadline(id, dispatched, committed, 50) != dispatched + std::chrono::milliseconds(50)) {
            std::cerr << "decision/movement timer was promoted to an animation deadline\n";
            return 1;
        }
    }
    if (argc == 3 && std::wstring(argv[1]) == L"--world") {
        WSADATA winsock{};
        if (WSAStartup(MAKEWORD(2, 2), &winsock) != 0) return 3;
        int result = 0;
        try { if (!verifySyntheticAuth(argv[2])) result = 20; }
        catch (const std::exception& error) { std::cerr << error.what() << '\n'; result = 5; }
        WSACleanup();
        return result;
    }
    if (argc == 3 && std::wstring(argv[1]) == L"--audio-session")
        return verifyAudioSession(t7::fs::path(argv[2])) ? 0 : 1;
    if (argc == 2 && std::wstring(argv[1]) == L"--frame-hooks")
        return verifyGraphicsHookExecution() ? 0 : 1;
    if (argc == 2 && std::wstring(argv[1]) == L"--movement")
        return verifyMovementOverlay() && verifyMovementHookExecution() ? 0 : 1;
    if (argc != 2) {
        std::wcerr << L"usage: T7.RuntimeTests.exe PACKAGE_ROOT\n";
        return 2;
    }
    WSADATA winsock{};
    if (WSAStartup(MAKEWORD(2, 2), &winsock) != 0) return 3;
    int result = 0;
    try {
        if (!verifyServerEventOrder(t7::fs::path(argv[1]))) result = 42;
        if (!verifyFakeBootstrapAdapter()) result = 21;
        if (!result && !verifyLobbyNews()) result = 33;
        if (!verifyJobAssignmentOrder()) result = 15;
        // The embedded interpreter has one process-wide owner.  A second
        // owner is rejected while the first is alive, then a fresh owner can
        // be created after deterministic close/finalize.
        auto ownerRoot = t7::fs::temp_directory_path() /
            (L"T7-Rekindle-python-owner-" + std::to_wstring(GetCurrentProcessId()));
        t7::fs::remove_all(ownerRoot);
        auto invalidOwnerRoot = t7::fs::temp_directory_path() /
            (L"T7-Rekindle-python-owner-invalid-" + std::to_wstring(GetCurrentProcessId()));
        t7::fs::remove_all(invalidOwnerRoot);
        {
            std::ofstream invalidCache(invalidOwnerRoot);
            invalidCache << "not a directory";
        }
        bool initializationFailed = false;
        try { t7::PythonHost invalid(argv[1], invalidOwnerRoot); }
        catch (const std::exception&) { initializationFailed = true; }
        if (!initializationFailed) result = 22;
        {
            t7::PythonHost first(argv[1], ownerRoot);
            t7::Config worldConfig;
            worldConfig.clientDirectory = t7::fs::path(argv[1]) / "synthetic-client" / "Bin";
            t7::WorldSnapshot world;
            world.ready = true; world.sampledAt = 1200; world.instanceStartedAt = 1000;
            world.player = {1.25f, 2.5f, 3.75f}; world.bot = {4.25f, 5.5f, 6.75f};
              world.playerHeading = 90.f; world.botHeading = -90.f;
            world.actors[0].terrain[0].ready = world.actors[0].terrain[0].hit = true;
            world.actors[0].terrain[0].material = 4;
            world.actors[0].terrain[0].normal = {-1,0,0};
            world.actors[19].ready = true; world.actors[19].position = {12.25f, 8.f, .5f};
            world.actors[19].targetId = 11;
            auto& targetTerrain = world.actors[19].targetTerrain;
            targetTerrain[1].ready = targetTerrain[1].hit = true;
            targetTerrain[1].material = 3; targetTerrain[1].position = {13.25f, 8.f, 1.5f};
            targetTerrain[2].ready = true; // A separate clear segment must not inherit the first hit.
            auto& blade = world.actors[19].blade;
            blade.ready = true; blade.spanMs = 50; blade.length = 1.03f;
            blade.base = {12,8,1}; blade.tip = {13.03f,8,1};
            blade.contacts[2].contact = targetTerrain[1];
            blade.contacts[2].targetId = 2; blade.contacts[2].time = .25f; blade.contacts[2].along = .75f;
            world.contactsReady = true; world.contactBatchId = 7; world.contactFrames = 17;
            t7::BladeContactEvent contactEvent;
            contactEvent.actorId = 20; contactEvent.entity = 123; contactEvent.sequence = 31;
            contactEvent.state = 238; contactEvent.hit = blade.contacts[2]; contactEvent.phaseAt = 900;
            contactEvent.occurredAt = 1187.5; contactEvent.targetState = 223; contactEvent.targetEntity = 456;
            world.contacts.push_back(contactEvent);
            const auto context = first.context(worldConfig, 1250, {1}, world);
            for (const char* field : {"\"contactJournal\":[\"dict\",", "\"batchId\":[\"int\",7]",
                "\"frames\":[\"int\",17]", "\"occurredAt\":[\"float\",1187.5]", "\"phaseAt\":[\"int\",900]",
                "\"entity\":[\"int\",123]", "\"targetEntity\":[\"int\",456]", "\"targetState\":[\"int\",223]"})
                if (context.find(field) == std::string::npos) throw std::runtime_error("world context lost contact journal identity/time");
            for (const char* field : {"\"ready\":[\"bool\",true]", "\"sampledAt\":[\"int\",1200]",
                "\"instanceStartedAt\":[\"int\",1000]", "[\"float\",1.25]", "[\"float\",6.75]",
                  "\"playerHeading\":[\"float\",90.0]", "\"botHeading\":[\"float\",-90.0]",
                  "\"clientDirectory\":[\"str\",", "synthetic-client", "\"terrain\":[\"dict\",",
                    "\"material\":[\"int\",4]", "\"hit\":[\"bool\",true]", "\"normal\":[\"list\",",
                    "\"blades\":[\"dict\",", "\"spanMs\":[\"int\",50]", "\"contacts\":[\"list\",",
                    "\"base\":[\"list\",", "\"tip\":[\"list\",", "\"time\":[\"float\",0.25]",
                    "\"along\":[\"float\",0.75]"}) {
                if (context.find(field) == std::string::npos) throw std::runtime_error("world Python context lost a field");
            }
            for (const char* field : {"\"actors\":[\"dict\",", "\"20\":[\"dict\",", "[\"float\",12.25]",
                                     "\"targetId\":[\"int\",11]"})
                if (context.find(field) == std::string::npos) throw std::runtime_error("world context lost multi-actor identity");
            const auto prefix = "\"targetTerrain\":[\"dict\",{\"2\":[\"dict\",";
            const auto targetsAt = context.find(prefix);
            if (targetsAt == std::string::npos
                || context.find("\"material\":[\"int\",3]", targetsAt) == std::string::npos
                || context.find("[\"float\",13.25]", targetsAt) == std::string::npos
                || context.find("\"3\":[\"dict\",{\"ready\":[\"bool\",true],\"hit\":[\"bool\",false]", targetsAt) == std::string::npos
                || context.find("\"targetTerrain\":[\"dict\",{}]") == std::string::npos)
                throw std::runtime_error("world context lost per-target terrain results");
            bool rejected = false;
            try { t7::PythonHost second(argv[1], ownerRoot); }
            catch (const std::exception&) { rejected = true; }
            if (!rejected) result = 16;
        }
        t7::fs::remove_all(ownerRoot);
        t7::fs::remove_all(invalidOwnerRoot);
        t7::Config config;
        config.bindAddress = "127.0.0.1";
        config.advertisedAddress = "127.0.0.1";
        config.ports[0] = config.ports[1] = config.ports[2] = 0;
        t7::Server server;
        server.start(config, argv[1]);
        const auto actual = server.boundConfig();
        if (!server.running() || !actual.ports[0] || !actual.ports[1] || !actual.ports[2]
            || actual.ports[0] == actual.ports[1] || actual.ports[1] == actual.ports[2]) {
            result = 4;
        }
        for (auto port : actual.ports) {
            SOCKET probe = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
            if (probe == INVALID_SOCKET) { result = 11; continue; }
            sockaddr_in address{}; address.sin_family = AF_INET;
            address.sin_addr.s_addr = htonl(INADDR_LOOPBACK); address.sin_port = htons(port);
            if (connect(probe, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) result = 12;
            closesocket(probe);
        }
        server.stop();
        const auto released = server.boundConfig();
        if (released.ports[0] || released.ports[1] || released.ports[2]) result = 24;

        // Cancellation is checked while readiness is waiting, and all
        // listeners must be released before start() reports the cancellation.
        bool cancellationObserved = false;
        try {
            server.start(config, argv[1], {}, [&cancellationObserved] {
                cancellationObserved = true;
                return true;
            });
            result = 9;
            server.stop();
        } catch (const std::exception& error) {
            if (!cancellationObserved || server.running()
                || std::string(error.what()).find("cancelled") == std::string::npos) result = 10;
        }

        // A second lifecycle uses the same object and confirms that Python,
        // listener ownership and the readiness gate are restartable.
        server.start(config, argv[1]);
        if (!server.running()) result = 6;
        server.stop();

        // A partial fixed-port bind must release the first listener when the
        // second listener is occupied.
        SOCKET reservations[3]{INVALID_SOCKET, INVALID_SOCKET, INVALID_SOCKET};
        sockaddr_in addresses[3]{};
        for (int i = 0; i != 3; ++i) {
            reservations[i] = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
            if (reservations[i] == INVALID_SOCKET) throw std::runtime_error("test reservation socket failed");
            addresses[i].sin_family = AF_INET; addresses[i].sin_addr.s_addr = htonl(INADDR_LOOPBACK); addresses[i].sin_port = 0;
            if (bind(reservations[i], reinterpret_cast<sockaddr*>(&addresses[i]), sizeof(addresses[i])) != 0)
                throw std::runtime_error("test reservation bind failed");
            int size = sizeof(addresses[i]);
            if (getsockname(reservations[i], reinterpret_cast<sockaddr*>(&addresses[i]), &size) != 0)
                throw std::runtime_error("test reservation getsockname failed");
        }
        t7::Config fixed = config;
        fixed.ports[0] = ntohs(addresses[0].sin_port);
        fixed.ports[1] = ntohs(addresses[1].sin_port);
        fixed.ports[2] = ntohs(addresses[2].sin_port);
        closesocket(reservations[0]); reservations[0] = INVALID_SOCKET;
        closesocket(reservations[2]); reservations[2] = INVALID_SOCKET;
        bool failed = false;
        try { server.start(fixed, argv[1]); }
        catch (const std::exception&) { failed = true; }
        if (!failed) { server.stop(); result = 7; }
        closesocket(reservations[1]); reservations[1] = INVALID_SOCKET;
        server.start(fixed, argv[1]);
        if (!server.running()) result = 8;
        server.stop();

        // Product sessions keep wire payloads disabled by default; a local
        // diagnostic fixture can opt in without changing the metadata log.
        if (!verifyWireCapture(argv[1], false)) result = 18;
        if (!verifyWireCapture(argv[1], true)) result = 19;
        if (!verifySyntheticAuth(argv[1])) result = 20;
        // Exercise the same Session assembly used by the C ABI while keeping
        // the fake client adapter confined to this native test host.
        if (!verifySessionAssembly(t7::fs::path(argv[1]))) result = 23;
        if (!verifyGraphicsSession(t7::fs::path(argv[1]))) result = 38;
        if (!verifyResourcePreflight(t7::fs::path(argv[1]))) result = 43;
        if (!verifyAudioSession(t7::fs::path(argv[1]))) result = 39;
        if (!verifyJournalContracts(t7::fs::path(argv[1]))) result = 25;
        if (!verifyDiagnosticLogs(t7::fs::path(argv[1]))) result = 37;
        if (!verifyNamedSessionExit(t7::fs::path(argv[1]), 0, true)) result = 28;
        if (!verifyNamedSessionExit(t7::fs::path(argv[1]), 7, false)) result = 29;
        if (!verifyNamedSessionExit(t7::fs::path(argv[1]), 4660, true)) result = 30;
        if (!verifyNamedSessionExit(t7::fs::path(argv[1]), 0xC0000005, false)) result = 31;
        if (!verifyWindowCloseCleanup(t7::fs::path(argv[1]), false)) result = 40;
        if (!verifyWindowCloseCleanup(t7::fs::path(argv[1]), true)) result = 41;
        if (!verifyFailureCleanup(t7::fs::path(argv[1]), true)) result = 26;
        if (!verifyFailureCleanup(t7::fs::path(argv[1]), false)) result = 27;
        if (!verifyOperationPublication(t7::fs::path(argv[1]), T7NB_OPERATION_SUCCEEDED)) result = 32;
        if (!verifyOperationPublication(t7::fs::path(argv[1]), T7NB_OPERATION_FAILED)) result = 33;
        if (!verifyOperationPublication(t7::fs::path(argv[1]), T7NB_OPERATION_CANCELLED)) result = 34;
        if (!verifyMovementOverlay()) result = 35;
        if (!verifyGraphicsHookExecution() || !verifyMovementHookExecution()) result = 36;

        // Journal writes are bounded and flushed on destruction without
        // exposing a second product process.  This is the disk-side contract
        // behind the bridge's in-memory cursor.
        auto journalRoot = t7::fs::temp_directory_path() /
            (L"T7-Rekindle-journal-test-" + std::to_wstring(GetCurrentProcessId()));
        std::error_code journalCleanup;
        t7::fs::remove_all(journalRoot, journalCleanup);
        {
            t7::Journal journal(journalRoot);
            for (int i = 0; i != 1100; ++i)
                journal.add(t7::DiagnosticLogLevel::Info, "TEST", static_cast<uint64_t>(i), "synthetic", "record", {1, 2, 3});
            if (journal.lines().size() > 1000) result = 13;
        }
        std::ifstream index(journalRoot / "frames-1.jsonl");
        std::string indexText((std::istreambuf_iterator<char>(index)), std::istreambuf_iterator<char>());
        if (!index || indexText.find("\"wireLength\":3") == std::string::npos) result = 14;
        t7::fs::remove_all(journalRoot, journalCleanup);

        auto rotationRoot = t7::fs::temp_directory_path() /
            (L"T7-Rekindle-journal-rotation-" + std::to_wstring(GetCurrentProcessId()));
        t7::fs::remove_all(rotationRoot, journalCleanup);
        {
            t7::Journal journal(rotationRoot, 1024, 2);
            for (int i = 0; i != 5; ++i)
                journal.add(t7::DiagnosticLogLevel::Info, "TEST", static_cast<uint64_t>(i), "synthetic", "rotation", t7::Bytes(700, 7));
        }
        if (t7::fs::exists(rotationRoot / "frames-1.bin")
            || !t7::fs::exists(rotationRoot / "frames-4.bin")
            || !t7::fs::exists(rotationRoot / "frames-5.jsonl")) result = 17;
        t7::fs::remove_all(rotationRoot, journalCleanup);
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        result = 5;
    }
    WSACleanup();
    return result;
}
