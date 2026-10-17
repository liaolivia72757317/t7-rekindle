#include "../../tests/reference/native-server/Server.h"
#include "../../src/Runtime/bridge/Session.h"
#include <cstring>
#include <iostream>

namespace t7r {
struct TitleRuntimeTests {
    static void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
    static void catalogueGate(const fs::path& root) {
        bridge::Session session(utf8(root.wstring()));
        session.snapshot_.state = T7RNB_STATE_RUNNING;
        Bytes catalogue(8 + TITLE_RECORD_SIZE, 0);
        const uint32_t header[] = {1, 1, 1000000};
        std::memcpy(catalogue.data(), header, sizeof(header));
        catalogue[12] = 'A';
        const std::string owned = R"({"titles":[{"titleId":1000000,"owned":true,"enabled":true}],"equippedTitleId":1000000})";
        require(session.publishTitles(1, owned, 0, true, catalogue) == T7RNB_OK, "custom title update was rejected");
        session.monitorTitles();
        require(session.titleRequest_.epoch == 0 && session.titleUpdates_.size() == 1 && session.titleError_.empty(),
            "custom title ownership was published before the native catalogue was ready");
        const std::string offline = R"({"titles":[],"equippedTitleId":null})";
        require(session.publishTitles(2, offline, 0, true, {}) == T7RNB_OK, "offline title update was rejected");
        session.monitorTitles();
        require(session.titleRequest_.epoch == 2
            && session.titleUpdates_.empty() && session.titleError_.empty(),
            "a waiting catalogue blocked logout or restored old account ownership");
    }
    static bool verify(const fs::path& root) {
        catalogueGate(root);
        PythonHost host(root, root / "title-revisions");
        Config config;
        host.initializeContext(config);
        auto state = host.create(host.context(config, 0, {}));
        Server server;
        server.signals_ = std::make_unique<detail::ServerSignals>();
        server.journal_ = std::make_unique<Journal>(root / "title-journal");
        server.stopping_ = false; server.ready_ = true;
        Server::Timers timers;
        uint64_t now = 1;
        auto apply = [&](Event input, const std::string& timerId = "") {
            Server::QueuedEvent event; static_cast<Event&>(event) = std::move(input);
            event.timerId = timerId;
            auto result = host.dispatchNative(event, state, now++, {1}, nullptr);
            auto phase = result.phase;
            bool committed = false;
            server.commit(result, event, host.version(), phase, detail::Clock::now(), 0, state, timers, committed);
            require(committed, "title transition did not commit");
            return result;
        };
        auto publish = [&](uint64_t epoch, const std::string& json, uint64_t requestId = 0, bool success = true) {
            server.publishTitles(epoch, json, requestId, success);
            Server::QueuedEvent event; std::vector<uint64_t> live;
            require(server.nextEvent(timers, event, live), "title publication was lost");
            server.dispatch(host, event, live, state, timers);
            if (!server.snapshot().error.empty()) throw std::runtime_error("native title event: " + server.snapshot().error);
        };
        const std::string owned = R"({"titles":[{"titleId":1008,"owned":true,"enabled":true}],"equippedTitleId":1008})";
        const std::string unequipped = R"({"titles":[{"titleId":1008,"owned":true,"enabled":true}],"equippedTitleId":null})";
        const std::string offline = R"({"titles":[],"equippedTitleId":null})";
        publish(1, owned);
        require(state.find("1008") != std::string::npos, "title snapshot without a game connection was discarded");
        server.sessions_[1] = {};
        Event connected; connected.type = "connected"; connected.role = "logic"; connected.connection = 1;
        apply(connected);
        Event auth; auth.type = "authenticated"; auth.connection = 1; apply(auth);
        // Exercise the native object result, not the legacy four-field adapter.
        Event select; select.type = "message"; select.connection = 1; select.id = 22;
        select.command = 31; select.body = {0,5,0,0,0,0};
        auto pending = apply(select);
        require(pending.send.empty() && pending.titleRequest.requestId == 22
            && pending.titleRequest.epoch == 1 && pending.titleRequest.titleId == 0,
            "game title write bypassed central confirmation or lost the seven-field transition");
        require(server.titleRequest().requestId == 22, "native title mailbox omitted the request");
        publish(1, unequipped, 22);
        require(server.titleRequest().requestId == 0 && state.find("titlePending") == std::string::npos,
            "successful title completion did not clear pending state");
        require(server.sessions_[1].send.size() == 3, "game did not receive result, basic info and owned-title array");
        const auto& response = server.sessions_[1].send.front().bytes;
        require(response.size() == 29 && response[24] == 6 && response.back() == 0, "game title confirmation layout differs");
        server.sessions_[1].send.clear(); server.sessions_[1].queued = 0;
        select.id = 23; select.body = {0,5,0,0,3,0xF0}; auto second = apply(select);
        require(server.titleRequest().titleId == 1008, "game title ID did not reach native mailbox");
        Event timeout; timeout.type = "timer"; timeout.connection = 1;
        timeout.serialized = second.timers.front().event;
        now = 20000; auto expired = apply(timeout, "1:title-request-timeout");
        require(!expired.send.empty() && expired.send[0].body.back() != 0 && server.titleRequest().requestId == 0,
            "title timeout left a live native request or acknowledged success");
        select.id = 24; apply(select);
        publish(2, offline);
        require(server.titleRequest().requestId == 0 && state.find("titlePending") == std::string::npos,
            "offline epoch did not invalidate pending title selection");
        const auto before = state;
        const auto queued = server.events_.size();
        server.publishTitles(1, owned, 24, true);
        require(server.events_.size() == queued && state == before, "old account epoch restored titles");
        server.stop();
        std::cout << "native/Python title catalogue gate, publication, confirmation, timeout and epoch isolation passed\n";
        return true;
    }
};
}
bool verifyTitleRuntime(const t7r::fs::path& root) {
    try { return t7r::TitleRuntimeTests::verify(root); }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return false; }
}
