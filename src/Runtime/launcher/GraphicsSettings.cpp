#include "GraphicsSettings.h"
#include "RemoteImage.h"
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
void GraphicsSettings::install(HANDLE process, uint32_t imageBase, DWORD threadId) {
    if (process_ || !process) throw std::runtime_error("graphics bridge installation state");
    if (readClientMemory(process, imageBase + GRAPHICS_TICK_RVA, GRAPHICS_TICK_ORIGINAL.size()) != GRAPHICS_TICK_ORIGINAL
        || readClientMemory(process, imageBase + 0x6EA040, 5) != Bytes{0x55, 0x8B, 0xEC, 0x6A, 0xFF})
        throw std::runtime_error("graphics frame/apply signature mismatch");
    for (auto rva : {AUDIO_MUSIC_RVA, AUDIO_EFFECTS_RVA})
        if (readClientMemory(process, imageBase + rva, 6) != Bytes{0x55, 0x8B, 0xEC, 0x8A, 0x45, 0x08})
            throw std::runtime_error("audio setter signature mismatch");
    constexpr auto size = GRAPHICS_PAGE_SIZE + AUDIO_PAGE_SIZE;
    const auto remote = VirtualAllocEx(process, nullptr, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) throw std::runtime_error(errorText("graphics mailbox allocation"));
    const auto pointer = reinterpret_cast<uintptr_t>(remote);
    bool patched = false;
    try {
        if (pointer > UINT32_MAX - size) throw std::runtime_error("settings mailbox outside x86 range");
        const auto base = static_cast<uint32_t>(pointer);
        auto bytes = buildGraphicsHook(base, imageBase, threadId, base + GRAPHICS_PAGE_SIZE + AUDIO_CODE_OFFSET);
        const auto audioBytes = buildAudioHook(base + GRAPHICS_PAGE_SIZE, imageBase, threadId);
        bytes.insert(bytes.end(), audioBytes.begin(), audioBytes.end());
        write(process, base, bytes.data(), bytes.size());
        DWORD previous = 0;
        if (!VirtualProtectEx(process, reinterpret_cast<void*>(pointer + GRAPHICS_CODE_OFFSET), 0x1000, PAGE_EXECUTE_READ, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + GRAPHICS_PAGE_SIZE + AUDIO_CODE_OFFSET), 0x1000, PAGE_EXECUTE_READ, &previous)
            || !FlushInstructionCache(process, remote, bytes.size()))
            throw std::runtime_error(errorText("graphics code protection/cache"));
        Bytes jump(7, 0x90); jump[0] = 0xE9;
        const uint32_t delta = base + GRAPHICS_CODE_OFFSET - (imageBase + GRAPHICS_TICK_RVA + 5);
        std::memcpy(jump.data() + 1, &delta, 4);
        patched = true; writeClientMemory(process, imageBase + GRAPHICS_TICK_RVA, jump);
        process_ = process; remote_ = base; imageBase_ = imageBase;
        audio_.attach(process, base + GRAPHICS_PAGE_SIZE, imageBase);
    } catch (...) {
        // install() runs before the first ResumeThread, so rollback cannot race execution.
        if (patched) writeClientMemory(process, imageBase + GRAPHICS_TICK_RVA, GRAPHICS_TICK_ORIGINAL);
        if (!VirtualFreeEx(process, remote, 0, MEM_RELEASE)) throw std::runtime_error(errorText("graphics allocation rollback"));
        throw;
    }
}
void GraphicsSettings::clear() {
    if (process_ && WaitForSingleObject(process_, 0) != WAIT_OBJECT_0)
        throw std::runtime_error("graphics bridge cleanup requires terminated owned client");
    audio_.clear();
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
