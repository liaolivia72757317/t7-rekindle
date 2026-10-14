#pragma once
#include "../core/Common.h"
#include "../core/WorldSnapshot.h"
#include "BladeParryContacts.h"

namespace t7 {
struct TerrainMailbox {
    uint32_t status = 0; // 0 unavailable, 1 clear, 2 hit.
    int32_t material = -1;
    std::array<float, 3> from{}, to{}, normal{};
    float fraction = 1;
};
static_assert(sizeof(TerrainMailbox) == 48, "terrain mailbox layout");
struct WorldMailbox {
    uint32_t command = 0, ready = 0;
    uint64_t instanceStartedAt = 0;
    std::array<float, 3> player{}, bot{};
    float playerYaw = 0, botYaw = 0; // Original controller radians.
    std::array<TerrainMailbox, 4> terrain{};
};
static_assert(sizeof(WorldMailbox) == 240, "world mailbox layout");
constexpr uint32_t MAX_BLADE_BODIES = 32;
struct BladeContactMailbox {
    TerrainMailbox contact{};
    float time = 1, along = 1;
};
static_assert(sizeof(BladeContactMailbox) == 56, "blade contact layout");
struct BladeMailbox {
    uint32_t bodyCount = 0, bodyWorld = 0;
    std::array<uint32_t, MAX_BLADE_BODIES> roots{};
    uint32_t poseReady = 0, weaponEntity = 0;
    std::array<float, 3> base{}, tip{};
    uint32_t traceReady = 0, spanMs = 0, previousTick = 0, previousWeapon = 0;
    uint64_t previousEpoch = 0;
    std::array<float, 3> previousBase{}, previousTip{};
    std::array<BladeContactMailbox, MAX_BLADE_CONTACTS> contacts{};
    uint32_t actorEntity = 0, state = 0, sequence = 0, phaseTick = 0;
    uint32_t previousSequence = 0, previousEntity = 0;
};
struct ActorMailbox {
    uint32_t ready = 0, targetId = 0;
    std::array<float, 3> position{};
    float yaw = 0;
    std::array<TerrainMailbox, 2> terrain{};
    uint32_t targetMask = 0;
    std::array<TerrainMailbox, MAX_WORLD_ACTORS> targetTerrain{};
    BladeMailbox blade{};
};
static_assert(offsetof(ActorMailbox, targetTerrain) == 124, "actor mailbox layout");
constexpr uint32_t ACTORS_DATA_OFFSET = 0x9000;
constexpr uint32_t WORLD_CODE_OFFSET = 0x1000, WORLD_PAGE_SIZE = 0x270000;
constexpr uint32_t TERRAIN_CODE_OFFSET = 0x2000;
constexpr uint32_t BLADE_POSE_CODE_OFFSET = 0x5000, BLADE_SWEEP_CODE_OFFSET = 0x6000;
constexpr uint32_t BLADE_DATA_OFFSET = 0x18000;
constexpr uint32_t TERRAIN_TARGET_ONLY_OFFSET = 244;
constexpr uint32_t FRAME_DATA_OFFSET = 0x19000, CONTACT_CODE_OFFSET = 0x1A000;
constexpr uint32_t LIVE_ACTORS_OFFSET = 0x20000;
constexpr uint32_t LIVE_CONTACTS_OFFSET = 0x30000, CONTACTS_DATA_OFFSET = 0x150000;
constexpr uint32_t MAX_FRAME_CONTACTS = 8192, GET_TICK_COUNT_IAT_RVA = 0x16F6228;
struct ContactFrameMailbox {
    uint32_t tick = 0, count = 0, dropped = 0, frames = 0;
    uint64_t epoch = 0;
    uint32_t batchId = 0, reserved = 0;
};
// Only the game thread writes live storage. Response storage stays immutable until the next request.
struct ContactEventMailbox {
    uint32_t actorId = 0, targetId = 0, entity = 0, targetEntity = 0;
    uint32_t state = 0, sequence = 0, phaseTick = 0, tick = 0, spanMs = 0;
    uint32_t targetState = 0, targetSequence = 0, targetPhaseTick = 0;
    uint64_t epoch = 0;
    std::array<float, 3> sourcePosition{}, targetPosition{};
    float sourceYaw = 0, targetYaw = 0;
    BladeContactMailbox hit{};
};
static_assert(sizeof(ContactEventMailbox) == 144, "contact journal layout");
static_assert(LIVE_ACTORS_OFFSET + MAX_WORLD_ACTORS * sizeof(ActorMailbox) + 4 <= LIVE_CONTACTS_OFFSET, "live actors capacity");
static_assert(LIVE_CONTACTS_OFFSET + MAX_FRAME_CONTACTS * sizeof(ContactEventMailbox) <= CONTACTS_DATA_OFFSET, "live contact capacity");
static_assert(CONTACTS_DATA_OFFSET + MAX_FRAME_CONTACTS * sizeof(ContactEventMailbox) <= WORLD_PAGE_SIZE, "contact response capacity");
static_assert(ACTORS_DATA_OFFSET + MAX_WORLD_ACTORS * sizeof(ActorMailbox) + 4 <= BLADE_DATA_OFFSET, "world mailbox capacity");
void verifyWorldFunctions(HANDLE process, uint32_t imageBase);
void verifyTerrainFunctions(HANDLE process, uint32_t imageBase);
Bytes buildTerrainHook(uint32_t address, uint32_t imageBase);
Bytes buildWorldHook(uint32_t address, uint32_t imageBase, uint32_t threadId, const DashAttackProfile& dash = {},
                     const MovementAnimationProfile& animation = {}, bool customLobby = false);
Bytes buildBladePoseHook(uint32_t address, uint32_t imageBase);
Bytes buildBladeSweepHook(uint32_t address, uint32_t imageBase);
Bytes buildContactFrameHook(uint32_t address);

class ClientWorld final {
public:
    void attach(HANDLE process, uint32_t address);
    void clear();
    bool poll(WorldSnapshot& value);
private:
    void request();
    HANDLE process_ = nullptr;
    uint32_t remote_ = 0;
    ULONGLONG requestedAt_ = 0;
    bool pending_ = false;
    std::array<uint32_t, MAX_WORLD_ACTORS> targets_{};
    std::array<uint32_t, MAX_WORLD_ACTORS> targetMasks_{};
    BladeParryContacts parryContacts_;
};
}
