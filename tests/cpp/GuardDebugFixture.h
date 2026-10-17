#pragma once
#include "../../src/Runtime/launcher/GuardDebugDraw.h"
#include "../../src/Runtime/launcher/WorldHookCode.h"
#include "WorldHookFixture.h"

namespace guardDebugFixture {
constexpr uint32_t BASE = 0x400000, DATA = BASE + 0x120000, DISPATCH = DATA + 0x1000;
constexpr uint32_t DEVICE_TABLE = DATA + 0x2000, STUBS = DATA + 0x3000;
constexpr uint32_t RENDERER = BASE + 0x6000, DEVICE = BASE + 0x7000;
constexpr uint32_t GATE = DATA, DRAWS = DATA + 4, UI_CALLS = DATA + 8, MARKER = DATA + 12;
constexpr uint32_t SAVED_MARKER = DATA + 16, CURRENT_STREAM = DATA + 20, BLOCK_REFS = DATA + 24, STREAM_REFS = DATA + 28;
constexpr uint32_t LOST = DATA + 32, DRAW_ERROR = DATA + 36, CAMERA_MODE = DATA + 40, VIEW_SEEN = DATA + 44;
constexpr uint32_t BLOCK = DATA + 0x100, BLOCK_TABLE = DATA + 0x120, STREAM = DATA + 0x180, STREAM_TABLE = DATA + 0x1A0;
constexpr uint32_t FPU = DATA + 48, SSE = DATA + 52, ESP_BEFORE = DATA + 56, ESP_AFTER = DATA + 60;
inline void require(bool yes, const char* message) { if (!yes) throw std::runtime_error(message); }
inline void put(HANDLE p, uint32_t at, uint32_t v) { worldFixture::writeWord(p, at, v); }
inline uint32_t get(HANDLE p, uint32_t at) {
    const auto b = t7r::readClientMemory(p, at, 4); uint32_t v; std::memcpy(&v, b.data(), 4); return v;
}
inline void prepare(HANDLE p) {
    put(p, DEVICE, DEVICE_TABLE); put(p, BLOCK, BLOCK_TABLE); put(p, STREAM, STREAM_TABLE);
    put(p, RENDERER + 0x748, DATA + 0x200); put(p, RENDERER + 0x74C, DATA + 0x200);
    put(p, RENDERER + 0x840, 0x40E00000); // Scene view sentinel; UI has a different matrix.
    auto stub = [&](uint32_t slot, uint32_t args, const t7r::WorldHookCode& body) {
        auto code = body.bytes; code.insert(code.end(), {0xC2, static_cast<uint8_t>(args), static_cast<uint8_t>(args >> 8)});
        require(code.size() <= 0x100, "guard COM fixture stub capacity");
        const auto at = STUBS + slot * 0x100;
        put(p, DEVICE_TABLE + slot * 4, at); t7r::writeClientMemory(p, at, code); return at;
    };
    for (auto item : {std::pair<uint32_t,uint32_t>{44,12}, {57,12}, {65,12}, {67,16},
                     {89,8}, {92,8}, {102,12}, {107,8}}) {
        t7r::WorldHookCode c; c.store(MARKER, 0xBAD);
        if (item.first == 44) {
            c.emit({0x83,0x7C,0x24,8,2}); const auto skip = c.branch(0x85);
            c.emit({0x8B,0x44,0x24,12,0x8B,0x00,0xA3}); c.word(VIEW_SEEN); c.bind(skip);
        }
        c.emit({0x33,0xC0}); stub(item.first, item.second, c);
    }
    t7r::WorldHookCode coop; coop.emit({0xA1}); coop.word(LOST); stub(3, 4, coop);
    t7r::WorldHookCode create;
    create.emit({0x8B,0x44,0x24,12,0xC7,0x00}); create.word(BLOCK);
    create.emit({0xFF,0x05}); create.word(BLOCK_REFS); create.emit({0x33,0xC0}); stub(59, 12, create);
    t7r::WorldHookCode capture; capture.emit({0xA1}); capture.word(MARKER); capture.emit({0xA3}); capture.word(SAVED_MARKER);
    capture.emit({0x33,0xC0}); put(p, BLOCK_TABLE + 16, stub(120, 4, capture));
    t7r::WorldHookCode apply; apply.emit({0xA1}); apply.word(SAVED_MARKER); apply.emit({0xA3}); apply.word(MARKER);
    apply.emit({0x33,0xC0}); put(p, BLOCK_TABLE + 20, stub(121, 4, apply));
    for (auto pair : {std::pair<uint32_t,uint32_t>{BLOCK_TABLE, BLOCK_REFS}, {STREAM_TABLE, STREAM_REFS}}) {
        t7r::WorldHookCode release; release.emit({0xFF,0x0D}); release.word(pair.second); release.emit({0x33,0xC0});
        put(p, pair.first + 8, stub(pair.first == BLOCK_TABLE ? 122 : 123, 4, release));
    }
    t7r::WorldHookCode getStream;
    for (auto pair : {std::pair<uint32_t,uint32_t>{12, STREAM}, {16, 24}, {20, 36}}) {
        getStream.emit({0x8B,0x44,0x24,static_cast<uint8_t>(pair.first),0xC7,0x00}); getStream.word(pair.second);
    }
    getStream.emit({0xFF,0x05}); getStream.word(STREAM_REFS); getStream.emit({0x33,0xC0}); stub(101, 20, getStream);
    t7r::WorldHookCode setStream; setStream.emit({0x8B,0x44,0x24,12,0xA3}); setStream.word(CURRENT_STREAM);
    setStream.emit({0x33,0xC0}); stub(100, 20, setStream);
    t7r::WorldHookCode draw; draw.emit({0xFF,0x05}); draw.word(DRAWS); draw.store(CURRENT_STREAM, 0);
    draw.emit({0xA1}); draw.word(DRAW_ERROR); stub(83, 20, draw);
    auto ui = t7r::GUARD_DEBUG_UI_ORIGINAL;
    t7r::WorldHookCode tail; tail.store(RENDERER + 0x8AC, 0); tail.store(RENDERER + 0x8BC, 0x3F800000);
    tail.emit({0xFF,0x05}); tail.word(UI_CALLS); tail.emit({0x8B,0xE5,0x5D,0xC2,4,0});
    ui.insert(ui.end(), tail.bytes.begin(), tail.bytes.end()); t7r::writeClientMemory(p, BASE + t7r::GUARD_DEBUG_UI_RVA, ui);
    t7r::WorldHookCode dispatch;
    dispatch.emit({0x9C,0x60,0x83,0x3D}); dispatch.word(GATE); dispatch.emit({1}); const auto idle = dispatch.branch(0x85);
    dispatch.store(MARKER, 0x12345678); dispatch.store(CURRENT_STREAM, STREAM);
    dispatch.store(RENDERER + 0x8AC, 0x3F800000); dispatch.store(RENDERER + 0x8BC, 0);
    dispatch.emit({0x83,0x3D}); dispatch.word(CAMERA_MODE); dispatch.emit({0}); const auto perspective = dispatch.branch(0x84);
    dispatch.store(RENDERER + 0x8AC, 0); dispatch.store(RENDERER + 0x8BC, 0x3F800000); dispatch.bind(perspective);
    dispatch.emit({0x89,0x25}); dispatch.word(ESP_BEFORE);
    dispatch.emit({0xD9,0xE8,0xB8}); dispatch.word(0x3F800000); dispatch.emit({0x66,0x0F,0x6E,0xC0,0x6A,0,0xB9}); dispatch.word(RENDERER);
    dispatch.call(BASE + t7r::GUARD_DEBUG_UI_RVA);
    dispatch.emit({0x89,0x25}); dispatch.word(ESP_AFTER);
    dispatch.emit({0xD9,0x1D}); dispatch.word(FPU); dispatch.emit({0x66,0x0F,0x7E,0x05}); dispatch.word(SSE);
    dispatch.store(GATE, 0); dispatch.bind(idle); dispatch.emit({0x61,0x9D,0xC3});
    t7r::writeClientMemory(p, DISPATCH, dispatch.bytes);
}
inline void frame(HANDLE p) {
    const uint32_t one = 1; SIZE_T written = 0;
    require(WriteProcessMemory(p, reinterpret_cast<void*>(static_cast<uintptr_t>(GATE)), &one, 4, &written) && written == 4,
        "guard debug fixture trigger");
    const auto deadline = GetTickCount64() + 3000;
    while (get(p, GATE)) { require(GetTickCount64() < deadline, "guard draw frame timeout"); Sleep(1); }
    require(get(p, ESP_BEFORE) == get(p, ESP_AFTER) && get(p, FPU) == 0x3F800000 && get(p, SSE) == 0x3F800000,
        "guard draw changed stack/FPU/SSE");
    require(get(p, MARKER) == 0x12345678 && get(p, CURRENT_STREAM) == STREAM
        && get(p, BLOCK_REFS) == 0 && get(p, STREAM_REFS) == 0, "guard drawing leaked render state or COM references");
}
inline void verify(HANDLE p, t7r::GuardDebugDraw& draw) {
    t7r::WorldSnapshot world; world.ready = true; world.instanceStartedAt = 1;
    auto& actor = world.actors[0]; actor.ready = true; actor.guardWeaponId = 701; actor.state = 223;
    actor.guard.center = {10, 20, 1}; actor.guard.cosYaw = 0; actor.guard.sinYaw = 1;
    auto vertices = t7r::guardDebugVertices(world, {});
    require(vertices.size() == 24 && vertices[0].color == 0xFF00D8E8
        && std::abs(vertices[0].position[0] - 9.4f) < .001f, "draw did not use sampled OBB");
    actor.state = 235; require(t7r::guardDebugVertices(world, {})[0].color == 0xFFFFD54F, "prepare color");
    actor.state = 236; require(t7r::guardDebugVertices(world, {})[0].color == 0xFF808080, "hold color");
    actor.blade.ready = true; actor.blade.base = {1,0,0}; actor.blade.tip = {1,1,0};
    actor.blade.sweepBase = {0,0,0}; actor.blade.sweepTip = {0,1,0}; actor.blade.sweepSteps = 2;
    require(t7r::guardDebugVertices(world, {}).size() == 34, "actual sweep strands missing");
    auto publish = [&] { world.sampledAt = GetTickCount64(); draw.update(true, &world); };
    frame(p); require(get(p, DRAWS) == 0, "debug rendered by default");
    draw.update(true); frame(p); require(get(p, DRAWS) == 0, "debug rendered before a world snapshot");
    publish(); frame(p); require(get(p, DRAWS) == 1 && get(p, VIEW_SEEN) == 0x40E00000, "scene camera draw missing");
    frame(p); require(get(p, DRAWS) == 2, "immutable geometry not retained between publications");
    put(p, CAMERA_MODE, 1); frame(p); require(get(p, DRAWS) == 2, "UI projection was used for world drawing"); put(p, CAMERA_MODE, 0);
    put(p, RENDERER + 0x74C, DATA + 0x300); frame(p); require(get(p, DRAWS) == 2, "offscreen target was drawn"); put(p, RENDERER + 0x74C, DATA + 0x200);
    draw.update(false); frame(p); require(get(p, DRAWS) == 2, "live disable failed");
    world.ready = false; publish(); frame(p); require(get(p, DRAWS) == 2, "unready world was drawn");
    draw.update(true); frame(p); require(get(p, DRAWS) == 2, "unready world was re-enabled");
    world.ready = true;
    publish(); put(p, LOST, 0x88760868); frame(p); require(get(p, DRAWS) == 2, "lost device was drawn");
    put(p, LOST, 0); publish(); frame(p); require(get(p, DRAWS) == 3, "device reset did not resume drawing");
    Sleep(260); frame(p); require(get(p, DRAWS) == 3, "stale world geometry was rendered");
    publish(); put(p, DRAW_ERROR, 0x8876086C); frame(p);
    bool failed = false; try { draw.update(true); } catch (const std::runtime_error&) { failed = true; }
    require(failed, "draw HRESULT was not surfaced");
    frame(p); require(get(p, DRAWS) == 4 && get(p, UI_CALLS) >= 11, "drawing failure interfered with the original UI");
}
}
