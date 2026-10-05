#include "RemoteImage.h"
#include "ProcessCleanup.h"
#include <algorithm>
#include <cstring>

namespace t7 {
namespace {
void checkCancelled(const std::function<bool()>& cancelled) {
    if (cancelled && cancelled()) throw std::runtime_error("client memory installation cancelled");
}
void writeChecked(HANDLE process, uintptr_t address, const unsigned char* source, size_t size) {
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(address), source, size, &actual) || actual != size)
        throw std::runtime_error(errorText("client memory write"));
    const auto checked = readClientMemory(process, address, size);
    if (!std::equal(checked.begin(), checked.end(), source)) throw std::runtime_error("client memory readback mismatch");
}
}
Bytes readClientMemory(HANDLE process, uintptr_t address, size_t size) {
    if (!size || address > UINTPTR_MAX - size) throw std::runtime_error("invalid client memory read extent");
    Bytes result(size); SIZE_T actual = 0;
    if (!ReadProcessMemory(process, reinterpret_cast<void*>(address), result.data(), size, &actual) || actual != size)
        throw std::runtime_error(errorText("client memory read"));
    return result;
}
void writeClientMemory(HANDLE process, uintptr_t address, const Bytes& bytes) {
    MEMORY_BASIC_INFORMATION region{};
    if (bytes.empty() || address > UINTPTR_MAX - bytes.size()
        || VirtualQueryEx(process, reinterpret_cast<void*>(address), &region, sizeof(region)) != sizeof(region)
        || region.State != MEM_COMMIT || bytes.size() > region.RegionSize
        || address - reinterpret_cast<uintptr_t>(region.BaseAddress) > region.RegionSize - bytes.size())
        throw std::runtime_error("client patch crosses an unavailable memory region");
    auto pointer = reinterpret_cast<void*>(address); DWORD old = 0, ignored = 0;
    if (!VirtualProtectEx(process, pointer, bytes.size(), PAGE_EXECUTE_READWRITE, &old))
        throw std::runtime_error(errorText("client patch page protection"));
    try { writeChecked(process, address, bytes.data(), bytes.size()); }
    catch (...) {
        cleanupAndRethrow(std::current_exception(), [&] {
            if (!VirtualProtectEx(process, pointer, bytes.size(), old, &ignored))
                throw std::runtime_error(errorText("client patch failed-write protection restore"));
        });
    }
    if (!VirtualProtectEx(process, pointer, bytes.size(), old, &ignored)
        || !FlushInstructionCache(process, pointer, bytes.size()))
        throw std::runtime_error(errorText("client patch page protection/cache restore"));
}
void applyRemotePatches(HANDLE process, uintptr_t base, const std::vector<MemoryPatch>& patches) {
    for (const auto& patch : patches) {
        if (patch.expected.empty() || patch.expected.size() != patch.replacement.size()
            || base > UINTPTR_MAX - patch.rva
            || readClientMemory(process, base + patch.rva, patch.expected.size()) != patch.expected)
            throw std::runtime_error("remote client patch signature mismatch at RVA=" + std::to_string(patch.rva));
    }
    for (const auto& patch : patches) writeClientMemory(process, base + patch.rva, patch.replacement);
}
void installClientImage(HANDLE process, HANDLE thread, const ClientImage& image,
                        const std::function<bool()>& cancelled) {
    checkCancelled(cancelled);
    BOOL wow64 = FALSE;
    if (!IsWow64Process(process, &wow64) || !wow64) throw std::runtime_error("client must be an x86 WOW64 process");
    WOW64_CONTEXT context{}; context.ContextFlags = WOW64_CONTEXT_INTEGER | WOW64_CONTEXT_CONTROL;
    if (!Wow64GetThreadContext(thread, &context)) throw std::runtime_error(errorText("client startup context"));
    uint32_t mappedBase = 0;
    const auto pebBase = readClientMemory(process, static_cast<uintptr_t>(context.Ebx) + 8, sizeof(mappedBase));
    std::memcpy(&mappedBase, pebBase.data(), sizeof(mappedBase));
    if (mappedBase != image.imageBase || context.Eax != image.imageBase + image.originalEntryRva
        || image.bytes.size() != 0x03B54000 || image.imageBase != 0x00400000)
        throw std::runtime_error("client startup context does not match the supported baseline");
    const auto originalHeader = readClientMemory(process, mappedBase, 0x400);
    IMAGE_DOS_HEADER dos{}; std::memcpy(&dos, originalHeader.data(), sizeof(dos));
    if (dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew < 0
        || static_cast<size_t>(dos.e_lfanew) > originalHeader.size() - sizeof(IMAGE_NT_HEADERS32))
        throw std::runtime_error("client mapped DOS header mismatch");
    IMAGE_NT_HEADERS32 nt{}; std::memcpy(&nt, originalHeader.data() + dos.e_lfanew, sizeof(nt));
    if (nt.Signature != IMAGE_NT_SIGNATURE || nt.OptionalHeader.AddressOfEntryPoint != image.originalEntryRva
        || nt.OptionalHeader.SizeOfImage != image.bytes.size())
        throw std::runtime_error("client mapped NT header mismatch");
    uint32_t end = 0;
    for (const auto& region : image.regions) {
        if (region.rva != end || !region.size || region.size % 0x1000
            || region.rva > image.bytes.size() || region.size > image.bytes.size() - region.rva)
            throw std::runtime_error("invalid prepared client memory region");
        end += region.size;
    }
    if (end != image.bytes.size()) throw std::runtime_error("incomplete client memory regions");
    for (const auto& region : image.regions) {
        checkCancelled(cancelled);
        auto pointer = reinterpret_cast<void*>(static_cast<uintptr_t>(image.imageBase) + region.rva);
        DWORD previous = 0;
        if (!VirtualProtectEx(process, pointer, region.size, PAGE_READWRITE, &previous))
            throw std::runtime_error(errorText("client image page preparation"));
        writeChecked(process, reinterpret_cast<uintptr_t>(pointer), image.bytes.data() + region.rva, region.size);
        if (!VirtualProtectEx(process, pointer, region.size, region.protection, &previous))
            throw std::runtime_error(errorText("client image final page protection"));
    }
    if (!FlushInstructionCache(process, reinterpret_cast<void*>(static_cast<uintptr_t>(image.imageBase)), image.bytes.size()))
        throw std::runtime_error(errorText("client image instruction flush"));
    checkCancelled(cancelled);
    context.Eax = image.imageBase + image.entryRva;
    if (!Wow64SetThreadContext(thread, &context)) throw std::runtime_error(errorText("client startup entry update"));
    WOW64_CONTEXT checked{}; checked.ContextFlags = WOW64_CONTEXT_INTEGER | WOW64_CONTEXT_CONTROL;
    if (!Wow64GetThreadContext(thread, &checked) || checked.Eax != context.Eax || checked.Eip != context.Eip
        || checked.Esp != context.Esp || checked.Ebx != context.Ebx)
        throw std::runtime_error("client startup context readback mismatch");
}
}
