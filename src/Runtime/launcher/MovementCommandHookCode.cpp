#include "MovementCommandHookCode.h"
#include "RemoteImage.h"
#include "SprintHookCode.h"
#include "WorldHookCode.h"
#include "ClientWorld.h"
#include <cstring>

namespace t7r {
namespace {
uint32_t floatBits(float value) {
    uint32_t bits; std::memcpy(&bits, &value, sizeof(bits)); return bits;
}
void componentLookup(WorldHookCode& c, uint32_t imageBase, uint32_t name) {
    // ECX owns the component; preserve floating-point state across the native lookup.
    c.emit({0x8B,0xF4,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC,0x68}); c.word(imageBase + name);
    c.call(imageBase + 0x26A1C0);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE6});
}
void dashAttackBranches(WorldHookCode& c, uint32_t imageBase, std::vector<size_t>& dash, std::vector<size_t>& other) {
    // Input must observe the live action, not the previous frame's cached dash phase.
    componentLookup(c, imageBase, 0x1783860);
    c.emit({0x85,0xC0}); other.push_back(c.branch(0x84));
    c.emit({0x81,0x38}); c.word(imageBase + 0x178380C); other.push_back(c.branch(0x85));
    for (uint32_t state : {438u,439u,440u,443u}) {
        c.emit({0x81,0x78,0x38}); c.word(state); dash.push_back(c.branch(0x84));
    }
    other.push_back(c.jump());
}
}
void verifyMovementCommandFunction(HANDLE process, uint32_t imageBase) {
    if (readClientMemory(process, imageBase + MOVEMENT_COMMAND_RVA, MOVEMENT_COMMAND_ORIGINAL.size())
            != MOVEMENT_COMMAND_ORIGINAL
        || readClientMemory(process, imageBase + MOVEMENT_COMMAND_RVA - 8, 8)
            != Bytes{0x55,0x8B,0xEC,0x53,0x8B,0x5D,8,0x57}
        || readClientMemory(process, imageBase + AIR_INPUT_RVA, AIR_INPUT_ORIGINAL.size()) != AIR_INPUT_ORIGINAL
        || readClientMemory(process, imageBase + AIR_INPUT_RVA - 6, 6) != Bytes{0x8B,0x0E,0x85,0xC9,0x74,6})
        throw std::runtime_error("movement command signature mismatch");
}
Bytes movementCommandJump(uint32_t address, uint32_t imageBase) {
    WorldHookCode c; c.emit({0xE9});
    c.word(address + MOVEMENT_COMMAND_CODE_OFFSET - (imageBase + MOVEMENT_COMMAND_RVA + 5));
    c.emit({0x90}); return c.bytes;
}
Bytes buildMovementCommandHook(uint32_t address, uint32_t imageBase, uint32_t threadId) {
    const auto code = address + MOVEMENT_COMMAND_CODE_OFFSET, data = address + SPRINT_DATA_OFFSET;
    WorldHookCode c;
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC,0x8B,0xF1}); // ESI command, EBX GeMovable.
    std::vector<size_t> native;
    c.emit({0x64,0xA1}); c.word(0x24); c.emit({0x3D}); c.word(threadId); native.push_back(c.branch(0x85));
    c.emit({0x81,0x3B}); c.word(imageBase + 0x17784E4); native.push_back(c.branch(0x85));
    // Observe the original forward axis before the existing local-player speed adaptation.
    // Match the movable entity against the live RID table; never change the command.
    std::vector<size_t> noAttempt;
    c.emit({0x8B,0x43,8,0x85,0xC0}); noAttempt.push_back(c.branch(0x84));
    c.emit({0x81,0x7E,0x28}); c.word(floatBits(-1.f)); noAttempt.push_back(c.branch(0x85));
    c.emit({0x50,0xFF,0x15}); c.word(imageBase + GET_TICK_COUNT_IAT_RVA); c.emit({0x5A});
    c.emit({0xBF}); c.word(address + LIVE_ACTORS_OFFSET);
    c.emit({0xB9}); c.word(MAX_WORLD_ACTORS);
    const auto attemptLoop = c.bytes.size();
    c.emit({0x39,0x97}); c.word(offsetof(ActorMailbox, blade) + offsetof(BladeMailbox, actorPointer));
    const auto matched = c.branch(0x84);
    c.emit({0x81,0xC7}); c.word(sizeof(ActorMailbox)); c.emit({0x49});
    c.bind(c.branch(0x85), attemptLoop);
    noAttempt.push_back(c.jump());
    c.bind(matched);
    c.emit({0x89,0x87}); c.word(offsetof(ActorMailbox, forwardAttemptTick));
    for (auto branch : noAttempt) c.bind(branch);
    c.emit({0x8B,0x43,8,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x3B,0x05}); c.word(data + 8); native.push_back(c.branch(0x85));
    c.emit({0xA1}); c.word(imageBase + 0x2E405F8); c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x83,0x78,0x38,4}); native.push_back(c.branch(0x85));
    c.emit({0x81,0x3E}); c.word(imageBase + 0x17782D8); native.push_back(c.branch(0x85));
    c.emit({0x80,0x7E,0x34,0}); native.push_back(c.branch(0x85)); // State-directed network commands own their parameters.
    c.emit({0x8B,0x46,0x0C,0x85,0xC0}); const auto stopped = c.branch(0x84);
    c.emit({0x3D}); c.word(floatBits(JOG_SPEED)); native.push_back(c.branch(0x85));
    c.bind(stopped);
    // The offline constructors leave MoveCommand::state uninitialized. Derive
    // it from the command's own axes, not a later keyboard snapshot.
    c.emit({0x33,0xFF});
    for (const auto& axis : std::array<std::array<unsigned char, 3>, 2>{{{0x28,4,8},{0x2C,1,2}}}) {
        c.emit({0x8B,0x46,axis[0],0x85,0xC0}); const auto zero = c.branch(0x84);
        c.emit({0x3D}); c.word(floatBits(-1.f)); const auto negative = c.branch(0x84);
        c.emit({0x3D}); c.word(floatBits(1.f)); native.push_back(c.branch(0x85));
        c.emit({0x83,0xCF,axis[2]}); const auto positive = c.jump();
        c.bind(negative); c.emit({0x83,0xCF,axis[1]}); c.bind(positive); c.bind(zero);
    }
    c.emit({0x8B,0x04,0xBD}); c.word(address + SPRINT_STATES_OFFSET); c.emit({0x89,0x46,0x30});
    c.emit({0x8B,0xC7,0x83,0xE0,3,0x83,0xF8,1}); native.push_back(c.branch(0x85));
    c.emit({0x81,0x7E,0x0C}); c.word(floatBits(JOG_SPEED)); native.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(data + 16); c.emit({2}); native.push_back(c.branch(0x85));
    c.emit({0xA1}); c.word(imageBase + 0x2E37804); c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
    for (unsigned char offset : std::array<unsigned char, 2>{0,4}) {
        c.emit({0x8B,0x50,static_cast<unsigned char>(0x68 + offset),0x3B,0x15}); c.word(data + offset);
        native.push_back(c.branch(0x85));
    }
    auto component = [&](uint32_t name, uint32_t table) {
        c.emit({0x8B,0x4B,8,0x68}); c.word(imageBase + name); c.call(imageBase + 0x26A1C0);
        c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
        c.emit({0x81,0x38}); c.word(imageBase + table); native.push_back(c.branch(0x85));
    };
    component(0x1783860, 0x178380C);
    c.emit({0x83,0x78,0x38,1}); const auto weaponChange = c.branch(0x84);
    c.emit({0x83,0x78,0x38,2}); native.push_back(c.branch(0x85));
    c.bind(weaponChange);
    component(0x178692C, 0x17868BC);
    c.emit({0x83,0x78,0x60,6}); native.push_back(c.branch(0x82));
    c.emit({0x8B,0x40,0x54,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x8B,0,0x83,0xE0,0x13,0x83,0xF8,1}); native.push_back(c.branch(0x85));
    // Preserve an established run before the native command updates physics and
    // animation. Repairing the speed in the next frame already exposed a walk pose.
    c.emit({0xC7,0x46,0x0C}); c.word(floatBits(RUN_SPEED));
    c.emit({0x80,0x7B,0x20,0}); native.push_back(c.branch(0x85)); // Airborne speed must not rearm RUN.
    c.emit({0x8B,0x46,0x30});
    for (const auto& state : std::array<std::pair<uint32_t, uint32_t>, 3>{{{2,10},{3,11},{9,12}}}) {
        c.emit({0x83,0xF8,static_cast<unsigned char>(state.first)}); const auto next = c.branch(0x85);
        c.emit({0xC7,0x46,0x30}); c.word(state.second); native.push_back(c.jump()); c.bind(next);
    }
    for (auto branch : native) c.bind(branch);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D});
    c.bytes.insert(c.bytes.end(), MOVEMENT_COMMAND_ORIGINAL.begin(), MOVEMENT_COMMAND_ORIGINAL.end());
    c.emit({0xE9}); c.word(imageBase + MOVEMENT_COMMAND_RVA + 6 - (code + static_cast<uint32_t>(c.bytes.size()) + 4));
    if (c.bytes.size() > AIR_INPUT_CODE_OFFSET - MOVEMENT_COMMAND_CODE_OFFSET)
        throw std::runtime_error("movement command hook overflow");
    return c.bytes;
}
Bytes airInputJump(uint32_t address, uint32_t imageBase) {
    WorldHookCode c; c.emit({0xE9});
    c.word(address + AIR_INPUT_CODE_OFFSET - (imageBase + AIR_INPUT_RVA + 5));
    c.emit({0x90}); return c.bytes;
}
Bytes buildAirInputHook(uint32_t address, uint32_t imageBase, uint32_t threadId) {
    const auto code = address + AIR_INPUT_CODE_OFFSET;
    WorldHookCode c;
    auto resume = [&] {
        c.emit({0xE9});
        c.word(imageBase + AIR_INPUT_RVA + 6 - (code + static_cast<uint32_t>(c.bytes.size()) + 4));
    };
    c.emit({0x9C,0x60}); // ECX child command, EBX GeMovable.
    std::vector<size_t> native, blocked;
    c.emit({0x64,0xA1}); c.word(0x24); c.emit({0x3D}); c.word(threadId); native.push_back(c.branch(0x85));
    c.emit({0x81,0x3B}); c.word(imageBase + 0x17784E4); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x43,8,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x3B,0x05}); c.word(address + SPRINT_DATA_OFFSET + 8); native.push_back(c.branch(0x85));
    c.emit({0xA1}); c.word(imageBase + 0x2E405F8); c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x83,0x78,0x38,4}); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x53,0x38,0x85,0xD2}); native.push_back(c.branch(0x84));
    c.emit({0x81,0x39}); c.word(imageBase + 0x17782E8); const auto jumpCommand = c.branch(0x84);
    c.emit({0x80,0x7A,0x0A,0}); const auto takingOff = c.branch(0x85);
    for (unsigned char flag : std::array<unsigned char, 2>{0x20,0x22}) {
        c.emit({0x80,0x7B,flag,0}); native.push_back(c.branch(0x84));
    }
    c.bind(takingOff);
    c.emit({0x81,0x39}); c.word(imageBase + 0x17782C8); blocked.push_back(c.branch(0x84));
    c.emit({0x81,0x39}); c.word(imageBase + 0x17782D8); native.push_back(c.branch(0x85));
    c.emit({0x80,0x79,0x34,0}); native.push_back(c.branch(0x85)); // State-directed corrections retain their native path.
    blocked.push_back(c.jump());
    c.bind(jumpCommand);
    c.emit({0x8B,0x4B,8});
    std::vector<size_t> dashAttack, otherAction;
    dashAttackBranches(c, imageBase, dashAttack, otherAction);
    for (auto branch : otherAction) c.bind(branch);
    c.emit({0x8B,0x53,0x38});
    c.emit({0x80,0x7B,0x20,0}); const auto airborne = c.branch(0x85);
    c.emit({0x80,0x7A,0x0A,0}); native.push_back(c.branch(0x84));
    c.bind(airborne);
    auto filteredJump = [&](bool blockCrouch) {
        // Keep the queued command unchanged; crouch=2 means no posture request.
        c.emit({0x61,0x9D,0xFF,0x71,0x0C,0xFF,0x71,8,0xC6,4,0x24,0});
        if (blockCrouch) {
            c.emit({0x83,0x7C,0x24,4,1}); const auto notCrouching = c.branch(0x85);
            c.emit({0xC7,0x44,0x24,4}); c.word(2); c.bind(notCrouching);
        }
        c.emit({0xFF,0x71,4,0xFF,0x31,0x8B,0xCC,0x8B,1,0x53,0xFF,0x50,8,0x83,0xC4,16});
        resume();
    };
    filteredJump(false);
    for (auto branch : dashAttack) c.bind(branch);
    filteredJump(true);
    for (auto branch : blocked) c.bind(branch);
    c.emit({0x61,0x9D}); resume();
    for (auto branch : native) c.bind(branch);
    c.emit({0x61,0x9D});
    c.bytes.insert(c.bytes.end(), AIR_INPUT_ORIGINAL.begin(), AIR_INPUT_ORIGINAL.end());
    resume();
    if (c.bytes.size() > CROUCH_ANIMATION_CODE_OFFSET - AIR_INPUT_CODE_OFFSET)
        throw std::runtime_error("air input hook overflow");
    return c.bytes;
}
Bytes crouchAnimationJump(uint32_t address, uint32_t imageBase) {
    WorldHookCode c; c.emit({0xE9});
    c.word(address + CROUCH_ANIMATION_CODE_OFFSET - (imageBase + CROUCH_ANIMATION_RVA + 5));
    return c.bytes;
}
Bytes buildCrouchAnimationHook(uint32_t address, uint32_t imageBase, uint32_t threadId) {
    const auto code = address + CROUCH_ANIMATION_CODE_OFFSET, identity = address + SPRINT_DATA_OFFSET + 8;
    WorldHookCode c;
    c.emit({0x9C,0x60,0x8B,0xEC});
    std::vector<size_t> native, blocked;
    c.emit({0x64,0xA1}); c.word(0x24); c.emit({0x3D}); c.word(threadId); native.push_back(c.branch(0x85));
    c.emit({0x8B,0xF9,0x81,0x39}); c.word(imageBase + 0x177722C); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x45,0x28,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x83,0x78,0x10,6}); native.push_back(c.branch(0x85));
    c.emit({0x83,0x78,0x14,15}); const auto inlineName = c.branch(0x86);
    c.emit({0x8B,0}); c.bind(inlineName);
    c.emit({0x81,0x38}); c.word(0x756F7243); native.push_back(c.branch(0x85)); // "Crou"
    c.emit({0x66,0x81,0x78,4,0x63,0x68}); native.push_back(c.branch(0x85)); // "ch"
    c.emit({0xA1}); c.word(imageBase + 0x2E405F8); c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x83,0x78,0x38,4}); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x0D}); c.word(identity); c.emit({0x85,0xC9}); native.push_back(c.branch(0x84));
    componentLookup(c, imageBase, 0x177867C);
    c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x81,0x38}); c.word(imageBase + 0x17784E4); native.push_back(c.branch(0x85));
    c.emit({0x39,0x78,0x58}); native.push_back(c.branch(0x85)); // Only this local movable's animation component.
    c.emit({0x8B,0x0D}); c.word(identity);
    dashAttackBranches(c, imageBase, blocked, native);
    for (auto branch : blocked) c.bind(branch);
    c.emit({0x61,0x9D,0xC2,4,0});
    for (auto branch : native) c.bind(branch);
    c.emit({0x61,0x9D});
    c.bytes.insert(c.bytes.end(), CROUCH_ANIMATION_ORIGINAL.begin(), CROUCH_ANIMATION_ORIGINAL.end());
    c.emit({0xE9}); c.word(imageBase + CROUCH_ANIMATION_RVA + 5 - (code + static_cast<uint32_t>(c.bytes.size()) + 4));
    if (c.bytes.size() > 0x400) throw std::runtime_error("crouch animation hook overflow");
    return c.bytes;
}
}
