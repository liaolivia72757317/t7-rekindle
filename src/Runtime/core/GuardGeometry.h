#pragma once
#include <array>
#include <cstdint>

namespace t7r {
// Project query geometry, in protocol-space metres. Axes are forward, lateral, up.
inline constexpr float GUARD_FORWARD_OFFSET = .65f, GUARD_HEIGHT_OFFSET = 1.05f;
inline constexpr std::array<float, 3> GUARD_HALF_EXTENTS{.15f, .6f, .9f};
inline constexpr std::array<uint32_t, 12> GUARD_VOLUME_STATES{223,224,225,226,231,232,233,234,235,241,247,253};
inline constexpr bool guardPrepareState(uint32_t state) { return state == 235 || state == 241 || state == 247 || state == 253; }
inline constexpr bool guardHoldState(uint32_t state) { return (state >= 223 && state <= 226) || (state >= 231 && state <= 234); }
struct GuardGeometry {
    std::array<float, 3> center{};
    float cosYaw = 1, sinYaw = 0;
};
}
