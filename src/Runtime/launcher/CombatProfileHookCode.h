#pragma once
#include "../core/Common.h"

namespace t7r {
constexpr uint32_t COMBAT_PROFILES_OFFSET = 0x4C0000, COMBAT_PROFILE_CODE_OFFSET = 0x4D1000;
constexpr uint32_t MAX_COMBAT_PROFILES = 2048, COMBAT_PROFILE_STRIDE = 32;
// ECX entity, EDX weapon tid -> EAX immutable profile entry or zero. Callee-saved registers survive.
Bytes buildCombatProfileLookup(uint32_t address, uint32_t imageBase);
void writeCombatProfiles(Bytes& page, const std::vector<CombatProfile>& profiles);
}
