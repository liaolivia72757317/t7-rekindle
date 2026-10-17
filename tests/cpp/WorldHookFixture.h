#pragma once
#include "../../src/Runtime/launcher/ClientWorld.h"
#include "../../src/Runtime/launcher/RemoteImage.h"
#include "../../src/Runtime/launcher/WorldHookCode.h"
#include <cmath>
#include <cstring>

namespace worldFixture {
constexpr uint32_t BASE = 0x400000, STATE = BASE + 0x9000, INSTANCE = BASE + 0x9100;
constexpr uint32_t RIDS = BASE + 0x9200, ENGINE = BASE + 0x9600, ENGINE_TABLE = BASE + 0x9700;
constexpr uint32_t ENTITY = BASE + 0x10000, MOVABLE = BASE + 0x14000, CONTROLLER = BASE + 0x18000;
constexpr uint32_t PLAYER = BASE + 0x1C000;
constexpr uint32_t TABLE = BASE + 0xAA00, ANCHOR = BASE + 0xAB00, RESOLVE = BASE + 0xAC00;
constexpr uint32_t FRAME_GATE = BASE + 0xAD00;
inline void word(t7r::Bytes& bytes, uint32_t v) {
    for (unsigned s = 0; s < 32; s += 8) bytes.push_back(static_cast<unsigned char>(v >> s));
}
inline void writeWord(HANDLE process, uint32_t address, uint32_t value) {
    t7r::Bytes bytes; word(bytes, value); t7r::writeClientMemory(process, address, bytes);
}
inline void requestFrame(HANDLE process) {
    // The client changes 2 to 1 when the frame completes. A patch-style
    // readback can race that acknowledgement; callers wait for 1 instead.
    const uint32_t command = 2;
    SIZE_T written = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(FRAME_GATE)),
            &command, sizeof(command), &written) || written != sizeof(command))
        throw std::runtime_error(t7r::errorText("fixture frame request"));
}
inline void prepare(HANDLE process) {
    writeWord(process, BASE + 0x2E405F8, STATE); writeWord(process, STATE + 0x38, 4);
    writeWord(process, BASE + 0x2E37804, INSTANCE); writeWord(process, INSTANCE + 0x68, 1234);
    writeWord(process, INSTANCE + 0x6C, 1); // Ensure the 64-bit epoch is not truncated.
    writeWord(process, BASE + 0x2E3FA8C, RIDS); writeWord(process, RIDS + 0x274, RIDS);
    writeWord(process, BASE + 0x2D27358, ENGINE); writeWord(process, ENGINE, ENGINE_TABLE);
    writeWord(process, ENGINE_TABLE + 0x28, RESOLVE);
    t7r::writeClientMemory(process, BASE + 0x647770, {0x55,0x8B,0xEC,0xFF,0x75,0x08,0x58,0x5D,0xC3});
    t7r::Bytes resolver{0x8B,0x44,0x24,4,0x48,0xC1,0xE0,8,0x05}; word(resolver, ENTITY);
    resolver.insert(resolver.end(), {0xC2,4,0}); t7r::writeClientMemory(process, RESOLVE, resolver);
    t7r::WorldHookCode component;
    component.emit({0x55,0x8B,0xEC,0x8B,0x55,0x08,0x56,0x8B,0x71,0x7C,0x81,0xFA});
    component.word(BASE + 0x179ED94); const auto movable = component.branch(0x85);
    component.emit({0x8B,0x81}); component.word(0x84); const auto done = component.jump();
    component.bind(movable); component.emit({0x8B,0x81}); component.word(0x80);
    component.bind(done); component.emit({0x5E,0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x26A1C0, component.bytes);
    t7r::writeClientMemory(process, BASE + 0x64C9D0, {0x83,0x79,0x34,1,0x0F,0x95,0xC0,0xC3});
    t7r::Bytes getter{0x55,0x8B,0xEC,0x8B,0x49,0x38,0x83,0xEC,0x0C,0x8B,0x45,8};
    for (unsigned char offset : std::array<unsigned char, 3>{0, 4, 8})
        getter.insert(getter.end(), {0x8B,0x51,static_cast<unsigned char>(0x10 + offset),0x89,0x50,offset});
    // Clobber x87/XMM contents without leaking one x87 stack slot per getter call.
    getter.insert(getter.end(), {0xD9,0xEE,0xDD,0xD9,0x0F,0x57,0xC0,0x8B,0xE5,0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x3D1200, getter);
    t7r::writeClientMemory(process, BASE + 0x2956F0, {0x53,0x8B,0xDC,0x83,0xEC,8,0x83,0xE4,0xF0});
    t7r::writeClientMemory(process, BASE + 0x3D11F0, {0x8B,0x49,0x38,0x8B,0x01,0x8B,0x40,0x74,0xFF,0xE0});
    t7r::writeClientMemory(process, BASE + 0x3D11B0, {0xD9,0x81,0xAC,0,0,0,0xC3});
    writeWord(process, TABLE + 0x34, BASE + 0x2956F0); writeWord(process, ANCHOR + 0x14, ANCHOR);
    writeWord(process, TABLE + 0x74, BASE + 0x3D11B0);
    for (uint32_t i = 0; i < t7r::MAX_WORLD_ACTORS; ++i) {
        writeWord(process, ENTITY + i * 0x100 + 0x7C, ENTITY);
        writeWord(process, ENTITY + i * 0x100 + 0x80, MOVABLE + i * 0x100);
        writeWord(process, ENTITY + i * 0x100 + 0x84, PLAYER + i * 0x100);
        writeWord(process, PLAYER + i * 0x100, BASE + 0x179ECE4);
        writeWord(process, PLAYER + i * 0x100 + 0x34, 1);
        writeWord(process, MOVABLE + i * 0x100, BASE + 0x17784E4);
        writeWord(process, MOVABLE + i * 0x100 + 0x38, CONTROLLER + i * 0x100);
        writeWord(process, CONTROLLER + i * 0x100, TABLE);
        writeWord(process, CONTROLLER + i * 0x100 + 0xA8, ANCHOR);
        const std::array<float, 3> position{270.f + i, 150.f + i, .25f};
        t7r::Bytes bytes(sizeof(position)); std::memcpy(bytes.data(), position.data(), bytes.size());
        t7r::writeClientMemory(process, CONTROLLER + i * 0x100 + 0x10, bytes);
        const float yaw = i == 0 ? -.5f : 2.5f;
        bytes.resize(sizeof(yaw)); std::memcpy(bytes.data(), &yaw, bytes.size());
        t7r::writeClientMemory(process, CONTROLLER + i * 0x100 + 0xAC, bytes);
    }
}
inline std::array<uint8_t, t7r::MAX_WORLD_ACTORS> camps() {
    std::array<uint8_t, t7r::MAX_WORLD_ACTORS> result{};
    result.fill(1);
    for (size_t i = 1; i <= 10; ++i) result[i] = 2;
    return result;
}
inline t7r::WorldSnapshot read(t7r::ClientWorld& reader,
        const std::array<uint8_t, t7r::MAX_WORLD_ACTORS>& actorCamps = camps()) {
    t7r::WorldSnapshot value;
    const auto deadline = GetTickCount64() + 3000;
    while (!reader.poll(value, actorCamps)) {
        if (GetTickCount64() >= deadline) throw std::runtime_error("world fixture response timeout");
        Sleep(5);
    }
    return value;
}
inline void verify(HANDLE process, t7r::ClientWorld& reader, uint32_t mailbox) {
    auto value = read(reader);
    if (t7r::readClientMemory(process, mailbox, 4) != t7r::Bytes(4, 0))
        throw std::runtime_error("world reader requested another frame before consumer acknowledgement");
    if (!value.ready || value.instanceStartedAt != 0x1000004D2ULL || !value.sampledAt
        || value.player != std::array<float, 3>{270, 150, .25f}
        || value.bot != std::array<float, 3>{271, 151, .25f}
        || std::abs(value.playerHeading + 28.64789f) > .0001f
        || std::abs(value.botHeading - 143.23945f) > .0001f)
        throw std::runtime_error("world entity/epoch/position snapshot mismatch");
    if (!value.actors[19].ready || value.actors[19].position != std::array<float, 3>{289,169,.25f})
        throw std::runtime_error("world dropped last actor: ready=" + std::to_string(value.actors[19].ready)
            + " heading=" + std::to_string(value.actors[19].heading) + " position=" + std::to_string(value.actors[19].position[0]));
    if (!value.actors[59].ready || value.actors[59].position != std::array<float, 3>{329,209,.25f})
        throw std::runtime_error("world dropped native actor 60");
    // A dead/choosing local player must not suspend the rest of the battle.
    writeWord(process, PLAYER + 0x34, 0);
    for (uint32_t state : {3u, 1u}) {
        writeWord(process, STATE + 0x38, state); read(reader);
        value = read(reader);
        if (!value.ready || value.actors[0].ready || !value.actors[1].ready || !value.actors[19].ready)
            throw std::runtime_error("spectating or hero selection suspended living actors");
    }
    writeWord(process, PLAYER + 0x34, 1);
    writeWord(process, STATE + 0x38, 4); read(reader); read(reader);
    value = read(reader); // Requests selected from the preceding complete frame.
    if (value.actors[0].targetId != 2 || value.actors[1].targetId != 1
        || value.actors[11].targetId != 11 || value.actors[19].targetId != 0
        || !value.actors[19].terrain[0].ready || value.actors[19].terrain[1].ready)
        throw std::runtime_error("world terrain target identity/camp/range filtering mismatch");
    // Resolve changed fixture state through complete game-frame queries.
    for (const auto& entry : std::array<std::pair<uint32_t, uint32_t>, 9>{{
        {STATE + 0x38, 4}, {BASE + 0x2E3FA8C, RIDS}, {ENTITY + 0x180, MOVABLE + 0x100},
        {ENTITY + 0x184, PLAYER + 0x100}, {PLAYER + 0x100, BASE + 0x179ECE4}, {PLAYER + 0x134, 1},
        {MOVABLE + 0x138, CONTROLLER + 0x100}, {ANCHOR + 0x14, ANCHOR},
        {TABLE + 0x74, BASE + 0x3D11B0}
    }}) {
        writeWord(process, entry.first, 0); read(reader);
        const auto missing = read(reader);
        const bool global = entry.first == STATE + 0x38 || entry.first == BASE + 0x2E3FA8C;
        if (global ? missing.ready : missing.actors[1].ready)
            throw std::runtime_error("world accepted an unavailable entity/instance");
        if (entry.first == ENTITY + 0x180 && (!missing.ready || !missing.actors[19].ready))
            throw std::runtime_error("one missing actor stopped unrelated actors");
        writeWord(process, entry.first, entry.second); read(reader);
        if (!read(reader).ready) throw std::runtime_error("world did not resume after entity recreation");
    }
    writeWord(process, PLAYER + 0x134, 0xFFFFFFFF); read(reader);
    if (read(reader).actors[1].ready) throw std::runtime_error("world accepted an uninitialized life state");
    writeWord(process, PLAYER + 0x134, 0);
    const std::array<float, 3> nearby{271.8f, 150.f, .25f};
    t7r::Bytes position(sizeof(nearby)); std::memcpy(position.data(), nearby.data(), position.size());
    t7r::writeClientMemory(process, CONTROLLER + 0x210, position);
    read(reader); read(reader); value = read(reader);
    if (value.actors[1].ready || !value.actors[2].ready || value.actors[0].targetId != 3
        || !value.actors[0].terrain[1].ready)
        throw std::runtime_error("corpse consumed the living opponent terrain probe");
    writeWord(process, PLAYER + 0x134, 1); read(reader); read(reader); value = read(reader);
    if (!value.actors[1].ready || value.actors[0].targetId != 2)
        throw std::runtime_error("world failed to reacquire a respawned actor");
    const std::array<float, 3> original{272.f, 152.f, .25f};
    std::memcpy(position.data(), original.data(), position.size());
    t7r::writeClientMemory(process, CONTROLLER + 0x210, position); read(reader);
    writeWord(process, CONTROLLER + 0x110, 0x7FC00000); read(reader);
    if (read(reader).actors[1].ready) throw std::runtime_error("world accepted a non-finite position");
    writeWord(process, CONTROLLER + 0x110, 0x43878000); read(reader);
    if (!read(reader).ready) throw std::runtime_error("world valid pose recovery");
    writeWord(process, CONTROLLER + 0xAC, 0x3FC90FDB); read(reader); // Turn without moving or reporting keys.
    value = read(reader);
    if (!value.ready || std::abs(value.playerHeading - 90.f) > .0001f
        || value.player != std::array<float, 3>{270, 150, .25f})
        throw std::runtime_error("world missed a stationary turn");
    writeWord(process, CONTROLLER + 0xAC, 0x7FC00000); read(reader);
    value = read(reader);
    if (value.actors[0].ready || !std::isfinite(value.actors[0].heading))
        throw std::runtime_error("world accepted or published a non-finite heading");
    writeWord(process, CONTROLLER + 0xAC, 0xBF000000); read(reader);
    if (!read(reader).ready) throw std::runtime_error("world valid heading recovery");
    if (reader.poll(value)) throw std::runtime_error("world request consumed an unrequested frame");
    Sleep(300);
    if (read(reader).ready) throw std::runtime_error("world acknowledged an expired mailbox sample");
    if (!read(reader).ready) throw std::runtime_error("world sample did not recover after delay");
}
}
