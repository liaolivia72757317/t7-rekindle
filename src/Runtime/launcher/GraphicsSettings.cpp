#include "GraphicsSettings.h"
#include "RemoteImage.h"
#include "RoundEndHookCode.h"
#include "MovementAnimationHookCode.h"
#include "MovementCommandHookCode.h"
#include "SprintHookCode.h"
#include <algorithm>
#include <cstring>

namespace t7 {
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
                               const MovementAnimationProfile& animation, bool customLobby, const fs::path& audioDirectory) {
    if (process_ || !process) throw std::runtime_error("graphics bridge installation state");
    if (readClientMemory(process, imageBase + GRAPHICS_TICK_RVA, GRAPHICS_TICK_ORIGINAL.size()) != GRAPHICS_TICK_ORIGINAL
        || readClientMemory(process, imageBase + 0x6EA040, 5) != Bytes{0x55, 0x8B, 0xEC, 0x6A, 0xFF})
        throw std::runtime_error("graphics frame/apply signature mismatch");
    for (auto rva : {AUDIO_MUSIC_RVA, AUDIO_EFFECTS_RVA})
        if (readClientMemory(process, imageBase + rva, 6) != Bytes{0x55, 0x8B, 0xEC, 0x8A, 0x45, 0x08})
            throw std::runtime_error("audio setter signature mismatch");
    verifyWorldFunctions(process, imageBase);
    if (customLobby) {
        if (readClientMemory(process, imageBase + NETWORK_HUD_RVA, NETWORK_HUD_ORIGINAL.size()) != NETWORK_HUD_ORIGINAL)
            throw std::runtime_error("network HUD signature mismatch");
        for (const auto& site : LOBBY_IDENTITY_SITES)
            if (readClientMemory(process, imageBase + site.first, 10)
                    != Bytes{0x8B,0x17,0x8B,0xCF,0x8B,0xF0,0xFF,0x52,0x28,0x50})
                throw std::runtime_error("custom lobby identity signature mismatch");
    }
    if (animation.enabled) verifyMovementAnimationFunction(process, imageBase);
    const auto audioRoot = buildAudioRoot(audioDirectory);
    Bytes originalAudioRoot{0x68};
    for (uint32_t value : {imageBase + 0x17799F0, imageBase + 0x16F6080}) {
        for (unsigned shift = 0; shift < 32; shift += 8)
            originalAudioRoot.push_back(static_cast<unsigned char>(value >> shift));
        if (originalAudioRoot.size() == 5) originalAudioRoot.insert(originalAudioRoot.end(), {0xFF, 0x15});
    }
    if (!audioRoot.empty() && readClientMemory(process, imageBase + AUDIO_ROOT_INIT_RVA, originalAudioRoot.size()) != originalAudioRoot)
        throw std::runtime_error("audio directory initialization signature mismatch");
    constexpr auto worldOffset = GRAPHICS_PAGE_SIZE + AUDIO_PAGE_SIZE;
    constexpr auto size = worldOffset + WORLD_PAGE_SIZE;
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
    try {
        if (pointer > UINT32_MAX - size) throw std::runtime_error("settings mailbox outside x86 range");
        const auto base = static_cast<uint32_t>(pointer);
        auto bytes = buildGraphicsHook(base, imageBase, threadId, base + GRAPHICS_PAGE_SIZE + AUDIO_CODE_OFFSET,
                                       base + worldOffset + WORLD_CODE_OFFSET);
        const auto audioBytes = buildAudioHook(base + GRAPHICS_PAGE_SIZE, imageBase, threadId, !audioRoot.empty());
        bytes.insert(bytes.end(), audioBytes.begin(), audioBytes.end());
        std::copy(audioRoot.begin(), audioRoot.end(), bytes.begin() + GRAPHICS_PAGE_SIZE + AUDIO_ROOT_OFFSET);
        const auto worldBytes = buildWorldHook(base + worldOffset, imageBase, threadId, dash, animation, customLobby);
        bytes.insert(bytes.end(), worldBytes.begin(), worldBytes.end());
        write(process, base, bytes.data(), bytes.size());
        if (customLobby) {
            const NetworkLatencyMailbox initial{};
            write(process, base + worldOffset + NETWORK_LATENCY_DATA_OFFSET, &initial, sizeof(initial));
        }
        DWORD previous = 0;
        if (!VirtualProtectEx(process, reinterpret_cast<void*>(pointer + GRAPHICS_CODE_OFFSET), 0x1000, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + GRAPHICS_PAGE_SIZE + AUDIO_CODE_OFFSET), 0x1000, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + worldOffset + WORLD_CODE_OFFSET), ACTORS_DATA_OFFSET - WORLD_CODE_OFFSET, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + worldOffset + CONTACT_CODE_OFFSET), LIVE_ACTORS_OFFSET - CONTACT_CODE_OFFSET, PAGE_EXECUTE_READ, &previous)
            || !FlushInstructionCache(process, remote, bytes.size()))
            throw std::runtime_error(errorText("graphics code protection/cache"));
        Bytes jump(7, 0x90); jump[0] = 0xE9;
        const uint32_t delta = base + GRAPHICS_CODE_OFFSET - (imageBase + GRAPHICS_TICK_RVA + 5);
        std::memcpy(jump.data() + 1, &delta, 4);
        roundEndPatched = true;
        for (const auto& entry : roundEndJumps(base + worldOffset, imageBase, customLobby))
            writeClientMemory(process, imageBase + entry.first, entry.second);
        if (animation.enabled) {
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
        std::vector<MemoryPatch> patches{{GRAPHICS_TICK_RVA, GRAPHICS_TICK_ORIGINAL, jump}};
        if (!audioRoot.empty()) {
            auto replacement = originalAudioRoot;
            const auto path = base + GRAPHICS_PAGE_SIZE + AUDIO_ROOT_OFFSET;
            std::memcpy(replacement.data() + 1, &path, sizeof(path));
            patches.push_back({AUDIO_ROOT_INIT_RVA, originalAudioRoot, replacement});
        }
        applyRemotePatches(process, imageBase, patches); patched = true;
        process_ = process; remote_ = base; imageBase_ = imageBase;
        customLobby_ = customLobby; playerId_ = 0;
        serverLatencyMs_ = UINT32_MAX;
        audio_.attach(process, base + GRAPHICS_PAGE_SIZE, imageBase, !audioRoot.empty());
        world_.attach(process, base + worldOffset);
    } catch (...) {
        // install() runs before the first ResumeThread, so rollback cannot race execution.
        if (patched) {
            writeClientMemory(process, imageBase + GRAPHICS_TICK_RVA, GRAPHICS_TICK_ORIGINAL);
            if (!audioRoot.empty()) writeClientMemory(process, imageBase + AUDIO_ROOT_INIT_RVA, originalAudioRoot);
        }
        if (animationPatched) writeClientMemory(process, imageBase + MOVEMENT_ANIMATION_RVA, MOVEMENT_ANIMATION_ORIGINAL);
        if (posePatched) writeClientMemory(process, imageBase + MOVEMENT_POSE_RVA, MOVEMENT_POSE_ORIGINAL);
        if (defenseExitPatched) writeClientMemory(process, imageBase + DEFENSE_EXIT_RVA, DEFENSE_EXIT_ORIGINAL);
        if (jumpPhysicsPatched) writeClientMemory(process, imageBase + JUMP_PHYSICS_RVA, JUMP_PHYSICS_ORIGINAL);
        if (movementCommandPatched) writeClientMemory(process, imageBase + MOVEMENT_COMMAND_RVA, MOVEMENT_COMMAND_ORIGINAL);
        if (roundEndPatched)
            for (const auto& entry : roundEndOriginals(imageBase, customLobby))
                writeClientMemory(process, imageBase + entry.first, entry.second);
        if (!VirtualFreeEx(process, remote, 0, MEM_RELEASE)) throw std::runtime_error(errorText("graphics allocation rollback"));
        throw;
    }
}
void GraphicsSettings::clear() {
    if (process_ && WaitForSingleObject(process_, 0) != WAIT_OBJECT_0)
        throw std::runtime_error("graphics bridge cleanup requires terminated owned client");
    audio_.clear();
    world_.clear();
    process_ = nullptr; remote_ = imageBase_ = pending_ = 0; requestedAt_ = 0; hasValues_ = false; lastRaw_ = {};
    customLobby_ = false; playerId_ = 0;
    serverLatencyMs_ = UINT32_MAX;
}
void GraphicsSettings::publishServerLatency(uint32_t milliseconds) {
    if (!customLobby_ || milliseconds == serverLatencyMs_) return;
    if (milliseconds != UINT32_MAX && milliseconds > 60000)
        throw std::runtime_error("invalid server latency");
    if (!process_) throw std::runtime_error("network HUD is not attached");
    write(process_, remote_ + GRAPHICS_PAGE_SIZE + AUDIO_PAGE_SIZE + NETWORK_LATENCY_DATA_OFFSET,
          &milliseconds, sizeof(milliseconds));
    serverLatencyMs_ = milliseconds;
}
void GraphicsSettings::publishPlayerId(uint32_t playerId) {
    if (!customLobby_ || !playerId || playerId == playerId_) return;
    if (!process_ || (playerId_ && playerId_ != playerId))
        throw std::runtime_error("custom lobby identity cannot change");
    write(process_, remote_ + GRAPHICS_PAGE_SIZE + AUDIO_PAGE_SIZE + LOBBY_IDENTITY_DATA_OFFSET,
          &playerId, sizeof(playerId));
    playerId_ = playerId;
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
