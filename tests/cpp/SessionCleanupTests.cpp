#include "../../src/Runtime/bridge/Session.h"
#include <atomic>
#include <chrono>
#include <future>
#include <thread>
#include <iostream>

bool verifyNamedSessionExit(const t7::fs::path& packageRoot, DWORD exitCode, bool normalExit) {
    auto alive = std::make_shared<std::atomic<bool>>(false);
    auto namePassed = std::make_shared<std::atomic<bool>>(false);
    const bool skipStartupAnimation = !normalExit;
    t7::Bootstrap::TestAdapter adapter;
    adapter.launch = [namePassed, alive, skipStartupAnimation](const t7::fs::path&, const t7::Config& config,
        const std::function<void(std::string)>&, const std::function<bool()>&, const std::function<void()>&) {
        *namePassed = config.playerName == u8"重燃玩家" && config.skipStartupAnimation == skipStartupAnimation;
        *alive = true;
    };
    adapter.running = [alive] { return alive->load(); };
    adapter.exitCode = [exitCode] { return exitCode; };
    auto session = t7::bridge::Session::createForTest(t7::utf8(packageRoot.wstring()), std::move(adapter));
    uint64_t operationId = 0;
    bool valid = session->submit(T7NB_OPERATION_START, t7::utf8((packageRoot / "synthetic-client").wstring()),
        operationId, u8"重燃玩家", skipStartupAnimation) == T7NB_OK;
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

bool verifyWindowCloseCleanup(const t7::fs::path& packageRoot, bool failCleanup) {
    struct State {
        std::atomic<bool> alive{false}, closed{false}, timedOut{false};
        std::atomic<unsigned> stops{0};
        std::promise<void> stopping, resume;
        std::shared_future<void> resumed = resume.get_future().share();
    };
    auto state = std::make_shared<State>();
    auto stopping = state->stopping.get_future();
    t7::Bootstrap::TestAdapter adapter;
    adapter.launch = [state](const t7::fs::path&, const t7::Config&,
        const std::function<void(std::string)>&, const std::function<bool()>&, const std::function<void()>&) {
        state->alive = true;
    };
    adapter.running = [state] { return state->alive.load(); };
    adapter.windowClosed = [state] { return state->closed.load(); };
    adapter.exitCode = [state]() -> DWORD {
        if (state->alive) throw std::runtime_error("exit code requested before window-close cleanup");
        return 0;
    };
    adapter.stop = [state, failCleanup] {
        if (++state->stops == 1) {
            state->stopping.set_value();
            state->timedOut = state->resumed.wait_for(std::chrono::seconds(5)) != std::future_status::ready;
            if (failCleanup) throw std::runtime_error("synthetic window-close cleanup failure");
        }
        state->alive = false;
    };
    auto session = t7::bridge::Session::createForTest(t7::utf8(packageRoot.wstring()), std::move(adapter));
    const auto directory = t7::utf8((packageRoot / "synthetic-client").wstring());
    t7::bridge::Snapshot snapshot;
    const auto waitState = [&](uint32_t expected) {
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        do {
            session->snapshot(snapshot);
            if (snapshot.state == expected && snapshot.operation == T7NB_OPERATION_NONE) return true;
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        } while (std::chrono::steady_clock::now() < deadline);
        return false;
    };
    uint64_t operationId = 0;
    bool valid = session->submit(T7NB_OPERATION_START, directory, operationId) == T7NB_OK;
    valid = waitState(T7NB_STATE_RUNNING) && valid;
    state->closed = true;
    const bool detected = stopping.wait_for(std::chrono::seconds(2)) == std::future_status::ready;
    session->snapshot(snapshot);
    valid = valid && detected && state->alive && snapshot.state == T7NB_STATE_STOPPING_CLIENT
        && (snapshot.flags & 1u) == 0;
    if (detected) {
        uint64_t rejected = 0;
        valid = session->submit(T7NB_OPERATION_START, directory, rejected) == T7NB_BUSY && valid;
        valid = session->submit(T7NB_OPERATION_CHECK, directory, rejected) == T7NB_BUSY && valid;
    }
    state->resume.set_value();
    valid = waitState(failCleanup ? T7NB_STATE_FAILED_CLEANING : T7NB_STATE_IDLE) && valid;
    if (failCleanup) {
        valid = valid && state->alive && (snapshot.flags & 1u) == 0
            && snapshot.errorCode == T7NB_ERROR_CLEANUP && snapshot.phase == "failed-cleaning";
        valid = session->submit(T7NB_OPERATION_STOP, {}, operationId) == T7NB_OK && valid;
        valid = waitState(T7NB_STATE_IDLE) && valid;
    } else {
        valid = valid && snapshot.phase == "client-window-closed";
    }
    valid = valid && !state->alive && !state->timedOut && snapshot.errorCode == 0 && (snapshot.flags & 1u)
        && snapshot.ports[0] == 0 && snapshot.ports[1] == 0 && snapshot.ports[2] == 0;
    if (!valid) std::cerr << "window-close cleanup: detected=" << detected << " state=" << snapshot.state
        << " error=" << snapshot.errorCode << " phase=" << snapshot.phase << " flags=" << snapshot.flags << '\n';
    session->requestClose();
    return session->waitForWorkerForTest(std::chrono::seconds(5)) && valid;
}
