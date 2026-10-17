#pragma once
#include "MovementAnimationFixture.h"

namespace movementPoseFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t DATA = BASE + 0xFA00, MODIFIER = BASE + 0xFB00, MODEL = BASE + 0xFC00;
constexpr uint32_t SCHEDULE = BASE + 0xFD00, NAME = BASE + 0xFD40, DISPATCH = BASE + 0xFE00;
constexpr uint32_t WRITER_RVA = 0x29FB47;
namespace defenseExit {
constexpr uint32_t DATA = BASE + 0xD0000, CONTEXT = DATA + 0x100, CHARACTER = DATA + 0x200;
constexpr uint32_t ROOT = DATA + 0x300, GRAPH = DATA + 0x500, ACTION = DATA + 0x700;
constexpr uint32_t ACTION_NAME = DATA + 0x900, GRAPH_NAME = DATA + 0x940, OTHER_NAME = DATA + 0x980;
constexpr uint32_t LAYER = DATA + 0xA00, DISPATCH = DATA + 0x1000, NATIVE = DATA + 0x1400;
constexpr uint32_t EVENT_RVA = 0xCE0D50, VARIABLE_RVA = 0x29ED60, LAYER_RVA = 0xCDD9B0;
constexpr uint32_t POSE = DATA + 32, WRITES = DATA + 36, HANDLER = DATA + 96;
inline void prepare(HANDLE process) {
    auto text = [&](uint32_t address, const std::string& value) {
        t7r::Bytes bytes(value.begin(), value.end()); bytes.push_back(0);
        t7r::writeClientMemory(process, address, bytes);
    };
    text(ACTION_NAME, "Action"); text(GRAPH_NAME, "2H_Flurry_Behavior.hkb"); text(OTHER_NAME, "Other");
    for (const auto& field : std::vector<std::pair<uint32_t, uint32_t>>{
        {DATA+8,ACTION}, {DATA+12,CONTEXT}, {CONTEXT,CHARACTER}, {CONTEXT+8,GRAPH},
        {CHARACTER+0x44,ROOT}, {ROOT,BASE+0x18DEEB4}, {GRAPH,BASE+0x18DEEB4},
        {GRAPH+0x20,GRAPH_NAME|1}, {ACTION,BASE+0x18E10DC}, {ACTION+0x20,ACTION_NAME|1},
        {ROOT+0xA0,DATA+0xB00}, {ROOT+0xA8,DATA+0xC00}, {HANDLER,BASE+EVENT_RVA},
        {BASE+0x2D37938,91}
    }) writeWord(process, field.first, field.second);
    movementAnimationFixture::writeFloat(process, LAYER + 0x30, .4f);
    // Native graph dispatch broadcasts Idle separately to Layer and Action.
    t7r::WorldHookCode c;
    c.emit({0x83,0x7C,0x24,8,3}); const auto notIdle = c.branch(0x85);
    c.emit({0xFF,0x05}); c.word(DATA + 28); c.store(LAYER + 0xC, 2);
    c.bind(notIdle); c.emit({0xC2,0x10,0});
    t7r::writeClientMemory(process, BASE + LAYER_RVA, c.bytes);
    c.bytes = {0xFF,0x74,0x24,8,0xFF,0x74,0x24,8};
    c.call(NATIVE); c.emit({0xC2,0x10,0});
    t7r::writeClientMemory(process, BASE + EVENT_RVA, c.bytes);
    c.bytes.clear(); c.emit({0xFF,0x05}); c.word(DATA + 24);
    c.emit({0x83,0x7C,0x24,8,3}); const auto keepState = c.branch(0x85);
    c.emit({0xC7,0x81}); c.word(0x84); c.word(0);
    c.bind(keepState); c.emit({0xC2,8,0});
    t7r::writeClientMemory(process, NATIVE, c.bytes);
    c.bytes = {0x55,0x8B,0xEC,0x57,0x8B,0x7D,8};
    c.emit({0x89,0x3D}); c.word(DATA + 40);
    c.emit({0x8B,0x45,12,0xA3}); c.word(DATA + 44);
    c.emit({0x8B,0x45,16,0xA3}); c.word(POSE);
    c.emit({0xFF,0x05}); c.word(WRITES);
    c.emit({0xD9,0xEE,0xDD,0xD9,0x0F,0x57,0xC0,0x33,0xC9,0x33,0xD2,0x5F,0x5D,0xC2,12,0});
    t7r::writeClientMemory(process, BASE + VARIABLE_RVA, c.bytes);
    c.bytes.clear(); c.emit({0x83,0x3D}); c.word(DATA); c.emit({0}); const auto disabled = c.branch(0x84);
    c.emit({0x9C,0x60}); c.store(DATA, 0);
    auto arguments = [&] {
        c.emit({0x6A,0,0x6A,0,0xFF,0x35}); c.word(DATA + 4);
        c.emit({0xFF,0x35}); c.word(DATA + 12);
    };
    arguments(); c.call(BASE + LAYER_RVA);
    c.emit({0x89,0x25}); c.word(DATA + 16);
    c.emit({0xD9,0xE8,0xB8}); c.word(0x3F800000); c.emit({0x66,0x0F,0x6E,0xC0});
    for (const auto& reg : std::vector<std::pair<uint32_t, uint32_t>>{
        {0xB8,0x66778899}, {0xBB,0x11223344}, {0xBA,0x22334455},
        {0xBE,0x33445566}, {0xBF,0x44556677}, {0xBD,0x55667788}
    }) { c.emit({static_cast<unsigned char>(reg.first)}); c.word(reg.second); }
    c.emit({0x8B,0x0D}); c.word(DATA + 8); c.emit({0xF9,0x9C,0x8F,0x05}); c.word(DATA + 48);
    arguments(); c.emit({0xFF,0x15}); c.word(HANDLER);
    c.emit({0x9C,0x8F,0x05}); c.word(DATA + 52);
    c.emit({0x89,0x25}); c.word(DATA + 20);
    for (const auto& reg : std::vector<std::pair<uint32_t, uint32_t>>{
        {0x05,56}, {0x1D,60}, {0x0D,64}, {0x15,68}, {0x35,72}, {0x3D,76}, {0x2D,80}
    }) { c.emit({0x89,static_cast<unsigned char>(reg.first)}); c.word(DATA + reg.second); }
    c.emit({0xD9,0x1D}); c.word(DATA + 88);
    c.emit({0x66,0x0F,0x7E,0x05}); c.word(DATA + 92);
    c.emit({0x61,0x9D}); c.bind(disabled); c.emit({0xC3});
    t7r::writeClientMemory(process, DISPATCH, c.bytes);
}
inline void verify(HANDLE process) {
    using namespace sprintFixture;
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    writeWord(process, CLOCK_MODE, 1); writeWord(process, CLOCK, 30000); writeWord(process, KEYS, 1);
    writeWord(process, FIGHT + 0x98, static_cast<uint32_t>(movementAnimationFixture::profile().weaponId));
    const auto previousModel = readWord(process, ANIMATION + 0x30);
    writeWord(process, ANIMATION + 0x30, MODEL); writeWord(process, MODEL + 0x10, CHARACTER);
    writeWord(process, worldFixture::MOVABLE + 0x60, 1);
    const auto events = readWord(process, ANIMATION_EVENTS);
    auto sample = [&](bool filtered, uint32_t event = 3, uint32_t state = 2, uint32_t action = 22, bool worker = false) {
        setFightState(process, state, 30000); writeWord(process, ACTION + 0x84, action);
        for (auto offset : {24u,28u,36u,40u,44u}) writeWord(process, DATA + offset, 0);
        writeWord(process, POSE, 2); writeWord(process, LAYER + 0xC, 0);
        writeWord(process, DATA + 4, event); writeWord(process, DATA, 1);
        if (worker) {
            movementAnimationFixture::runWorker(process, DISPATCH);
        } else {
            worldFixture::requestFrame(process);
        }
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, worldFixture::FRAME_GATE) != 1) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("defense exit event sample timeout");
            Sleep(1);
        }
        if (readWord(process, DATA) != 0 || readWord(process, DATA + 24) != (filtered ? 0u : 1u)
            || readWord(process, ACTION + 0x84) != (!filtered && event == 3 ? 0u : action))
            throw std::runtime_error("defense fade-out still activates the intermediate Idle pose");
        if (readWord(process, DATA + 28) != (event == 3 ? 1u : 0u)
            || readWord(process, LAYER + 0xC) != (event == 3 ? 2u : 0u)
            || movementAnimationFixture::readFloat(process, LAYER + 0x30) != .4f)
            throw std::runtime_error("defense exit consumed the layer event or changed its native fade");
        if (readWord(process, POSE) != (filtered ? 0u : 2u) || readWord(process, WRITES) != (filtered ? 1u : 0u)
            || (filtered && (readWord(process, DATA + 40) != ROOT || readWord(process, DATA + 44) != 91)))
            throw std::runtime_error("defense exit did not reset locomotion through the native writer in the same event");
        if (readWord(process, DATA + 16) != readWord(process, DATA + 20))
            throw std::runtime_error("defense exit event changed ESP");
        for (const auto& reg : std::vector<std::pair<uint32_t, uint32_t>>{
            {60,0x11223344}, {72,0x33445566}, {76,0x44556677}, {80,0x55667788}, {88,0x3F800000}, {92,0x3F800000}
        }) if (readWord(process, DATA + reg.first) != reg.second)
            throw std::runtime_error("defense exit event changed preserved registers/FPU/SSE");
        if (filtered && (readWord(process, DATA + 56) != 0x66778899 || readWord(process, DATA + 64) != ACTION
            || readWord(process, DATA + 68) != 0x22334455 || readWord(process, DATA + 48) != readWord(process, DATA + 52)))
            throw std::runtime_error("filtered defense exit changed native register inputs or flags");
    };
    sample(true);
    sample(true, 3, 2, 22, true);
    for (auto state : {305u,308u,307u,306u,223u,224u,225u,226u}) {
        sample(false, 23, state); sample(true);
    }
    for (uint32_t direction = 1; direction <= 8; ++direction) {
        writeWord(process, worldFixture::MOVABLE + 0x60, direction); sample(true);
    }
    writeWord(process, worldFixture::MOVABLE + 0x60, 0); sample(false);
    writeWord(process, worldFixture::MOVABLE + 0x60, 1); sample(true);
    for (uint32_t flags : {1u,0x10000u,0x10001u}) {
        writeWord(process, worldFixture::MOVABLE + 0x20, flags);
        sample(flags != 0x10001); sample(flags != 0x10001, 3, 2, 22, true);
    }
    writeWord(process, worldFixture::MOVABLE + 0x20, 0);
    sample(false, 27, 235); sample(false, 23, 305); sample(false, 3, 2, 23); sample(true);
    for (const auto& field : std::vector<std::pair<uint32_t, uint32_t>>{
        {KEYS,17}, {FIGHT+0x98,100000},
        {worldFixture::STATE+0x38,3}, {worldFixture::PLAYER+0x34,0},
        {CONTEXT,CHARACTER+0x1000}, {GRAPH+0x20,OTHER_NAME}, {ACTION+0x20,OTHER_NAME},
        {DATA+12,0}, {CONTEXT+8,0}, {MODEL+0x10,0}, {CHARACTER+0x44,0},
        {ROOT+0xA0,0}, {ROOT+0xA8,0}, {BASE+0x2D37938,UINT32_MAX},
        {sprintFixture::INPUT+0x60,5}, {sprintFixture::INPUT+0x54,0}
    }) {
        const auto previous = readWord(process, field.first);
        writeWord(process, field.first, field.second); sample(false); sample(false, 3, 2, 22, true);
        writeWord(process, field.first, previous); sample(true); sample(true, 3, 2, 22, true);
    }
    // Resolve the current model each time; a replaced character must not reuse the old identity.
    writeWord(process, MODEL + 0x10, CHARACTER + 0x1000); sample(false);
    writeWord(process, CHARACTER + 0x1044, ROOT); writeWord(process, CONTEXT, CHARACTER + 0x1000);
    sample(true); sample(true, 3, 2, 22, true);
    writeWord(process, MODEL + 0x10, CHARACTER); writeWord(process, CONTEXT, CHARACTER);
    // Only the physical-jump case emits the native Jump/EndJump pair.
    if (movementAnimationFixture::readFloat(process, worldFixture::CONTROLLER + 0x24) != 3
        || readWord(process, ANIMATION_EVENTS) != events + 2)
        throw std::runtime_error("defense exit event changed physical speed or emitted animation events");
    writeWord(process, ANIMATION + 0x30, previousModel);
    writeWord(process, KEYS, 0); writeWord(process, CLOCK_MODE, 0); writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
inline void prepare(HANDLE process) {
    defenseExit::prepare(process);
    t7r::WorldHookCode c;
    c.emit({0x83,0x3D}); c.word(DATA); c.emit({0}); const auto disabled = c.branch(0x84);
    c.emit({0x9C,0x60,0xBE}); c.word(MODIFIER); c.emit({0x8B,0x3D}); c.word(DATA + 4);
    c.emit({0xB8}); c.word(SCHEDULE); c.emit({0xBA}); c.word(BASE + WRITER_RVA); c.emit({0xFF,0xD2});
    c.emit({0xA3}); c.word(DATA + 12); c.emit({0x89,0x0D}); c.word(DATA + 16);
    c.emit({0x61,0x9D}); c.bind(disabled); c.emit({0xC3});
    t7r::writeClientMemory(process, DISPATCH, c.bytes);
    // Native writer inputs: ESI modifier, EDI model, EAX {time, AttackMoveDir}.
    c.bytes = {0xFF,0x70,4,0x8B,0xCE};
    c.emit({0x8B,0x14,0x24,0x89,0x15}); c.word(DATA + 8);
    c.emit({0x83,0xC4,4,0xC3});
    t7r::writeClientMemory(process, BASE + WRITER_RVA, c.bytes);
    writeWord(process, SCHEDULE + 4, 2); writeWord(process, DATA + 4, MODEL);
}
inline void verify(HANDLE process) {
    using namespace sprintFixture;
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    writeWord(process, CLOCK_MODE, 1); writeWord(process, CLOCK, 20000);
    writeWord(process, DATA, 1); writeWord(process, KEYS, 1);
    writeWord(process, FIGHT + 0x98, static_cast<uint32_t>(movementAnimationFixture::profile().weaponId));
    const auto previousModel = readWord(process, ANIMATION + 0x30);
    writeWord(process, ANIMATION + 0x30, MODEL);
    writeWord(process, worldFixture::MOVABLE + 0x60, 1);
    const auto events = readWord(process, ANIMATION_EVENTS);
    auto sample = [&](uint32_t state, const std::string& name, uint32_t expected, bool worker = false) {
        setFightState(process, state, 20000);
        writeWord(process, MODIFIER + 0x18, static_cast<uint32_t>(name.size()));
        writeWord(process, MODIFIER + 0x1C, name.size() > 15 ? 31 : 15);
        writeWord(process, MODIFIER + 8, NAME);
        t7r::Bytes text(name.begin(), name.end()); text.push_back(0);
        t7r::writeClientMemory(process, name.size() > 15 ? NAME : MODIFIER + 8, text);
        writeWord(process, DATA + 8, 99);
        if (worker) movementAnimationFixture::runWorker(process, DISPATCH);
        else worldFixture::requestFrame(process);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, worldFixture::FRAME_GATE) != 1) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("movement pose sample timeout");
            Sleep(1);
        }
        if (readWord(process, DATA + 8) != expected)
            throw std::runtime_error("defense exit retained combat locomotion: state=" + std::to_string(state)
                + " node=" + name + " expected=" + std::to_string(expected)
                + " actual=" + std::to_string(readWord(process, DATA + 8)));
        if (readWord(process, DATA + 12) != SCHEDULE || readWord(process, DATA + 16) != MODIFIER)
            throw std::runtime_error("movement pose writer changed native register inputs");
    };
    for (const auto& direction : {"Up", "Right", "Down", "Left"}) {
        const auto name = std::string("Parry") + direction;
        const uint32_t combatPose = name == "ParryLeft" ? 1 : 2;
        writeWord(process, SCHEDULE + 4, combatPose);
        sample(308, name + "Prepare", combatPose); sample(224, name + "Hold", combatPose);
        sample(2, name + "Hold", 0); sample(2, name + "Prepare", 0);
        sample(224, name + "Hold", combatPose, true); sample(2, name + "Hold", 0, true);
        sample(237, name + "Hold", combatPose); // A new attack keeps native combat selection.
    }
    writeWord(process, SCHEDULE + 4, 2);
    sample(2, "AttackRightHold", 2); sample(2, "Idle", 2);
    writeWord(process, DATA + 4, MODEL + 0x100); sample(2, "ParryRightHold", 2);
    sample(2, "ParryRightHold", 2, true);
    writeWord(process, DATA + 4, MODEL);
    writeWord(process, FIGHT + 0x98, 100000); sample(2, "ParryRightHold", 2);
    writeWord(process, FIGHT + 0x98, static_cast<uint32_t>(movementAnimationFixture::profile().weaponId));
    writeWord(process, worldFixture::MOVABLE + 0x60, 0); sample(2, "ParryRightHold", 2);
    for (uint32_t direction = 1; direction <= 8; ++direction) {
        writeWord(process, worldFixture::MOVABLE + 0x60, direction); sample(2, "ParryRightHold", 0);
    }
    writeWord(process, worldFixture::MOVABLE + 0x60, 1);
    writeWord(process, KEYS, 17); sample(2, "ParryRightHold", 2); writeWord(process, KEYS, 1);
    for (uint32_t flags : {1u,0x10000u,0x10001u}) {
        writeWord(process, worldFixture::MOVABLE + 0x20, flags);
        const uint32_t expected = flags == 0x10001 ? 2 : 0;
        sample(2, "ParryRightHold", expected); sample(2, "ParryRightHold", expected, true);
    }
    writeWord(process, worldFixture::MOVABLE + 0x20, 0); sample(2, "ParryRightHold", 0);
    // Only the physical-jump case emits the native Jump/EndJump pair.
    if (movementAnimationFixture::readFloat(process, worldFixture::CONTROLLER + 0x24) != 3
        || readWord(process, ANIMATION_EVENTS) != events + 2)
        throw std::runtime_error("movement pose selection changed physical speed or sent an animation event");
    writeWord(process, ANIMATION + 0x30, previousModel);
    writeWord(process, DATA, 0); writeWord(process, KEYS, 0); writeWord(process, CLOCK_MODE, 0);
    writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
