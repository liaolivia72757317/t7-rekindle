#include "MovementAnimationHookCode.h"
#include "RemoteImage.h"
#include "SprintHookCode.h"
#include "WorldHookCode.h"
#include "CombatProfileHookCode.h"
#include <cmath>

namespace t7r {
namespace {
constexpr uint32_t WRITER_RVA = 0x3D2BF2, RETURN_RVA = 0x3D2C0B, CONSTANTS_OFFSET = 0x700;
constexpr uint32_t POSE_OFFSET = 0x800, DEFENSE_EXIT_OFFSET = 0xC00;
constexpr uint32_t VARIABLE_WRITER_RVA = 0x29ED60;
}
void verifyMovementAnimationFunction(HANDLE process, uint32_t imageBase) {
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {MOVEMENT_ANIMATION_RVA, {0xF3,0x0F,0x10,0x46,0x5C,0xF3,0x0F,0x10,0x4C,0x24,0x10}},
        {WRITER_RVA, {0x8B,0x4E,0x58,0xF3,0x0F,0x10,0x46,0x5C,0x8B,0x01,0x51,0xF3,0x0F,0x11,0x04,0x24}},
        {RETURN_RVA, {0x5F,0x5E,0x5B,0x8B,0xE5,0x5D,0xC3}},
        {MOVEMENT_POSE_RVA, MOVEMENT_POSE_ORIGINAL},
        {DEFENSE_EXIT_RVA, DEFENSE_EXIT_ORIGINAL},
        {VARIABLE_WRITER_RVA, {0x55,0x8B,0xEC,0x57,0x8B,0x7D,8}}
    }) {
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("movement animation writer signature mismatch");
    }
}
Bytes movementAnimationJump(uint32_t address, uint32_t imageBase) {
    WorldHookCode c; c.emit({0xE9});
    c.word(address + MOVEMENT_ANIMATION_CODE_OFFSET - (imageBase + MOVEMENT_ANIMATION_RVA + 5));
    return c.bytes;
}
Bytes movementPoseJump(uint32_t address, uint32_t imageBase) {
    WorldHookCode c; c.emit({0xE9});
    c.word(address + MOVEMENT_ANIMATION_CODE_OFFSET + POSE_OFFSET - (imageBase + MOVEMENT_POSE_RVA + 5));
    return c.bytes;
}
Bytes defenseExitJump(uint32_t address, uint32_t imageBase) {
    WorldHookCode c; c.emit({0xE9});
    c.word(address + MOVEMENT_ANIMATION_CODE_OFFSET + DEFENSE_EXIT_OFFSET - (imageBase + DEFENSE_EXIT_RVA + 5));
    c.bytes.resize(DEFENSE_EXIT_ORIGINAL.size(), 0x90);
    return c.bytes;
}
Bytes buildMovementAnimationHook(uint32_t address, uint32_t imageBase, uint32_t,
                                 const MovementAnimationProfile& profile, bool dynamicProfiles) {
    if (!profile.enabled && !dynamicProfiles) return {};
    if (!dynamicProfiles && profile.weaponId <= 0) throw std::runtime_error("invalid movement animation weapon");
    for (float value : {profile.forwardSlow, profile.lateralSlow, profile.backwardSlow, profile.forwardSprint})
        if (!dynamicProfiles && (!std::isfinite(value) || value <= 0)) throw std::runtime_error("invalid movement animation profile");
    if (!dynamicProfiles && (profile.forwardSprint <= profile.forwardSlow || profile.forwardSprint <= profile.lateralSlow))
        throw std::runtime_error("invalid movement animation gait order");
    const auto code = address + MOVEMENT_ANIMATION_CODE_OFFSET, data = address + SPRINT_DATA_OFFSET;
    const auto constants = code + CONSTANTS_OFFSET;
    WorldHookCode c;
    auto jump = [&](uint32_t target) { c.emit({0xE9}); c.word(target - (code + static_cast<uint32_t>(c.bytes.size()) + 4)); };
    auto restore = [&] { c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D}); };
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    std::vector<size_t> native;
    auto skipPhysicalJump = [&](unsigned char movable) {
        // Loss of ground contact alone still uses locomotion, not the native jump pose.
        c.emit({0x80,movable,0x20,0}); const auto grounded = c.branch(0x84);
        c.emit({0x80,movable,0x22,0}); native.push_back(c.branch(0x85));
        c.bind(grounded);
    };
    // Native animation jobs may run on workers. Scope every hook by the current entity/model, not the startup thread.
    c.emit({0x8B,0x46,8,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x3B,0x05}); c.word(data + 8); native.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(data + 28); c.emit({4}); native.push_back(c.branch(0x85));
    skipPhysicalJump(0x7E);
    auto component = [&](uint32_t name, uint32_t table) {
        c.emit({0x8B,0x4E,8,0x68}); c.word(imageBase + name); c.call(imageBase + 0x26A1C0);
        c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
        c.emit({0x81,0x38}); c.word(imageBase + table); native.push_back(c.branch(0x85));
    };
    component(0x178692C, 0x17868BC);
    c.emit({0x83,0x78,0x60,6}); native.push_back(c.branch(0x82));
    c.emit({0x8B,0x40,0x54,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0xF6,0x00,0x10}); native.push_back(c.branch(0x85)); // Crouch retains its native graph coordinates.
    component(0x1783860, 0x178380C);
    c.emit({0x8B,0x58,0x38});
    if (dynamicProfiles) {
        c.emit({0x8B,0x90}); c.word(0x98); c.emit({0x8B,0x4E,8});
        c.call(address + COMBAT_PROFILE_CODE_OFFSET);
        c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
        c.emit({0x89,0x84,0x24}); c.word(0x204);
    } else {
        c.emit({0x81,0xB8}); c.word(0x98); c.word(static_cast<uint32_t>(profile.weaponId));
        native.push_back(c.branch(0x85));
    }
    c.emit({0x83,0xFB,1}); const auto weaponChange = c.branch(0x84);
    c.emit({0x83,0xFB,2}); const auto idle = c.branch(0x84);
    std::vector<size_t> shield;
    if (dynamicProfiles) for (uint32_t state : {95u, 309u}) {
        c.emit({0x81,0xFB}); c.word(state); shield.push_back(c.branch(0x84));
    }
    c.emit({0x8D,0x83}); c.word(static_cast<uint32_t>(-223));
    c.emit({0x83,0xF8,3}); const auto holding = c.branch(0x86);
    c.emit({0x8D,0x83}); c.word(static_cast<uint32_t>(-305));
    c.emit({0x83,0xF8,3}); native.push_back(c.branch(0x87));
    c.bind(weaponChange); c.bind(idle); c.bind(holding);
    for (auto branch : shield) c.bind(branch);
    c.emit({0x8B,0x46,0x60,0x8D,0x50,0xFF,0x83,0xFA,7}); native.push_back(c.branch(0x87));
    c.emit({0x33,0xFF}); // EDI selects established running, independently of physical velocity.
    auto coordinate = [&](uint32_t offset) {
        if (dynamicProfiles) {
            c.emit({0x8B,0x94,0x24}); c.word(0x204);
            c.emit({0x83,0xC2,static_cast<unsigned char>(offset - 4)});
        } else { c.emit({0xBA}); c.word(constants + offset); }
    };
    coordinate(16); // Forward diagonals share the lateral Slow property.
    c.emit({0x83,0xF8,1}); const auto notForward = c.branch(0x85);
    coordinate(12); c.bind(notForward);
    c.emit({0x8D,0x48,0xFC,0x83,0xF9,2}); const auto notBackward = c.branch(0x87);
    coordinate(20); c.bind(notBackward);
    std::vector<size_t> slow;
    c.emit({0x83,0xFB,1}); const auto weaponChangeGait = c.branch(0x84);
    c.emit({0x83,0xFB,2}); slow.push_back(c.branch(0x85));
    c.bind(weaponChangeGait);
    c.emit({0x83,0x3D}); c.word(data + 16); c.emit({2}); slow.push_back(c.branch(0x85));
    c.emit({0x83,0xF8,2}); const auto forward = c.branch(0x86);
    c.emit({0x83,0xF8,8}); slow.push_back(c.branch(0x85));
    c.bind(forward); c.emit({0x47});
    for (auto branch : slow) c.bind(branch);
    c.emit({0x8B,0xDA}); // EBX Slow coordinate and EDI gait survive native getters.
    c.emit({0x8B,0x4E,0x38,0x85,0xC9}); native.push_back(c.branch(0x84));
    c.emit({0x8B,0x01,0xFF,0x50,0x2C,0x3C,1}); const auto inactive = c.branch(0x85);
    c.emit({0x8B,0x4E,0x38,0x8B,0x01,0xFF,0x50,0x40,0xD9,0x9C,0x24}); c.word(0x200);
    c.emit({0xF3,0x0F,0x10,0x84,0x24}); c.word(0x200);
    c.emit({0x0F,0x2F,0x05}); c.word(constants); const auto belowFloor = c.branch(0x82);
    c.emit({0xF3,0x0F,0x10,0x45,0x34,0xF3,0x0F,0x5F,0x05}); c.word(constants);
    const auto floorApplied = c.jump(); c.bind(inactive); c.bind(belowFloor);
    c.emit({0xF3,0x0F,0x10,0x45,0x34}); // Original [ESP+0x10], before pushfd/pushad.
    c.bind(floorApplied);
    // Both gaits meet at JOG_SPEED -> Slow. Arming a run must not move the blend backwards while velocity catches up.
    c.emit({0x85,0xFF}); const auto slowGait = c.branch(0x84);
    c.emit({0x0F,0x2F,0x05}); c.word(constants + 4); const auto slowVelocity = c.branch(0x82);
    c.emit({0xF3,0x0F,0x5C,0x05}); c.word(constants + 4);
    c.emit({0xF3,0x0F,0x5E,0x05}); c.word(constants + 8);
    c.emit({0xF3,0x0F,0x5D,0x05}); c.word(constants + 28);
    if (dynamicProfiles) {
        c.emit({0x8B,0x8C,0x24}); c.word(0x204); c.emit({0xF3,0x0F,0x10,0x49,20});
    } else { c.emit({0xF3,0x0F,0x10,0x0D}); c.word(constants + 24); }
    c.emit({0xF3,0x0F,0x5C,0x0B,0xF3,0x0F,0x59,0xC1,0xF3,0x0F,0x58,0x03});
    const auto mapped = c.jump(); c.bind(slowGait); c.bind(slowVelocity);
    // Old running velocity is capped immediately when defense selects Slow.
    c.emit({0xF3,0x0F,0x5E,0x05}); c.word(constants + 4);
    c.emit({0xF3,0x0F,0x5D,0x05}); c.word(constants + 28);
    c.emit({0xF3,0x0F,0x59,0x03}); c.bind(mapped);
    c.emit({0x0F,0x2E,0x46,0x5C}); const auto same = c.branch(0x84);
    c.emit({0xF3,0x0F,0x11,0x46,0x5C}); restore(); jump(imageBase + WRITER_RVA);
    c.bind(same); restore(); jump(imageBase + RETURN_RVA);
    for (auto branch : native) c.bind(branch);
    restore(); c.bytes.insert(c.bytes.end(), MOVEMENT_ANIMATION_ORIGINAL.begin(), MOVEMENT_ANIMATION_ORIGINAL.end());
    jump(imageBase + MOVEMENT_ANIMATION_RVA + 5);
    if (c.bytes.size() > CONSTANTS_OFFSET) throw std::runtime_error("movement animation hook overflow");
    c.bytes.resize(CONSTANTS_OFFSET, 0);
    for (float value : {1.5f, JOG_SPEED, RUN_SPEED - JOG_SPEED, profile.forwardSlow, profile.lateralSlow,
                       profile.backwardSlow, profile.forwardSprint, 1.f}) {
        uint32_t bits; std::memcpy(&bits, &value, 4); c.word(bits);
    }
    c.bytes.resize(POSE_OFFSET, 0);
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    native.clear();
    // The native modifier still sees Parry nodes while the upper layer fades out.
    // Once defense has ended, select ordinary locomotion through its existing writer.
    c.emit({0x83,0x7E,0x18,5}); native.push_back(c.branch(0x82));
    c.emit({0x8D,0x56,8,0x83,0x7E,0x1C,15}); const auto inlineName = c.branch(0x86);
    c.emit({0x8B,0x12}); c.bind(inlineName);
    c.emit({0x81,0x3A}); c.word(0x72726150); native.push_back(c.branch(0x85)); // "Parr"
    c.emit({0x80,0x7A,4,'y'}); native.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(data + 28); c.emit({4}); native.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(data + 8); c.emit({0}); native.push_back(c.branch(0x84));
    auto poseComponent = [&](uint32_t name, uint32_t table) {
        c.emit({0x8B,0x0D}); c.word(data + 8); c.emit({0x68}); c.word(imageBase + name);
        c.call(imageBase + 0x26A1C0);
        c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
        c.emit({0x81,0x38}); c.word(imageBase + table); native.push_back(c.branch(0x85));
    };
    auto poseProfile = [&] {
        if (dynamicProfiles) {
            c.emit({0x8B,0x90}); c.word(0x98); c.emit({0x8B,0x0D}); c.word(data + 8);
            c.call(address + COMBAT_PROFILE_CODE_OFFSET);
            c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
        } else {
            c.emit({0x81,0xB8}); c.word(0x98); c.word(static_cast<uint32_t>(profile.weaponId));
            native.push_back(c.branch(0x85));
        }
    };
    poseComponent(0x177867C, 0x17784E4);
    skipPhysicalJump(0x78);
    c.emit({0x8B,0x48,0x60,0x49,0x83,0xF9,7}); native.push_back(c.branch(0x87));
    c.emit({0x8B,0x48,0x58,0x85,0xC9}); native.push_back(c.branch(0x84));
    c.emit({0x81,0x39}); c.word(imageBase + 0x177722C); native.push_back(c.branch(0x85));
    c.emit({0x39,0x79,0x30}); native.push_back(c.branch(0x85)); // EDI is this modifier's model.
    poseComponent(0x1783860, 0x178380C);
    c.emit({0x83,0x78,0x38,2}); native.push_back(c.branch(0x85));
    poseProfile();
    poseComponent(0x178692C, 0x17868BC);
    c.emit({0x83,0x78,0x60,6}); native.push_back(c.branch(0x82));
    c.emit({0x8B,0x40,0x54,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0xF6,0x00,0x10}); native.push_back(c.branch(0x85));
    restore(); c.emit({0x6A,0,0x8B,0xCE}); jump(imageBase + MOVEMENT_POSE_RVA + 5);
    for (auto branch : native) c.bind(branch);
    restore(); c.bytes.insert(c.bytes.end(), MOVEMENT_POSE_ORIGINAL.begin(), MOVEMENT_POSE_ORIGINAL.end());
    jump(imageBase + MOVEMENT_POSE_RVA + 5);
    if (c.bytes.size() > DEFENSE_EXIT_OFFSET) throw std::runtime_error("movement pose hook overflow");
    c.bytes.resize(DEFENSE_EXIT_OFFSET, 0);
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    native.clear();
    // Idle must still reach Layer; only Action's Parry -> Idle transition is suppressed.
    // The existing layer fade then blends the defense pose directly into locomotion.
    c.emit({0x83,0x7D,0x2C,3}); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x75,0x18,0x81,0x3E}); c.word(imageBase + 0x18E10DC); native.push_back(c.branch(0x85));
    c.emit({0x83,0xBE}); c.word(0x84); c.emit({22}); native.push_back(c.branch(0x85));
    auto nodeName = [&](const char* name) {
        c.emit({0x83,0xE0,0xFE,0x85,0xC0}); native.push_back(c.branch(0x84)); // hkStringPtr ownership bit.
        const auto length = std::strlen(name) + 1;
        size_t offset = 0;
        for (; offset + 4 <= length; offset += 4) {
            uint32_t value; std::memcpy(&value, name + offset, 4);
            c.emit({0x81,0x78,static_cast<unsigned char>(offset)}); c.word(value); native.push_back(c.branch(0x85));
        }
        for (; offset < length; ++offset) {
            c.emit({0x80,0x78,static_cast<unsigned char>(offset),static_cast<unsigned char>(name[offset])});
            native.push_back(c.branch(0x85));
        }
    };
    c.emit({0x8B,0x46,0x20}); nodeName("Action");
    c.emit({0x8B,0x5D,0x28,0x85,0xDB}); native.push_back(c.branch(0x84)); // hkbContext.
    c.emit({0x8B,0x43,8,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x81,0x38}); c.word(imageBase + 0x18DEEB4); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x40,0x20}); nodeName("2H_Flurry_Behavior.hkb");
    c.emit({0x8B,0x3B,0x85,0xFF}); native.push_back(c.branch(0x84)); // Context character, not a cached graph pointer.
    c.emit({0x83,0x3D}); c.word(data + 28); c.emit({4}); native.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(data + 8); c.emit({0}); native.push_back(c.branch(0x84));
    poseComponent(0x179ED94, 0x179ECE4);
    c.emit({0x8B,0xC8}); c.call(imageBase + 0x64C9D0);
    c.emit({0x84,0xC0}); native.push_back(c.branch(0x85));
    poseComponent(0x177867C, 0x17784E4);
    skipPhysicalJump(0x78);
    c.emit({0x8B,0x48,0x60,0x49,0x83,0xF9,7}); native.push_back(c.branch(0x87));
    c.emit({0x8B,0x48,0x58,0x85,0xC9}); native.push_back(c.branch(0x84));
    c.emit({0x81,0x39}); c.word(imageBase + 0x177722C); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x49,0x30,0x85,0xC9}); native.push_back(c.branch(0x84));
    c.emit({0x39,0x79,0x10}); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x77,0x44,0x85,0xF6}); native.push_back(c.branch(0x84));
    c.emit({0x81,0x3E}); c.word(imageBase + 0x18DEEB4); native.push_back(c.branch(0x85));
    for (uint32_t offset : {0xA0u, 0xA8u}) {
        c.emit({0x83,0xBE}); c.word(offset); c.emit({0}); native.push_back(c.branch(0x84));
    }
    c.emit({0x83,0x3D}); c.word(imageBase + 0x2D37938); c.emit({0}); native.push_back(c.branch(0x8C));
    poseComponent(0x1783860, 0x178380C);
    c.emit({0x83,0x78,0x38,2}); native.push_back(c.branch(0x85));
    poseProfile();
    poseComponent(0x178692C, 0x17868BC);
    c.emit({0x83,0x78,0x60,6}); native.push_back(c.branch(0x82));
    c.emit({0x8B,0x40,0x54,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0xF6,0x00,0x10}); native.push_back(c.branch(0x85));
    // Clear the root graph's mapped variable now, before the modifier's next scheduled write.
    c.emit({0x6A,0,0xFF,0x35}); c.word(imageBase + 0x2D37938); c.emit({0x56});
    c.call(imageBase + VARIABLE_WRITER_RVA);
    restore(); c.emit({0xC2,0x10,0});
    for (auto branch : native) c.bind(branch);
    restore(); c.bytes.insert(c.bytes.end(), DEFENSE_EXIT_ORIGINAL.begin(), DEFENSE_EXIT_ORIGINAL.end());
    jump(imageBase + DEFENSE_EXIT_RVA + static_cast<uint32_t>(DEFENSE_EXIT_ORIGINAL.size()));
    if (c.bytes.size() > 0x1000) throw std::runtime_error("defense exit hook overflow");
    return c.bytes;
}
}
