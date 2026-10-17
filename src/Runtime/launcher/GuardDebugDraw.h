#pragma once
#include "../core/Common.h"
#include "../core/WorldSnapshot.h"
#include <deque>

namespace t7r {
struct GuardDebugVertex { std::array<float, 3> position{}; uint32_t color = 0; };
static_assert(sizeof(GuardDebugVertex) == 16, "debug line vertex layout");
constexpr uint32_t GUARD_DEBUG_MAX_VERTICES = 32768;
constexpr uint32_t GUARD_DEBUG_STAGING = 0x1000, GUARD_DEBUG_ACTIVE = 0x81000;
constexpr uint32_t GUARD_DEBUG_CODE = 0x101000, GUARD_DEBUG_PAGE_SIZE = 0x103000;
constexpr uint32_t GUARD_DEBUG_UI_RVA = 0x2346A0;
inline const Bytes GUARD_DEBUG_UI_ORIGINAL{0x55,0x8B,0xEC,0x83,0xEC,0x40};
struct GuardDebugMailbox {
    uint32_t enabled = 0, pending = 0, count = 0, sampledTick = 0;
    uint32_t error = 0, frames = 0, drawing = 0, activeTick = 0, activeCount = 0;
    uint32_t stateBlock = 0, stream = 0, streamOffset = 0, streamStride = 0;
};
Bytes buildGuardDebugHook(uint32_t address, uint32_t imageBase);
std::vector<GuardDebugVertex> guardDebugVertices(const WorldSnapshot& world,
                                               const std::deque<BladeContactEvent>& contacts);
class GuardDebugDraw final {
public:
    void install(HANDLE process, uint32_t imageBase); // Before the owned client resumes.
    void update(bool enabled, const WorldSnapshot* world = nullptr);
    void clear(); // After the owned client exits.
private:
    HANDLE process_ = nullptr;
    uint32_t remote_ = 0;
    uint64_t epoch_ = 0;
    bool enabled_ = false, worldReady_ = false;
    std::deque<BladeContactEvent> contacts_;
};
}
