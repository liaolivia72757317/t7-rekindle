#include "GraphicsSettings.h"
#include "RemoteImage.h"
#include "RoundEndHookCode.h"
#include "MovementAnimationHookCode.h"
#include "MovementCommandHookCode.h"
#include "SprintHookCode.h"
#include "CombatProfileHookCode.h"
#include <algorithm>
#include <cstring>

namespace t7r {
namespace {
void write(HANDLE process, uint32_t address, const void* data, size_t size) {
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(address)), data, size, &actual)
        || actual != size) throw std::runtime_error(errorText("graphics mailbox write"));
    const auto checked = readClientMemory(process, address, size);
    if (std::memcmp(data, checked.data(), size)) throw std::runtime_error("graphics mailbox readback mismatch");
}
}
void GraphicsSettings::install(HANDLE process, uint32_t imageBase, DWORD threadId, const DashAttackProfile& dash,
                               const MovementAnimationProfile& animation, const std::vector<CombatProfile>& profiles) {
    const bool movementEnabled = animation.enabled || !profiles.empty();
    if (process_ || !process) throw std::runtime_error("graphics bridge installation state");
    if (readClientMemory(process, imageBase + GRAPHICS_TICK_RVA, GRAPHICS_TICK_ORIGINAL.size()) != GRAPHICS_TICK_ORIGINAL
        || readClientMemory(process, imageBase + 0x6EA040, 5) != Bytes{0x55, 0x8B, 0xEC, 0x6A, 0xFF})
        throw std::runtime_error("graphics frame/apply signature mismatch");
    for (auto rva : {AUDIO_MUSIC_RVA, AUDIO_EFFECTS_RVA})
        if (readClientMemory(process, imageBase + rva, 6) != Bytes{0x55, 0x8B, 0xEC, 0x8A, 0x45, 0x08})
            throw std::runtime_error("audio setter signature mismatch");
    verifyWorldFunctions(process, imageBase);
    if (readClientMemory(process, imageBase + TITLE_REFRESH_RVA, 6) != Bytes{0x55,0x8B,0xEC,0x6A,0xFF,0x68})
        throw std::runtime_error("title table refresh signature mismatch");
    if (readClientMemory(process, imageBase + TITLE_REINDEX_RVA, 6) != Bytes{0x55,0x8B,0xEC,0x83,0xEC,0x14})
        throw std::runtime_error("title table index signature mismatch");
    if (!profiles.empty() && readClientMemory(process, imageBase + 0xF34F0, 4) != Bytes{0x8B,0x41,0x2C,0xC3})
        throw std::runtime_error("combat profile hero getter signature mismatch");
    if (movementEnabled) verifyMovementAnimationFunction(process, imageBase);
    constexpr auto worldOffset = GRAPHICS_PAGE_SIZE + AUDIO_PAGE_SIZE;
    constexpr auto titleOffset = worldOffset + WORLD_PAGE_SIZE;
    constexpr auto size = titleOffset + TITLE_PAGE_SIZE;
    const auto remote = VirtualAllocEx(process, nullptr, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) throw std::runtime_error(errorText("graphics mailbox allocation"));
    const auto pointer = reinterpret_cast<uintptr_t>(remote);
    bool patched = false;
    bool roundEndPatched = false;
    bool animationPatched = false;
    bool posePatched = false;
    bool defenseExitPatched = false;
    bool jumpPhysicsPatched = false;
    bool movementCommandPatched = false;
    bool airInputPatched = false;
    try {
        if (pointer > UINT32_MAX - size) throw std::runtime_error("settings mailbox outside x86 range");
        const auto base = static_cast<uint32_t>(pointer);
        auto bytes = buildGraphicsHook(base, imageBase, threadId, base + GRAPHICS_PAGE_SIZE + AUDIO_CODE_OFFSET,
                                  base + worldOffset + WORLD_CODE_OFFSET, base + titleOffset + TITLE_CODE_OFFSET);
        const auto audioBytes = buildAudioHook(base + GRAPHICS_PAGE_SIZE, imageBase, threadId);
        bytes.insert(bytes.end(), audioBytes.begin(), audioBytes.end());
        const auto worldBytes = buildWorldHook(base + worldOffset, imageBase, threadId, dash, animation, profiles);
        bytes.insert(bytes.end(), worldBytes.begin(), worldBytes.end());
        const auto titleBytes = buildTitleHook(base + titleOffset, imageBase, threadId);
        bytes.insert(bytes.end(), titleBytes.begin(), titleBytes.end());
        write(process, base, bytes.data(), bytes.size());
        DWORD previous = 0;
        if (!VirtualProtectEx(process, reinterpret_cast<void*>(pointer + GRAPHICS_CODE_OFFSET), 0x1000, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + titleOffset + TITLE_CODE_OFFSET), 0x1000, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + GRAPHICS_PAGE_SIZE + AUDIO_CODE_OFFSET), 0x1000, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + worldOffset + WORLD_CODE_OFFSET), WORLD_CODE_END - WORLD_CODE_OFFSET, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + worldOffset + CONTACT_CODE_OFFSET), LIVE_ACTORS_OFFSET - CONTACT_CODE_OFFSET, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + worldOffset + COMBAT_PROFILE_CODE_OFFSET), 0x1000, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + worldOffset + COMBAT_PROFILES_OFFSET), COMBAT_PROFILE_CODE_OFFSET - COMBAT_PROFILES_OFFSET, PAGE_READONLY, &previous)
            || !FlushInstructionCache(process, remote, bytes.size()))
            throw std::runtime_error(errorText("graphics code protection/cache"));
        Bytes jump(7, 0x90); jump[0] = 0xE9;
        const uint32_t delta = base + GRAPHICS_CODE_OFFSET - (imageBase + GRAPHICS_TICK_RVA + 5);
        std::memcpy(jump.data() + 1, &delta, 4);
        roundEndPatched = true;
        for (const auto& entry : roundEndJumps(base + worldOffset, imageBase))
            writeClientMemory(process, imageBase + entry.first, entry.second);
        if (movementEnabled) {
            animationPatched = true;
            writeClientMemory(process, imageBase + MOVEMENT_ANIMATION_RVA, movementAnimationJump(base + worldOffset, imageBase));
            posePatched = true;
            writeClientMemory(process, imageBase + MOVEMENT_POSE_RVA, movementPoseJump(base + worldOffset, imageBase));
            defenseExitPatched = true;
            writeClientMemory(process, imageBase + DEFENSE_EXIT_RVA, defenseExitJump(base + worldOffset, imageBase));
        }
        jumpPhysicsPatched = true;
        writeClientMemory(process, imageBase + JUMP_PHYSICS_RVA, jumpPhysicsJump(base + worldOffset, imageBase));
        movementCommandPatched = true;
        writeClientMemory(process, imageBase + MOVEMENT_COMMAND_RVA, movementCommandJump(base + worldOffset, imageBase));
        airInputPatched = true;
        writeClientMemory(process, imageBase + AIR_INPUT_RVA, airInputJump(base + worldOffset, imageBase));
        applyRemotePatches(process, imageBase, {{GRAPHICS_TICK_RVA, GRAPHICS_TICK_ORIGINAL, jump}}); patched = true;
        process_ = process; remote_ = base; imageBase_ = imageBase;
        audio_.attach(process, base + GRAPHICS_PAGE_SIZE, imageBase);
        world_.attach(process, base + worldOffset);
        titles_.attach(process, base + titleOffset);
    } catch (...) {
        // install() runs before the first ResumeThread, so rollback cannot race execution.
        if (patched) {
            writeClientMemory(process, imageBase + GRAPHICS_TICK_RVA, GRAPHICS_TICK_ORIGINAL);
        }
        if (animationPatched) writeClientMemory(process, imageBase + MOVEMENT_ANIMATION_RVA, MOVEMENT_ANIMATION_ORIGINAL);
        if (posePatched) writeClientMemory(process, imageBase + MOVEMENT_POSE_RVA, MOVEMENT_POSE_ORIGINAL);
        if (defenseExitPatched) writeClientMemory(process, imageBase + DEFENSE_EXIT_RVA, DEFENSE_EXIT_ORIGINAL);
        if (jumpPhysicsPatched) writeClientMemory(process, imageBase + JUMP_PHYSICS_RVA, JUMP_PHYSICS_ORIGINAL);
        if (movementCommandPatched) writeClientMemory(process, imageBase + MOVEMENT_COMMAND_RVA, MOVEMENT_COMMAND_ORIGINAL);
        if (airInputPatched) writeClientMemory(process, imageBase + AIR_INPUT_RVA, AIR_INPUT_ORIGINAL);
        if (roundEndPatched)
            for (const auto& entry : roundEndOriginals(imageBase))
                writeClientMemory(process, imageBase + entry.first, entry.second);
        if (!VirtualFreeEx(process, remote, 0, MEM_RELEASE)) throw std::runtime_error(errorText("graphics allocation rollback"));
        throw;
    }
}
void GraphicsSettings::clear() {
    titles_.clear();
    if (process_ && WaitForSingleObject(process_, 0) != WAIT_OBJECT_0)
        throw std::runtime_error("graphics bridge cleanup requires terminated owned client");
    audio_.clear();
    world_.clear();
    process_ = nullptr; remote_ = imageBase_ = pending_ = 0; requestedAt_ = 0; hasValues_ = false; lastRaw_ = {};
}
void GraphicsSettings::request(uint32_t command) {
    // A request becomes visible only after its immutable payload has been written.
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process_, reinterpret_cast<void*>(static_cast<uintptr_t>(remote_)), &command, 4, &actual) || actual != 4)
        throw std::runtime_error(errorText("graphics request publication"));
    pending_ = command; requestedAt_ = GetTickCount64();
}
bool GraphicsSettings::poll(GraphicsValues& values, uint32_t& result, bool& applied) {
    if (!process_) return false;
    if (!pending_) { request(1); return false; }
    const auto bytes = readClientMemory(process_, remote_, 0x50);
    uint32_t command = 0; std::memcpy(&command, bytes.data(), 4);
    if (command != 0) {
        if (GetTickCount64() - requestedAt_ > 10000) throw std::runtime_error("graphics frame thread response timed out");
        return false;
    }
    applied = pending_ == 2; pending_ = 0;
    std::memcpy(&result, bytes.data() + 4, 4);
    hasValues_ = result == 1 || result == 2;
    if (hasValues_) {
        std::memcpy(&lastRaw_, bytes.data() + 0x10, sizeof(lastRaw_));
        values = decodeGraphicsValues(lastRaw_);
    }
    return true;
}
void GraphicsSettings::apply(const GraphicsValues& values) {
    if (!process_ || pending_ || !hasValues_) throw std::runtime_error("graphics bridge is not ready to apply");
    const auto map = buildGraphicsMap(remote_ + GRAPHICS_MAP_OFFSET, imageBase_, values);
    write(process_, remote_ + GRAPHICS_MAP_OFFSET, map.data(), map.size());
    write(process_, remote_ + 0x50, &lastRaw_, sizeof(lastRaw_));
    write(process_, remote_ + GRAPHICS_DISPLAY_REQUEST_OFFSET, &values.fullScreen, sizeof(values.fullScreen));
    request(2);
}
}
