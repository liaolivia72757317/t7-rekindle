#include "AudioSettings.h"
#include "RemoteImage.h"
#include <cmath>
#include <cstring>

namespace t7 {
bool validAudioValues(const AudioValues& v) {
    return v.musicMuted <= 1 && v.effectsMuted <= 1
        && std::isfinite(v.musicVolume) && v.musicVolume >= 0 && v.musicVolume <= 1
        && std::isfinite(v.effectsVolume) && v.effectsVolume >= 0 && v.effectsVolume <= 1;
}
bool sameAudioValues(const AudioValues& a, const AudioValues& b) {
    return a.musicMuted == b.musicMuted && a.musicVolume == b.musicVolume
        && a.effectsMuted == b.effectsMuted && a.effectsVolume == b.effectsVolume;
}
namespace {
void write(HANDLE process, uint32_t address, const void* data, size_t size) {
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(address)), data, size, &actual)
        || actual != size) throw std::runtime_error(errorText("audio mailbox write"));
}
}
void AudioSettings::attach(HANDLE process, uint32_t address, uint32_t imageBase) {
    if (process_ || !process || !address) throw std::runtime_error("audio mailbox attachment state");
    process_ = process; remote_ = address; imageBase_ = imageBase;
}
void AudioSettings::clear() {
    if (process_ && WaitForSingleObject(process_, 0) != WAIT_OBJECT_0)
        throw std::runtime_error("audio mailbox cleanup requires terminated owned client");
    process_ = nullptr; remote_ = imageBase_ = pending_ = 0; requestedAt_ = 0; hasValues_ = false;
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
        if (GetTickCount64() - requestedAt_ > 10000) throw std::runtime_error("audio frame thread response timed out");
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
