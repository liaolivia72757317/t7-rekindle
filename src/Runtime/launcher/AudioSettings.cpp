#include "AudioSettings.h"
#include "RemoteImage.h"
#include <cmath>
#include <cstring>
#include <iomanip>
#include <locale>
#include <sstream>

namespace t7 {
Bytes buildAudioRoot(const fs::path& directory) {
    if (directory.empty()) return {};
    if (!directory.is_absolute()) throw std::runtime_error("audio directory must be absolute");
    auto value = directory.lexically_normal().make_preferred().wstring();
    if (value.find(L'\0') != value.npos || value.size() > MAX_PATH - 32)
        throw std::runtime_error("audio directory exceeds legacy engine path limit");
    if (value.back() != L'\\') value.push_back(L'\\');
    value.push_back(L'\0');
    const auto begin = reinterpret_cast<const unsigned char*>(value.data());
    return Bytes(begin, begin + value.size() * sizeof(wchar_t));
}
bool validAudioValues(const AudioValues& v) {
    return v.musicMuted <= 1 && v.effectsMuted <= 1
        && std::isfinite(v.musicVolume) && v.musicVolume >= 0 && v.musicVolume <= 1
        && std::isfinite(v.effectsVolume) && v.effectsVolume >= 0 && v.effectsVolume <= 1;
}
bool sameAudioValues(const AudioValues& a, const AudioValues& b) {
    return a.musicMuted == b.musicMuted && a.musicVolume == b.musicVolume
        && a.effectsMuted == b.effectsMuted && a.effectsVolume == b.effectsVolume;
}
std::string describeAudioValues(const AudioValues& values) {
    std::ostringstream text;
    text.imbue(std::locale::classic()); text << std::setprecision(9);
    text << "musicMuted=" << values.musicMuted << "; musicVolume=" << values.musicVolume
         << "; effectsMuted=" << values.effectsMuted << "; effectsVolume=" << values.effectsVolume;
    return text.str();
}
namespace {
void write(HANDLE process, uint32_t address, const void* data, size_t size) {
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(address)), data, size, &actual)
        || actual != size) throw std::runtime_error(errorText("audio mailbox write"));
}
}
void AudioSettings::attach(HANDLE process, uint32_t address, uint32_t imageBase, bool combatBanks) {
    if (process_ || !process || !address) throw std::runtime_error("audio mailbox attachment state");
    process_ = process; remote_ = address; imageBase_ = imageBase;
    combatEnabled_ = combatBanks;
}
void AudioSettings::clear() {
    if (process_ && WaitForSingleObject(process_, 0) != WAIT_OBJECT_0)
        throw std::runtime_error("audio mailbox cleanup requires terminated owned client");
    process_ = nullptr; remote_ = imageBase_ = pending_ = 0; requestedAt_ = 0; hasValues_ = false;
    combatEnabled_ = combatPending_ = false; combatEpoch_ = 0;
}
bool AudioSettings::pollCombat(uint64_t instanceEpoch, std::array<uint32_t, 6>& results) {
    if (!process_ || !combatEnabled_ || !instanceEpoch) return false;
    if (combatPending_) {
        const auto bytes = readClientMemory(process_, remote_ + COMBAT_AUDIO_OFFSET, 32);
        uint32_t command = 0; std::memcpy(&command, bytes.data(), 4);
        if (command) return false;
        std::memcpy(results.data(), bytes.data() + 8, sizeof(results));
        combatPending_ = false;
        return true;
    }
    if (instanceEpoch == combatEpoch_) return false;
    // Publish a fresh request only after the previous frame-thread operation completed.
    const std::array<uint32_t, 8> request{{1, 0, 0, 0, 0, 0, 0, 0}};
    write(process_, remote_ + COMBAT_AUDIO_OFFSET + 4, request.data() + 1, 28);
    write(process_, remote_ + COMBAT_AUDIO_OFFSET, request.data(), 4);
    combatEpoch_ = instanceEpoch; combatPending_ = true;
    return false;
}
void AudioSettings::request(uint32_t command) {
    write(process_, remote_, &command, 4);
    pending_ = command; requestedAt_ = GetTickCount64();
}
bool AudioSettings::poll(AudioValues& values, uint32_t& result, bool& applied) {
    if (!process_) return false;
    if (!pending_) { request(1); return false; }
    const auto bytes = readClientMemory(process_, remote_, 0x20);
    uint32_t command = 0; std::memcpy(&command, bytes.data(), 4);
    if (command) {
        const auto elapsed = GetTickCount64() - requestedAt_;
        if (elapsed > 10000) throw std::runtime_error("audio frame thread response timed out; pendingCommand="
            + std::to_string(pending_) + "; observedCommand=" + std::to_string(command)
            + "; elapsedMs=" + std::to_string(elapsed) + "; hasSnapshot=" + std::to_string(hasValues_));
        return false;
    }
    applied = pending_ == 2; pending_ = 0;
    std::memcpy(&result, bytes.data() + 4, 4);
    hasValues_ = result == 1 || result == 2;
    if (hasValues_) {
        std::memcpy(&lastValues_, bytes.data() + 0x10, sizeof(lastValues_));
        if (!validAudioValues(lastValues_)) throw std::runtime_error("invalid client audio snapshot");
        values = lastValues_;
    }
    return true;
}
void AudioSettings::apply(const AudioValues& values) {
    if (!validAudioValues(values)) throw std::runtime_error("invalid audio settings");
    if (!process_ || pending_ || !hasValues_) throw std::runtime_error("audio mailbox is not ready to apply");
    const auto map = buildAudioMap(remote_ + 0x100, imageBase_);
    write(process_, remote_ + 0x100, map.data(), map.size());
    write(process_, remote_ + 0x20, &lastValues_, sizeof(lastValues_));
    write(process_, remote_ + 0x30, &values, sizeof(values));
    request(2);
}
}
