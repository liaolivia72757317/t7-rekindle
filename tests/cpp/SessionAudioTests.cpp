#include "../../src/Runtime/bridge/Session.h"
#include <atomic>
#include <iostream>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
template<class F> void waitFor(F condition) {
    const auto deadline = GetTickCount64() + 10000;
    while (!condition()) { require(GetTickCount64() < deadline, "audio session wait timed out"); Sleep(10); }
}
}
bool verifyAudioSession(const t7::fs::path& packageRoot) {
    std::shared_ptr<t7::bridge::Session> session;
    try {
        auto lock = std::make_shared<std::mutex>();
        auto game = std::make_shared<t7::AudioValues>();
        auto applied = std::make_shared<bool>(false);
        auto calls = std::make_shared<std::atomic<unsigned>>(0);
        t7::Bootstrap::TestAdapter adapter;
        adapter.check = [](const auto&, const auto&, const auto&) {};
        adapter.launch = [](const auto&, const auto&, const auto&, const auto&, const auto&) {};
        adapter.pollAudio = [=](t7::AudioValues& value, uint32_t& result, bool& completed) {
            std::lock_guard<std::mutex> guard(*lock);
            value = *game; result = 1; completed = *applied; *applied = false; return true;
        };
        adapter.applyAudio = [=](const t7::AudioValues& value) {
            std::lock_guard<std::mutex> guard(*lock);
            *game = value; *applied = true; ++*calls;
        };
        session = t7::bridge::Session::createForTest(t7::utf8(packageRoot.wstring()), adapter);
        t7::bridge::AudioSnapshot audio;
        session->audio(audio);
        require(audio.state == T7NB_AUDIO_UNAVAILABLE, "idle audio state");
        uint64_t operation = 0;
        require(session->submit(T7NB_OPERATION_START, t7::utf8((packageRoot / "synthetic-client").wstring()), operation) == T7NB_OK, "audio test start");
        waitFor([&] { session->audio(audio); return audio.state == T7NB_AUDIO_READY; });
        const auto revision = audio.revision;
        auto change = audio.values; change.musicMuted = 1;
        require(session->applyAudio(revision - 1, change) == T7NB_NOT_READY, "stale audio revision accepted");
        require(session->applyAudio(revision, change) == T7NB_OK, "audio submission");
        require(session->applyAudio(revision, change) == T7NB_BUSY, "duplicate audio submission");
        waitFor([&] { session->audio(audio); return audio.state == T7NB_AUDIO_READY && audio.values.musicMuted == 1; });
        require(audio.revision > revision && calls->load() == 1, "audio apply acknowledgement");
        const auto afterApply = audio.revision;
        {
            std::lock_guard<std::mutex> guard(*lock); game->effectsVolume = .25f;
        }
        waitFor([&] { session->audio(audio); return audio.revision > afterApply; });
        require(audio.values.effectsVolume == .25f && calls->load() == 1, "game readback caused feedback write");
        require(session->submit(T7NB_OPERATION_STOP, {}, operation) == T7NB_OK, "audio test stop");
        session->audio(audio);
        require(audio.state == T7NB_AUDIO_UNAVAILABLE && session->applyAudio(audio.revision, change) == T7NB_NOT_READY,
            "stop retained writable audio state");
        session->requestClose(); require(session->waitForWorkerForTest(std::chrono::seconds(10)), "audio cleanup wait");
        require(session->audio(audio) == T7NB_INVALID_HANDLE, "released audio session");
        std::cout << "Audio session queue, revisions, readback, duplicate rejection and shutdown passed\n";
        return true;
    } catch (const std::exception& e) {
        if (session) { session->requestClose(); session->waitForWorkerForTest(std::chrono::seconds(10)); }
        std::cerr << e.what() << '\n'; return false;
    }
}
