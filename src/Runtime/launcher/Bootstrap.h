#pragma once
#include "../core/DiagnosticLog.h"
#include "../core/Common.h"
#include "MovementOverlay.h"
#include "LobbyNews.h"
#include "DebugClient.h"
#include "StartupGate.h"
#include "ClientFrameRuntime.h"
#include "GuardDebugDraw.h"
#include "OutputDevice.h"
#include "ClientWindowMonitor.h"
#include <functional>
#include <memory>
namespace t7r {
class Bootstrap {
public:
    // Test-only seam for lifecycle tests.  Product Session instances use the
    // default constructor and always execute the real client adapter below.
    struct TestAdapter {
        std::function<void(const fs::path&, const Config&, const DiagnosticLogCallback&)> check;
        std::function<void(const fs::path&, const Config&, const DiagnosticLogCallback&,
                           const std::function<bool()>&, const std::function<void()>&)> launch;
        std::function<bool()> running;
        std::function<bool()> windowClosed;
        std::function<void()> stop;
        std::function<DWORD()> exitCode;
        std::function<bool(GraphicsValues&, uint32_t&, bool&)> pollGraphics;
        std::function<void(const GraphicsValues&)> applyGraphics;
        std::function<bool(AudioValues&, uint32_t&, bool&)> pollAudio;
        std::function<void(const AudioValues&)> applyAudio;
        std::function<bool(WorldSnapshot&)> pollWorld;
    };
    explicit Bootstrap(TestAdapter adapter = {});
    ~Bootstrap();
    void check(const fs::path& directory, const Config& config, const DiagnosticLogCallback& log = {});
    void launch(const fs::path& directory, const Config& config, const DiagnosticLogCallback& log,
                const std::function<bool()>& cancelled = {}, const std::function<void()>& adapting = {},
                const std::function<void(std::string)>& warning = {}, bool traceAudio = false);
    void updateGuardDebug(bool enabled, const WorldSnapshot* world = nullptr);
    bool running() const;
    bool windowClosed();
    DWORD exitCode() const;
    void stop();
    bool pollGraphics(GraphicsValues& values, uint32_t& result, bool& applied);
    void applyGraphics(const GraphicsValues& values);
    bool pollAudio(AudioValues& values, uint32_t& result, bool& applied);
    void applyAudio(const AudioValues& values);
    void updateTitleDescriptions(const Bytes& bytes) { frameRuntime_.titles().update(bytes); }
    void pollTitleDescriptions() { frameRuntime_.titles().poll(); }
    bool titleCatalogueApplied() { return frameRuntime_.titles().applied(); }
    bool pollWorld(WorldSnapshot& value, const std::array<uint8_t, MAX_WORLD_ACTORS>& camps = {});
private:
    HANDLE process_ = nullptr;
    std::unique_ptr<DebugClient> debugClient_;
    DWORD pid_ = 0;
    MovementOverlay movementOverlay_;
    LobbyNews lobbyNews_;
    StartupGate startupGate_;
    ClientFrameRuntime frameRuntime_;
    GuardDebugDraw guardDebug_;
    ClientWindowMonitor windowMonitor_;
    OutputDevice outputDevice_;
    uint32_t clientImageBase_ = 0;
    bool outputDeviceVerified_ = false;
    TestAdapter testAdapter_;
    bool testRunning_ = false;
};
}
