#pragma once
#include "MovementAnimationFixture.h"

namespace movementCommandFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t DATA = BASE + 0xC0000, DISPATCH = DATA + 0x100;
constexpr uint32_t COMMAND = DATA + 0x40, REQUEST = DATA, TARGET = DATA + 4;
constexpr uint32_t APPLY_RVA = 0x3CFE30, HOOK_RVA = 0x3CFE38;
inline void prepare(HANDLE process) {
    t7r::WorldHookCode c;
    c.emit({0x9C,0x60,0x83,0x3D}); c.word(REQUEST); c.emit({0}); const auto idle = c.branch(0x84);
    c.emit({0xFF,0x35}); c.word(TARGET); c.emit({0xB9}); c.word(COMMAND); c.call(BASE + APPLY_RVA);
    c.store(REQUEST, 0); c.bind(idle); c.emit({0x61,0x9D,0xC3});
    t7r::writeClientMemory(process, DISPATCH, c.bytes);
    // Native MoveCommand applies state before speed and before the animation update.
    c.bytes = {0x55,0x8B,0xEC,0x53,0x8B,0x5D,8,0x57,0x8B,3,0x8B,0xF9,0x8B,0xCB};
    c.emit({0x8B,0x47,0x30,0x89,0x43,0x2C,0x83,0xE8,10,0x83,0xF8,2,0x0F,0x96,0x83}); c.word(0xCC);
    c.emit({0x8B,0x47,0x0C,0x8B,0x4B,0x38,0x89,0x41,0x24,0xA3}); c.word(movementAnimationFixture::VELOCITY);
    c.emit({0x5F,0x5B,0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + APPLY_RVA, c.bytes);
    writeWord(process, COMMAND, BASE + 0x17782D8);
    writeWord(process, TARGET, worldFixture::MOVABLE);
}
inline void verify(HANDLE process, t7r::ClientWorld& reader, uint32_t mailbox) {
    using namespace sprintFixture;
    using worldFixture::MOVABLE;
    using movementAnimationFixture::readFloat;
    using movementAnimationFixture::writeFloat;
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    writeWord(process, CLOCK_MODE, 1); writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, FIGHT + 0x98, static_cast<uint32_t>(movementAnimationFixture::profile().weaponId));
    writeWord(process, movementAnimationFixture::DATA, MOVABLE);
    setFightState(process, 2);
    const uint32_t states[]{1,2,6,1,4,3,5,4,8,9,7,8,1,2,6,1};
    auto frame = [&](uint32_t keys, uint32_t tracking, float commandSpeed, uint32_t state, float speed,
                     uint32_t initialState = 0xDEADBEEF, bool noncanonicalAxis = false) {
        writeWord(process, KEYS, keys); writeWord(process, mailbox + t7r::SPRINT_DATA_OFFSET + 16, tracking);
        writeWord(process, COMMAND + 0x30, initialState);
        writeFloat(process, COMMAND + 0x0C, commandSpeed);
        writeFloat(process, COMMAND + 0x28, static_cast<float>(((keys & 8) != 0) - ((keys & 4) != 0)));
        writeFloat(process, COMMAND + 0x2C, static_cast<float>(((keys & 2) != 0) - ((keys & 1) != 0)));
        if (noncanonicalAxis) writeFloat(process, COMMAND + 0x28, .5f);
        const auto entity = readWord(process, MOVABLE + 8);
        const auto ridIndex = (entity - worldFixture::ENTITY) / 0x100;
        const auto attemptAddress = mailbox + t7r::LIVE_ACTORS_OFFSET
            + ridIndex * static_cast<uint32_t>(sizeof(t7r::ActorMailbox))
            + static_cast<uint32_t>(offsetof(t7r::ActorMailbox, forwardAttemptTick));
        writeWord(process, attemptAddress, 0);
        writeWord(process, REQUEST, 1); worldFixture::requestFrame(process);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, worldFixture::FRAME_GATE) != 1) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("movement command frame timeout");
            Sleep(1);
        }
        if (readWord(process, MOVABLE + 0x2C) != state || readFloat(process, worldFixture::CONTROLLER + 0x24) != speed)
            throw std::runtime_error("queued movement command changed locomotion state/speed before animation: keys="
                + std::to_string(keys) + " entity=" + std::to_string(entity)
                + " state=" + std::to_string(readWord(process, MOVABLE + 0x2C))
                + " speed=" + std::to_string(readFloat(process, worldFixture::CONTROLLER + 0x24)));
        const auto attempt = readWord(process, attemptAddress);
        if ((attempt != 0) != ((keys & 4) != 0 && (keys & 8) == 0 && !noncanonicalAxis))
            throw std::runtime_error("native forward movement attempt did not match its RID and command axis: keys="
                + std::to_string(keys) + " rid=" + std::to_string(ridIndex + 1)
                + " tick=" + std::to_string(attempt));
    };
    // Every A/D edge is a new native command, not just a changed input cache.
    for (uint32_t fightState : {2u, 1u, 2u}) {
        setFightState(process, fightState);
        for (uint32_t keys : {1u,5u,1u,9u,5u,13u,9u,1u}) {
            const auto state = keys == 5 ? 11u : keys == 9 ? 12u : 10u;
            frame(keys, 2, 3, state, 5);
            if (std::abs(readFloat(process, MOVABLE + 0x5C) - movementAnimationFixture::profile().forwardSprint) > .00001f)
                throw std::runtime_error("running A/D command blended a walking animation");
        }
    }
    for (uint32_t keys = 0; keys < 16; ++keys)
        frame(keys, 0, keys == 0 ? 0.f : 3.f, states[keys], keys == 0 ? 0.f : 3.f);
    for (uint32_t state : {223u,305u,237u,35u}) {
        setFightState(process, state); frame(5, 2, 3, 3, 3);
    }
    setFightState(process, 2); frame(21, 2, 3, 3, 3); // Crouch cancels carried running.
    writeWord(process, MOVABLE + 0x20, 0x00010001); frame(9, 2, 3, 9, 5);
    if (readWord(process, MOVABLE + 0xCC) & 0xFF) throw std::runtime_error("airborne command restored dash eligibility");
    writeWord(process, MOVABLE + 0x20, 0); frame(9, 2, 3, 12, 5);
    writeWord(process, movementAnimationFixture::DATA, 0);
    // Other actors, state-directed commands and special speeds retain native semantics.
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY + 0x100);
    frame(5, 2, 3, 2, 3, 2); writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, worldFixture::ENTITY + 19 * 0x100 + 8, 119);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY + 19 * 0x100);
    frame(5, 2, 3, 2, 3, 2);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, worldFixture::ENTITY + 19 * 0x100 + 8, 0);
    writeWord(process, COMMAND + 0x34, 1);
    frame(5, 2, 3, 2, 3, 2); writeWord(process, COMMAND + 0x34, 0);
    frame(5, 2, 4, 2, 4, 2);
    frame(5, 2, 3, 2, 3, 2, true);
    writeWord(process, worldFixture::STATE + 0x38, 2);
    frame(5, 2, 3, 2, 3, 2); writeWord(process, worldFixture::STATE + 0x38, 4);
    frame(5, 2, 3, 11, 5, 2);
    t7r::WorldSnapshot observed;
    if (reader.poll(observed)) throw std::runtime_error("movement capture expected a fresh world request");
    worldFixture::requestFrame(process);
    const auto deadline = GetTickCount64() + 3000;
    while (readWord(process, worldFixture::FRAME_GATE) != 1) {
        if (GetTickCount64() >= deadline) throw std::runtime_error("movement capture freeze timeout");
        Sleep(1);
    }
    observed = worldFixture::read(reader);
    if (!observed.actors[0].forwardAttemptAt || observed.actors[0].forwardAttemptAt > observed.sampledAt)
        throw std::runtime_error("native forward attempt was not decoded with the frozen pose batch");
    writeWord(process, KEYS, 0); writeWord(process, CLOCK_MODE, 0); writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
