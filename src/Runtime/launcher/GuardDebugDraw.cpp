#include "GuardDebugDraw.h"
#include "RemoteImage.h"
#include <cstring>

namespace t7r {
namespace {
void write(HANDLE process, uint32_t address, const void* value, size_t size) {
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(address)), value, size, &actual)
        || actual != size) throw std::runtime_error(errorText("guard debug publication"));
}
}
void GuardDebugDraw::install(HANDLE process, uint32_t imageBase) {
    if (process_ || !process) throw std::runtime_error("guard debug installation state");
    if (readClientMemory(process, imageBase + GUARD_DEBUG_UI_RVA, GUARD_DEBUG_UI_ORIGINAL.size()) != GUARD_DEBUG_UI_ORIGINAL)
        throw std::runtime_error("guard debug UI camera signature mismatch");
    const auto page = VirtualAllocEx(process, nullptr, GUARD_DEBUG_PAGE_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!page) throw std::runtime_error(errorText("guard debug allocation"));
    bool patchAttempted = false;
    try {
        const auto pointer = reinterpret_cast<uintptr_t>(page);
        if (pointer > UINT32_MAX - GUARD_DEBUG_PAGE_SIZE) throw std::runtime_error("guard debug outside x86 range");
        const auto address = static_cast<uint32_t>(pointer);
        const auto code = buildGuardDebugHook(address, imageBase);
        write(process, address + GUARD_DEBUG_CODE, code.data(), code.size());
        const float identity[]{1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
        write(process, address + 0x80, identity, sizeof(identity));
        DWORD old = 0;
        if (!VirtualProtectEx(process, reinterpret_cast<void*>(pointer + GUARD_DEBUG_CODE), 0x2000, PAGE_EXECUTE_READ, &old)
            || !FlushInstructionCache(process, page, GUARD_DEBUG_PAGE_SIZE))
            throw std::runtime_error(errorText("guard debug code protection"));
        auto jump = GUARD_DEBUG_UI_ORIGINAL;
        jump[0] = 0xE9; jump[5] = 0x90;
        const uint32_t delta = address + GUARD_DEBUG_CODE - (imageBase + GUARD_DEBUG_UI_RVA + 5);
        std::memcpy(jump.data() + 1, &delta, 4);
        patchAttempted = true;
        writeClientMemory(process, imageBase + GUARD_DEBUG_UI_RVA, jump);
        process_ = process; remote_ = address;
    } catch (...) {
        if (patchAttempted) writeClientMemory(process, imageBase + GUARD_DEBUG_UI_RVA, GUARD_DEBUG_UI_ORIGINAL);
        if (!VirtualFreeEx(process, page, 0, MEM_RELEASE)) throw std::runtime_error(errorText("guard debug rollback"));
        throw;
    }
}
void GuardDebugDraw::update(bool enabled, const WorldSnapshot* world) {
    if (!process_) {
        if (enabled) throw std::runtime_error("guard debug renderer not installed");
        return;
    }
    if (world) worldReady_ = world->ready;
    enabled = enabled && worldReady_;
    if (enabled != enabled_) {
        const uint32_t value = enabled ? 1 : 0;
        write(process_, remote_, &value, sizeof(value));
        enabled_ = enabled;
        if (!enabled) { contacts_.clear(); epoch_ = 0; }
    }
    if (!enabled) return;
    const auto bytes = readClientMemory(process_, remote_, sizeof(GuardDebugMailbox));
    GuardDebugMailbox box{}; std::memcpy(&box, bytes.data(), sizeof(box));
    if (box.error) {
        enabled_ = false;
        throw std::runtime_error("guard debug renderer disabled; HRESULT=" + std::to_string(box.error));
    }
    if (!world) return;
    if (epoch_ != world->instanceStartedAt || !world->ready) { contacts_.clear(); epoch_ = world->instanceStartedAt; }
    if (world->ready) for (const auto& event : world->contacts) {
        if (contacts_.size() == 512) contacts_.pop_front();
        contacts_.push_back(event);
    }
    while (!contacts_.empty() && contacts_.front().occurredAt + 300 < world->sampledAt) contacts_.pop_front();
    if (box.pending) return; // The render thread alone consumes this immutable publication.
    const auto vertices = guardDebugVertices(*world, contacts_);
    const uint32_t values[]{static_cast<uint32_t>(vertices.size()), static_cast<uint32_t>(world->sampledAt)};
    write(process_, remote_ + offsetof(GuardDebugMailbox, count), values, sizeof(values));
    if (!vertices.empty()) write(process_, remote_ + GUARD_DEBUG_STAGING, vertices.data(), vertices.size() * sizeof(GuardDebugVertex));
    const uint32_t pending = 1;
    write(process_, remote_ + offsetof(GuardDebugMailbox, pending), &pending, sizeof(pending));
}
void GuardDebugDraw::clear() {
    if (process_ && WaitForSingleObject(process_, 0) != WAIT_OBJECT_0)
        throw std::runtime_error("guard debug cleanup requires terminated owned client");
    process_ = nullptr; remote_ = 0; enabled_ = false; worldReady_ = false; epoch_ = 0; contacts_.clear();
}
}
