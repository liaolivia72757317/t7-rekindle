#pragma once
#include "WorldHookFixture.h"
#include "../../src/Runtime/launcher/WorldHookCode.h"

namespace terrainFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
constexpr uint32_t MANAGER = BASE + 0xB000, ENTITY = BASE + 0xB100, MAP = BASE + 0xB200;
constexpr uint32_t COLLISION = BASE + 0xB300, DATA = BASE + 0xB400, WRAPPER = BASE + 0xB500;
constexpr uint32_t BODY = BASE + 0xB600, SHAPE = BASE + 0xB700, PHYSICS = BASE + 0xB800;
constexpr uint32_t OWNER = BASE + 0xB900, WORLD = BASE + 0xBA00, CHILD = BASE + 0xBB00;
constexpr uint32_t ACTOR = BASE + 0xBC00, ACTOR_CHILD = BASE + 0xBD00;
constexpr uint32_t HIT = BASE + 0xBE00, CONTROL = BASE + 0xBF00;
constexpr uint32_t MODE = CONTROL, MATERIAL = CONTROL + 4, FRACTION = CONTROL + 8;
constexpr uint32_t HEIGHT = CONTROL + 12, CALLS = CONTROL + 16, ERRORS = CONTROL + 20;

inline void prepare(HANDLE process) {
    for (const auto& p : std::vector<std::pair<uint32_t, uint32_t>>{
        {BASE+0x2E24AF8,MANAGER}, {MANAGER+0x1C,ENTITY}, {ENTITY+0x7C,ENTITY}, {ENTITY+0x80,MAP},
        {MAP,BASE+0x177E7EC}, {MAP+0x1C,COLLISION}, {COLLISION,BASE+0x177C1DC}, {COLLISION+0x38,DATA},
        {DATA,BASE+0x1764C58}, {DATA+0x1C,WRAPPER}, {WRAPPER,BASE+0x1764B68}, {WRAPPER+0x20,14},
        {WRAPPER+0x3C,BODY}, {BODY+8,WORLD}, {BODY+0x10,SHAPE}, {SHAPE+8,16},
        {BASE+0x2D379A8,PHYSICS}, {PHYSICS+0x10,OWNER}, {OWNER+0x10,WORLD},
        {WORLD,BASE+0x1ACFF4C}, {WORLD+0x74,WORLD}, {CHILD+0xC,BODY+0x10}, {ACTOR_CHILD+0xC,ACTOR},
        {MODE,1}, {MATERIAL,4}, {FRACTION,0x3F000000}, {HEIGHT,0x40000000}, {HIT,0xBF800000}
    }) writeWord(process, p.first, p.second);
    t7r::WorldHookCode c;
    c.emit({0x55,0x8B,0xEC,0x83,0xE4,0xF0,0x83,0xEC,0x58,0x56,0x57});
    c.emit({0x8B,0x75,0x0C,0x8B,0x7D,8});
    c.emit({0x83,0x7F,36,0}); const auto terrain = c.branch(0x85);
    c.call(BASE + 0x80000); // Blade fixture query, with EDI input and ESI collector.
    const auto bladeDone = c.jump(); c.bind(terrain);
    c.emit({0xFF,0x05}); c.word(CALLS);
    // A nearer actor must not truncate the subsequent scene query.
    c.store(HIT + 0x10, 0x3DCCCCCD);
    auto hit = [&](uint32_t body) {
        c.emit({0x68}); c.word(HIT); c.emit({0x68}); c.word(body);
        c.emit({0x8B,0xCE,0x8B,0x06,0xFF,0x10});
    };
    hit(ACTOR_CHILD);
    c.emit({0x83,0x7E,0x60,0}); const auto filtered = c.branch(0x84);
    c.emit({0xFF,0x05}); c.word(ERRORS); c.bind(filtered);
    c.emit({0x83,0x3D}); c.word(MODE); c.emit({0}); const auto clear = c.branch(0x84);
    // Synthetic obstacle height clips the interpolated ray, not the character's feet.
    c.emit({0xF3,0x0F,0x10,0x47,0x18,0xF3,0x0F,0x5C,0x47,8,0xF3,0x0F,0x59,0x05}); c.word(FRACTION);
    c.emit({0xF3,0x0F,0x58,0x47,8,0x0F,0x2F,0x05}); c.word(HEIGHT); const auto above = c.branch(0x87);
    c.emit({0xA1}); c.word(FRACTION); c.emit({0xA3}); c.word(HIT + 0x10);
    hit(CHILD);
    c.bind(above); c.bind(clear); c.bind(bladeDone); c.emit({0x5F,0x5E,0x8B,0xE5,0x5D,0xC2,8,0});
    t7r::writeClientMemory(process, BASE + 0x10FA3B0, c.bytes);

    c.bytes.clear();
    c.emit({0x53,0x8B,0x5C,0x24,0x0C,0x57,0x8B,0xF9});
    c.emit({0x0F,0x28,0x03,0x0F,0x29,0x47,0x10}); // Exercise aligned collector storage.
    c.emit({0x8B,0x43,0x10,0x89,0x47,0x20,0x89,0x47,4});
    c.emit({0x8B,0x44,0x24,0x0C,0x8B,0x40,0x0C,0x89,0x47,0x60});
    c.emit({0xC7,0x47,0x30}); c.word(42); c.emit({0xC7,0x47,0x34}); c.word(7);
    c.emit({0x5F,0x5B,0xC2,8,0});
    t7r::writeClientMemory(process, BASE + 0x108BFA0, c.bytes);

    c.bytes.clear();
    c.emit({0x8B,0x41,8,0x85,0xC0,0x74,0x28});
    c.emit({0x8B,0x51,0x0C,0x83,0x3A,7}); const auto rightKey = c.branch(0x84);
    c.emit({0xFF,0x05}); c.word(ERRORS); c.bind(rightKey);
    c.emit({0xA1}); c.word(MATERIAL); c.emit({0xC3});
    c.bytes.resize(0x2F, 0xCC); c.emit({0x83,0xC8,0xFF,0xC3});
    t7r::writeClientMemory(process, BASE + 0x29D4B0, c.bytes);
}

inline void verifyMultiTarget(HANDLE process, t7r::ClientWorld& reader) {
    // A pose change needs a full sampling/selection/query cycle, including the queued request.
    auto camps = worldFixture::camps();
    auto sample = [&] { worldFixture::read(reader, camps); worldFixture::read(reader, camps); return worldFixture::read(reader, camps); };
    auto position = [&](uint32_t index, std::array<float, 3> value) {
        t7r::Bytes bytes(sizeof(value)); std::memcpy(bytes.data(), value.data(), bytes.size());
        t7r::writeClientMemory(process, worldFixture::CONTROLLER + index * 0x100 + 0x10, bytes);
    };
    const std::array<uint32_t, 6> changed{0, 1, 2, 3, 11, 59};
    std::array<t7r::Bytes, 6> original, headings;
    for (size_t i = 0; i < changed.size(); ++i) {
        const auto controller = worldFixture::CONTROLLER + changed[i] * 0x100;
        original[i] = t7r::readClientMemory(process, controller + 0x10, 12);
        headings[i] = t7r::readClientMemory(process, controller + 0xAC, 4);
    }
    writeWord(process, worldFixture::CONTROLLER + 0xAC, 0);
    writeWord(process, worldFixture::CONTROLLER + 0xBAC, 0);
    position(1, {271,150,-.25f}); position(2, {271.5f,150.5f,.25f});
    position(3, {272,150,.25f}); position(11, {270.5f,150,.25f});
    auto value = sample();
    const std::array<std::array<float, 3>, 3> points{{{270.5f,150,1}, {270.75f,150.25f,1.25f}, {271,150,1.25f}}};
    for (size_t i = 0; i < points.size(); ++i) {
        const auto& target = value.actors[0].targetTerrain[i + 1];
        if (!target.ready || !target.hit || target.material != 4 || target.position != points[i]
            || target.normal != std::array<float, 3>{-1,0,0})
            throw std::runtime_error("multi-target terrain reused or lost a target segment");
    }
    if (value.actors[0].targetTerrain[0].ready || value.actors[0].targetTerrain[11].ready
        || value.actors[0].targetId != 2 || value.actors[0].terrain[1].position != points[0]
        || !value.actors[11].targetTerrain[1].ready || !value.actors[1].targetTerrain[0].ready
        || !value.actors[1].targetTerrain[11].ready || value.actors[1].targetTerrain[2].ready)
        throw std::runtime_error("multi-target terrain camp or nearest-target compatibility mismatch");
    writeWord(process, HEIGHT, 0x3F900000); value = sample(); // Only the lower target segment hits this step.
    if (!value.actors[0].targetTerrain[1].hit || !value.actors[0].targetTerrain[2].ready
        || value.actors[0].targetTerrain[2].hit || !value.actors[0].targetTerrain[3].ready
        || value.actors[0].targetTerrain[3].hit || value.actors[0].terrain[0].hit)
        throw std::runtime_error("multi-target terrain shared an obstruction between targets");
    writeWord(process, HEIGHT, 0x40000000);
    for (const auto& excluded : std::array<std::array<float, 3>, 3>{{
        {273,150,.25f}, {271.5f,150.5f,1.5f}, {269,150,.25f}
    }}) {
        position(2, excluded); value = sample();
        if (value.actors[0].targetTerrain[2].ready || !value.actors[0].targetTerrain[1].ready
            || !value.actors[0].targetTerrain[3].ready)
            throw std::runtime_error("multi-target terrain retained an out-of-envelope target");
        position(2, {271.5f,150.5f,.25f}); sample();
    }
    writeWord(process, worldFixture::PLAYER + 0x234, 0); value = sample();
    if (value.actors[0].targetTerrain[2].ready || !value.actors[0].targetTerrain[3].ready)
        throw std::runtime_error("multi-target terrain retained a corpse");
    writeWord(process, worldFixture::PLAYER + 0x234, 1);
    writeWord(process, MAP + 0x1C, 0); value = sample();
    for (size_t i = 1; i <= 3; ++i)
        if (value.actors[0].targetTerrain[i].ready)
            throw std::runtime_error("multi-target terrain accepted missing geometry as clear");
    writeWord(process, MAP + 0x1C, COLLISION);
    // RID 60 uses the high half of the native target mask; camp comes from the roster.
    position(59, {271.5f,150.5f,.25f});
    camps[59] = 2;
    value = sample();
    if (!value.actors[59].ready || !value.actors[0].targetTerrain[59].ready
        || !value.actors[0].targetTerrain[59].hit)
        throw std::runtime_error("native target mask lost RID 60");
    camps[59] = 1;
    value = sample();
    if (value.actors[0].targetTerrain[59].ready)
        throw std::runtime_error("native target mask retained a same-camp RID 60");
    for (size_t i = 0; i < changed.size(); ++i) {
        const auto controller = worldFixture::CONTROLLER + changed[i] * 0x100;
        t7r::writeClientMemory(process, controller + 0x10, original[i]);
        t7r::writeClientMemory(process, controller + 0xAC, headings[i]);
    }
    sample();
}

inline void verify(HANDLE process, t7r::ClientWorld& reader) {
    auto sample = [&] { worldFixture::read(reader); return worldFixture::read(reader); };
    auto value = sample();
    const auto& forward = value.terrain[0][0];
    const auto& target = value.terrain[0][1];
    if (!value.ready || !forward.ready || !forward.hit || forward.material != 4
        || !target.ready || !target.hit || target.material != 4
        || std::abs(target.position[0] - 270.5f) > .0001f
        || std::abs(target.position[1] - 150.5f) > .0001f || target.position[2] != 1.25f
        || target.normal != std::array<float, 3>{-1,0,0}
        || std::abs(forward.direction[0] - std::cos(-.5f)) > .0001f
        || std::abs(forward.direction[1] + std::sin(-.5f)) > .0001f)
        throw std::runtime_error("terrain point/normal/material/heading contract mismatch");
    const auto& reversed = value.terrain[1][1];
    if (!reversed.hit || reversed.direction[0] >= 0 || reversed.direction[1] >= 0
        || reversed.position != target.position)
        throw std::runtime_error("terrain actor reversal mismatch");
    for (uint32_t material : {0u,1u,3u,7u,UINT32_MAX,99u}) {
        writeWord(process, MATERIAL, material); value = sample();
        const auto expected = material <= 7 ? static_cast<int32_t>(material) : -1;
        if (!value.terrain[0][0].hit || value.terrain[0][0].material != expected)
            throw std::runtime_error("terrain lost actual material or fabricated an unknown material");
    }
    writeWord(process, MATERIAL, 4);
    for (const auto& p : std::vector<std::pair<uint32_t, uint32_t>>{
        {MAP+0x1C,COLLISION}, {BODY+8,WORLD}, {OWNER+0x10,WORLD}, {SHAPE+8,16}, {WRAPPER+0x20,14}
    }) {
        writeWord(process, p.first, 0); value = sample();
        if (!value.ready || value.terrain[0][0].ready)
            throw std::runtime_error("missing scene geometry accepted as clear terrain");
        writeWord(process, p.first, p.second);
    }
    writeWord(process, HEIGHT, 0x3F000000); value = sample(); // Low step below the weapon.
    if (!value.terrain[0][0].ready || value.terrain[0][0].hit)
        throw std::runtime_error("low obstacle below the weapon incorrectly blocked it");
    writeWord(process, HEIGHT, 0x40000000);
    writeWord(process, MODE, 0); value = sample();
    if (!value.terrain[0][0].ready || value.terrain[0][0].hit)
        throw std::runtime_error("native clear query lost or actor mistaken for terrain");
    writeWord(process, MODE, 1);
    // An exact overlap still has a clear target segment and never enters Havok as a degenerate ray.
    const auto original = t7r::readClientMemory(process, worldFixture::CONTROLLER + 0x110, 12);
    t7r::writeClientMemory(process, worldFixture::CONTROLLER + 0x110,
                         t7r::readClientMemory(process, worldFixture::CONTROLLER + 0x10, 12));
    value = sample();
    if (!value.actors[0].terrain[1].ready || value.actors[0].terrain[1].hit || value.actors[0].targetId != 2
        || !value.actors[0].targetTerrain[1].ready || value.actors[0].targetTerrain[1].hit)
        throw std::runtime_error("coincident actor terrain segment mismatch");
    t7r::writeClientMemory(process, worldFixture::CONTROLLER + 0x110, original); sample();
    for (const auto& p : std::vector<std::pair<uint32_t, uint32_t>>{
        {HIT,0xBF800000}, {FRACTION,0x3F000000}
    }) {
        writeWord(process, p.first, 0x7FC00000); value = sample();
        if (!value.ready || value.terrain[0][0].ready)
            throw std::runtime_error("terrain accepted non-finite native hit data");
        writeWord(process, p.first, p.second);
    }
    verifyMultiTarget(process, reader);
    const auto errors = t7r::readClientMemory(process, ERRORS, 4);
    if (errors != t7r::Bytes(4, 0)) throw std::runtime_error("terrain collector admitted an actor or used the wrong shape key");
}
}
