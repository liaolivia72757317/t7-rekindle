#pragma once
#include "WorldHookFixture.h"
#include "../../src/Runtime/launcher/SprintHookCode.h"
#include "../../src/Runtime/launcher/WorldHookCode.h"

namespace sprintFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
constexpr uint32_t INPUT = BASE + 0xC000, FIGHT = BASE + 0xC100, OFFLINE = BASE + 0xC200;
constexpr uint32_t KEYS = BASE + 0xC300, CLOCK = KEYS + 4, FRAMES = KEYS + 8;
constexpr uint32_t EVENTS = KEYS + 12, DIRECTION = KEYS + 16, MOVES = KEYS + 20;
constexpr uint32_t RESET_SPEED = KEYS + 24, APPLIED_RESET = KEYS + 28;
constexpr uint32_t STATE_WRITES = KEYS + 32, JUMP_COMMANDS = KEYS + 36;
constexpr uint32_t ANIMATION_EVENTS = KEYS + 40, ANIMATION_NAME = KEYS + 44;
constexpr uint32_t ANIMATION_LENGTH = KEYS + 48, ANIMATION_CAPACITY = KEYS + 52;
constexpr uint32_t COMMAND_SPEED = KEYS + 56, COMMAND_MAX_SPEED = KEYS + 60;
constexpr uint32_t CLOCK_MODE = KEYS + 64;
constexpr uint32_t ANIMATION = BASE + 0xC500;
inline t7r::DashAttackProfile profile() {
    // Deliberately different from the current weapon: the hook must consume the supplied snapshot.
    t7r::DashAttackProfile value;
    value.enabled = true; value.weaponId = 1030411; value.weaponCategory = 32;
    value.durationMs = 1709; value.moveMs = 1117; value.speedMilli = 3210;
    value.contactStartMs = 100; value.contactEndMs = 1300; value.reboundMs = 777;
    value.durationSource = "table";
    return value;
}
inline uint32_t readWord(HANDLE process, uint32_t address) {
    const auto bytes = t7r::readClientMemory(process, address, 4);
    uint32_t value; std::memcpy(&value, bytes.data(), 4); return value;
}
inline void setFightState(HANDLE process, uint32_t state, uint32_t phaseTick) {
    writeWord(process, FIGHT + 0x48, phaseTick);
    writeWord(process, FIGHT + 0x40, readWord(process, FIGHT + 0x40) + 1);
    writeWord(process, FIGHT + 0x38, state);
}
inline void setFightState(HANDLE process, uint32_t state) {
    setFightState(process, state, readWord(process, CLOCK));
}
inline void prepare(HANDLE process) {
    t7r::WorldHookCode c;
    c.emit({0x55,0x8B,0xEC,0x8B,0x55,8,0x56,0x8B,0x71,0x7C});
    std::vector<size_t> done;
    for (const auto& field : std::array<std::array<uint32_t, 2>, 3>{{
        {0x1777D50,0x88}, {0x1783860,0x8C}, {0x17736BC,0x90}
    }}) {
        c.emit({0x81,0xFA}); c.word(BASE + field[0]); const auto next = c.branch(0x85);
        c.emit({0x8B,0x81}); c.word(field[1]); c.emit({0x85,0xC0}); done.push_back(c.branch(0x85));
        c.bind(next);
    }

    for (const auto& item : std::vector<std::pair<uint32_t, uint32_t>>{
        {0x178692C, INPUT}, {0x1783860, FIGHT}, {0x17ADDFC, OFFLINE}
    }) {
        c.emit({0x81,0xFA}); c.word(BASE + item.first); const auto next = c.branch(0x85);
        c.emit({0xB8}); c.word(item.second); done.push_back(c.jump()); c.bind(next);
    }
    c.emit({0x81,0xFA}); c.word(BASE + 0x179ED94); const auto movable = c.branch(0x85);
    c.emit({0x8B,0x81}); c.word(0x84); done.push_back(c.jump()); c.bind(movable);
    c.emit({0x8B,0x81}); c.word(0x80);
    for (auto branch : done) c.bind(branch);
    c.emit({0x5E,0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x26A1C0, c.bytes);
    for (const auto& item : std::vector<std::pair<uint32_t, uint32_t>>{
        {INPUT,BASE+0x17868BC}, {INPUT+0x60,6}, {INPUT+0x54,KEYS},
        {FIGHT,BASE+0x178380C}, {FIGHT+0x38,2}, {FIGHT+0x40,1}, {FIGHT+0x48,1000}, {CLOCK,1000},
        {worldFixture::TABLE+0x3C,BASE+0x295FA0}, {BASE+0x16F6228,BASE+0xC400}
    }) writeWord(process, item.first, item.second);
    c.bytes.clear(); c.emit({0xFF,0x05}); c.word(FRAMES);
    c.emit({0xA1}); c.word(RESET_SPEED); c.emit({0x3B,0x05}); c.word(APPLIED_RESET);
    const auto unchanged = c.branch(0x84);
    c.emit({0xA3}); c.word(APPLIED_RESET); c.store(worldFixture::CONTROLLER + 0x24, 0x40400000);
    c.store(worldFixture::MOVABLE + 0x2C, 0); // Offline line commands carry no movement state.
    c.store(worldFixture::MOVABLE + 0xCC, 0); // Native takeoff also clears the running byte.
    c.bind(unchanged);
    c.emit({0x83,0x3D}); c.word(CLOCK_MODE); c.emit({1}); const auto controlled = c.branch(0x84);
    c.emit({0xFF,0x15}); c.word(BASE + 0x1900); c.emit({0xC3});
    c.bind(controlled); c.emit({0xA1}); c.word(CLOCK); c.emit({0xC3});
    t7r::writeClientMemory(process, BASE + 0xC400, c.bytes);
    t7r::writeClientMemory(process, BASE + 0x4C6E70,
        {0x55,0x8B,0xEC,0x83,0xEC,0x10,0x8B,0xC1,0x8B,0x4D,8});
    t7r::writeClientMemory(process, BASE + 0x4971B0, {0x8B,0x01,0xC3});
    t7r::writeClientMemory(process, BASE + 0x3D2550,
        {0x55,0x8B,0xEC,0x8B,0x49,0x38,0xF3,0x0F,0x10,0x45,8,
         0xF3,0x0F,0x11,0x41,0x24,0x5D,0xC2,4,0});
    auto begin = [&]() { c.bytes.clear(); c.emit({0x55,0x8B,0xEC,0x6A,0xFF,0x68}); c.word(0); c.emit({0x58,0x58}); };
    begin(); c.emit({0x8B,0x45,8,0x88,0x81}); c.word(0xCC);
    c.emit({0xFF,0x05}); c.word(EVENTS); c.emit({0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x3D2E20, c.bytes);
    begin(); c.emit({0x8B,0x45,8,0x89,0x41,0x2C});
    c.emit({0xFF,0x05}); c.word(STATE_WRITES); c.emit({0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x3D2570, c.bytes);
    begin(); c.emit({0x8B,0x45,8,0xA3}); c.word(COMMAND_SPEED);
    c.emit({0x8B,0x45,12,0xA3}); c.word(COMMAND_MAX_SPEED);
    c.emit({0x8B,0x45,0x14,0x8B,0x00,0xA3}); c.word(DIRECTION);
    c.emit({0xFF,0x05}); c.word(MOVES); c.emit({0x5D,0xC2,16,0});
    t7r::writeClientMemory(process, BASE + 0x75D140, c.bytes);
    begin(); c.store(DIRECTION, 0); c.emit({0xFF,0x05}); c.word(MOVES); c.emit({0x5D,0xC3});
    t7r::writeClientMemory(process, BASE + 0x75D3B0, c.bytes);
    // The adjacent native entry queues a jump, not a stop command.
    begin(); c.emit({0xFF,0x05}); c.word(JUMP_COMMANDS); c.emit({0x5D,0xC3});
    t7r::writeClientMemory(process, BASE + 0x75D080, c.bytes);
    writeWord(process, worldFixture::MOVABLE + 0x58, ANIMATION);
    writeWord(process, ANIMATION, BASE + 0x177722C);
    writeWord(process, ANIMATION + 0x30, ANIMATION);
    writeWord(process, BASE + 0x177722C + 0x44, BASE + 0x3BEEC0);
    c.bytes.clear(); c.emit({0x55,0x8B,0xEC,0x56,0x57,0xFF,0x75,8,0x8B,0xF9,0x8B,0x37,0x58});
    for (const auto& field : std::array<std::pair<uint32_t, uint32_t>, 3>{{
        {0,ANIMATION_NAME}, {16,ANIMATION_LENGTH}, {20,ANIMATION_CAPACITY}
    }}) {
        c.emit({0x8B,0x50,static_cast<unsigned char>(field.first),0x89,0x15}); c.word(field.second);
    }
    c.emit({0xFF,0x05}); c.word(ANIMATION_EVENTS);
    c.emit({0x5F,0x5E,0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x3BEEC0, c.bytes);
}
inline void pump(HANDLE process) {
    const auto before = readWord(process, FRAMES);
    const auto deadline = GetTickCount64() + 3000;
    while (readWord(process, FRAMES) - before < 10) {
        if (GetTickCount64() >= deadline) throw std::runtime_error("sprint frame timeout");
        Sleep(1);
    }
}
inline void expect(HANDLE process, float speed, bool running, const char* reason) {
    pump(process);
    uint32_t bits; std::memcpy(&bits, &speed, 4);
    if (readWord(process, worldFixture::CONTROLLER + 0x24) != bits
        || (readWord(process, worldFixture::MOVABLE + 0xCC) & 0xFF) != static_cast<uint32_t>(running))
        throw std::runtime_error(reason);
}
inline void expectDirection(HANDLE process, uint32_t direction, bool running) {
    // Native animation resources: clockwise MoveDir values start at forward=1.
    // Native movement states enumerate LEFT first instead; do not compare to
    // a second copy of the production WASD-to-state table.
    const uint32_t moveDirs[]{0,0,1,8,7,6,5,4,3,2,1,8,2};
    const auto state = readWord(process, worldFixture::MOVABLE + 0x2C);
    if (state == 0 || state >= std::size(moveDirs) || moveDirs[state] != direction || (state >= 10) != running)
        throw std::runtime_error("native animation direction disagrees with held keys");
}
inline void verifyPreparation(HANDLE process) {
    const auto axes = t7r::readClientMemory(process, worldFixture::ANCHOR, 8);
    const auto active = readWord(process, worldFixture::CONTROLLER + 8);
    const auto position = t7r::readClientMemory(process, worldFixture::CONTROLLER + 0x10, 12);
    const auto moves = readWord(process, MOVES), jumps = readWord(process, JUMP_COMMANDS);
    for (uint32_t keys = 0; keys < 16; ++keys) {
        writeWord(process, worldFixture::STATE + 0x38, 2);
        writeWord(process, worldFixture::CONTROLLER + 8, 0);
        writeWord(process, KEYS, keys);
        // Native key commands update these axes even while Active suspends execution.
        const float forward = static_cast<float>(((keys & 1) != 0) - ((keys & 2) != 0));
        const float right = static_cast<float>(((keys & 8) != 0) - ((keys & 4) != 0));
        const std::array<float, 2> direction{right, -forward};
        t7r::Bytes savedDirection(sizeof(direction));
        std::memcpy(savedDirection.data(), direction.data(), savedDirection.size());
        t7r::writeClientMemory(process, worldFixture::ANCHOR, savedDirection);
        expect(process, 3, false, "preparation armed running");
        expectDirection(process, 0, false);
        if (readWord(process, worldFixture::CONTROLLER + 8) != 0)
            throw std::runtime_error("preparation frame reactivated the native controller");
        writeWord(process, CLOCK, readWord(process, CLOCK) + 6000); pump(process);
        // GAME restores the existing controller; there is no new key event or movement command.
        writeWord(process, worldFixture::STATE + 0x38, 4);
        writeWord(process, worldFixture::CONTROLLER + 8, 1);
        expect(process, forward || right ? 3.f : 0.f, false, "preparation time counted toward running");
        if (readWord(process, KEYS) != keys || readWord(process, MOVES) != moves
            || readWord(process, JUMP_COMMANDS) != jumps || readWord(process, worldFixture::CONTROLLER + 8) != 1
            || t7r::readClientMemory(process, worldFixture::ANCHOR, 8) != savedDirection
            || t7r::readClientMemory(process, worldFixture::CONTROLLER + 0x10, 12) != position)
            throw std::runtime_error("preparation handoff changed native input/axes/position or replayed a command");
    }
    t7r::writeClientMemory(process, worldFixture::ANCHOR, axes);
    writeWord(process, worldFixture::CONTROLLER + 8, active);
    writeWord(process, KEYS, 0); pump(process);
}
inline void verifyJump(HANDLE process) {
    uint32_t count = readWord(process, ANIMATION_EVENTS);
    auto expectEvent = [&](const char* name) {
        pump(process);
        uint32_t prefix; std::memcpy(&prefix, name, 4);
        if (readWord(process, ANIMATION_EVENTS) != ++count
            || readWord(process, ANIMATION_NAME) != prefix
            || readWord(process, ANIMATION_LENGTH) != std::strlen(name)
            || readWord(process, ANIMATION_CAPACITY) != 15)
            throw std::runtime_error("physical jump transition did not dispatch its native animation event");
    };
    auto expectUnchanged = [&]() {
        pump(process);
        if (readWord(process, ANIMATION_EVENTS) != count)
            throw std::runtime_error("jump animation replayed without a physical transition");
    };
    writeWord(process, KEYS, 33); expectUnchanged(); // Space can be held while physics rejects takeoff.
    writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001); expectEvent("Jump");
    writeWord(process, KEYS, 9); expectUnchanged(); // Releasing Space/changing direction must not end a jump.
    writeWord(process, KEYS, 5); expectUnchanged();
    writeWord(process, worldFixture::MOVABLE + 0x20, 0); expectEvent("EndJump");
    writeWord(process, worldFixture::MOVABLE + 0x20, 1); expectUnchanged(); // Falling from a ledge is not takeoff.
    writeWord(process, worldFixture::MOVABLE + 0x20, 0); expectUnchanged();
    writeWord(process, worldFixture::STATE + 0x38, 2);
    writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001); expectUnchanged();
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    writeWord(process, worldFixture::STATE + 0x38, 4); expectUnchanged();
    // Keep the edge pending until the original animation component/model is ready.
    writeWord(process, worldFixture::MOVABLE + 0x58, 0);
    writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001); expectUnchanged();
    writeWord(process, ANIMATION, BASE + 0x1777228);
    writeWord(process, worldFixture::MOVABLE + 0x58, ANIMATION); expectUnchanged();
    writeWord(process, ANIMATION + 0x30, 0);
    writeWord(process, ANIMATION, BASE + 0x177722C); expectUnchanged();
    writeWord(process, ANIMATION + 0x30, ANIMATION); expectEvent("Jump");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0); expectEvent("EndJump");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001); expectEvent("Jump");
    writeWord(process, worldFixture::STATE + 0x38, 0); expectEvent("EndJump");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    writeWord(process, worldFixture::STATE + 0x38, 4); expectUnchanged();
    writeWord(process, KEYS, 0); pump(process);
}
inline void verifyJumpAttack(HANDLE process) {
    for (uint32_t keys : {0u, 1u, 5u, 9u}) {
        for (const auto& chain : std::array<std::array<uint32_t, 4>, 4>{{
            {235,237,238,239}, {241,243,244,245}, {247,249,250,251}, {253,255,256,257}
        }}) {
            writeWord(process, KEYS, 0); setFightState(process, 2); pump(process);
            writeWord(process, KEYS, keys); pump(process);
            writeWord(process, CLOCK, readWord(process, CLOCK) + 1000);
            expect(process, keys ? 5.f : 0.f, keys != 0, "jump attack run setup");
            const auto moves = readWord(process, MOVES);
            writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001); pump(process);
            auto events = readWord(process, ANIMATION_EVENTS);
            for (auto state : chain) {
                setFightState(process, state);
                expect(process, keys ? 3.f : 0.f, false, "airborne attack kept run eligibility");
                if (readWord(process, ANIMATION_EVENTS) != events
                    || readWord(process, ANIMATION_NAME) != 0x706D754A
                    || readWord(process, worldFixture::MOVABLE + 0x20) != 0x00010001
                    || readWord(process, MOVES) != moves)
                    throw std::runtime_error("attack phase did not preserve the physical jump and its animation");
                pump(process);
                if (readWord(process, ANIMATION_EVENTS) != events)
                    throw std::runtime_error("unchanged attack phase replayed the jump animation");
            }
            setFightState(process, 2); pump(process);
            if (readWord(process, ANIMATION_EVENTS) != events
                || readWord(process, ANIMATION_NAME) != 0x706D754A)
                throw std::runtime_error("attack recovery ended the jump before landing");
            writeWord(process, CLOCK, readWord(process, CLOCK) + 2000);
            expect(process, keys ? 3.f : 0.f, false, "attack recovery rearmed running in midair");
            writeWord(process, worldFixture::MOVABLE + 0x20, 0);
            expect(process, keys ? 3.f : 0.f, false, "jump attack did not land in walking or idle state");
            const auto moveState = readWord(process, worldFixture::MOVABLE + 0x2C);
            if (readWord(process, ANIMATION_EVENTS) != ++events
                || readWord(process, ANIMATION_NAME) != 0x4A646E45 || moveState < 1 || moveState > 9)
                throw std::runtime_error("jump attack landing did not end the jump exactly once");
            setFightState(process, chain[0]); pump(process);
            if (readWord(process, ANIMATION_EVENTS) != events)
                throw std::runtime_error("ground attack dispatched a jump animation");
        }
    }
    writeWord(process, KEYS, 1); setFightState(process, 2); pump(process);
    writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001); pump(process);
    setFightState(process, 238); pump(process);
    const auto beforeLanding = readWord(process, ANIMATION_EVENTS);
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    expect(process, 3, false, "landing during a strike did not restore walking");
    expectDirection(process, 1, false);
    for (uint32_t state : {239u, 2u}) { setFightState(process, state); pump(process); }
    if (readWord(process, ANIMATION_EVENTS) != beforeLanding + 1
        || readWord(process, ANIMATION_NAME) != 0x4A646E45)
        throw std::runtime_error("grounded recovery restarted the completed jump");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001); pump(process);
    const auto beforeReaction = readWord(process, ANIMATION_EVENTS);
    for (uint32_t state : {35u, 240u, 246u, 252u, 258u}) { setFightState(process, state); pump(process); }
    if (readWord(process, ANIMATION_EVENTS) != beforeReaction)
        throw std::runtime_error("jump refresh replaced a hurt or parried reaction");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0); pump(process);
    writeWord(process, KEYS, 0); setFightState(process, 2); pump(process);
}
inline void verifyTerrainWarmup(HANDLE process) {
    uint32_t tick = readWord(process, CLOCK);
    auto time = [&](uint32_t delta) { tick += delta; writeWord(process, CLOCK, tick); };
    const auto events = readWord(process, ANIMATION_EVENTS);
    const auto moves = readWord(process, MOVES), jumps = readWord(process, JUMP_COMMANDS);
    for (uint32_t keys : {1u, 5u, 9u}) {
        writeWord(process, KEYS, 0); pump(process);
        writeWord(process, KEYS, keys); expect(process, 3, false, "terrain warmup setup");
        time(400); expect(process, 3, false, "terrain warmup armed early");
        for (uint32_t flags : {1u, 0u, 1u, 0u}) {
            writeWord(process, worldFixture::MOVABLE + 0x20, flags);
            time(100); expect(process, 3, false, "terrain contact change armed running early");
        }
        time(199); expect(process, 3, false, "terrain warmup armed before 1000 ms");
        time(1); expect(process, 5, true, "terrain contact changes reset or paused run warmup");
    }
    writeWord(process, KEYS, 0); pump(process);
    writeWord(process, KEYS, 1); expect(process, 3, false, "airborne deadline setup");
    time(600); expect(process, 3, false, "airborne deadline armed early");
    writeWord(process, worldFixture::MOVABLE + 0x20, 1);
    time(399); expect(process, 3, false, "airborne deadline armed before 1000 ms");
    time(1); expect(process, 5, false, "terrain airborne flag blocked the run deadline");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    expect(process, 5, true, "terrain landing restarted completed run warmup");

    // Defense still cancels running; fresh warmup can start during terrain contact loss.
    writeWord(process, worldFixture::MOVABLE + 0x20, 1);
    setFightState(process, 223); expect(process, 3, false, "airborne defense retained running");
    time(1500); expect(process, 3, false, "airborne defense accumulated run time");
    setFightState(process, 2); expect(process, 3, false, "defense release reused run warmup");
    time(400); expect(process, 3, false, "defense recovery armed running early");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    time(599); expect(process, 3, false, "defense recovery armed before 1000 ms");
    time(1); expect(process, 5, true, "terrain contact loss delayed defense recovery warmup");
    if (readWord(process, ANIMATION_EVENTS) != events || readWord(process, MOVES) != moves
        || readWord(process, JUMP_COMMANDS) != jumps)
        throw std::runtime_error("terrain run warmup replayed native animation or movement commands");
    writeWord(process, KEYS, 0); pump(process);
}
inline void verifyAirMovement(HANDLE process) {
    setFightState(process, 2);
    for (uint32_t takeoff : {0u,1u,5u,9u,2u,4u,8u}) {
        writeWord(process, worldFixture::MOVABLE + 0x20, 0);
        writeWord(process, KEYS, 0); pump(process);
        writeWord(process, KEYS, takeoff); pump(process);
        const auto state = readWord(process, worldFixture::MOVABLE + 0x2C);
        writeWord(process, worldFixture::MOVABLE + 0x20, 0x10001); pump(process);
        const auto moves = readWord(process, MOVES);
        for (uint32_t keys : {0u,1u,2u,4u,8u,9u,5u,13u,33u}) {
            writeWord(process, KEYS, keys);
            expect(process, takeoff ? 3.f : 0.f, false, "jump input changed the takeoff speed");
            if (readWord(process, worldFixture::MOVABLE + 0x2C) != state
                || readWord(process, KEYS) != keys || readWord(process, MOVES) != moves)
                throw std::runtime_error("jump changed direction, rewrote input, or queued movement before landing");
        }
        // Last key state, not the takeoff direction, owns the first grounded frame.
        const uint32_t landingKeys = takeoff ? 0u : 8u;
        writeWord(process, KEYS, landingKeys); pump(process);
        writeWord(process, worldFixture::MOVABLE + 0x20, 0);
        expect(process, landingKeys ? 3.f : 0.f, false, "landing did not resume the latest input");
        expectDirection(process, landingKeys ? 3 : 0, false);
        if (readWord(process, MOVES) != moves + 1
            || readWord(process, DIRECTION) != (landingKeys ? 0x68676972u : 0u))
            throw std::runtime_error("landing did not queue exactly one current movement/stop command");
        pump(process);
        if (readWord(process, MOVES) != moves + 1) throw std::runtime_error("landing replayed movement more than once");
    }
    writeWord(process, KEYS, 0); pump(process);
}
inline void verifyWeaponChange(HANDLE process) {
    uint32_t tick = readWord(process, CLOCK);
    auto time = [&](uint32_t delta) { tick += delta; writeWord(process, CLOCK, tick); pump(process); };
    for (uint32_t keys : {1u, 5u, 9u}) {
        writeWord(process, KEYS, 0); setFightState(process, 2); pump(process);
        writeWord(process, KEYS, keys); pump(process); time(400);
        setFightState(process, 1); expect(process, 3, false, "weapon change interrupted run warmup");
        time(599); expect(process, 3, false, "weapon change armed running before the original deadline");
        time(1); expect(process, 5, true, "weapon change reset or paused run warmup");
        const auto events = readWord(process, EVENTS);
        for (uint32_t state : {2u, 1u, 2u, 1u, 2u}) {
            setFightState(process, state); time(200);
            expect(process, 5, true, "weapon change interrupted an established run");
            expectDirection(process, keys == 1 ? 1 : keys == 5 ? 8 : 2, true);
            if (readWord(process, EVENTS) != events)
                throw std::runtime_error("weapon change toggled the running flag");
        }
    }
    // Starting movement while equipping uses the same uninterrupted warmup.
    writeWord(process, KEYS, 0); setFightState(process, 1); pump(process); time(1000);
    expect(process, 0, false, "stationary weapon change armed running");
    writeWord(process, KEYS, 1); pump(process); time(500);
    setFightState(process, 2); pump(process); time(499);
    expect(process, 3, false, "weapon change completion shortened run warmup");
    time(1); expect(process, 5, true, "weapon change completion restarted run warmup");
    for (uint32_t keys : {0u, 2u, 4u, 17u}) {
        setFightState(process, 1); writeWord(process, KEYS, keys);
        expect(process, keys ? 3.f : 0.f, false, "weapon change ignored a movement interruption");
        writeWord(process, KEYS, 1); pump(process); time(999);
        expect(process, 3, false, "weapon change reused canceled run warmup");
        time(1); expect(process, 5, true, "weapon change did not allow running to restart");
    }
    setFightState(process, 35); expect(process, 3, false, "hit during weapon change retained running");
    setFightState(process, 2); pump(process); time(999);
    expect(process, 3, false, "hit during weapon change kept elapsed run time");
    time(1); expect(process, 5, true, "hit recovery did not rearm running");
    writeWord(process, KEYS, 0); pump(process);
}
inline void verify(HANDLE process) {
    const auto config = profile();
    const float initialSpeed = static_cast<float>(config.speedMilli) / 1000.f;
    writeWord(process, CLOCK, 1000);
    writeWord(process, CLOCK_MODE, 1);
    verifyPreparation(process);
    uint32_t tick = readWord(process, CLOCK);
    auto time = [&](uint32_t delta) { tick += delta; writeWord(process, CLOCK, tick); pump(process); };
    for (uint32_t mask = 0; mask < 16; ++mask) {
        writeWord(process, KEYS, mask); pump(process);
        const int forward = ((mask & 1) != 0) - ((mask & 2) != 0);
        const int right = ((mask & 8) != 0) - ((mask & 4) != 0);
        const uint32_t direction = forward > 0 ? (right > 0 ? 2 : right < 0 ? 8 : 1)
            : forward < 0 ? (right > 0 ? 4 : right < 0 ? 6 : 5) : right > 0 ? 3 : right < 0 ? 7 : 0;
        expectDirection(process, direction, false);
    }
    verifyTerrainWarmup(process);
    verifyJump(process);
    verifyJumpAttack(process);
    verifyAirMovement(process);
    verifyWeaponChange(process);
    tick = readWord(process, CLOCK);
    writeWord(process, KEYS, 1); expect(process, 3, false, "jog speed");
    time(999); expect(process, 3, false, "sprint armed too early");
    time(1); expect(process, 5, true, "run speed/HUD did not arm");
    if (readWord(process, worldFixture::MOVABLE + 0x2C) != 10) throw std::runtime_error("run state did not follow speed");
    const auto writes = readWord(process, STATE_WRITES); pump(process);
    if (readWord(process, STATE_WRITES) != writes) throw std::runtime_error("duplicate locomotion state writes");
    const auto events = readWord(process, EVENTS); time(100);
    if (readWord(process, EVENTS) != events) throw std::runtime_error("duplicate running events");
    writeWord(process, KEYS, 9); expect(process, 5, true, "diagonal movement reset running");
    // A new keyboard command can reset velocity; the next frame restores the selected speed.
    writeWord(process, RESET_SPEED, 1);
    expect(process, 5, true, "native key command lost run velocity");
    expectDirection(process, 2, true);
    writeWord(process, KEYS, 5); pump(process);
    expectDirection(process, 8, true);
    const auto jumpEvents = readWord(process, ANIMATION_EVENTS);
    writeWord(process, KEYS, 37); expect(process, 5, true, "Space reset an established run before takeoff");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001);
    expect(process, 5, false, "takeoff retained dash eligibility or lost run speed");
    expectDirection(process, 8, true);
    // The native takeoff callback clears RUN without discarding the carried run speed.
    writeWord(process, RESET_SPEED, 2);
    expect(process, 5, false, "native takeoff restored dash eligibility or lost run speed");
    time(3000);
    writeWord(process, KEYS, 9); expect(process, 5, false, "Space release or diagonal change restored airborne dash eligibility");
    writeWord(process, RESET_SPEED, 3);
    expect(process, 5, false, "airborne key command restored dash eligibility or lost run speed"); expectDirection(process, 8, true);
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    expect(process, 5, true, "landing restarted the acceleration delay"); expectDirection(process, 2, true);
    if (readWord(process, ANIMATION_EVENTS) != jumpEvents + 2)
        throw std::runtime_error("retaining running changed takeoff/landing animation transitions");
    for (int jump = 0; jump < 2; ++jump) {
        writeWord(process, KEYS, 33); expect(process, 5, true, "repeat jump key reset running");
        writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001);
        expect(process, 5, false, "repeat takeoff retained dash eligibility or lost run speed");
        writeWord(process, worldFixture::MOVABLE + 0x20, 0);
        expect(process, 5, true, "landing with Space held reset running");
    }
    writeWord(process, KEYS, 1);
    writeWord(process, worldFixture::MOVABLE + 0x20, 1);
    expect(process, 5, false, "falling retained dash eligibility or lost run speed");
    expectDirection(process, 1, true);
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    expect(process, 5, true, "landing after a fall did not restore dash eligibility");
    // Airborne direction keys stay locked; crouching or taking a hit still cancels running.
    for (uint32_t keys : {0u, 2u, 4u, 17u, 1u}) {
        writeWord(process, KEYS, 1); pump(process); time(1000);
        expect(process, 5, true, "run setup before airborne interruption");
        writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001); pump(process);
        if (keys == 1) setFightState(process, 35);
        else writeWord(process, KEYS, keys);
        const bool interrupted = keys == 17 || keys == 1;
        expect(process, interrupted ? 3.f : 5.f, false, "airborne input changed the locked movement speed");
        setFightState(process, 2); writeWord(process, KEYS, 1); time(3000);
        expect(process, interrupted ? 3.f : 5.f, false, "interrupted run rearmed in midair");
        writeWord(process, worldFixture::MOVABLE + 0x20, 0); pump(process);
        time(999); expect(process, interrupted ? 3.f : 5.f, !interrupted, "landing reused canceled run eligibility");
        time(1); expect(process, 5, true, "canceled run did not rearm on the ground");
    }
    writeWord(process, KEYS, 0); pump(process);
    writeWord(process, KEYS, 1); pump(process); time(500);
    writeWord(process, KEYS, 33); expect(process, 3, false, "jump created run eligibility before acceleration completed");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0x00010001);
    writeWord(process, KEYS, 1); time(3000); expect(process, 3, false, "slow takeoff accelerated in midair");
    writeWord(process, worldFixture::MOVABLE + 0x20, 0); pump(process);
    time(999); expect(process, 3, false, "slow jump counted airtime toward ground acceleration");
    time(1); expect(process, 5, true, "slow jump did not rearm after ground acceleration");
    for (const auto& chain : std::vector<std::vector<uint32_t>>{
        {235,237,238,239}, {241,243,244,245}, {247,249,250,251}, {253,255,256,257},
        {305,223,231,223,227}, {308,224,232,224,228}, {307,225,233,225,229}, {306,226,234,226,230}
    }) {
        for (auto state : chain) {
            setFightState(process, state);
            expect(process, 3, false, "attack/parry retained run speed or HUD");
            writeWord(process, KEYS, 1); time(1000);
            expect(process, 3, false, "held movement armed running during attack/parry");
            writeWord(process, KEYS, 9);
            expect(process, 3, false, "direction change accelerated attack/parry");
        }
        setFightState(process, 2); pump(process); time(999);
        expect(process, 3, false, "attack/parry kept elapsed run time");
        time(1); expect(process, 5, true, "attack/parry recovery did not rearm running");
    }
    auto dash = [&](bool advancing) {
        expect(process, advancing ? initialSpeed : 0.f, false, "dash movement window used the wrong speed");
        expectDirection(process, advancing ? 1u : 0u, false);
        if (readWord(process, DIRECTION) != (advancing ? 0x7075u : 0u))
            throw std::runtime_error("dash movement window kept the wrong native command");
        uint32_t bits; std::memcpy(&bits, &initialSpeed, 4);
        if (advancing && (readWord(process, COMMAND_SPEED) != bits
            || readWord(process, COMMAND_MAX_SPEED) != bits))
            throw std::runtime_error("dash did not use the original fixed-point initial speed");
    };
    auto keyCommand = [&](uint32_t keys) {
        writeWord(process, KEYS, keys);
        writeWord(process, RESET_SPEED, readWord(process, RESET_SPEED) + 1);
    };
    setFightState(process, 438); dash(false);
    for (uint32_t keys : {1u, 9u, 4u, 2u, 0u}) {
        keyCommand(keys); dash(false);
    }
    time(200); dash(false);
    setFightState(process, 439); dash(true);
    for (uint32_t keys : {1u, 9u, 4u, 2u, 0u}) {
        keyCommand(keys); dash(true); time(100); dash(true);
    }
    time(config.moveMs - 501); dash(true);
    time(1); dash(false); // Movement stops at the configured boundary, not the animation deadline.
    const auto stoppedMoves = readWord(process, MOVES); pump(process);
    if (readWord(process, MOVES) != stoppedMoves)
        throw std::runtime_error("expired dash repeatedly issued stop commands");
    for (uint32_t keys : {1u, 9u, 4u, 2u, 0u}) {
        keyCommand(keys); dash(false);
    }
    time(config.durationMs - config.moveMs); dash(false);
    setFightState(process, 2); pump(process);
    // A new attack gets a fresh deadline, including GetTickCount wrap and skipped frames.
    tick = 0xFFFFFF00; time(0);
    setFightState(process, 439); dash(true);
    time(config.moveMs - 1); dash(true); time(1); dash(false);
    setFightState(process, 2); pump(process);
    setFightState(process, 439); dash(true);
    time(config.moveMs + 200); dash(false);
    // Same state and same keys, but a new sequence is a new native action.
    setFightState(process, 439); dash(true);
    const auto movesBeforeSequence = readWord(process, MOVES);
    setFightState(process, 439); dash(true);
    if (readWord(process, MOVES) != movesBeforeSequence + 1)
        throw std::runtime_error("new dash sequence reused the previous movement command");
    // First observation can arrive after the entire movement interval.
    setFightState(process, 2); pump(process);
    setFightState(process, 439, tick - config.moveMs); dash(false);
    if (readWord(process, JUMP_COMMANDS)) throw std::runtime_error("dash movement invoked the jump command");
    setFightState(process, 2); expect(process, 0, false, "released keys did not stop after dash");
    if (readWord(process, DIRECTION)) throw std::runtime_error("released keys resumed motion");
    expectDirection(process, 0, false);
    for (uint32_t keys : {0u, 2u, 4u, 8u, 3u, 17u, 33u}) {
        writeWord(process, KEYS, keys); pump(process); time(1000);
        expect(process, keys == 0 || keys == 3 ? 0.f : 3.f, false, "stop/backward/strafe/crouch/jump armed running");
    }
    writeWord(process, KEYS, 1); pump(process); time(1000); expect(process, 5, true, "run rearm");
    setFightState(process, 35); expect(process, 3, false, "hurt kept sprint ready");
    setFightState(process, 2); pump(process); time(999); expect(process, 3, false, "hurt kept elapsed run time");
    time(1); expect(process, 5, true, "hurt recovery rearm");
    writeWord(process, worldFixture::STATE + 0x38, 0); expect(process, 3, false, "leaving kept sprint ready");
    writeWord(process, worldFixture::STATE + 0x38, 4); pump(process); time(1000);
    writeWord(process, worldFixture::INSTANCE + 0x6C, 2); expect(process, 3, false, "epoch reused sprint readiness");
    time(1000); expect(process, 5, true, "epoch recovery");
    // Tick subtraction remains valid across GetTickCount wraparound.
    writeWord(process, KEYS, 0); pump(process); tick = 0xFFFFFF00; time(0);
    writeWord(process, KEYS, 1); pump(process); time(1000); expect(process, 5, true, "clock wrap broke run timer");
    setFightState(process, 439); pump(process);
    writeWord(process, KEYS, 4); pump(process);
    setFightState(process, 35); expect(process, 3, false, "interrupted dash speed");
    if (readWord(process, DIRECTION) != 0x7466656C) throw std::runtime_error("dash interruption did not restore held direction");
    if (readWord(process, JUMP_COMMANDS)) throw std::runtime_error("dash recovery invoked the jump command");
    writeWord(process, KEYS, 0); setFightState(process, 2);
    writeWord(process, worldFixture::INSTANCE + 0x6C, 1); pump(process);
    writeWord(process, CLOCK_MODE, 0);
}
}
