#include "../../src/Runtime/bridge/Session.h"
#include <atomic>
#include <iostream>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
template<class F> void waitFor(F condition) {
    const auto deadline = GetTickCount64() + 10000;
    while (!condition()) { require(GetTickCount64() < deadline, "audio session wait timed out"); Sleep(10); }
}
std::string logs(const std::shared_ptr<t7r::bridge::Session>& session) {
    uint64_t cursor = 0; uint32_t required = 0, flags = 0;
    std::vector<uint8_t> buffer(1024 * 1024);
    require(session->readLogs(cursor, buffer.data(), static_cast<uint32_t>(buffer.size()), required, flags) == T7RNB_OK,
        "audio diagnostic log read");
    return {buffer.begin(), buffer.begin() + required};
}
size_t occurrences(const std::string& text, const std::string& marker) {
    size_t count = 0, offset = 0;
    while ((offset = text.find(marker, offset)) != std::string::npos) { ++count; offset += marker.size(); }
    return count;
}
}
bool verifyAudioSession(const t7r::fs::path& packageRoot) {
    std::shared_ptr<t7r::bridge::Session> session;
    try {
        auto lock = std::make_shared<std::mutex>();
        auto game = std::make_shared<t7r::AudioValues>();
        auto applied = std::make_shared<bool>(false);
        auto calls = std::make_shared<std::atomic<unsigned>>(0);
        auto polls = std::make_shared<std::atomic<unsigned>>(0);
        auto mode = std::make_shared<std::atomic<unsigned>>(0);
        t7r::Bootstrap::TestAdapter adapter;
        adapter.check = [](const auto&, const auto&, const auto&) {};
        adapter.launch = [](const auto&, const auto&, const auto&, const auto&, const auto&) {};
        adapter.pollAudio = [=](t7r::AudioValues& value, uint32_t& result, bool& completed) {
            ++*polls;
            if (mode->load() == 2) throw std::runtime_error("fixture audio mailbox timeout");
            std::lock_guard<std::mutex> guard(*lock);
            value = *game; result = mode->load() == 1 ? 0u : 1u;
            completed = *applied; *applied = false; return true;
        };
        adapter.applyAudio = [=](const t7r::AudioValues& value) {
            std::lock_guard<std::mutex> guard(*lock);
            *game = value; *applied = true; ++*calls;
        };
        session = t7r::bridge::Session::createForTest(t7r::utf8(packageRoot.wstring()), adapter);
        require(session->setLogLevel(T7RNB_LOG_DEBUG) == T7RNB_OK, "audio diagnostics level");
        t7r::bridge::AudioSnapshot audio;
        session->audio(audio);
        require(audio.state == T7RNB_AUDIO_UNAVAILABLE, "idle audio state");
        uint64_t operation = 0;
        require(session->submit(T7RNB_OPERATION_START, t7r::utf8((packageRoot / "synthetic-client" / "Bin").wstring()), operation, u8"新玩家", false, {1, 2, 3}) == T7RNB_OK, "audio test start");
        waitFor([&] { session->audio(audio); return audio.state == T7RNB_AUDIO_READY; });
        waitFor([&] { return logs(session).find("Audio settings snapshot; state=ready;") != std::string::npos; });
        require(logs(session).find("musicMuted=0; musicVolume=1; effectsMuted=0; effectsVolume=1") != std::string::npos,
            "initial audio settings omitted values");
        const auto stablePolls = polls->load();
        waitFor([&] { return polls->load() >= stablePolls + 5; });
        require(occurrences(logs(session), "Audio settings snapshot;") == 1, "unchanged audio settings flooded logs");
        const auto revision = audio.revision;
        auto change = audio.values; change.musicMuted = 1;
        require(session->applyAudio(revision - 1, change) == T7RNB_NOT_READY, "stale audio revision accepted");
        require(session->applyAudio(revision, change) == T7RNB_OK, "audio submission");
        require(session->applyAudio(revision, change) == T7RNB_BUSY, "duplicate audio submission");
        waitFor([&] { session->audio(audio); return audio.state == T7RNB_AUDIO_READY && audio.values.musicMuted == 1; });
        require(audio.revision > revision && calls->load() == 1, "audio apply acknowledgement");
        const auto afterApply = audio.revision;
        {
            std::lock_guard<std::mutex> guard(*lock); game->effectsVolume = .25f;
        }
        waitFor([&] { session->audio(audio); return audio.revision > afterApply; });
        require(audio.values.effectsVolume == .25f && calls->load() == 1, "game readback caused feedback write");
        waitFor([&] { return logs(session).find("effectsVolume=0.25") != std::string::npos; });
        require(logs(session).find("Audio settings apply requested;") != std::string::npos,
            "audio apply request was not logged");
        mode->store(1);
        waitFor([&] { return logs(session).find("Audio settings snapshot; state=unavailable;") != std::string::npos; });
        const auto unavailableCount = occurrences(logs(session), "state=unavailable;");
        const auto unavailablePolls = polls->load();
        waitFor([&] { return polls->load() >= unavailablePolls + 5; });
        require(occurrences(logs(session), "state=unavailable;") == unavailableCount, "unavailable audio flooded logs");
        mode->store(0);
        waitFor([&] { session->audio(audio); return audio.state == T7RNB_AUDIO_READY; });
        mode->store(2);
        waitFor([&] { return logs(session).find("Audio synchronization stopped: fixture audio mailbox timeout") != std::string::npos; });
        require(logs(session).find("scope=settings-mailbox; playbackStatus=not-observed") != std::string::npos,
            "mailbox failure was not distinguished from playback failure");
        require(logs(session).find("Audio settings last snapshot;") != std::string::npos,
            "mailbox failure omitted the last settings snapshot");
        t7r::bridge::Snapshot runtime;
        require(session->snapshot(runtime) == T7RNB_OK && runtime.state == T7RNB_STATE_RUNNING && calls->load() == 1,
            "audio diagnostics changed session lifetime or wrote settings");
        require(session->submit(T7RNB_OPERATION_STOP, {}, operation) == T7RNB_OK, "audio test stop");
        session->audio(audio);
        require(audio.state == T7RNB_AUDIO_UNAVAILABLE && session->applyAudio(audio.revision, change) == T7RNB_NOT_READY,
            "stop retained writable audio state");
        session->requestClose(); require(session->waitForWorkerForTest(std::chrono::seconds(10)), "audio cleanup wait");
        require(session->audio(audio) == T7RNB_INVALID_HANDLE, "released audio session");
        session = t7r::bridge::Session::createForTest(t7r::utf8(packageRoot.wstring()), adapter);
        require(session->setLogLevel(T7RNB_LOG_DEBUG) == T7RNB_OK, "audio diagnostics level");
        require(session->submit(T7RNB_OPERATION_START, t7r::utf8((packageRoot / "synthetic-client" / "Bin").wstring()), operation, u8"新玩家", false, {1, 2, 3}) == T7RNB_OK,
            "initial audio failure test start");
        waitFor([&] { return logs(session).find("Audio synchronization stopped:") != std::string::npos; });
        const auto initialFailure = logs(session);
        const auto lastSnapshot = initialFailure.find("Audio settings last snapshot;");
        require(lastSnapshot != std::string::npos && initialFailure.find("values=not-current", lastSnapshot) != std::string::npos
            && initialFailure.find("musicVolume=", lastSnapshot) == std::string::npos, "unread defaults were reported as game audio values");
        session->requestClose(); require(session->waitForWorkerForTest(std::chrono::seconds(10)), "initial audio failure cleanup");
        std::cout << "Audio session settings, bounded diagnostics, mailbox failure and shutdown passed\n";
        return true;
    } catch (const std::exception& e) {
        if (session) { session->requestClose(); session->waitForWorkerForTest(std::chrono::seconds(10)); }
        std::cerr << e.what() << '\n'; return false;
    }
}
