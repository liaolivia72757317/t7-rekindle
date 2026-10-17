#include "../../tests/reference/native-server/Server.h"
#include <algorithm>
#include <iostream>

namespace t7r {
struct ServerAudioDiagnosticsTests {
    static void verify(const fs::path& root) {
        Server server;
        server.signals_ = std::make_unique<detail::ServerSignals>();
        server.stopping_ = false;
        auto filter = std::make_shared<DiagnosticLogFilter>(); filter->minimum.store(DiagnosticLogLevel::Debug);
        server.journal_ = std::make_unique<Journal>(root / "wire", 64 * 1024 * 1024, 160, filter);
        std::vector<std::string> observed;
        bool calledUnderLock = false;
        server.audioLog_ = [&](DiagnosticLogLevel level, const std::string& line) {
            if (level != DiagnosticLogLevel::Debug) throw std::runtime_error("wrong audio notification log level");
            std::thread inspect([&] {
                if (server.mutex_.try_lock()) server.mutex_.unlock(); else calledUnderLock = true;
            });
            inspect.join();
            observed.push_back(line);
        };
        Server::QueuedEvent event; event.id = 7; event.type = "world";
        Server::Timers timers;
        Transition transition; transition.state = "updated";
        transition.logs = {{DiagnosticLogLevel::Debug, "Audio announcement; localPlayer=1; streak=2; stage=queued"},
                           {DiagnosticLogLevel::Info, "unrelated business record"}};
        std::string state; bool committed = false;
        server.commit(transition, event, "fixture", "battle", detail::Clock::now(), 0, state, timers, committed);
        if (!committed || state != "updated" || calledUnderLock || observed.size() != 1
            || observed[0].find("event=7; queuedTickMs=") == std::string::npos)
            throw std::runtime_error("audio notification forwarding/commit contract");
        for (unsigned i = 0; i < 1100; ++i) server.journal_->add(DiagnosticLogLevel::Info, "TEST", 0, "fixture", "filler");
        const auto tail = server.journal_->lines();
        if (observed.size() != 1 || std::any_of(tail.begin(), tail.end(), [](const auto& line) {
                return line.find("Audio announcement;") != std::string::npos;
            })) throw std::runtime_error("audio notification did not outlive bounded journal tail");
        server.audioLog_ = [](DiagnosticLogLevel, const std::string&) { throw std::runtime_error("fixture log sink failure"); };
        transition.state = "still-committed"; committed = false;
        server.commit(transition, event, "fixture", "battle", detail::Clock::now(), 0, state, timers, committed);
        if (!committed || state != "still-committed") throw std::runtime_error("diagnostic failure changed game state");
        const auto errors = server.journal_->lines();
        if (std::none_of(errors.begin(), errors.end(), [](const auto& line) {
                return line.find("Audio announcement live log failed;") != std::string::npos;
            })) throw std::runtime_error("diagnostic failure was silently discarded");
        server.stop();
    }
};
}
bool verifyServerAudioDiagnostics() {
    const auto root = t7r::fs::temp_directory_path() / (L"t7r-server-audio-" + std::to_wstring(GetCurrentProcessId())
                                                    + L"-" + std::to_wstring(GetTickCount64()));
    bool ownsRoot = false;
    const auto cleanup = [&] {
        if (!ownsRoot) return;
        if (t7r::fs::weakly_canonical(root).parent_path() != t7r::fs::canonical(t7r::fs::temp_directory_path()))
            throw std::runtime_error("server audio fixture cleanup extent");
        t7r::fs::remove_all(root);
    };
    try {
        ownsRoot = t7r::fs::create_directory(root);
        if (!ownsRoot) throw std::runtime_error("server audio fixture already exists");
        t7r::ServerAudioDiagnosticsTests::verify(root); cleanup();
        std::cout << "Server audio diagnostics: live forwarding, retention and failure isolation passed\n"; return true;
    } catch (const std::exception& error) {
        std::cerr << "Server audio diagnostics: " << error.what() << '\n'; cleanup(); return false;
    }
}
