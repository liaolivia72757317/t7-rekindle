#pragma once
#include "../core/Common.h"
#include <array>

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
std::string describeAudioValues(const AudioValues& values);
constexpr uint32_t AUDIO_CODE_OFFSET = 0x1000, AUDIO_PAGE_SIZE = 0x2000;
constexpr uint32_t AUDIO_MUSIC_RVA = 0x6ECB20, AUDIO_EFFECTS_RVA = 0x6ECAD0;
constexpr uint32_t AUDIO_ROOT_OFFSET = 0x800, AUDIO_ROOT_INIT_RVA = 0x3E5FDD;
constexpr uint32_t COMBAT_AUDIO_OFFSET = 0x400, COMBAT_AUDIO_NAMES_OFFSET = 0x500;
constexpr uint32_t AUDIO_LOAD_BANK_IAT_RVA = 0x16F6084;
inline constexpr std::array<const char*, 6> COMBAT_AUDIO_BANKS{{
    "Media_Weapon.bnk", "Weapon.bnk", "Media_Hit.bnk", "Hit.bnk", "Media_Combat.bnk", "Combat.bnk"
}};
static_assert(COMBAT_AUDIO_NAMES_OFFSET + COMBAT_AUDIO_BANKS.size() * 32 <= AUDIO_ROOT_OFFSET, "combat bank names capacity");
static_assert(AUDIO_ROOT_OFFSET + MAX_PATH * sizeof(wchar_t) < AUDIO_CODE_OFFSET, "audio root capacity");
Bytes buildAudioRoot(const fs::path& directory);
Bytes buildAudioMap(uint32_t address, uint32_t imageBase);
Bytes buildAudioHook(uint32_t address, uint32_t imageBase, uint32_t threadId, bool combatBanks = false);

class AudioSettings final {
public:
    void attach(HANDLE process, uint32_t address, uint32_t imageBase, bool combatBanks = false);
    void clear();
    bool poll(AudioValues& values, uint32_t& result, bool& applied);
    void apply(const AudioValues& values);
    bool pollCombat(uint64_t instanceEpoch, std::array<uint32_t, 6>& results);
private:
    void request(uint32_t command);
    HANDLE process_ = nullptr;
    uint32_t remote_ = 0, imageBase_ = 0, pending_ = 0;
    ULONGLONG requestedAt_ = 0;
    AudioValues lastValues_;
    bool hasValues_ = false;
    bool combatEnabled_ = false, combatPending_ = false;
    uint64_t combatEpoch_ = 0;
};
}
