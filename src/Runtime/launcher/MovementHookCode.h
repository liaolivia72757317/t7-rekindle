#pragma once
#include "../core/Common.h"
#include <array>

namespace t7 {
struct MovementHookSite { uint32_t rva; std::array<unsigned char, 5> original; };
inline constexpr std::array<MovementHookSite, 3> MOVEMENT_HOOK_SITES{{
    {0x71590, {0x55, 0x8B, 0xEC, 0x6A, 0xFF}}, // resource open
    {0xA4FBB, {0x8B, 0xF8, 0x8D, 0x4F, 0x01}}, // XML pool allocation size
    {0xA4FD9, {0xC6, 0x04, 0x3E, 0x00, 0x56}}, // XML buffer before parse
}};
inline constexpr size_t MOVEMENT_HOOK_PAGE_SIZE = 4096;
struct MovementHookPage {
    Bytes bytes;
    std::array<uint32_t, 3> entries{};
    uint32_t breakpoint = 0;
};
MovementHookPage buildMovementHooks(uint32_t remoteBase, uint32_t imageBase, bool skipStartupAnimation = false);
Bytes movementJump(uint32_t source, uint32_t destination);
}
