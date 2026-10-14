#pragma once
#include <array>
#include <cstdint>
#include <vector>

namespace t7 {
struct TerrainContact {
    bool ready = false, hit = false;
    int32_t material = -1;
    std::array<float, 3> position{}, normal{}, direction{};
};
constexpr size_t MAX_WORLD_ACTORS = 20;
constexpr size_t MAX_BLADE_CONTACTS = MAX_WORLD_ACTORS + 2; // Blocking scene, actor RIDs, non-blocking ground.
struct BladeContact {
    TerrainContact contact{};
    uint32_t targetId = 0; // Zero is scene geometry; otherwise the actor RID.
    float time = 1, along = 1;
};
struct BladeTrace {
    bool ready = false;
    bool poseReady = false;
    uint32_t spanMs = 0;
    float length = 0;
    std::array<float, 3> base{}, tip{};
    std::array<BladeContact, MAX_BLADE_CONTACTS> contacts{};
};
struct ActorSnapshot {
    bool ready = false;
    uint32_t targetId = 0;
    std::array<float, 3> position{};
    float heading = 0;
    std::array<TerrainContact, 2> terrain{};
    std::array<TerrainContact, MAX_WORLD_ACTORS> targetTerrain{}; // Indexed by target RID - 1.
    BladeTrace blade{};
    uint32_t entity = 0, state = 0, sequence = 0;
    uint64_t phaseAt = 0;
};
struct BladeContactEvent {
    BladeContact hit{};
    uint32_t actorId = 0, entity = 0, targetEntity = 0, state = 0, sequence = 0;
    uint32_t targetState = 0, targetSequence = 0;
    uint64_t instanceStartedAt = 0, phaseAt = 0, targetPhaseAt = 0;
    double occurredAt = 0;
    std::array<float, 3> sourcePosition{}, targetPosition{};
    float sourceHeading = 0, targetHeading = 0;
    bool weaponContact = false;
};
// Internal Runtime -> Business data, not part of the launcher C ABI.
struct WorldSnapshot {
    bool ready = false;
    uint64_t sampledAt = 0, instanceStartedAt = 0;
    std::array<float, 3> player{}, bot{};
    float playerHeading = 0, botHeading = 0; // Degrees, normalized to [-180, 180].
    // Per actor: forward reach, then the segment ending at the opponent (not beyond it).
    std::array<std::array<TerrainContact, 2>, 2> terrain{};
    std::array<ActorSnapshot, MAX_WORLD_ACTORS> actors{};
    bool contactsReady = false;
    uint32_t contactFrames = 0, droppedContacts = 0, contactBatchId = 0;
    std::vector<BladeContactEvent> contacts{};
};
}
