#include "SprintHookCode.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"
#include "CombatProfileHookCode.h"
#include <cstring>

namespace t7r {
namespace {
constexpr uint32_t DIRECTIONS_OFFSET = 0x200, STATES_OFFSET = SPRINT_STATES_OFFSET;
constexpr uint32_t STATE_KEYS_OFFSET = SPRINT_STATES_OFFSET + 0x40;
constexpr uint32_t JUMP_EVENT_OFFSET = 0x180, END_JUMP_EVENT_OFFSET = 0x198;
constexpr uint32_t SPEED_RVA = 0x3D2550, RUNNING_RVA = 0x3D2E20, STATE_RVA = 0x3D2570;
constexpr uint32_t LINE_MOVE_RVA = 0x75D140, STOP_MOVE_RVA = 0x75D3B0;
constexpr uint32_t SEND_HEADING_RVA = 0x475F50;
constexpr uint32_t ANIMATION_VTABLE_RVA = 0x177722C, ANIMATION_EVENT_RVA = 0x3BEEC0;
uint32_t floatBits(float value) {
    uint32_t result; std::memcpy(&result, &value, sizeof(result)); return result;
}
}
void verifySprintFunctions(HANDLE process, uint32_t imageBase) {
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {SPEED_RVA, {0x55,0x8B,0xEC,0x8B,0x49,0x38,0xF3,0x0F,0x10,0x45,8}},
        {RUNNING_RVA, {0x55,0x8B,0xEC,0x6A,0xFF,0x68}},
        {STATE_RVA, {0x55,0x8B,0xEC,0x6A,0xFF,0x68}},
        {LINE_MOVE_RVA, {0x55,0x8B,0xEC,0x6A,0xFF,0x68}},
        {STOP_MOVE_RVA, {0x55,0x8B,0xEC,0x6A,0xFF,0x68}},
        {SEND_HEADING_RVA, {0x55,0x8B,0xEC,0x56,0x8B,0xF1,0x8B,0x46,8}},
        {ANIMATION_EVENT_RVA, {0x55,0x8B,0xEC,0x56,0x57,0xFF,0x75,8,0x8B,0xF9,0x8B,0x37}},
        {JUMP_PHYSICS_RVA, JUMP_PHYSICS_ORIGINAL},
        {JUMP_PHYSICS_RVA + 6, {0x5F,0x5E,0x5D,0xC2,4,0}},
        {0x29B200, {0x56,0x8B,0xF1,0x8B,0x06,0x8B,0x40,0x2C,0xFF,0xD0}},
        {0x29B217, {0x66,0x0F,0x6E,0x86,0xEC,0,0,0}},
        {0x4C6E70, {0x55,0x8B,0xEC,0x83,0xEC,0x10,0x8B,0xC1,0x8B,0x4D,8}},
        {0x4971B0, {0x8B,0x01,0xC3}},
        {0x5D3CB0, {0x55,0x8B,0xEC,0x8B,0x55,8,0x56,0x8B,0x71,0x24}}
    }) {
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("sprint function signature mismatch");
    }
}
void emitRunEligibleState(WorldHookCode& c, uint32_t imageBase) {
    c.emit({0x60,0x8B,0x48,0x38});
    std::vector<size_t> eligible, blocked, shield;
    for (unsigned char state : std::array<unsigned char, 2>{1,2}) {
        c.emit({0x83,0xF9,state}); eligible.push_back(c.branch(0x84));
    }
    for (uint32_t state : {95u,309u}) {
        c.emit({0x81,0xF9}); c.word(state); shield.push_back(c.branch(0x84));
    }
    blocked.push_back(c.jump());
    for (auto branch : shield) c.bind(branch);
    // The live hero resource defines light-shield infantry (heroCategory=1),
    // including heroes carrying medium shields; weapon performance is unrelated.
    c.emit({0x8B,0x48,8,0x85,0xC9}); blocked.push_back(c.branch(0x84));
    c.emit({0x68}); c.word(imageBase + 0x179ED94); c.call(imageBase + 0x26A1C0);
    c.emit({0x85,0xC0}); blocked.push_back(c.branch(0x84));
    c.emit({0x81,0x38}); c.word(imageBase + 0x179ECE4); blocked.push_back(c.branch(0x85));
    c.emit({0x8B,0x50,0x2C,0x85,0xD2}); blocked.push_back(c.branch(0x8E));
    c.emit({0x8B,0x0D}); c.word(imageBase + 0x24409D0);
    c.emit({0x85,0xC9}); blocked.push_back(c.branch(0x84));
    // Original hero-table accessor uses the table's integer-key index at +4.
    c.emit({0x52,0x83,0xC1,4}); c.call(imageBase + 0x5D3CB0);
    c.emit({0x85,0xC0}); blocked.push_back(c.branch(0x84));
    c.emit({0x83,0xB8}); c.word(0x1C05); c.emit({1}); blocked.push_back(c.branch(0x85));
    for (auto branch : eligible) c.bind(branch);
    c.emit({0xB8}); c.word(1); const auto done = c.jump();
    for (auto branch : blocked) c.bind(branch);
    c.emit({0x33,0xC0}); c.bind(done); c.emit({0x85,0xC0,0x61});
}
void writeSprintDirections(Bytes& page) {
    // Indexed by W/S/A/D bits. Opposing keys cancel on each axis.
    const char* directions[]{"", "up", "down", "", "left", "leftUp", "leftDown", "left",
        "right", "rightUp", "rightDown", "right", "", "up", "down", ""};
    auto nativeString = [&](size_t offset, const char* value) {
        const auto length = static_cast<uint32_t>(std::strlen(value));
        const uint32_t capacity = 15;
        std::memcpy(page.data() + offset, value, length + 1);
        std::memcpy(page.data() + offset + 16, &length, 4);
        std::memcpy(page.data() + offset + 20, &capacity, 4);
    };
    for (size_t i = 0; i < 16; ++i) nativeString(DIRECTIONS_OFFSET + i * 24, directions[i]);
    nativeString(JUMP_EVENT_OFFSET, "Jump");
    nativeString(END_JUMP_EVENT_OFFSET, "EndJump");
    // Native states enumerate forward/left-forward/left/.../right-forward.
    const uint32_t states[]{1,2,6,1,4,3,5,4,8,9,7,8,1,2,6,1};
    std::memcpy(page.data() + STATES_OFFSET, states, sizeof(states));
    const uint32_t stateKeys[]{0,0,1,5,4,6,2,10,8,9,1,5,9};
    std::memcpy(page.data() + STATE_KEYS_OFFSET, stateKeys, sizeof(stateKeys));
}
Bytes buildSprintHook(uint32_t address, uint32_t imageBase, const DashAttackProfile& dash, bool dynamicProfiles) {
    const auto dashMoveMs = dash.enabled ? dash.moveMs : 0;
    const auto dashSpeed = dash.enabled ? static_cast<float>(dash.speedMilli) / 1000.f : 0.f;
    // State contains identities/timestamps, never a pointer reused across frames.
    const auto data = address + SPRINT_DATA_OFFSET;
    const auto epoch = data, identity = data + 8, began = data + 12, tracking = data + 16; // 0 idle, 1 warming, 2 running.
    const auto lastDash = data + 20, lastKeys = data + 24, game = data + 28;
    const auto now = data + 32, offline = data + 36, speed = data + 40, running = data + 44;
    const auto jumping = data + 48, animationIdentity = data + 52;
    const auto phaseTick = data + 56, dashMoving = data + 60;
    const auto sequence = data + 64, lastSequence = data + 68;
    const auto selectedMoveMs = data + 72, selectedDashSpeed = data + 76;
    const auto airLocked = data + 80, airKeys = data + 84, resumeMovement = data + 88;
    WorldHookCode c;
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    std::vector<size_t> invalid;
    auto nonzero = [&](unsigned char modrm) {
        c.emit({0x85,modrm}); invalid.push_back(c.branch(0x84));
    };
    auto component = [&](uint32_t name, uint32_t table) {
        c.emit({0x8B,0xCF,0x68}); c.word(imageBase + name); c.call(imageBase + 0x26A1C0);
        nonzero(0xC0);
        if (table) {
            c.emit({0x81,0x38}); c.word(imageBase + table); invalid.push_back(c.branch(0x85));
        }
    };
    c.emit({0xA1}); c.word(imageBase + 0x2E405F8); nonzero(0xC0);
    c.emit({0x8B,0x40,0x38,0xA3}); c.word(game);
    c.emit({0xA1}); c.word(imageBase + 0x2E3FA8C); nonzero(0xC0);
    c.emit({0x83,0xB8}); c.word(0x274); c.emit({0}); invalid.push_back(c.branch(0x84));
    c.emit({0xA1}); c.word(imageBase + 0x2D27358); nonzero(0xC0);
    c.emit({0x6A,1}); c.call(imageBase + 0x647770);
    c.emit({0x83,0xC4,4,0x83,0xF8,0xFF}); invalid.push_back(c.branch(0x84));
    c.emit({0x8B,0x0D}); c.word(imageBase + 0x2D27358);
    c.emit({0x8B,0x11,0x50,0xFF,0x52,0x28}); nonzero(0xC0);
    c.emit({0x83,0x78,0x7C,0}); invalid.push_back(c.branch(0x84));
    c.emit({0x8B,0xF8}); // EDI entity until its components have been resolved.
    c.emit({0x8B,0x35}); c.word(imageBase + 0x2E37804); nonzero(0xF6);
    std::vector<size_t> changed;
    for (uint32_t offset : {0u, 4u}) {
        c.emit({0x8B,0x46,static_cast<unsigned char>(0x68 + offset),0x3B,0x05}); c.word(epoch + offset);
        changed.push_back(c.branch(0x85));
    }
    c.emit({0x3B,0x3D}); c.word(identity); const auto sameEntity = c.branch(0x84);
    for (auto branch : changed) c.bind(branch);
    c.store(tracking, 0); c.store(lastDash, 0); c.store(jumping, 0); c.store(animationIdentity, 0);
    c.store(dashMoving, 0); c.store(lastSequence, 0);
    c.store(airLocked, 0);
    for (uint32_t offset : {0u, 4u}) {
        c.emit({0x8B,0x46,static_cast<unsigned char>(0x68 + offset),0xA3}); c.word(epoch + offset);
    }
    c.emit({0x89,0x3D}); c.word(identity); c.bind(sameEntity);
    component(0x177867C, 0x17784E4);
    c.emit({0x8B,0xD8,0x8B,0x48,0x38}); nonzero(0xC9); // EBX movable
    c.emit({0x8B,0x01,0x81,0x78,0x3C}); c.word(imageBase + 0x295FA0);
    invalid.push_back(c.branch(0x85));
    c.emit({0x8B,0x81}); c.word(0xA8); nonzero(0xC0);
    c.emit({0x83,0x78,0x14,0}); invalid.push_back(c.branch(0x84));
    component(0x17ADDFC, 0); c.emit({0xA3}); c.word(offline);
    component(0x1783860, 0x178380C);
    emitRunEligibleState(c, imageBase);
    c.emit({0x0F,0x95,0x84,0x24}); c.word(0x200);
    c.emit({0x8B,0x70,0x38}); // ESI battle state
    c.emit({0x8B,0x48,0x40,0x89,0x0D}); c.word(sequence);
    c.emit({0x8B,0x48,0x48,0x89,0x0D}); c.word(phaseTick);
    if (dynamicProfiles) {
        c.emit({0x8B,0x90}); c.word(0x98); c.emit({0x8B,0xCF});
        c.call(address + COMBAT_PROFILE_CODE_OFFSET);
        c.store(selectedMoveMs, 0); c.store(selectedDashSpeed, 0);
        c.emit({0x85,0xC0}); const auto noProfile = c.branch(0x84);
        c.emit({0x8B,0x48,24,0x89,0x0D}); c.word(selectedMoveMs);
        c.emit({0x8B,0x48,28,0x89,0x0D}); c.word(selectedDashSpeed); c.bind(noProfile);
    }
    component(0x178692C, 0x17868BC);
    c.emit({0x83,0x78,0x60,6}); invalid.push_back(c.branch(0x82));
    c.emit({0x8B,0x40,0x54}); nonzero(0xC0);
    c.emit({0x8B,0x38,0x83,0xE7,0x3F}); // EDI W/S/A/D/crouch/jump
    c.store(resumeMovement, 0);
    c.emit({0x83,0x3D}); c.word(game); c.emit({4}); const auto outsideAirControl = c.branch(0x85);
    c.emit({0x8B,0x43,0x38,0x80,0x78,0x0A,0}); const auto takingOff = c.branch(0x85);
    std::vector<size_t> landed;
    for (unsigned char flag : std::array<unsigned char, 2>{0x20,0x22}) {
        c.emit({0x80,0x7B,flag,0}); landed.push_back(c.branch(0x84));
    }
    c.bind(takingOff);
    c.emit({0x83,0x3D}); c.word(airLocked); c.emit({0}); const auto locked = c.branch(0x85);
    // Capture the applied native direction, not keyboard changes made after takeoff.
    c.emit({0x8B,0x43,0x2C,0x33,0xD2,0x83,0xF8,12}); const auto unknownState = c.branch(0x87);
    c.emit({0x8B,0x14,0x85}); c.word(address + STATE_KEYS_OFFSET); c.bind(unknownState);
    c.emit({0x89,0x15}); c.word(airKeys); c.store(airLocked, 1);
    c.bind(locked); c.emit({0x83,0xE7,0x30,0x0B,0x3D}); c.word(airKeys);
    const auto airInputReady = c.jump();
    for (auto branch : landed) c.bind(branch);
    c.emit({0xA1}); c.word(airLocked); c.emit({0xA3}); c.word(resumeMovement);
    c.bind(outsideAirControl); c.store(airLocked, 0); c.bind(airInputReady);
    c.emit({0xA1}); c.word(imageBase + 0x16F6228); nonzero(0xC0);
    c.emit({0xFF,0xD0,0xA3}); c.word(now);
    c.store(speed, floatBits(JOG_SPEED)); c.store(running, 0);

    auto move = [&](float maxSpeed, bool selectedDash = false) {
        // EAX direction mask; original commands retain collision/gravity and own the movement queue.
        c.emit({0x83,0xE0,0x0F,0x6B,0xC0,24,0x05}); c.word(address + DIRECTIONS_OFFSET);
        c.emit({0x8B,0x0D}); c.word(offline);
        c.emit({0x83,0x78,0x10,0}); const auto stop = c.branch(0x84);
        c.emit({0x50,0x6A,0});
        if (selectedDash && dynamicProfiles) { c.emit({0xFF,0x35}); c.word(selectedDashSpeed); }
        else { c.emit({0x68}); c.word(floatBits(maxSpeed)); }
        c.emit({0xFF,0x35}); c.word(speed); c.call(imageBase + LINE_MOVE_RVA);
        const auto done = c.jump(); c.bind(stop); c.call(imageBase + STOP_MOVE_RVA); c.bind(done);
    };
    c.emit({0x83,0x3D}); c.word(game); c.emit({4}); const auto notGame = c.branch(0x85);
    c.emit({0x8B,0xC6,0x2D}); c.word(438);
    c.emit({0x83,0xF8,1}); const auto dashBranch = c.branch(0x86);
    const auto normal = c.bytes.size();
    c.emit({0x83,0x3D}); c.word(lastDash); c.emit({0}); const auto noRestore = c.branch(0x84);
    c.emit({0x8B,0xC7}); move(RUN_SPEED); c.store(lastDash, 0);
    c.bind(noRestore);
    std::vector<size_t> reset;
    c.emit({0x83,0x3D}); c.word(game); c.emit({4}); reset.push_back(c.branch(0x85));
    // Equipping and light-shield defense preserve the same continuous run timer.
    c.emit({0x80,0xBC,0x24}); c.word(0x200); c.emit({0}); reset.push_back(c.branch(0x84));
    c.emit({0x8B,0xC7,0x83,0xE0,0x13,0x83,0xF8,1}); reset.push_back(c.branch(0x85));
    // Carry an established run through jump input/takeoff/landing while still moving forward.
    c.emit({0x83,0x3D}); c.word(tracking); c.emit({2}); const auto keepRunning = c.branch(0x84);
    // Terrain contact loss alone is not a physical jump and must retain run warmup.
    c.emit({0x80,0x7B,0x20,0}); const auto notAirborne = c.branch(0x84);
    c.emit({0x80,0x7B,0x22,0}); reset.push_back(c.branch(0x85));
    c.bind(notAirborne);
    c.emit({0xF7,0xC7}); c.word(0x20); reset.push_back(c.branch(0x85)); // No new run starts while jumping.
    c.emit({0x83,0x3D}); c.word(tracking); c.emit({0}); const auto started = c.branch(0x85);
    c.emit({0xA1}); c.word(now); c.emit({0xA3}); c.word(began); c.store(tracking, 1);
    c.bind(started);
    c.emit({0xA1}); c.word(now); c.emit({0x2B,0x05}); c.word(began);
    c.emit({0x3D}); c.word(RUN_DELAY_MS); const auto warming = c.branch(0x82);
    c.bind(keepRunning); c.store(tracking, 2);
    c.store(speed, floatBits(RUN_SPEED)); c.store(running, 1);
    const auto armed = c.jump();
    for (auto branch : reset) c.bind(branch);
    c.store(tracking, 0);
    const auto resetDone = c.jump();

    c.bind(notGame); c.emit({0x33,0xFF}); const auto inactive = c.jump(); c.bind(inactive, normal);
    c.bind(dashBranch);
    // Movement and blade contacts use the same native action clock, including delayed first frames.
    c.store(tracking, 0); c.store(speed, 0);
    c.emit({0x33,0xD2,0x81,0xFE}); c.word(439); const auto preparation = c.branch(0x85);
    c.emit({0xA1}); c.word(now); c.emit({0x2B,0x05}); c.word(phaseTick);
    if (dynamicProfiles) { c.emit({0x3B,0x05}); c.word(selectedMoveMs); }
    else { c.emit({0x3D}); c.word(dashMoveMs); }
    const auto expired = c.branch(0x83);
    if (dynamicProfiles) { c.emit({0xA1}); c.word(selectedDashSpeed); c.emit({0xA3}); c.word(speed); }
    else c.store(speed, floatBits(dashSpeed));
    c.emit({0x42});
    c.bind(preparation); c.bind(expired);
    c.emit({0x3B,0x35}); c.word(lastDash); const auto phaseChanged = c.branch(0x85);
    c.emit({0xA1}); c.word(sequence); c.emit({0x3B,0x05}); c.word(lastSequence);
    const auto sequenceChanged = c.branch(0x85);
    c.emit({0x3B,0x3D}); c.word(lastKeys); const auto keysChanged = c.branch(0x85);
    c.emit({0x3B,0x15}); c.word(dashMoving); const auto sameDash = c.branch(0x84);
    c.bind(phaseChanged); c.bind(sequenceChanged); c.bind(keysChanged);
    c.emit({0x89,0x35}); c.word(lastDash); c.emit({0x89,0x3D}); c.word(lastKeys);
    c.emit({0xA1}); c.word(sequence); c.emit({0xA3}); c.word(lastSequence);
    c.emit({0x89,0x15}); c.word(dashMoving); c.emit({0x8B,0xC2}); move(dashSpeed, true);
    c.bind(sameDash); c.bind(warming); c.bind(armed); c.bind(resetDone);
    // Native infantry turning checks command speed, not velocity. Preserve the
    // stop command's zero speed so the original TurnDir updater can select Turn.
    c.emit({0x83,0x3D}); c.word(game); c.emit({4}); const auto outsideGame = c.branch(0x85);
    c.emit({0x83,0x3D}); c.word(lastDash); c.emit({0}); const auto dashSpeedSelected = c.branch(0x85);
    c.emit({0x8B,0xC7,0x83,0xE0,0x0F,0x83,0x3C,0x85}); c.word(address + STATES_OFFSET);
    c.emit({1}); const auto moving = c.branch(0x85);
    c.store(speed, 0); c.bind(moving); c.bind(dashSpeedSelected); c.bind(outsideGame);
    // Input edges were consumed in midair. On landing, resume the current native
    // key snapshot once, including a released key's stop command; never replay Jump.
    c.emit({0x83,0x3D}); c.word(resumeMovement); c.emit({0}); const auto noLandingMove = c.branch(0x84);
    // The native sender cached turns discarded in midair. Force its current
    // heading once before movement, even when the camera has stopped rotating.
    std::vector<size_t> noHeading;
    c.emit({0x8B,0x0D}); c.word(identity); c.emit({0x68}); c.word(imageBase + 0x1781950);
    c.call(imageBase + 0x26A1C0);
    c.emit({0x85,0xC0}); noHeading.push_back(c.branch(0x84));
    c.emit({0x81,0x38}); c.word(imageBase + 0x1781904); noHeading.push_back(c.branch(0x85));
    c.emit({0x8B,0x48,0x38,0x85,0xC9}); noHeading.push_back(c.branch(0x84));
    // Resolve the active infantry sender owned by GePlayerMountBladeDirInput.
    c.emit({0x81,0x39}); c.word(imageBase + 0x1781D90); noHeading.push_back(c.branch(0x85));
    c.emit({0x8B,0x89}); c.word(0xA4);
    c.emit({0x85,0xC9}); noHeading.push_back(c.branch(0x84));
    c.emit({0x81,0x39}); c.word(imageBase + 0x17825EC); noHeading.push_back(c.branch(0x85));
    c.emit({0x6A,1}); c.call(imageBase + SEND_HEADING_RVA);
    for (auto branch : noHeading) c.bind(branch);
    c.emit({0x8B,0xC7}); move(RUN_SPEED); c.bind(noLandingMove);
    // Reapply only when the native key tree or a phase change altered the commanded speed.
    c.emit({0x8B,0x4B,0x38,0xA1}); c.word(speed); c.emit({0x3B,0x41,0x24});
    const auto sameSpeed = c.branch(0x84);
    c.emit({0x50,0x8B,0xCB}); c.call(imageBase + SPEED_RVA); c.bind(sameSpeed);
    // Keep the frame-selected gait in the state consumed by the native animation
    // writer, rather than writing MoveDir behind its cache.
    c.emit({0x8B,0xC7,0x83,0xE0,0x0F});
    c.emit({0x83,0x3D}); c.word(lastDash); c.emit({0}); const auto ordinary = c.branch(0x84);
    c.emit({0xA1}); c.word(dashMoving);
    c.bind(ordinary);
    c.emit({0x8B,0x04,0x85}); c.word(address + STATES_OFFSET);
    c.emit({0x83,0x3D}); c.word(running); c.emit({0}); const auto walking = c.branch(0x84);
    std::vector<size_t> selected;
    for (const auto& state : std::array<std::pair<uint32_t, uint32_t>, 3>{{{2,10},{3,11},{9,12}}}) {
        c.emit({0x83,0xF8,static_cast<unsigned char>(state.first)}); const auto next = c.branch(0x85);
        c.emit({0xB8}); c.word(state.second); selected.push_back(c.jump()); c.bind(next);
    }
    c.bind(walking); for (auto branch : selected) c.bind(branch);
    c.emit({0x3B,0x43,0x2C}); const auto sameState = c.branch(0x84);
    c.emit({0x50,0x8B,0xCB}); c.call(imageBase + STATE_RVA); c.bind(sameState);
    // RUN arms dash inputs and the HUD; airborne movement keeps only its speed and direction.
    c.emit({0xA1}); c.word(running);
    c.emit({0x80,0x7B,0x20,0}); const auto groundedRun = c.branch(0x84);
    c.emit({0x33,0xC0}); c.bind(groundedRun);
    c.emit({0x38,0x83}); c.word(0xCC);
    const auto sameRunning = c.branch(0x84);
    c.emit({0x50,0x8B,0xCB}); c.call(imageBase + RUNNING_RVA); c.bind(sameRunning);
    // Observe the native physical jump, not queued input or behavior-tree event delivery.
    // Falling off a ledge sets airborne (0x20) but not the jump flag (0x22).
    std::vector<size_t> noAnimation;
    c.emit({0x8B,0x4B,0x58,0x85,0xC9}); noAnimation.push_back(c.branch(0x84));
    c.emit({0x81,0x39}); c.word(imageBase + ANIMATION_VTABLE_RVA); noAnimation.push_back(c.branch(0x85));
    c.emit({0x83,0x79,0x30,0}); noAnimation.push_back(c.branch(0x84));
    c.emit({0x3B,0x0D}); c.word(animationIdentity); const auto sameAnimation = c.branch(0x84);
    c.emit({0x89,0x0D}); c.word(animationIdentity); c.store(jumping, 0); c.bind(sameAnimation);
    c.emit({0x33,0xD2}); // EDX desired jump pose.
    std::vector<size_t> grounded;
    c.emit({0x83,0x3D}); c.word(game); c.emit({4}); grounded.push_back(c.branch(0x85));
    for (unsigned char flag : std::array<unsigned char, 2>{0x20, 0x22}) {
        c.emit({0x80,0x7B,flag,0}); grounded.push_back(c.branch(0x84));
    }
    c.emit({0x42}); for (auto branch : grounded) c.bind(branch);
    c.emit({0x3B,0x15}); c.word(jumping); noAnimation.push_back(c.branch(0x84));
    c.emit({0x89,0x15}); c.word(jumping);
    c.emit({0x85,0xD2,0xB8}); c.word(address + END_JUMP_EVENT_OFFSET);
    c.emit({0xBA}); c.word(address + JUMP_EVENT_OFFSET);
    c.emit({0x0F,0x45,0xC2,0x50}); c.call(imageBase + ANIMATION_EVENT_RVA);
    for (auto branch : noAnimation) c.bind(branch);
    const auto done = c.jump();
    for (auto branch : invalid) c.bind(branch);
    c.store(tracking, 0); c.store(lastDash, 0); c.store(identity, 0);
    c.store(dashMoving, 0); c.store(lastSequence, 0);
    c.store(jumping, 0); c.store(animationIdentity, 0);
    c.store(airLocked, 0);
    c.bind(done);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D,0xC3});
    if (c.bytes.size() > JUMP_PHYSICS_CODE_OFFSET - SPRINT_CODE_OFFSET)
        throw std::runtime_error("sprint hook overflow");
    return c.bytes;
}
Bytes jumpPhysicsJump(uint32_t address, uint32_t imageBase) {
    WorldHookCode c; c.emit({0xE9});
    c.word(address + JUMP_PHYSICS_CODE_OFFSET - (imageBase + JUMP_PHYSICS_RVA + 5));
    c.emit({0x90}); return c.bytes;
}
Bytes buildJumpPhysicsHook(uint32_t address, uint32_t imageBase, uint32_t threadId) {
    const auto code = address + JUMP_PHYSICS_CODE_OFFSET;
    WorldHookCode c;
    auto resume = [&] {
        c.emit({0xE9});
        c.word(imageBase + JUMP_PHYSICS_RVA + 6 - (code + static_cast<uint32_t>(c.bytes.size()) + 4));
    };
    c.emit({0x9C,0x60});
    std::vector<size_t> native;
    c.emit({0x64,0xA1}); c.word(0x24); c.emit({0x3D}); c.word(threadId); native.push_back(c.branch(0x85));
    // ESI is GeMovable, EDI the applied composite command, ECX its controller.
    c.emit({0x8B,0x46,8,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x3B,0x05}); c.word(address + SPRINT_DATA_OFFSET + 8); native.push_back(c.branch(0x85));
    c.emit({0xA1}); c.word(imageBase + 0x2E405F8); c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x83,0x78,0x38,4}); native.push_back(c.branch(0x85));
    for (unsigned char flag : std::array<unsigned char, 2>{0x20,0x22}) {
        c.emit({0x80,0x7E,flag,0}); native.push_back(c.branch(0x84));
    }
    c.emit({0x80,0x79,0x0A,0}); native.push_back(c.branch(0x85)); // Preserve a newly queued takeoff.
    c.emit({0x8B,0x01,0x81,0xB8}); c.word(0xD0); c.word(imageBase + 0x29B200);
    native.push_back(c.branch(0x85));
    c.emit({0x81,0x3F}); c.word(imageBase + 0x17782A4); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x57,0x10,0x85,0xD2}); native.push_back(c.branch(0x84));
    c.emit({0x8B,0x5F,0x14,0x3B,0xD3}); native.push_back(c.branch(0x83));
    const auto child = c.bytes.size();
    c.emit({0x8B,2,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x81,0x38}); c.word(imageBase + 0x17782C8); const auto heading = c.branch(0x84);
    c.emit({0x81,0x38}); c.word(imageBase + 0x17782D8); const auto movement = c.branch(0x84);
    c.emit({0x81,0x38}); c.word(imageBase + 0x17782E8); native.push_back(c.branch(0x85));
    c.emit({0x83,0x78,0x0C,1}); native.push_back(c.branch(0x86)); // Preserve crouch in a combined command.
    const auto jumpOnly = c.jump();
    c.bind(movement); c.emit({0x80,0x78,0x34,0}); native.push_back(c.branch(0x85));
    c.bind(heading); c.bind(jumpOnly); c.emit({0x83,0xC2,4,0x3B,0xD3}); c.bind(c.branch(0x82), child);
    // Rejected airborne input must not add a gravity step or erase the frame
    // clock's remainder. Corrections, crouch and activation still flush natively.
    c.emit({0x61,0x9D}); resume();
    for (auto branch : native) c.bind(branch);
    c.emit({0x61,0x9D});
    c.bytes.insert(c.bytes.end(), JUMP_PHYSICS_ORIGINAL.begin(), JUMP_PHYSICS_ORIGINAL.end());
    resume();
    if (c.bytes.size() > 0x400) throw std::runtime_error("jump physics hook overflow");
    return c.bytes;
}
}
