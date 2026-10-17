#pragma once
#include "MovementCommandFixture.h"

namespace lightShieldRunFixture {
using namespace sprintFixture;
using movementAnimationFixture::readFloat;
using movementAnimationFixture::writeFloat;
constexpr uint32_t HERO_TABLE = BASE + 0xF0000, HERO_ROW = HERO_TABLE + 0x100;
constexpr uint32_t HERO_TABLE_GLOBAL = BASE + 0x24409D0;
inline void prepare(HANDLE process) {
    // Native resource lookup contract: this=table, argument=hero ID, result=row or null.
    t7r::WorldHookCode c;
    c.emit({0x55,0x8B,0xEC,0x8B,0x55,8,0x56,0x8B,0x71,0x24,0x33,0xC0,0x3B,0x16});
    const auto missing = c.branch(0x85);
    c.emit({0x8B,0xC6}); c.bind(missing); c.emit({0x5E,0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x5D3CB0, c.bytes);
    writeWord(process, HERO_TABLE + 4 + 0x24, HERO_ROW);
    writeWord(process, FIGHT + 8, worldFixture::ENTITY);
}
inline void verify(HANDLE process, uint32_t mailbox) {
    using worldFixture::MOVABLE;
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    const auto savedTable = readWord(process, HERO_TABLE_GLOBAL);
    writeWord(process, HERO_TABLE_GLOBAL, HERO_TABLE);
    writeWord(process, HERO_ROW, 2102);
    writeWord(process, HERO_ROW + 0x1C05, 1); // Native light-shield hero category, not shield performance grade.
    writeWord(process, worldFixture::PLAYER + 0x2C, 2102);
    writeWord(process, FIGHT + 0x98, 501);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, MOVABLE + 0x20, 0);
    writeWord(process, movementAnimationFixture::DATA, MOVABLE);
    writeWord(process, movementAnimationFixture::ACTIVE, 1);
    writeWord(process, CLOCK_MODE, 1);
    const auto events = readWord(process, ANIMATION_EVENTS);
    uint32_t tick = 10000;
    auto frame = [&](uint32_t state, uint32_t keys, bool run, bool command = false, bool animation = true) {
        setFightState(process, state, tick);
        writeWord(process, KEYS, keys); writeWord(process, CLOCK, tick);
        const float speed = keys == 0 ? 0.f : run ? 5.f : 3.f;
        writeFloat(process, movementAnimationFixture::VELOCITY, speed);
        if (command) {
            writeWord(process, movementCommandFixture::COMMAND + 0x30, 0xDEADBEEF);
            writeFloat(process, movementCommandFixture::COMMAND + 0x0C, 3);
            writeFloat(process, movementCommandFixture::COMMAND + 0x28,
                static_cast<float>(((keys & 8) != 0) - ((keys & 4) != 0)));
            writeFloat(process, movementCommandFixture::COMMAND + 0x2C, -1);
            writeWord(process, movementCommandFixture::REQUEST, 1);
        }
        worldFixture::requestFrame(process);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, worldFixture::FRAME_GATE) != 1) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("light shield frame timeout");
            Sleep(1);
        }
        if (readFloat(process, worldFixture::CONTROLLER + 0x24) != speed
            || (readWord(process, MOVABLE + 0xCC) & 0xFF) != static_cast<uint32_t>(run))
            throw std::runtime_error("light shield changed run speed/eligibility: state=" + std::to_string(state));
        if (readWord(process, FIGHT + 0x38) != state || readWord(process, ANIMATION_EVENTS) != events)
            throw std::runtime_error("light shield locomotion replaced defense or dispatched an animation event");
        if (command && (readFloat(process, movementCommandFixture::COMMAND + 0x0C) != speed
            || readWord(process, movementCommandFixture::COMMAND + 0x30) != (keys == 5 ? 11u : keys == 9 ? 12u : 10u)))
            throw std::runtime_error("light shield direction command lost the established run");
        if (animation && keys != 0) {
            const float expected = run ? 4.2f : keys == 1 ? 2.8f : 2.f;
            if (std::abs(readFloat(process, MOVABLE + 0x5C) - expected) > .00001f)
                throw std::runtime_error("light shield locomotion selected the wrong animation gait");
        }
    };
    auto timer = [&](uint32_t began, uint32_t tracking) {
        if (readWord(process, mailbox + t7r::SPRINT_DATA_OFFSET + 12) != began
            || readWord(process, mailbox + t7r::SPRINT_DATA_OFFSET + 16) != tracking)
            throw std::runtime_error("light shield defense reset, paused or shortened run warmup");
    };
    for (bool defending : {false, true}) for (uint32_t keys : {1u, 5u, 9u}) {
        tick += 2000; frame(2, 0, false);
        const auto began = tick;
        frame(defending ? 95 : 2, keys, false); timer(began, 1);
        tick = began + 250; frame(95, keys, false); timer(began, 1);
        tick = began + 400; frame(309, keys, false); timer(began, 1);
        tick = began + 650; frame(2, keys, false); timer(began, 1);
        tick = began + 800; frame(95, keys, false); timer(began, 1);
        tick = began + 999; frame(309, keys, false); timer(began, 1);
        tick = began + 1000; frame(309, keys, true); timer(began, 2);
        for (uint32_t state : {2u, 95u, 309u, 2u}) {
            for (uint32_t direction : {1u, 5u, 9u, 1u}) {
                ++tick; frame(state, direction, true, true); timer(began, 2);
            }
        }
    }
    // Other hero categories and missing native resources must keep ordinary shield restrictions.
    for (uint32_t category : {0u, 2u, 3u, 5u, 16u}) {
        writeWord(process, HERO_ROW + 0x1C05, category);
        frame(95, 1, false); tick += 1500; frame(309, 1, false);
        const auto began = tick;
        frame(2, 1, false); timer(began, 1);
        tick += 999; frame(2, 1, false); ++tick; frame(2, 1, true);
    }
    writeWord(process, HERO_ROW + 0x1C05, 1);
    writeWord(process, HERO_TABLE_GLOBAL, 0); frame(309, 1, false);
    writeWord(process, HERO_TABLE_GLOBAL, HERO_TABLE);
    writeWord(process, HERO_ROW, 999); tick += 1500; frame(309, 1, false);
    writeWord(process, HERO_ROW, 2102);
    for (uint32_t state : {305u, 223u, 235u, 237u, 238u, 239u, 35u, 41u, 111u}) {
        tick += 2000; frame(2, 0, false); frame(2, 1, false); tick += 1000; frame(2, 1, true);
        frame(state, 1, false, false, state == 305 || state == 223);
        tick += 1500; frame(state, 1, false, false, state == 305 || state == 223);
        const auto began = tick;
        frame(309, 1, false); timer(began, 1);
    }
    // Only defense transitions are exempt: stop, backward/sideways movement and crouch still interrupt running.
    for (uint32_t keys : {0u, 2u, 4u, 17u}) {
        tick += 1000; frame(309, 1, true);
        frame(309, keys, false, false, false);
        const auto began = tick;
        frame(309, 1, false); timer(began, 1);
        tick += 999; frame(309, 1, false); ++tick; frame(309, 1, true);
    }
    writeWord(process, HERO_TABLE_GLOBAL, savedTable);
    frame(2, 0, false);
    writeWord(process, CLOCK_MODE, 0); writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
