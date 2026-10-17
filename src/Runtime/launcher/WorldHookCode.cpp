#include "ClientWorld.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"
#include "SprintHookCode.h"
#include "ChatInputHookCode.h"
#include "WeaponTrailHookCode.h"
#include "RoundEndHookCode.h"
#include "MovementAnimationHookCode.h"
#include "MovementCommandHookCode.h"
#include "CombatProfileHookCode.h"
#include <algorithm>
#include <cstring>

namespace t7r {
namespace {
constexpr uint32_t RID_LOOKUP_RVA = 0x647770, COMPONENT_RVA = 0x26A1C0, POSITION_RVA = 0x3D1200;
constexpr uint32_t RID_MANAGER_RVA = 0x2E3FA8C, ENTITY_MANAGER_RVA = 0x2D27358;
constexpr uint32_t INSTANCE_RVA = 0x2E37804, LOCAL_ACTOR_RVA = 0x2E405F8;
constexpr uint32_t MOVABLE_NAME_RVA = 0x177867C, MOVABLE_VTABLE_RVA = 0x17784E4;
constexpr uint32_t PLAYER_NAME_RVA = 0x179ED94, PLAYER_VTABLE_RVA = 0x179ECE4, IS_DEAD_RVA = 0x64C9D0;
constexpr uint32_t CONTROLLER_POSITION_RVA = 0x2956F0;
constexpr uint32_t HEADING_RVA = 0x3D11F0, CONTROLLER_HEADING_RVA = 0x3D11B0;
constexpr uint32_t ACTOR_STRIDE = sizeof(ActorMailbox), TARGET_TERRAIN = offsetof(ActorMailbox, targetTerrain);
}
void verifyWorldFunctions(HANDLE process, uint32_t imageBase) {
    verifyTerrainFunctions(process, imageBase);
    verifySprintFunctions(process, imageBase);
    verifyChatInputFunctions(process, imageBase);
    verifyWeaponTrailFunctions(process, imageBase);
    verifyRoundEndFunctions(process, imageBase);
    verifyMovementCommandFunction(process, imageBase);
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {RID_LOOKUP_RVA, {0x55,0x8B,0xEC,0xFF,0x75,0x08}},
        {COMPONENT_RVA, {0x55,0x8B,0xEC,0x8B,0x55,0x08,0x56,0x8B,0x71,0x7C}},
        {IS_DEAD_RVA, {0x83,0x79,0x34,0x01,0x0F,0x95,0xC0,0xC3}},
        {POSITION_RVA, {0x55,0x8B,0xEC,0x8B,0x49,0x38,0x83,0xEC,0x0C}},
        {CONTROLLER_POSITION_RVA, {0x53,0x8B,0xDC,0x83,0xEC,0x08,0x83,0xE4,0xF0}},
        {HEADING_RVA, {0x8B,0x49,0x38,0x8B,0x01,0x8B,0x40,0x74,0xFF,0xE0}},
        {CONTROLLER_HEADING_RVA, {0xD9,0x81,0xAC,0x00,0x00,0x00,0xC3}}
    }) {
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("world reader function signature mismatch");
    }
}
Bytes buildWorldHook(uint32_t address, uint32_t imageBase, uint32_t threadId, const DashAttackProfile& dash,
                     const MovementAnimationProfile& animation, const std::vector<CombatProfile>& profiles) {
    if (!address || address > UINT32_MAX - WORLD_PAGE_SIZE || !imageBase
        || imageBase > UINT32_MAX - LOCAL_ACTOR_RVA - 4 || !threadId)
        throw std::runtime_error("world hook outside x86 range");
    WorldHookCode c;
    c.emit({0x9C,0x60,0x64,0xA1}); c.word(0x24);
    c.emit({0x3D}); c.word(threadId); const auto wrongThread = c.branch(0x85);
    c.call(address + CHAT_INPUT_CODE_OFFSET);
    c.call(address + SPRINT_CODE_OFFSET);
    c.call(address + WEAPON_TRAIL_CODE_OFFSET);
    c.call(address + ROUND_END_CODE_OFFSET);
    c.emit({0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC}); // FXSAVE; CLD
    std::vector<size_t> invalid;
    auto nonzero = [&](unsigned char modrm) {
        c.emit({0x85,modrm}); invalid.push_back(c.branch(0x84));
    };
    c.emit({0xA1}); c.word(imageBase + LOCAL_ACTOR_RVA); nonzero(0xC0);
    // World simulation continues while the local player is choosing or observing.
    c.emit({0x83,0x78,0x38,1}); const auto choosing = c.branch(0x84);
    c.emit({0x83,0x78,0x38,3}); const auto observing = c.branch(0x84);
    c.emit({0x83,0x78,0x38,4}); invalid.push_back(c.branch(0x85));
    c.bind(choosing); c.bind(observing);
    c.emit({0x8B,0x35}); c.word(imageBase + INSTANCE_RVA); nonzero(0xF6);
    const auto frame = address + FRAME_DATA_OFFSET;
    const auto response = frame + static_cast<uint32_t>(sizeof(ContactFrameMailbox));
    c.emit({0xFF,0x15}); c.word(imageBase + GET_TICK_COUNT_IAT_RVA);
    c.emit({0xA3}); c.word(frame); c.emit({0xA3}); c.word(address + BLADE_DATA_OFFSET);
    for (uint32_t offset : {0u, 4u}) {
        c.emit({0x8B,0x46,static_cast<unsigned char>(0x68 + offset),0xA3}); c.word(frame + 16 + offset);
    }
    c.emit({0xA1}); c.word(imageBase + RID_MANAGER_RVA); nonzero(0xC0);
    c.emit({0x83,0xB8}); c.word(0x274); c.emit({0}); invalid.push_back(c.branch(0x84));
    c.emit({0xA1}); c.word(imageBase + ENTITY_MANAGER_RVA); nonzero(0xC0);
    const auto live = address + LIVE_ACTORS_OFFSET, batch = address + ACTORS_DATA_OFFSET;
    const auto ridAddress = batch + static_cast<uint32_t>(MAX_WORLD_ACTORS * sizeof(ActorMailbox));
    c.store(ridAddress, 1);
    c.emit({0xBF}); c.word(live); // Live sampling never waits for business acknowledgement.
    const auto poseLoop = c.bytes.size();
    c.emit({0xC7,0x07,0,0,0,0});
    std::vector<size_t> missing;
    auto actorNonzero = [&](unsigned char modrm) {
        c.emit({0x85,modrm}); missing.push_back(c.branch(0x84));
    };
    c.emit({0xFF,0x35}); c.word(ridAddress); c.call(imageBase + RID_LOOKUP_RVA);
    c.emit({0x83,0xC4,4,0x83,0xF8,0xFF}); missing.push_back(c.branch(0x84)); // cdecl
    c.emit({0x8B,0x0D}); c.word(imageBase + ENTITY_MANAGER_RVA);
    c.emit({0x8B,0x11,0x50,0xFF,0x52,0x28}); actorNonzero(0xC0);
    c.emit({0x83,0x78,0x7C,0}); missing.push_back(c.branch(0x84));
    c.emit({0x8B,0xD8,0x8B,0xC8,0x68}); c.word(imageBase + PLAYER_NAME_RVA); c.call(imageBase + COMPONENT_RVA);
    actorNonzero(0xC0); // EBX retains the entity while querying its GePlayer life state.
    c.emit({0x81,0x38}); c.word(imageBase + PLAYER_VTABLE_RVA); missing.push_back(c.branch(0x85));
    c.emit({0x8B,0xC8}); c.call(imageBase + IS_DEAD_RVA);
    c.emit({0x84,0xC0}); missing.push_back(c.branch(0x85)); // Corpses cannot consume a target ray.
    c.emit({0x8B,0xCB}); c.call(address + BLADE_POSE_CODE_OFFSET);
    c.emit({0x8B,0xCB,0x68}); c.word(imageBase + MOVABLE_NAME_RVA); c.call(imageBase + COMPONENT_RVA);
    actorNonzero(0xC0);
    c.emit({0x81,0x38}); c.word(imageBase + MOVABLE_VTABLE_RVA); missing.push_back(c.branch(0x85));
    c.emit({0x8B,0xD8,0x8B,0x50,0x38}); actorNonzero(0xD2); // EBX retains this entity's movable.
    c.emit({0x8B,0x02,0x81,0x78,0x34}); c.word(imageBase + CONTROLLER_POSITION_RVA);
    missing.push_back(c.branch(0x85));
    c.emit({0x81,0x78,0x74}); c.word(imageBase + CONTROLLER_HEADING_RVA);
    missing.push_back(c.branch(0x85));
    c.emit({0x8B,0x82}); c.word(0xA8); actorNonzero(0xC0);
    c.emit({0x83,0x78,0x14,0}); missing.push_back(c.branch(0x84));
    // Read orientation and position from the same entity on the same game frame.
    c.emit({0x8B,0xCB}); c.call(imageBase + HEADING_RVA);
    c.emit({0xD9,0x5F,20}); // FSTP yaw.
    // The original getter returns protocol-space XYZ (including the controller's vertical offset).
    c.emit({0x8B,0xCB,0x8D,0x47,8,0x50}); c.call(imageBase + POSITION_RVA);
    c.emit({0x0F,0xB6,0x43,0x20,0x89,0x87});
    c.word(offsetof(ActorMailbox, blade) + offsetof(BladeMailbox, airborne));

    for (unsigned char offset : std::array<unsigned char, 4>{8, 12, 16, 20}) {
        c.emit({0x8B,0x47,offset,0x25}); c.word(0x7FFFFFFF);
        c.emit({0x3D}); c.word(0x47C35000);
        missing.push_back(c.branch(0x83));
    }
    constexpr uint32_t guard = offsetof(ActorMailbox, blade) + offsetof(BladeMailbox, guard);
    c.emit({0xD9,0x47,20,0xD9,0xFB,0xD9,0x9F}); c.word(guard + offsetof(GuardGeometry, cosYaw));
    c.emit({0xD9,0x9F}); c.word(guard + offsetof(GuardGeometry, sinYaw));
    auto immediateFloat = [&](float value) {
        uint32_t bits; std::memcpy(&bits, &value, 4); c.emit({0xB8}); c.word(bits);
        c.emit({0x66,0x0F,0x6E,0xC8});
    };
    for (uint32_t axis = 0; axis < 3; ++axis) {
        immediateFloat(axis == 2 ? GUARD_HEIGHT_OFFSET : GUARD_FORWARD_OFFSET);
        if (axis < 2) {
            c.emit({0xF3,0x0F,0x10,0x87}); c.word(guard + (axis == 0 ? offsetof(GuardGeometry, cosYaw) : offsetof(GuardGeometry, sinYaw)));
            c.emit({0xF3,0x0F,0x59,0xC8});
        }
        c.emit({0xF3,0x0F,0x10,0x47,static_cast<unsigned char>(8 + axis * 4)});
        c.emit({0xF3,0x0F,static_cast<unsigned char>(axis == 1 ? 0x5C : 0x58),0xC1});
        c.emit({0xF3,0x0F,0x11,0x87}); c.word(guard + axis * 4);
    }
    c.emit({0xC7,0x07,1,0,0,0});
    for (auto branch : missing) c.bind(branch);
    c.emit({0x81,0xC7}); c.word(ACTOR_STRIDE); c.emit({0xFF,0x05}); c.word(ridAddress);
    c.emit({0x83,0x3D}); c.word(ridAddress); c.emit({static_cast<unsigned char>(MAX_WORLD_ACTORS + 1)});
    c.bind(c.branch(0x82), poseLoop);
    c.call(address + BLADE_SWEEP_CODE_OFFSET);
    c.call(address + CONTACT_CODE_OFFSET);
    c.emit({0x83,0x3D}); c.word(address); c.emit({1}); const auto unrequested = c.branch(0x85);
    c.store(address, 2); c.store(address + 4, 0);
    // Preserve host-selected terrain targets; freeze poses/contacts only when a response is requested.
    c.emit({0xBB}); c.word(live); c.emit({0xBA}); c.word(batch);
    c.emit({0xB9}); c.word(MAX_WORLD_ACTORS);
    const auto targetsLoop = c.bytes.size();
    for (uint32_t offset : {static_cast<uint32_t>(offsetof(ActorMailbox, targetId)),
            static_cast<uint32_t>(offsetof(ActorMailbox, targetMask)),
            static_cast<uint32_t>(offsetof(ActorMailbox, targetMask) + 4)}) {
        c.emit({0x8B,0x82}); c.word(offset); c.emit({0x89,0x83}); c.word(offset);
    }
    c.emit({0x81,0xC3}); c.word(ACTOR_STRIDE); c.emit({0x81,0xC2}); c.word(ACTOR_STRIDE);
    c.emit({0x49}); c.bind(c.branch(0x85), targetsLoop);
    auto freeze = [&](uint32_t from, uint32_t to, uint32_t words) {
        c.emit({0x56,0xBE}); c.word(from); c.emit({0xBF}); c.word(to);
        c.emit({0xB9}); c.word(words); c.emit({0xF3,0xA5,0x5E});
    };
    auto resetAttempts = [&] {
        c.emit({0xBF}); c.word(live + offsetof(ActorMailbox, forwardAttemptTick));
        c.emit({0xB9}); c.word(MAX_WORLD_ACTORS);
        const auto loop = c.bytes.size();
        c.emit({0xC7,0x07,0,0,0,0,0x81,0xC7}); c.word(ACTOR_STRIDE);
        c.emit({0x49}); c.bind(c.branch(0x85), loop);
    };
    freeze(live, batch, static_cast<uint32_t>(MAX_WORLD_ACTORS) * ACTOR_STRIDE / 4);
    resetAttempts();
    c.emit({0xFF,0x05}); c.word(frame + offsetof(ContactFrameMailbox, batchId));
    freeze(frame, response, sizeof(ContactFrameMailbox) / 4);
    freeze(frame + 16, address + 8, 2);
    c.emit({0x56,0xBE}); c.word(address + LIVE_CONTACTS_OFFSET);
    c.emit({0xBF}); c.word(address + CONTACTS_DATA_OFFSET);
    c.emit({0x69,0x0D}); c.word(frame + 4); c.word(sizeof(ContactEventMailbox) / 4);
    c.emit({0xF3,0xA5,0x5E});
    c.store(frame + 4, 0); c.store(frame + 8, 0); c.store(frame + 12, 0);
    c.emit({0xBF}); c.word(batch);
    const auto queryLoop = c.bytes.size();
    c.emit({0xC7,0x47,24,0,0,0,0,0xC7,0x47,72,0,0,0,0});
    for (uint32_t i = 0; i < MAX_WORLD_ACTORS; ++i) {
        c.emit({0xC7,0x87}); c.word(TARGET_TERRAIN + i * sizeof(TerrainMailbox)); c.word(0);
    }
    c.emit({0x83,0x3F,1}); const auto noSource = c.branch(0x85);
    auto fromRecord = [&](unsigned char offset, uint32_t destination) {
        c.emit({0x8B,0x47,offset,0xA3}); c.word(destination);
    };
    for (unsigned char i : std::array<unsigned char, 3>{0, 4, 8}) fromRecord(8 + i, address + 16 + i);
    fromRecord(20, address + 40);
    c.store(address + 240, 0); // Scratch target availability; no zero-length fallback ray.
    c.store(address + TERRAIN_TARGET_ONLY_OFFSET, 0);
    c.call(address + TERRAIN_CODE_OFFSET);
    for (uint32_t i = 0; i < sizeof(TerrainMailbox); i += 4) {
        c.emit({0xA1}); c.word(address + 48 + i);
        c.emit({0x89,0x47,static_cast<unsigned char>(24 + i)});
    }
    // One forward ray per source, then one independent segment for each requested enemy.
    c.store(address + TERRAIN_TARGET_ONLY_OFFSET, 1);
    c.emit({0x33,0xD2,0x8D,0x9F}); c.word(TARGET_TERRAIN); // EDX target index, EBX result slot.
    const auto targetLoop = c.bytes.size();
    c.emit({0x0F,0xA3,0x97}); c.word(offsetof(ActorMailbox, targetMask)); const auto notRequested = c.branch(0x83);
    c.emit({0x69,0xC2}); c.word(ACTOR_STRIDE); c.emit({0x05}); c.word(batch);
    c.emit({0x83,0x38,1}); const auto missingTarget = c.branch(0x85);
    for (unsigned char i : std::array<unsigned char, 3>{0, 4, 8}) {
        c.emit({0x8B,0x48,static_cast<unsigned char>(8 + i),0x89,0x0D}); c.word(address + 28 + i);
    }
    c.store(address + 240, 1);
    c.call(address + TERRAIN_CODE_OFFSET);
    for (unsigned char i = 0; i < sizeof(TerrainMailbox); i += 4) {
        c.emit({0xA1}); c.word(address + 96 + i); c.emit({0x89,0x43,i});
    }
    c.emit({0x8D,0x42,1,0x3B,0x47,4}); const auto notNearest = c.branch(0x85);
    for (unsigned char i = 0; i < sizeof(TerrainMailbox); i += 4) {
        c.emit({0x8B,0x43,i,0x89,0x47,static_cast<unsigned char>(72 + i)});
    }
    c.bind(notNearest); c.bind(missingTarget); c.bind(notRequested);
    c.emit({0x83,0xC3,48,0x42,0x83,0xFA,static_cast<unsigned char>(MAX_WORLD_ACTORS)}); c.bind(c.branch(0x82), targetLoop);
    c.bind(noSource);
    c.emit({0x81,0xC7}); c.word(ACTOR_STRIDE); c.emit({0x81,0xFF}); c.word(ridAddress);
    c.bind(c.branch(0x82), queryLoop);
    c.emit({0x3B,0x35}); c.word(imageBase + INSTANCE_RVA); invalid.push_back(c.branch(0x85));
    for (uint32_t offset : {0u, 4u}) {
        c.emit({0x8B,0x46,static_cast<unsigned char>(0x68 + offset),0x3B,0x05}); c.word(address + 8 + offset);
        invalid.push_back(c.branch(0x85));
    }
    c.store(address + 4, 1);
    const auto complete = c.jump();
    for (auto branch : invalid) c.bind(branch);
    // An invalid game frame breaks sweep continuity but does not replay the last response.
    c.emit({0xBF}); c.word(live); c.emit({0xB9}); c.word(MAX_WORLD_ACTORS);
    const auto resetLoop = c.bytes.size();
    for (uint32_t offset : {static_cast<uint32_t>(offsetof(ActorMailbox, blade) + offsetof(BladeMailbox, previousWeapon)),
            static_cast<uint32_t>(offsetof(ActorMailbox, blade) + offsetof(BladeMailbox, previousMotionEntity))}) {
        c.emit({0xC7,0x87}); c.word(offset); c.word(0);
    }
    c.emit({0x81,0xC7}); c.word(ACTOR_STRIDE); c.emit({0x49}); c.bind(c.branch(0x85), resetLoop);
    resetAttempts();
    c.emit({0x83,0x3D}); c.word(address); c.emit({0}); const auto invalidUnrequested = c.branch(0x84);
    c.store(address + 4, 0); c.store(response + 4, 0); c.store(response + 8, 0);
    c.bind(complete);
    c.store(address, 0); // Publish response last; live storage continues on subsequent game frames.
    c.bind(unrequested); c.bind(invalidUnrequested);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5}); // FXRSTOR; restore saved stack
    c.bind(wrongThread); c.emit({0x61,0x9D,0xC3});
    if (c.bytes.size() > TERRAIN_CODE_OFFSET - WORLD_CODE_OFFSET) throw std::runtime_error("world hook overflow");
    Bytes page(WORLD_PAGE_SIZE, 0);
    std::memcpy(page.data() + WORLD_CODE_OFFSET, c.bytes.data(), c.bytes.size());
    const auto terrain = buildTerrainHook(address, imageBase);
    std::memcpy(page.data() + TERRAIN_CODE_OFFSET, terrain.data(), terrain.size());
    const auto sprint = buildSprintHook(address, imageBase, dash, !profiles.empty());
    std::memcpy(page.data() + SPRINT_CODE_OFFSET, sprint.data(), sprint.size());
    const auto jumpPhysics = buildJumpPhysicsHook(address, imageBase, threadId);
    static_assert(JUMP_PHYSICS_CODE_OFFSET + 0x400 <= CHAT_INPUT_CODE_OFFSET, "jump physics code capacity");
    std::memcpy(page.data() + JUMP_PHYSICS_CODE_OFFSET, jumpPhysics.data(), jumpPhysics.size());
    const auto chatInput = buildChatInputHook(address, imageBase);
    std::memcpy(page.data() + CHAT_INPUT_CODE_OFFSET, chatInput.data(), chatInput.size());
    const auto bladePose = buildBladePoseHook(address, imageBase);
    std::memcpy(page.data() + BLADE_POSE_CODE_OFFSET, bladePose.data(), bladePose.size());
    const auto bladeSweep = buildBladeSweepHook(address, imageBase);
    std::memcpy(page.data() + BLADE_SWEEP_CODE_OFFSET, bladeSweep.data(), bladeSweep.size());
    const auto contactFrame = buildContactFrameHook(address);
    if (contactFrame.size() > WEAPON_GUARD_CODE_OFFSET - CONTACT_CODE_OFFSET)
        throw std::runtime_error("contact journal overlaps weapon guard hook");
    std::memcpy(page.data() + CONTACT_CODE_OFFSET, contactFrame.data(), contactFrame.size());
    const auto weaponGuard = buildWeaponGuardHook(address);
    if (weaponGuard.size() > MOVEMENT_COMMAND_CODE_OFFSET - WEAPON_GUARD_CODE_OFFSET)
        throw std::runtime_error("weapon guard overlaps movement command hook");
    std::copy(weaponGuard.begin(), weaponGuard.end(), page.begin() + WEAPON_GUARD_CODE_OFFSET);
    const auto movementCommand = buildMovementCommandHook(address, imageBase, threadId);
    static_assert(MOVEMENT_COMMAND_CODE_OFFSET + 0x1000 <= MOVEMENT_ANIMATION_CODE_OFFSET, "movement command code capacity");
    std::copy(movementCommand.begin(), movementCommand.end(), page.begin() + MOVEMENT_COMMAND_CODE_OFFSET);
    const auto airInput = buildAirInputHook(address, imageBase, threadId);
    std::copy(airInput.begin(), airInput.end(), page.begin() + AIR_INPUT_CODE_OFFSET);
    const auto crouchAnimation = buildCrouchAnimationHook(address, imageBase, threadId);
    static_assert(CROUCH_ANIMATION_CODE_OFFSET + 0x400 <= MOVEMENT_ANIMATION_CODE_OFFSET, "crouch animation code capacity");
    std::copy(crouchAnimation.begin(), crouchAnimation.end(), page.begin() + CROUCH_ANIMATION_CODE_OFFSET);
    const auto movementAnimation = buildMovementAnimationHook(address, imageBase, threadId, animation, !profiles.empty());
    static_assert(MOVEMENT_ANIMATION_CODE_OFFSET + 0x1000 <= ROUND_END_CODE_OFFSET, "movement animation code capacity");
    std::copy(movementAnimation.begin(), movementAnimation.end(), page.begin() + MOVEMENT_ANIMATION_CODE_OFFSET);
    writeSprintDirections(page);
    const auto roundEnd = buildRoundEndHooks(address, imageBase);
    static_assert(ROUND_END_CODE_OFFSET + 0x1000 <= LIVE_ACTORS_OFFSET, "round end code capacity");
    static_assert(ROUND_END_DATA_OFFSET + sizeof(RoundEndMailbox) <= WORLD_CODE_OFFSET, "round end data capacity");
    std::memcpy(page.data() + ROUND_END_CODE_OFFSET, roundEnd.data(), roundEnd.size());
    const auto weaponTrail = buildWeaponTrailHook(address, imageBase);
    std::memcpy(page.data() + WEAPON_TRAIL_CODE_OFFSET, weaponTrail.data(), weaponTrail.size());
    writeWeaponTrailPreset(page);
    writeCombatProfiles(page, profiles);
    const auto lookup = buildCombatProfileLookup(address, imageBase);
    static_assert(CONTACTS_DATA_OFFSET + MAX_FRAME_CONTACTS * sizeof(ContactEventMailbox) <= COMBAT_PROFILES_OFFSET,
                  "contact journal overlaps combat profiles");
    static_assert(COMBAT_PROFILE_CODE_OFFSET + 0x1000 <= ACTORS_DATA_OFFSET, "combat code overlaps response actors");
    if (lookup.size() > 0x1000) throw std::runtime_error("combat profile code capacity");
    std::copy(lookup.begin(), lookup.end(), page.begin() + COMBAT_PROFILE_CODE_OFFSET);
    return page;
}
}
