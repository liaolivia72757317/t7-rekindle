#include "GuardDebugDraw.h"
#include "ClientWorld.h"
#include "WorldHookCode.h"
#include <d3d9.h>

namespace t7r {
namespace {
Bytes captureSceneCamera(uint32_t address, uint32_t imageBase) {
    WorldHookCode c;
    const auto ready = address + static_cast<uint32_t>(offsetof(GuardDebugMailbox, sceneCameraReady));
    c.emit({0x9C,0x60,0x81,0x7C,0x24,0x24}); c.word(imageBase + GUARD_DEBUG_UI_RETURN_RVA);
    std::vector<size_t> exit{c.branch(0x85)};
    c.store(ready, 0);
    c.emit({0x83,0x3D}); c.word(address); c.emit({1}); exit.push_back(c.branch(0x85));
    c.emit({0x3B,0x0D}); c.word(imageBase + 0x2D20008); exit.push_back(c.branch(0x85));
    c.emit({0x81,0x39}); c.word(imageBase + 0x175B188); exit.push_back(c.branch(0x85));
    c.emit({0x83,0xB9}); c.word(0x568); c.emit({0}); exit.push_back(c.branch(0x85));
    c.emit({0x8B,0x81}); c.word(0x74C); c.emit({0x85,0xC0}); exit.push_back(c.branch(0x84));
    c.emit({0x81,0xB9}); c.word(0x8AC); c.word(0x3F800000); exit.push_back(c.branch(0x85));
    c.emit({0x83,0xB9}); c.word(0x8BC); c.emit({0}); exit.push_back(c.branch(0x85));
    // Scene composition may target an intermediate texture instead of the primary target.
    c.emit({0xA3}); c.word(address + static_cast<uint32_t>(offsetof(GuardDebugMailbox, sceneTarget)));
    c.emit({0x8D,0xB1}); c.word(0x840); c.emit({0xBF}); c.word(address + GUARD_DEBUG_SCENE_VIEW);
    c.emit({0xB9}); c.word(32); c.emit({0xFC,0xF3,0xA5}); c.store(ready, 1);
    for (auto branch : exit) c.bind(branch);
    c.emit({0x61,0x9D});
    c.bytes.insert(c.bytes.end(), GUARD_DEBUG_UI_ORIGINAL.begin(), GUARD_DEBUG_UI_ORIGINAL.end());
    c.emit({0xE9});
    c.word(imageBase + GUARD_DEBUG_UI_RVA + static_cast<uint32_t>(GUARD_DEBUG_UI_ORIGINAL.size())
        - (address + GUARD_DEBUG_CODE + static_cast<uint32_t>(c.bytes.size()) + 4));
    return c.bytes;
}
}
Bytes buildGuardDebugHook(uint32_t address, uint32_t imageBase) {
    if (!address || address > UINT32_MAX - GUARD_DEBUG_PAGE_SIZE || !imageBase
        || imageBase > UINT32_MAX - 0x2D2000C) throw std::runtime_error("guard debug hook address range");
    WorldHookCode c;
    auto field = [&](size_t offset) { return address + static_cast<uint32_t>(offset); };
    const auto enabled = field(offsetof(GuardDebugMailbox, enabled)), pending = field(offsetof(GuardDebugMailbox, pending));
    const auto error = field(offsetof(GuardDebugMailbox, error)), drawing = field(offsetof(GuardDebugMailbox, drawing));
    const auto activeCount = field(offsetof(GuardDebugMailbox, activeCount));
    const auto stateBlock = field(offsetof(GuardDebugMailbox, stateBlock)), stream = field(offsetof(GuardDebugMailbox, stream));
    const auto streamOffset = field(offsetof(GuardDebugMailbox, streamOffset)), streamStride = field(offsetof(GuardDebugMailbox, streamStride));
    auto push = [&](uint32_t value) { c.emit({0x68}); c.word(value); };
    auto pushMemory = [&](uint32_t value) { c.emit({0xFF,0x35}); c.word(value); };
    auto deviceCall = [&](uint32_t slot) { c.emit({0x56,0xFF,0x97}); c.word(slot * 4); };
    auto failed = [&](std::vector<size_t>& branches) { c.emit({0x85,0xC0}); branches.push_back(c.branch(0x88)); };
    auto recordError = [&] { c.emit({0xA3}); c.word(error); c.store(enabled, 0); };
    c.bytes = GUARD_DEBUG_COMPOSITE_ORIGINAL;
    c.emit({0x9C,0x60,0x8B,0xCE}); // The native composition preserves ESI, its renderer.
    std::vector<size_t> exit;
    c.emit({0x83,0x3D}); c.word(enabled); c.emit({1}); exit.push_back(c.branch(0x85));
    c.emit({0x3B,0x0D}); c.word(imageBase + 0x2D20008); exit.push_back(c.branch(0x85));
    c.emit({0x81,0x39}); c.word(imageBase + 0x175B188); exit.push_back(c.branch(0x85));
    c.emit({0x83,0xB9}); c.word(0x568); c.emit({0}); exit.push_back(c.branch(0x85));
    // Draw after the native full-screen composition, using its saved scene camera.
    c.emit({0x83,0x3D}); c.word(field(offsetof(GuardDebugMailbox, sceneCameraReady))); c.emit({1});
    exit.push_back(c.branch(0x85)); c.store(field(offsetof(GuardDebugMailbox, sceneCameraReady)), 0);
    c.emit({0x8B,0x81}); c.word(0x74C); c.emit({0x85,0xC0}); exit.push_back(c.branch(0x84));
    c.emit({0x3B,0x05}); c.word(field(offsetof(GuardDebugMailbox, sceneTarget))); exit.push_back(c.branch(0x85));
    c.emit({0x33,0xC0,0xBA}); c.word(1);
    c.emit({0xF0,0x0F,0xB1,0x15}); c.word(drawing); exit.push_back(c.branch(0x85));
    c.emit({0x8B,0xD9,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC}); // FXSAVE and CLD.
    std::vector<size_t> finish, beforeCapture, beforeChange, changed;
    c.emit({0x83,0x3D}); c.word(pending); c.emit({1}); const auto reuse = c.branch(0x85);
    c.emit({0xA1}); c.word(field(offsetof(GuardDebugMailbox, count)));
    c.emit({0x3D}); c.word(GUARD_DEBUG_MAX_VERTICES); finish.push_back(c.branch(0x87));
    c.emit({0xA3}); c.word(activeCount);
    c.emit({0x8B,0xC8,0xC1,0xE1,2,0xBE}); c.word(address + GUARD_DEBUG_STAGING);
    c.emit({0xBF}); c.word(address + GUARD_DEBUG_ACTIVE); c.emit({0xF3,0xA5});
    c.emit({0xA1}); c.word(field(offsetof(GuardDebugMailbox, sampledTick)));
    c.emit({0xA3}); c.word(field(offsetof(GuardDebugMailbox, activeTick))); c.store(pending, 0);
    c.bind(reuse);
    c.emit({0x83,0x3D}); c.word(activeCount); c.emit({0}); finish.push_back(c.branch(0x84));
    c.emit({0xFF,0x15}); c.word(imageBase + GET_TICK_COUNT_IAT_RVA);
    c.emit({0x2B,0x05}); c.word(field(offsetof(GuardDebugMailbox, activeTick)));
    c.emit({0x3D}); c.word(250); finish.push_back(c.branch(0x87));
    c.emit({0x8B,0xB3}); c.word(0x11C); c.emit({0x85,0xF6}); finish.push_back(c.branch(0x84));
    c.emit({0x8B,0x3E});
    deviceCall(3); failed(finish); // A lost/resetting device is retried without retaining COM resources.
    c.store(stateBlock, 0); c.store(stream, 0);
    push(stateBlock); push(D3DSBT_ALL); deviceCall(59); failed(beforeCapture);
    c.emit({0xA1}); c.word(stateBlock); c.emit({0x8B,0x10,0x50,0xFF,0x52,16}); failed(beforeChange);
    push(streamStride); push(streamOffset); push(stream); push(0); deviceCall(101); failed(beforeChange);
    auto checkedCall = [&](uint32_t slot, std::initializer_list<uint32_t> reversedArgs) {
        for (auto arg : reversedArgs) push(arg);
        deviceCall(slot); failed(changed);
    };
    checkedCall(92, {0}); checkedCall(107, {0}); // Fixed-function colored lines.
    checkedCall(89, {D3DFVF_XYZ | D3DFVF_DIFFUSE});
    checkedCall(102, {1, 0});
    checkedCall(65, {0, 0});
    checkedCall(44, {address + 0x80, D3DTS_WORLD});
    checkedCall(44, {address + GUARD_DEBUG_SCENE_VIEW, D3DTS_VIEW});
    checkedCall(44, {address + GUARD_DEBUG_SCENE_VIEW + 64, D3DTS_PROJECTION});
    // The composition target does not retain scene depth; debug lines are an overlay.
    for (auto pair : {std::pair<uint32_t, uint32_t>{D3DRS_ZENABLE, D3DZB_FALSE},
            {D3DRS_ZWRITEENABLE, FALSE}, {D3DRS_ZFUNC, D3DCMP_LESSEQUAL}, {D3DRS_LIGHTING, FALSE},
            {D3DRS_FOGENABLE, FALSE}, {D3DRS_ALPHABLENDENABLE, FALSE}, {D3DRS_ALPHATESTENABLE, FALSE},
            {D3DRS_STENCILENABLE, FALSE}, {D3DRS_SCISSORTESTENABLE, FALSE}, {D3DRS_CULLMODE, D3DCULL_NONE},
            {D3DRS_CLIPPLANEENABLE, 0}, {D3DRS_FILLMODE, D3DFILL_SOLID}, {D3DRS_COLORWRITEENABLE, 15}})
        checkedCall(57, {pair.second, pair.first});
    checkedCall(67, {D3DTOP_SELECTARG1, D3DTSS_COLOROP, 0});
    checkedCall(67, {D3DTA_DIFFUSE, D3DTSS_COLORARG1, 0});
    checkedCall(67, {D3DTOP_SELECTARG1, D3DTSS_ALPHAOP, 0});
    checkedCall(67, {D3DTA_DIFFUSE, D3DTSS_ALPHAARG1, 0});
    checkedCall(67, {D3DTOP_DISABLE, D3DTSS_COLOROP, 1});
    push(sizeof(GuardDebugVertex)); push(address + GUARD_DEBUG_ACTIVE);
    c.emit({0xA1}); c.word(activeCount); c.emit({0xD1,0xE8,0x50}); push(D3DPT_LINELIST);
    deviceCall(83); failed(changed);
    c.emit({0xFF,0x05}); c.word(field(offsetof(GuardDebugMailbox, frames)));
    const auto restore = c.jump();
    for (auto branch : changed) c.bind(branch);
    recordError(); c.bind(restore);
    // ALL restores transforms/shaders/FVF/render state. UP also unbinds stream zero.
    auto restoreResult = [&] {
        c.emit({0x85,0xC0}); const auto ok = c.branch(0x89); recordError(); c.bind(ok);
    };
    c.emit({0xA1}); c.word(stateBlock); c.emit({0x8B,0x10,0x50,0xFF,0x52,20}); restoreResult();
    pushMemory(streamStride); pushMemory(streamOffset); pushMemory(stream); push(0); deviceCall(100); restoreResult();
    const auto release = c.jump();
    for (auto branch : beforeChange) c.bind(branch);
    recordError(); c.bind(release);
    for (auto pointer : {stream, stateBlock}) {
        c.emit({0xA1}); c.word(pointer); c.emit({0x85,0xC0}); const auto empty = c.branch(0x84);
        c.emit({0x8B,0x10,0x50,0xFF,0x52,8}); c.store(pointer, 0); c.bind(empty);
    }
    const auto done = c.jump();
    for (auto branch : beforeCapture) c.bind(branch);
    recordError(); c.bind(done);
    for (auto branch : finish) c.bind(branch);
    c.store(drawing, 0);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5}); // FXRSTOR.
    for (auto branch : exit) c.bind(branch);
    c.emit({0x61,0x9D});
    c.emit({0xE9});
    c.word(imageBase + GUARD_DEBUG_COMPOSITE_RVA + static_cast<uint32_t>(GUARD_DEBUG_COMPOSITE_ORIGINAL.size())
        - (address + GUARD_DEBUG_DRAW_CODE + static_cast<uint32_t>(c.bytes.size()) + 4));
    auto code = captureSceneCamera(address, imageBase);
    if (code.size() > 0x1000 || c.bytes.size() > 0x1000) throw std::runtime_error("guard debug code capacity");
    code.resize(GUARD_DEBUG_DRAW_CODE - GUARD_DEBUG_CODE, 0);
    code.insert(code.end(), c.bytes.begin(), c.bytes.end());
    return code;
}
}
