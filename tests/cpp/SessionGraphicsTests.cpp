#include "../../src/Runtime/bridge/Session.h"
#include <atomic>
#include <iostream>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
template<class F> void waitFor(F condition) {
    const auto deadline = GetTickCount64() + 10000;
    while (!condition()) { require(GetTickCount64() < deadline, "graphics session wait timed out"); Sleep(10); }
}
}
bool verifyGraphicsSession(const t7::fs::path& packageRoot) {
    std::shared_ptr<t7::bridge::Session> session;
    try {
        auto lock = std::make_shared<std::mutex>();
        auto game = std::make_shared<t7::GraphicsValues>();
        auto applied = std::make_shared<bool>(false);
        auto calls = std::make_shared<std::atomic<unsigned>>(0);
        const GUID outputDevice{0x12345678, 0x1234, 0x5678, {1,2,3,4,5,6,7,8}};
        auto deviceSeen = std::make_shared<std::atomic<bool>>(false);
        t7::Bootstrap::TestAdapter adapter;
        adapter.check = [](const auto&, const auto&, const auto&) {};
        adapter.launch = [=](const auto&, const t7::Config& config, const auto&, const auto&, const auto&) {
            deviceSeen->store(IsEqualGUID(config.outputDevice, outputDevice));
        };
        adapter.pollGraphics = [=](t7::GraphicsValues& value, uint32_t& result, bool& completed) {
            std::lock_guard<std::mutex> guard(*lock);
            value = *game; result = 1; completed = *applied; *applied = false; return true;
        };
        adapter.applyGraphics = [=](const t7::GraphicsValues& value) {
            std::lock_guard<std::mutex> guard(*lock);
            *game = value; *applied = true; ++*calls;
        };
        session = t7::bridge::Session::createForTest(t7::utf8(packageRoot.wstring()), adapter);
        t7::bridge::GraphicsSnapshot graphics;
        session->graphics(graphics);
        require(graphics.state == T7NB_GRAPHICS_UNAVAILABLE, "idle graphics state");
        uint64_t operation = 0;
        require(session->setOutputDevice(outputDevice) == T7NB_OK, "output device selection rejected");
        require(session->submit(T7NB_OPERATION_START, t7::utf8((packageRoot / "synthetic-client").wstring()), operation) == T7NB_OK, "graphics test start");
        waitFor([&] { session->graphics(graphics); return graphics.state == T7NB_GRAPHICS_READY; });
        require(deviceSeen->load(), "output device was lost between the command and the client launch");
        const auto revision = graphics.revision;
        auto change = graphics.values; change.quality = 1;
        require(session->applyGraphics(revision - 1, change) == T7NB_NOT_READY, "stale graphics revision accepted");
        require(session->applyGraphics(revision, change) == T7NB_OK, "graphics submission");
        require(session->applyGraphics(revision, change) == T7NB_BUSY, "duplicate graphics submission");
        waitFor([&] { session->graphics(graphics); return graphics.state == T7NB_GRAPHICS_READY && graphics.values.quality == 1; });
        require(graphics.revision > revision && calls->load() == 1, "graphics apply acknowledgement");
        const auto afterApply = graphics.revision;
        {
            std::lock_guard<std::mutex> guard(*lock); game->viewDistance = 159;
        }
        waitFor([&] { session->graphics(graphics); return graphics.revision > afterApply; });
        require(graphics.values.viewDistance == 159 && calls->load() == 1, "game readback caused feedback write");
        require(session->submit(T7NB_OPERATION_STOP, {}, operation) == T7NB_OK, "graphics test stop");
        session->graphics(graphics);
        require(graphics.state == T7NB_GRAPHICS_UNAVAILABLE && session->applyGraphics(graphics.revision, change) == T7NB_NOT_READY,
            "stop retained writable graphics state");
        session->requestClose(); require(session->waitForWorkerForTest(std::chrono::seconds(10)), "graphics cleanup wait");
        require(session->graphics(graphics) == T7NB_INVALID_HANDLE, "released graphics session");
        std::cout << "Graphics session queue, revisions, readback, duplicate rejection and shutdown passed\n";
        return true;
    } catch (const std::exception& e) {
        if (session) { session->requestClose(); session->waitForWorkerForTest(std::chrono::seconds(10)); }
        std::cerr << e.what() << '\n'; return false;
    }
}
