#pragma once
#include "MovementAnimationFixture.h"
#include "../../src/Runtime/launcher/CombatProfileHookCode.h"

namespace combatProfileFixture {
inline void verify(HANDLE process, uint32_t mailbox) {
    using namespace sprintFixture;
    using movementAnimationFixture::writeFloat;
    using movementAnimationFixture::readFloat;
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    auto dash = profile(); dash.weaponId = 501; dash.moveMs = 100; dash.speedMilli = 4200;
    auto otherDash = dash; otherDash.weaponId = 502; otherDash.moveMs = 500; otherDash.speedMilli = 2700;
    const std::vector<t7r::CombatProfile> profiles{
        {1101, {true, 501, 2.f, 1.5f, 1.f, 3.8f}, dash},
        {2102, {true, 501, 2.8f, 2.f, 1.2f, 4.2f}, dash},
        {2102, {true, 502, 2.4f, 1.8f, 1.f, 3.6f}, otherDash}
    };
    t7r::Bytes page(t7r::WORLD_PAGE_SIZE, 0);
    t7r::writeCombatProfiles(page, profiles);
    bool rejected = false;
    try { t7r::writeCombatProfiles(page, {profiles[0], profiles[0]}); }
    catch (const std::exception&) { rejected = true; }
    if (!rejected) throw std::runtime_error("duplicate combat profile key accepted");
    t7r::writeClientMemory(process, mailbox + t7r::COMBAT_PROFILES_OFFSET,
        t7r::Bytes(page.begin() + t7r::COMBAT_PROFILES_OFFSET,
                  page.begin() + t7r::COMBAT_PROFILES_OFFSET + 4 + profiles.size() * t7r::COMBAT_PROFILE_STRIDE));
    t7r::writeClientMemory(process, mailbox + t7r::COMBAT_PROFILE_CODE_OFFSET, t7r::buildCombatProfileLookup(mailbox, BASE));
    t7r::writeClientMemory(process, mailbox + t7r::SPRINT_CODE_OFFSET, t7r::buildSprintHook(mailbox, BASE, {}, true));
    t7r::writeClientMemory(process, mailbox + t7r::MOVEMENT_ANIMATION_CODE_OFFSET,
        t7r::buildMovementAnimationHook(mailbox, BASE, 0, {}, true));
    writeWord(process, worldFixture::STATE + 0x38, 4);
    writeWord(process, sprintFixture::INPUT + 0x60, 6);
    writeWord(process, worldFixture::PLAYER + 0x34, 1);
    writeWord(process, worldFixture::MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    writeWord(process, CLOCK_MODE, 1);
    writeWord(process, movementAnimationFixture::DATA, worldFixture::MOVABLE);
    writeWord(process, movementAnimationFixture::ACTIVE, 1);
    writeFloat(process, movementAnimationFixture::VELOCITY, 3.f);
    auto frame = [&](uint32_t tick, float physical, float animation) {
        writeWord(process, CLOCK, tick);
        worldFixture::requestFrame(process);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, worldFixture::FRAME_GATE) != 1) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("combat profile frame timeout");
            Sleep(1);
        }
        const auto actual = readFloat(process, worldFixture::MOVABLE + 0x5C);
        const auto speed = readFloat(process, worldFixture::CONTROLLER + 0x24);
        if (std::abs(actual-animation) > .00001f || std::abs(speed-physical) > .00001f)
            throw std::runtime_error("selected combat profile mismatch: animation=" + std::to_string(actual)
                + " speed=" + std::to_string(speed));
    };
    writeWord(process, KEYS, 1);
    writeWord(process, FIGHT + 0x98, 501);
    writeWord(process, worldFixture::PLAYER + 0x2C, 1101);
    setFightState(process, 305, 1000); frame(1000, 3, 2);
    writeWord(process, worldFixture::PLAYER + 0x2C, 2102);
    frame(1001, 3, 2.8f); // Same weapon, different native hero identity.
    writeWord(process, KEYS, 4); frame(1002, 3, 2);
    writeWord(process, KEYS, 2); frame(1003, 3, 1.2f);
    writeWord(process, KEYS, 1); setFightState(process, 309, 1004); frame(1004, 3, 2.8f);
    writeWord(process, FIGHT + 0x98, 502); frame(1005, 3, 2.4f);
    writeWord(process, FIGHT + 0x98, 999); frame(1006, 3, 3); // Never borrow the preceding weapon's coordinates.
    writeWord(process, FIGHT + 0x98, 501); writeWord(process, worldFixture::PLAYER + 0x2C, 999);
    frame(1007, 3, 3);
    writeWord(process, worldFixture::PLAYER + 0x2C, 2102);
    setFightState(process, 439, 2000); frame(2000, 4.2f, 3);
    frame(2099, 4.2f, 3); frame(2100, 0, 3);
    writeWord(process, FIGHT + 0x98, 502);
    setFightState(process, 439, 3000); frame(3200, 2.7f, 3); frame(3500, 0, 3);
    writeWord(process, FIGHT + 0x98, 999);
    setFightState(process, 439, 4000); frame(4000, 0, 3);
    // The new weapon selects animation coordinates without restarting locomotion.
    writeWord(process, FIGHT + 0x98, 501);
    setFightState(process, 2, 5000); frame(5000, 3, 2.8f);
    writeWord(process, FIGHT + 0x98, 502);
    setFightState(process, 1, 5400); frame(5400, 3, 2.4f);
    frame(5999, 3, 2.4f); frame(6000, 5, 2.4f);
    writeFloat(process, movementAnimationFixture::VELOCITY, 5.f);
    frame(6001, 5, 3.6f);
    setFightState(process, 2, 6200); frame(6200, 5, 3.6f);
    writeWord(process, FIGHT + 0x98, 501);
    setFightState(process, 1, 6300); frame(6300, 5, 4.2f);
    setFightState(process, 2, 7300); frame(7300, 5, 4.2f);
    if (readWord(process, mailbox + t7r::SPRINT_DATA_OFFSET + 12) != 5000)
        throw std::runtime_error("weapon profile change restarted the run timer");
    writeWord(process, CLOCK_MODE, 0); writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
