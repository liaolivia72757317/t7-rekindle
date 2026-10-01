#include "../../src/Runtime/bridge/Session.h"
#include <atomic>
#include <chrono>
#include <thread>
#include <iostream>

bool verifyNamedSessionExit(const t7::fs::path& packageRoot, DWORD exitCode, bool normalExit) {
    auto alive = std::make_shared<std::atomic<bool>>(false);
    auto namePassed = std::make_shared<std::atomic<bool>>(false);
    t7::Bootstrap::TestAdapter adapter;
    adapter.launch = [namePassed, alive](const t7::fs::path&, const t7::Config& config,
        const std::function<void(std::string)>&, const std::function<bool()>&, const std::function<void()>&) {
        *namePassed = config.playerName == u8"重燃玩家";
        *alive = true;
    };
    adapter.running = [alive] { return alive->load(); };
    adapter.exitCode = [exitCode] { return exitCode; };
    auto session = t7::bridge::Session::createForTest(t7::utf8(packageRoot.wstring()), std::move(adapter));
    uint64_t operationId = 0;
    bool valid = session->submit(T7NB_OPERATION_START, t7::utf8((packageRoot / "synthetic-client").wstring()),
        operationId, u8"重燃玩家") == T7NB_OK;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    t7::bridge::Snapshot snapshot;
    do {
        session->snapshot(snapshot);
        if (snapshot.state == T7NB_STATE_RUNNING) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    } while (std::chrono::steady_clock::now() < deadline);
    valid = valid && *namePassed && snapshot.state == T7NB_STATE_RUNNING;
    *alive = false;
    do {
        session->snapshot(snapshot);
        if (snapshot.flags & 1u) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    } while (std::chrono::steady_clock::now() < deadline);
    valid = valid && snapshot.state == (normalExit ? T7NB_STATE_IDLE : T7NB_STATE_FAILED)
        && snapshot.errorCode == (normalExit ? 0u : T7NB_ERROR_CLIENT_EXIT)
        && snapshot.phase == (normalExit ? "client-exited" : "client-exited-error") && (snapshot.flags & 1u);
    if (!valid) std::cerr << "named exit check: expected=" << exitCode << " state=" << snapshot.state
        << " error=" << snapshot.errorCode << " phase=" << snapshot.phase << " flags=" << snapshot.flags
        << " namePassed=" << namePassed->load() << '\n';
    session->requestClose();
    return session->waitForWorkerForTest(std::chrono::seconds(5)) && valid;
}

bool verifyFailureCleanup(const t7::fs::path& packageRoot, bool cancel) {
    struct State {
        std::weak_ptr<t7::bridge::Session> session;
        std::atomic<bool> stopObserved{false};
        std::atomic<bool> correctPhase{false};
        std::atomic<bool> launched{false};
    };
    auto state = std::make_shared<State>();
    t7::Bootstrap::TestAdapter adapter;
    adapter.launch = [state, cancel](const t7::fs::path&, const t7::Config&,
        const std::function<void(std::string)>&, const std::function<bool()>&, const std::function<void()>&) {
        state->launched = true;
        if (cancel) {
            auto session = state->session.lock();
            t7::bridge::Snapshot snapshot;
            session->snapshot(snapshot);
            if (session->cancel(snapshot.operationId) != T7NB_OK)
                throw std::runtime_error("test cancellation was not accepted");
        }
    };
    adapter.running = [state]() -> bool {
        if (!state->launched) return false;
        throw std::runtime_error("synthetic owned client failure");
    };
    adapter.stop = [state, cancel] {
        auto session = state->session.lock();
        if (!session) return;
        t7::bridge::Snapshot snapshot;
        session->snapshot(snapshot);
        state->correctPhase = snapshot.state == T7NB_STATE_STOPPING_CLIENT
            && snapshot.phase == "client-stop" && (snapshot.flags & 1u) == 0
            && snapshot.errorCode == (cancel ? T7NB_ERROR_CANCELLED : T7NB_ERROR_OPERATION);
        state->stopObserved = true;
    };
    auto session = t7::bridge::Session::createForTest(t7::utf8(packageRoot.wstring()), std::move(adapter));
    state->session = session;
    uint64_t operationId = 0;
    bool valid = session->submit(T7NB_OPERATION_START, t7::utf8((packageRoot / "synthetic-client").wstring()), operationId) == T7NB_OK;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    t7::bridge::Snapshot snapshot;
    do {
        session->snapshot(snapshot);
        if (state->stopObserved && (snapshot.flags & 1u)) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    } while (std::chrono::steady_clock::now() < deadline);
    valid = valid && state->stopObserved && state->correctPhase
        && snapshot.state == (cancel ? T7NB_STATE_IDLE : T7NB_STATE_FAILED)
        && snapshot.ports[0] == 0 && snapshot.ports[1] == 0 && snapshot.ports[2] == 0;
    session->requestClose();
    return session->waitForWorkerForTest(std::chrono::seconds(5)) && valid;
}
