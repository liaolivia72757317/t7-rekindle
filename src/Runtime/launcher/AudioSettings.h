#pragma once
#include "../core/Common.h"

namespace t7 {
struct AudioValues {
    uint32_t musicMuted = 0;
    float musicVolume = 1;
    uint32_t effectsMuted = 0;
    float effectsVolume = 1;
};
static_assert(sizeof(AudioValues) == 16, "audio mailbox layout");
bool validAudioValues(const AudioValues& values);
bool sameAudioValues(const AudioValues& left, const AudioValues& right);
constexpr uint32_t AUDIO_CODE_OFFSET = 0x1000, AUDIO_PAGE_SIZE = 0x2000;
constexpr uint32_t AUDIO_MUSIC_RVA = 0x6ECB20, AUDIO_EFFECTS_RVA = 0x6ECAD0;
Bytes buildAudioMap(uint32_t address, uint32_t imageBase);
Bytes buildAudioHook(uint32_t address, uint32_t imageBase, uint32_t threadId);

class AudioSettings final {
public:
    void attach(HANDLE process, uint32_t address, uint32_t imageBase);
    void clear();
    bool poll(AudioValues& values, uint32_t& result, bool& applied);
    void apply(const AudioValues& values);
private:
    void request(uint32_t command);
    HANDLE process_ = nullptr;
    uint32_t remote_ = 0, imageBase_ = 0, pending_ = 0;
    ULONGLONG requestedAt_ = 0;
    AudioValues lastValues_;
    bool hasValues_ = false;
};
}
