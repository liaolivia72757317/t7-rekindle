#pragma once
#include "MovementCommandFixture.h"

namespace jumpControlFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t DATA = BASE + 0xE0000, DISPATCH = DATA + 0x100, CHILDREN = DATA + 0x200;
constexpr uint32_t GROUP = DATA + 0x240, HEADING = DATA + 0x280, MOVE = DATA + 0x2C0;
constexpr uint32_t JUMP = DATA + 0x300, POSITION = DATA + 0x340;
constexpr uint32_t REQUEST = DATA, JUMPS = DATA + 4, CROUCHES = DATA + 8, CORRECTIONS = DATA + 12;
constexpr uint32_t COMPOSITE_RVA = 0x3CFBF0, COMMAND_CALL_RVA = 0x3CFC09;
constexpr uint32_t HEADING_REQUEST = DATA + 16, HEADING_SENDS = DATA + 20, MOVES_AT_HEADING = DATA + 24;
constexpr uint32_t GAME_DIRECTION = DATA + 0x500, HEADING_SENDER = DATA + 0x580, DIRECTION_INPUT = DATA + 0x600;
constexpr uint32_t HEADING_GROUP = DATA + 0x700, HEADING_CHILD = DATA + 0x740, HEADING_SCALE = DATA + 0x744;
constexpr uint32_t SEND_HEADING_RVA = 0x475F50;
constexpr uint32_t ANIMATION_REQUEST = DATA + 28, ANIMATION_TARGET = DATA + 32;
constexpr uint32_t EVENT_NAME = DATA + 0x780, EVENT_TEXT = DATA + 0x7C0, OTHER_ANIMATION = DATA + 0x800;
inline void prepare(HANDLE process) {
    t7r::WorldHookCode c;
    c.emit({0x9C,0x60,0x83,0x3D}); c.word(REQUEST); c.emit({0}); const auto idle = c.branch(0x84);
    c.emit({0x68}); c.word(worldFixture::MOVABLE); c.emit({0xB9}); c.word(GROUP);
    c.call(BASE + COMPOSITE_RVA); c.store(REQUEST, 0); c.bind(idle);
    c.emit({0x83,0x3D}); c.word(HEADING_REQUEST); c.emit({0}); const auto noHeading = c.branch(0x84);
    c.emit({0x6A,0,0xB9}); c.word(HEADING_SENDER); c.call(BASE + SEND_HEADING_RVA);
    c.store(HEADING_REQUEST, 0); c.bind(noHeading);
    c.emit({0x83,0x3D}); c.word(ANIMATION_REQUEST); c.emit({0}); const auto noAnimation = c.branch(0x84);
    c.emit({0x68}); c.word(EVENT_NAME); c.emit({0x8B,0x0D}); c.word(ANIMATION_TARGET);
    c.call(BASE + 0x3BEEC0); c.store(ANIMATION_REQUEST, 0);
    c.bind(noAnimation); c.emit({0x61,0x9D,0xC3});
    t7r::writeClientMemory(process, DISPATCH, c.bytes);
    // Native composite traversal calls each child separately, including mixed corrections.
    t7r::writeClientMemory(process, BASE + COMPOSITE_RVA,
        {0x55,0x8B,0xEC,0x56,0x8B,0x71,0x10,0x57,0x8B,0x79,0x14,0x3B,0xF7,0x74,0x18,
         0x53,0x8B,0x5D,8,0x8B,0x0E,0x85,0xC9,0x74,6,0x8B,1,0x53,0xFF,0x50,8,
         0x83,0xC6,4,0x3B,0xF7,0x75,0xED,0x5B,0x5F,0x5E,0x5D,0xC2,4,0});
    c.bytes = {0x8B,0x44,0x24,4,0x8B,0x40,0x38,0x8B,0x51,8,0x89,0x90}; c.word(0xAC);
    c.emit({0xC2,4,0}); t7r::writeClientMemory(process, DATA + 0x400, c.bytes);
    c.bytes = {0x83,0x79,0x0C,1}; const auto noCrouch = c.branch(0x87);
    c.emit({0xFF,0x05}); c.word(CROUCHES); c.bind(noCrouch);
    c.emit({0x80,0x79,8,0}); const auto noJump = c.branch(0x84);
    c.emit({0xFF,0x05}); c.word(JUMPS);
    c.emit({0x8B,0x44,0x24,4,0x8B,0x40,0x38,0xC6,0x40,0x0A,1});
    c.bind(noJump); c.emit({0xC2,4,0}); t7r::writeClientMemory(process, DATA + 0x440, c.bytes);
    c.bytes.clear(); c.emit({0xFF,0x05}); c.word(CORRECTIONS); c.emit({0xC2,4,0});
    t7r::writeClientMemory(process, DATA + 0x4C0, c.bytes);
    for (const auto& entry : std::array<std::pair<uint32_t, uint32_t>, 4>{{
        {HEADING,0x17782C8}, {MOVE,0x17782D8}, {JUMP,0x17782E8}, {POSITION,0x17782B8}
    }}) writeWord(process, entry.first, BASE + entry.second);
    writeWord(process, BASE + 0x17782C8 + 8, DATA + 0x400);
    writeWord(process, BASE + 0x17782D8 + 8, BASE + movementCommandFixture::APPLY_RVA);
    writeWord(process, BASE + 0x17782E8 + 8, DATA + 0x440);
    writeWord(process, BASE + 0x17782B8 + 8, DATA + 0x4C0);
    writeWord(process, GROUP + 0x10, CHILDREN);

    writeWord(process, GAME_DIRECTION, BASE + 0x1781904);
    // GeGameDirectionImpl -> GePlayerMountBladeDirInput -> active GeMBInputOfflineInfantry.
    writeWord(process, GAME_DIRECTION + 0x38, DIRECTION_INPUT);
    writeWord(process, DIRECTION_INPUT, BASE + 0x1781D90);
    writeWord(process, DIRECTION_INPUT + 0xA4, HEADING_SENDER);
    writeWord(process, HEADING_SENDER, BASE + 0x17825EC);
    writeWord(process, HEADING_SENDER + 8, DIRECTION_INPUT);
    writeWord(process, HEADING_GROUP + 0x10, HEADING_CHILD);
    writeWord(process, HEADING_GROUP + 0x14, HEADING_CHILD + 4);
    writeWord(process, HEADING_CHILD, HEADING);
    writeWord(process, HEADING_SCALE, 0x42652EE0); // Native radians-to-degrees quantization.
    // Cache the desired heading before dispatch. The air-input hook can discard
    // the command even though subsequent unchanged-camera updates are deduplicated.
    c.bytes = {0x55,0x8B,0xEC,0x56,0x8B,0xF1,0x8B,0x46,8};
    c.emit({0xF3,0x0F,0x10,0x80}); c.word(0xCC);
    c.emit({0xF3,0x0F,0x59,0x05}); c.word(HEADING_SCALE);
    c.emit({0xF3,0x0F,0x2C,0xC0,0x3B,0x46,0x10}); const auto changed = c.branch(0x85);
    c.emit({0x80,0x7D,8,1}); const auto unchanged = c.branch(0x85);
    c.bind(changed); c.emit({0x89,0x46,0x10,0xFF,0x05}); c.word(HEADING_SENDS);
    c.emit({0xA1}); c.word(sprintFixture::MOVES); c.emit({0xA3}); c.word(MOVES_AT_HEADING);
    c.emit({0x8B,0x46,8,0x8B,0x80}); c.word(0xCC); c.emit({0xA3}); c.word(HEADING + 8);
    c.emit({0x68}); c.word(worldFixture::MOVABLE); c.emit({0xB9}); c.word(HEADING_GROUP);
    c.call(BASE + COMPOSITE_RVA); c.bind(unchanged); c.emit({0x5E,0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + SEND_HEADING_RVA, c.bytes);
}
inline void verifyLandingHeading(HANDLE process) {
    using worldFixture::MOVABLE;
    using worldFixture::CONTROLLER;
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    const auto owner = readWord(process, MOVABLE + 8), heading = readWord(process, CONTROLLER + 0xAC);
    const auto direction = readWord(process, worldFixture::ENTITY + 0x94);
    const auto clockMode = readWord(process, sprintFixture::CLOCK_MODE);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, worldFixture::ENTITY + 0x94, GAME_DIRECTION);
    writeWord(process, sprintFixture::CLOCK_MODE, 1);
    sprintFixture::setFightState(process, 2);
    auto frame = [&] {
        writeWord(process, HEADING_REQUEST, 1);
        worldFixture::requestFrame(process);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, worldFixture::FRAME_GATE) != 1) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("landing heading frame timeout");
            Sleep(1);
        }
    };
    auto takeoff = [&](uint32_t keys, bool running) {
        writeWord(process, MOVABLE + 0x20, 0); writeWord(process, CONTROLLER + 8, 1);
        writeWord(process, sprintFixture::KEYS, 0); frame();
        writeWord(process, DIRECTION_INPUT + 0xCC, 0x3E800000); // Initial heading: 0.25 radians.
        writeWord(process, HEADING_SENDER + 0x10, 0xFFFFFFFF);
        writeWord(process, sprintFixture::KEYS, keys); frame();
        if (running) {
            writeWord(process, sprintFixture::CLOCK, readWord(process, sprintFixture::CLOCK) + 1000); frame();
        }
        writeWord(process, MOVABLE + 0x20, 0x10001); frame();
    };
    for (uint32_t keys : {1u,5u,9u,0u}) for (bool running : {false,true}) {
        takeoff(1, running);
        const auto moves = readWord(process, sprintFixture::MOVES), jumps = readWord(process, JUMPS);
        const auto target = running ? 0xBFC00000u : 0x3FC00000u;
        writeWord(process, DIRECTION_INPUT + 0xCC, target); frame();
        const auto sent = readWord(process, HEADING_SENDS);
        writeWord(process, sprintFixture::KEYS, keys); frame(); frame();
        if (readWord(process, CONTROLLER + 0xAC) != 0x3E800000
            || readWord(process, HEADING_SENDS) != sent || readWord(process, sprintFixture::MOVES) != moves)
            throw std::runtime_error("airborne camera turn was applied or unchanged input was replayed");
        writeWord(process, MOVABLE + 0x20, 0); frame();
        if (readWord(process, CONTROLLER + 0xAC) != target || readWord(process, HEADING_SENDS) != sent + 1
            || readWord(process, sprintFixture::MOVEMENT_HEADING) != target
            || readWord(process, MOVES_AT_HEADING) != moves || readWord(process, sprintFixture::MOVES) != moves + 1)
            throw std::runtime_error("landing did not resync the stationary camera before restoring movement");
        sprintFixture::expectDirection(process, keys == 5 ? 8 : keys == 9 ? 2 : keys ? 1 : 0, running && keys);
        if (readWord(process, CONTROLLER + 0x24) != (keys ? (running ? 0x40A00000u : 0x40400000u) : 0u)
            || readWord(process, sprintFixture::DIRECTION) != (keys == 5 ? 0x7466656Cu : keys == 9 ? 0x68676972u : keys ? 0x7075u : 0u))
            throw std::runtime_error("landing heading changed held-key movement or released-key stop");
        frame(); frame();
        if (readWord(process, HEADING_SENDS) != sent + 1 || readWord(process, sprintFixture::MOVES) != moves + 1
            || readWord(process, JUMPS) != jumps)
            throw std::runtime_error("landing repeatedly resent heading/movement or replayed jump");
    }
    // Missing or unrelated direction components must not receive the offline call.
    for (const auto& missing : std::array<std::pair<uint32_t, uint32_t>, 6>{{
        {worldFixture::ENTITY + 0x94,0}, {GAME_DIRECTION,BASE + 0x1781908},
        {GAME_DIRECTION + 0x38,0}, {DIRECTION_INPUT,BASE + 0x1781D94},
        {DIRECTION_INPUT + 0xA4,0}, {HEADING_SENDER,BASE + 0x17824B4}
    }}) {
        takeoff(1, false);
        writeWord(process, DIRECTION_INPUT + 0xCC, 0x3FC00000); frame();
        const auto sent = readWord(process, HEADING_SENDS), moves = readWord(process, sprintFixture::MOVES);
        const auto original = readWord(process, missing.first); writeWord(process, missing.first, missing.second);
        writeWord(process, MOVABLE + 0x20, 0); frame();
        if (readWord(process, HEADING_SENDS) != sent || readWord(process, sprintFixture::MOVES) != moves + 1)
            throw std::runtime_error("landing used an unrelated direction controller or lost movement");
        writeWord(process, missing.first, original);
    }
    writeWord(process, sprintFixture::KEYS, 0); frame();
    writeWord(process, worldFixture::ENTITY + 0x94, direction);
    writeWord(process, MOVABLE + 8, owner); writeWord(process, CONTROLLER + 0xAC, heading);
    writeWord(process, sprintFixture::CLOCK_MODE, clockMode); writeWord(process, worldFixture::FRAME_GATE, 0);
}
inline void verify(HANDLE process) {
    using worldFixture::MOVABLE;
    using worldFixture::CONTROLLER;
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    const auto owner = readWord(process, MOVABLE + 8), flags = readWord(process, CONTROLLER + 8);
    const auto heading = readWord(process, CONTROLLER + 0xAC);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    auto invoke = [&](std::initializer_list<uint32_t> commands) {
        uint32_t end = CHILDREN;
        for (auto command : commands) { writeWord(process, end, command); end += 4; }
        writeWord(process, GROUP + 0x14, end); writeWord(process, REQUEST, 1);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, REQUEST)) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("jump control command timeout");
            Sleep(1);
        }
    };
    auto setup = [&](uint32_t physicalFlags, bool pending = false) {
        writeWord(process, MOVABLE + 0x20, physicalFlags);
        writeWord(process, CONTROLLER + 8, pending ? 0x10001 : 1);
        writeWord(process, CONTROLLER + 0xAC, 0x3E800000);
        writeWord(process, CONTROLLER + 0x24, 0x40400000);
        writeWord(process, MOVABLE + 0x2C, 2);
        writeWord(process, HEADING + 8, 0x3F800000);
        writeWord(process, MOVE + 0x0C, 0x40800000); writeWord(process, MOVE + 0x30, 9);
        writeWord(process, JUMP + 8, 1); writeWord(process, JUMP + 0x0C, 2);
        writeWord(process, JUMPS, 0); writeWord(process, CROUCHES, 0);
    };
    auto expect = [&](bool changedDirection, uint32_t jumps) {
        if (readWord(process, CONTROLLER + 0xAC) != (changedDirection ? 0x3F800000u : 0x3E800000u)
            || readWord(process, CONTROLLER + 0x24) != (changedDirection ? 0x40800000u : 0x40400000u)
            || readWord(process, MOVABLE + 0x2C) != (changedDirection ? 9u : 2u)
            || readWord(process, JUMPS) != jumps || readWord(process, JUMP + 8) != 1)
            throw std::runtime_error("jump allowed direction changes/repeated takeoff or changed the queued input");
    };
    for (uint32_t state : {438u,439u,440u,443u}) {
        // Change the live action without a frame tick: input must not use a cached dash phase.
        sprintFixture::setFightState(process, state);
        setup(0); invoke({HEADING,MOVE,JUMP,JUMP});
        if (readWord(process, JUMPS) || readWord(process, CONTROLLER + 8) != 1)
            throw std::runtime_error("dash attack allowed a jump or queued a delayed takeoff");
        expect(true, 0);
        writeWord(process, JUMP + 8, 0); invoke({JUMP});
        writeWord(process, JUMP + 8, 1); writeWord(process, JUMP + 0x0C, 1);
        const auto corrected = readWord(process, CORRECTIONS);
        invoke({POSITION,JUMP}); expect(true, 0);
        if (readWord(process, CROUCHES) || readWord(process, JUMP + 0x0C) != 1
            || readWord(process, CORRECTIONS) != corrected + 1)
            throw std::runtime_error("dash attack allowed crouching, changed queued input or lost position correction");
        for (uint32_t physicalFlags : {0u,1u,0x10001u}) {
            setup(physicalFlags); writeWord(process, JUMP + 8, 0); writeWord(process, JUMP + 0x0C, 1);
            invoke({JUMP});
            if (readWord(process, CROUCHES) || readWord(process, JUMPS) || readWord(process, JUMP + 0x0C) != 1)
                throw std::runtime_error("dash attack accepted crouch-only input on the ground or in midair");
            writeWord(process, JUMP + 0x0C, 0); invoke({JUMP});
            if (readWord(process, CROUCHES) != 1)
                throw std::runtime_error("dash attack discarded the stand-up command");
        }
        sprintFixture::setFightState(process, 2);
        setup(0); writeWord(process, JUMP + 0x0C, 1); invoke({HEADING,MOVE,JUMP}); expect(true, 1);
        if (readWord(process, CROUCHES) != 1)
            throw std::runtime_error("dash completion did not restore crouching");
    }
    for (uint32_t state : {241u,243u,244u,245u,2u}) {
        sprintFixture::setFightState(process, state);
        setup(0); invoke({HEADING,MOVE,JUMP}); expect(true, 1);
    }
    setup(0x10001); invoke({HEADING,MOVE,JUMP}); expect(false, 0);
    for (uint32_t state : {241u,243u,244u,245u,2u}) {
        sprintFixture::setFightState(process, state);
        invoke({HEADING,MOVE,JUMP}); expect(false, 0);
    }
    writeWord(process, JUMP + 8, 0); invoke({JUMP});
    writeWord(process, JUMP + 8, 1); invoke({JUMP}); expect(false, 0);
    writeWord(process, JUMP + 0x0C, 1); const auto corrections = readWord(process, CORRECTIONS);
    invoke({POSITION,HEADING,MOVE,JUMP}); expect(false, 0);
    if (readWord(process, CROUCHES) != 1 || readWord(process, CORRECTIONS) != corrections + 1)
        throw std::runtime_error("jump restrictions discarded crouch or position correction");
    setup(0); invoke({HEADING,MOVE,JUMP,JUMP}); expect(true, 1);
    setup(0, true); invoke({HEADING,MOVE,JUMP}); expect(false, 0); // Same-frame repeated takeoff.
    setup(0x10001); invoke({HEADING,MOVE,JUMP}); expect(false, 0);
    setup(0); invoke({HEADING,MOVE,JUMP}); expect(true, 1); // Landing restores all controls.
    setup(1); invoke({HEADING,MOVE,JUMP}); expect(true, 0); // Falling is not a new jump.
    setup(0x10000); invoke({HEADING,MOVE,JUMP}); expect(true, 1);
    setup(0x10001); writeWord(process, MOVE + 0x34, 1); invoke({MOVE});
    if (readWord(process, MOVABLE + 0x2C) != 9 || readWord(process, CONTROLLER + 0x24) != 0x40800000)
        throw std::runtime_error("jump restriction discarded state-directed movement");
    writeWord(process, MOVE + 0x34, 0);
    sprintFixture::setFightState(process, 439);
    setup(0x10001); writeWord(process, worldFixture::STATE + 0x38, 2);
    invoke({HEADING,MOVE,JUMP}); expect(true, 1); writeWord(process, worldFixture::STATE + 0x38, 4);
    setup(0x10001); writeWord(process, MOVABLE + 8, worldFixture::ENTITY + 0x100);
    invoke({HEADING,MOVE,JUMP}); expect(true, 1);
    setup(0); writeWord(process, MOVABLE + 8, owner); writeWord(process, CONTROLLER + 8, flags);
    sprintFixture::setFightState(process, 2);
    writeWord(process, CONTROLLER + 0xAC, heading);
    writeWord(process, worldFixture::FRAME_GATE, 0);
}
inline void verifyCrouchAnimation(HANDLE process) {
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    writeWord(process, ANIMATION_TARGET, sprintFixture::ANIMATION);
    writeWord(process, OTHER_ANIMATION, BASE + 0x177722C);
    auto invoke = [&](const char* event, bool allowed, bool heap = false) {
        const auto length = static_cast<uint32_t>(std::strlen(event));
        t7r::Bytes name(24, 0);
        if (heap) {
            std::memcpy(name.data(), &EVENT_TEXT, 4);
            t7r::writeClientMemory(process, EVENT_TEXT, t7r::Bytes(event, event + length + 1));
        } else std::memcpy(name.data(), event, length + 1);
        const uint32_t capacity = heap ? 31u : 15u;
        std::memcpy(name.data() + 16, &length, 4); std::memcpy(name.data() + 20, &capacity, 4);
        t7r::writeClientMemory(process, EVENT_NAME, name);
        const auto before = readWord(process, sprintFixture::ANIMATION_EVENTS);
        writeWord(process, ANIMATION_REQUEST, 1);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, ANIMATION_REQUEST)) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("crouch animation command timeout");
            Sleep(1);
        }
        if (readWord(process, sprintFixture::ANIMATION_EVENTS) != before + (allowed ? 1u : 0u)
            || t7r::readClientMemory(process, EVENT_NAME, name.size()) != name)
            throw std::runtime_error("dash crouch animation gate dispatched the wrong event or changed its name");
    };
    for (uint32_t state : {438u,439u,440u,443u}) {
        sprintFixture::setFightState(process, state);
        invoke("Crouch", false); invoke("Crouch", false, true);
        invoke("EndCrouch", true); invoke("Jump", true); invoke("CrouchOther", true);
        sprintFixture::setFightState(process, 2); invoke("Crouch", true);
    }
    for (uint32_t state : {241u,243u,244u,245u}) {
        sprintFixture::setFightState(process, state); invoke("Crouch", true);
    }
    sprintFixture::setFightState(process, 439);
    writeWord(process, ANIMATION_TARGET, OTHER_ANIMATION); invoke("Crouch", true);
    writeWord(process, ANIMATION_TARGET, sprintFixture::ANIMATION);
    writeWord(process, worldFixture::STATE + 0x38, 2); invoke("Crouch", true);
    writeWord(process, worldFixture::STATE + 0x38, 4); sprintFixture::setFightState(process, 2);
    writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
