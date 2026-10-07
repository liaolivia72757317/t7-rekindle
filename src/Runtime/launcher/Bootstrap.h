#pragma once
#include "../core/Common.h"
#include "MovementOverlay.h"
#include "DebugClient.h"
#include <functional>
#include <memory>
namespace t7 {
class Bootstrap {
public:
    // Test-only seam for lifecycle tests.  Product Session instances use the
    // default constructor and always execute the real client adapter below.
    struct TestAdapter {
        std::function<void(const fs::path&, const Config&, const std::function<void(std::string)>&)> check;
        std::function<void(const fs::path&, const Config&, const std::function<void(std::string)>&,
                           const std::function<bool()>&, const std::function<void()>&)> launch;
        std::function<bool()> running;
        std::function<void()> stop;
        std::function<DWORD()> exitCode;
    };
    explicit Bootstrap(TestAdapter adapter = {});
    ~Bootstrap();
    void check(const fs::path& directory, const Config& config, const std::function<void(std::string)>& log = {});
    void launch(const fs::path& directory, const Config& config, const std::function<void(std::string)>& log,
                const std::function<bool()>& cancelled = {}, const std::function<void()>& adapting = {},
                const std::function<void(std::string)>& warning = {});
    bool running() const;
    DWORD exitCode() const;
    void stop();
private:
    HANDLE process_ = nullptr;
    std::unique_ptr<DebugClient> debugClient_;
    DWORD pid_ = 0;
    MovementOverlay movementOverlay_;
    TestAdapter testAdapter_;
    bool testRunning_ = false;
};
}
