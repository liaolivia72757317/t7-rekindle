#include "OutputDevice.h"
#include <d3d9.h>
#include <cstring>
#include <memory>

namespace t7 {
namespace {
constexpr uint32_t RENDERER_INITIALIZE_RVA = 0x2364E0, RESOLUTION_ADAPTER_RVA = 0x33B444;
void put32(Bytes& code, uint32_t value) {
    for (int i = 0; i < 4; ++i) code.push_back(static_cast<unsigned char>(value >> (i * 8)));
}
void jump(Bytes& code, uint32_t address, uint32_t target) {
    code.push_back(0xE9); put32(code, target - (address + static_cast<uint32_t>(code.size()) + 4));
}
void read(HANDLE process, uintptr_t address, void* data, size_t size) {
    SIZE_T done = 0;
    if (!ReadProcessMemory(process, reinterpret_cast<void*>(address), data, size, &done) || done != size)
        throw std::runtime_error(errorText("read output device"));
}
void write(HANDLE process, uintptr_t address, const Bytes& bytes) {
    SIZE_T done = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(address), bytes.data(), bytes.size(), &done) || done != bytes.size())
        throw std::runtime_error(errorText("write output device hook"));
}
}

OutputDevice resolveOutputDevice(const GUID& identifier) {
    auto release = [](IDirect3D9* value) { if (value) value->Release(); };
    std::unique_ptr<IDirect3D9, decltype(release)> d3d(Direct3DCreate9(D3D_SDK_VERSION), release);
    if (!d3d) throw std::runtime_error("host D3D9 unavailable");
    OutputDevice result; const GUID empty{};
    result.selected = std::memcmp(&identifier, &empty, sizeof(GUID)) != 0;
    bool found = false;
    for (UINT index = 0; index < d3d->GetAdapterCount(); ++index) {
        D3DADAPTER_IDENTIFIER9 adapter{};
        if (FAILED(d3d->GetAdapterIdentifier(index, 0, &adapter))) throw std::runtime_error("output device identification failed");
        if ((result.selected && IsEqualGUID(adapter.DeviceIdentifier, identifier)) || (!result.selected && index == 0)) {
            result.ordinal = index; result.identifier = adapter.DeviceIdentifier; result.name = adapter.Description;
            found = true; break;
        }
    }
    if (!found) throw std::runtime_error("selected output device not found; select an available device");
    D3DCAPS9 caps{};
    if (FAILED(d3d->GetDeviceCaps(result.ordinal, D3DDEVTYPE_HAL, &caps))
        || caps.PixelShaderVersion < D3DPS_VERSION(3,0) || caps.VertexShaderVersion < D3DVS_VERSION(3,0))
        throw std::runtime_error("selected output device D3D9 capability gate failed");
    return result;
}

OutputDeviceHooks buildOutputDeviceHooks(uint32_t remoteBase, uint32_t imageBase, uint32_t ordinal) {
    OutputDeviceHooks result;
    // Preserve the original prologue, replacing only the adapter argument before renderer initialization.
    result.code = {0x55, 0x8B, 0xEC, 0xC7, 0x45, 0x1C}; put32(result.code, ordinal);
    result.code.insert(result.code.end(), {0x8B, 0x45, 0x08});
    jump(result.code, remoteBase, imageBase + RENDERER_INITIALIZE_RVA + 6);
    result.code.resize(32, 0xCC);
    result.code.insert(result.code.end(), {0x8B, 0xD0, 0xB9}); put32(result.code, ordinal);
    result.code.insert(result.code.end(), {0x8B, 0x3A});
    jump(result.code, remoteBase, imageBase + RESOLUTION_ADAPTER_RVA + 6);
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {RENDERER_INITIALIZE_RVA, {0x55,0x8B,0xEC,0x8B,0x45,0x08}},
        {RESOLUTION_ADAPTER_RVA, {0x8B,0xD0,0x33,0xC9,0x8B,0x3A}}}) {
        Bytes replacement;
        jump(replacement, imageBase + entry.first, remoteBase + (entry.first == RENDERER_INITIALIZE_RVA ? 0 : 32));
        replacement.resize(entry.second.size(), 0x90);
        result.patches.push_back({entry.first, entry.second, std::move(replacement)});
    }
    return result;
}

void installOutputDevice(HANDLE process, uint32_t imageBase, const OutputDevice& device) {
    if (!device.selected) return;
    auto remote = VirtualAllocEx(process, nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) throw std::runtime_error(errorText("allocate output device hook"));
    try {
        const auto address = reinterpret_cast<uintptr_t>(remote);
        if (address > UINT32_MAX - 4096) throw std::runtime_error("output device hook exceeds x86 address space");
        const auto hooks = buildOutputDeviceHooks(static_cast<uint32_t>(address), imageBase, device.ordinal);
        for (const auto& patch : hooks.patches) {
            Bytes original(patch.expected.size()); read(process, imageBase + patch.rva, original.data(), original.size());
            if (original != patch.expected) throw std::runtime_error("output device hook signature mismatch");
        }
        write(process, address, hooks.code);
        DWORD old = 0;
        if (!VirtualProtectEx(process, remote, 4096, PAGE_EXECUTE_READ, &old)
            || !FlushInstructionCache(process, remote, hooks.code.size())) throw std::runtime_error(errorText("protect output device hook"));
        // Installed while the owned client's main thread is still suspended.
        for (const auto& patch : hooks.patches) {
            auto target = reinterpret_cast<void*>(static_cast<uintptr_t>(imageBase + patch.rva));
            if (!VirtualProtectEx(process, target, patch.replacement.size(), PAGE_EXECUTE_READWRITE, &old))
                throw std::runtime_error(errorText("unprotect output device entry"));
            write(process, imageBase + patch.rva, patch.replacement);
            DWORD ignored = 0;
            if (!VirtualProtectEx(process, target, patch.replacement.size(), old, &ignored)
                || !FlushInstructionCache(process, target, patch.replacement.size()))
                throw std::runtime_error(errorText("protect output device entry"));
        }
    } catch (...) {
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        throw;
    }
}

void verifyOutputDevice(HANDLE process, uint32_t imageBase, const OutputDevice& device) {
    uint32_t renderer = 0, vtable = 0, ordinal = 0, adapter = 0, renderDevice = 0;
    read(process, imageBase + 0x2D20008, &renderer, 4);
    if (!renderer) throw std::runtime_error("output device renderer is not ready");
    read(process, renderer, &vtable, 4);
    read(process, renderer + 0x11C, &renderDevice, 4);
    if (vtable != imageBase + 0x175B188 || !renderDevice) throw std::runtime_error("output device renderer identity mismatch");
    read(process, renderer + 0x458, &ordinal, 4);
    read(process, renderer + 0x468, &adapter, 4);
    GUID identifier{};
    if (!adapter) throw std::runtime_error("output device adapter is not ready");
    read(process, adapter + 0x43C, &identifier, sizeof(identifier));
    if (ordinal != device.ordinal || !IsEqualGUID(identifier, device.identifier))
        throw std::runtime_error("client output device differs from the selected adapter");
}
}
