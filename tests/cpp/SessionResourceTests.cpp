#include "../../src/Runtime/bridge/Session.h"
#include <iostream>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
t7r::bridge::Operation waitFor(const std::shared_ptr<t7r::bridge::Session>& session, uint64_t id) {
    t7r::bridge::Operation result;
    const auto deadline = GetTickCount64() + 10000;
    do {
        require(session->operation(id, result) == T7RNB_OK, "operation lookup");
        if (result.status >= T7RNB_OPERATION_SUCCEEDED) return result;
        Sleep(5);
    } while (GetTickCount64() < deadline);
    throw std::runtime_error("operation timed out");
}
}

bool verifyResourcePreflight(const t7r::fs::path& packageRoot) {
    std::shared_ptr<t7r::bridge::Session> session;
    try {
        bool launched = false;
        auto entered = std::make_shared<std::atomic<bool>>(false);
        auto proceed = std::make_shared<std::atomic<bool>>(true);
        t7r::Bootstrap::TestAdapter adapter;
        adapter.check = [entered, proceed](const auto&, const auto&, const auto&) {
            entered->store(true);
            while (!proceed->load()) Sleep(1);
        };
        adapter.launch = [&launched](const auto&, const t7r::Config& config, const auto&, const auto&, const auto&) {
            require(config.ports[0] == 21001 && config.ports[1] == 21002 && config.ports[2] == 21003,
                "native lifecycle lost Rust endpoints");
            require(config.combatProfiles.size() == 1 && config.combatProfiles[0].heroId == 1101,
                "native lifecycle lost Rust combat profiles");
            launched = true;
        };
        session = t7r::bridge::Session::createForTest(t7r::utf8(packageRoot.wstring()), adapter);
        const auto directory = t7r::utf8((packageRoot / "synthetic-client/Bin").wstring());
        uint64_t id = 0;
        require(session->submit(T7RNB_OPERATION_START, directory, id) == T7RNB_INVALID_ARGUMENT,
            "start without a Rust gateway accepted");
        require(session->submit(T7RNB_OPERATION_CHECK, directory, id) == T7RNB_OK, "native check submit");
        require(waitFor(session, id).status == T7RNB_OPERATION_SUCCEEDED && !launched, "preflight launched a client");
        t7r::bridge::Snapshot snapshot;
        session->snapshot(snapshot);
        require(!snapshot.ports[0] && !snapshot.ports[1] && !snapshot.ports[2], "preflight created listeners");
        entered->store(false); proceed->store(false);
        require(session->submit(T7RNB_OPERATION_CHECK, directory, id) == T7RNB_OK, "cancel check submit");
        const auto deadline = GetTickCount64() + 5000;
        while (!entered->load() && GetTickCount64() < deadline) Sleep(1);
        const bool didEnter = entered->load();
        const auto cancelled = session->cancel(id);
        proceed->store(true);
        require(didEnter && cancelled == T7RNB_OK, "preflight cancellation rejected");
        require(waitFor(session, id).status == T7RNB_OPERATION_CANCELLED, "preflight cancellation lost");
        t7r::CombatProfile profile; profile.heroId = 1101;
        require(session->submit(T7RNB_OPERATION_START, directory, id, "Player", false,
            {21001, 21002, 21003}, {profile}) == T7RNB_OK, "Rust start submit");
        require(waitFor(session, id).status == T7RNB_OPERATION_SUCCEEDED && launched, "Rust start after cancelled preflight");
        require(session->submit(T7RNB_OPERATION_STOP, {}, id) == T7RNB_OK, "stop submit");
        require(waitFor(session, id).status == T7RNB_OPERATION_SUCCEEDED, "stop failed");
        session->requestClose();
        require(session->waitForWorkerForTest(std::chrono::seconds(10)), "worker cleanup");
        std::cout << "Native preflight, Rust gateway requirement, cancellation and profile handoff passed\n";
        return true;
    } catch (const std::exception& error) {
        if (session) { session->requestClose(); session->waitForWorkerForTest(std::chrono::seconds(10)); }
        std::cerr << error.what() << '\n';
        return false;
    }
}
