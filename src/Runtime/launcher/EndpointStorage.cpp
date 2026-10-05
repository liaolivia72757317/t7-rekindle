#include "EndpointStorage.h"
#include "RemoteImage.h"
#include "ProcessCleanup.h"
#include <cstring>

namespace t7 {
namespace {
constexpr uint32_t PAGE_SIZE = 4096;
class Code {
public:
    Bytes bytes;
    void emit(std::initializer_list<unsigned char> values) { bytes.insert(bytes.end(), values); }
    void word(uint32_t value) {
        for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<unsigned char>(value >> shift));
    }
    size_t branch(unsigned char condition = 0) {
        if (condition) emit({0x0F, condition}); else emit({0xE9});
        const auto field = bytes.size(); word(0); return field;
    }
    void bind(size_t field) {
        const auto displacement = static_cast<uint32_t>(bytes.size() - field - 4);
        std::memcpy(bytes.data() + field, &displacement, 4);
    }
    void call(uint32_t function) { emit({0xB8}); word(function); emit({0xFF, 0xD0}); }
};
Bytes storageCode(uint32_t result, const EndpointAllocator& allocator,
                  const std::array<uint32_t, 7>& sizes, bool allocate) {
    Code code;
    std::vector<size_t> failures;
    size_t success = 0;
    if (allocate) {
        for (size_t i = 0; i < sizes.size(); ++i) {
            if (!sizes[i]) continue;
            code.emit({0x68}); code.word(sizes[i]);
            if (i < 3) {
                code.emit({0xB9}); code.word(allocator.object);
                code.call(allocator.vectorAllocate); // thiscall, including allocator accounting
            } else {
                code.call(allocator.stringAllocate);
                code.emit({0x83, 0xC4, 0x04}); // cdecl operator new
            }
            code.emit({0x85, 0xC0});
            if (i < 3) {
                const auto allocated = code.branch(0x85);
                // The client counts the request before _aligned_malloc, including null results.
                code.emit({0x68}); code.word(sizes[i]); code.emit({0x6A, 0x00, 0xB9}); code.word(allocator.object);
                code.call(allocator.vectorFree);
                failures.push_back(code.branch()); code.bind(allocated);
            } else failures.push_back(code.branch(0x84));
            code.emit({0xA3}); code.word(result + static_cast<uint32_t>(i) * 4);
        }
        code.emit({0x33, 0xC0}); success = code.branch();
        for (auto field : failures) code.bind(field);
    }
    // Reverse allocation order; null results also cover partial allocation failure.
    for (size_t i = sizes.size(); i-- > 0;) {
        const auto slot = result + static_cast<uint32_t>(i) * 4;
        code.emit({0xA1}); code.word(slot);
        code.emit({0x85, 0xC0}); const auto empty = code.branch(0x84);
        if (i < 3) { code.emit({0x68}); code.word(sizes[i]); }
        code.emit({0x50});
        if (i < 3) {
            code.emit({0xB9}); code.word(allocator.object); code.call(allocator.vectorFree);
        } else {
            code.call(allocator.stringFree); code.emit({0x83, 0xC4, 0x04});
        }
        code.emit({0xC7, 0x05}); code.word(slot); code.word(0);
        code.bind(empty);
    }
    code.emit({0xB8}); code.word(allocate ? 1u : 0u);
    if (allocate) code.bind(success);
    code.emit({0xC2, 0x04, 0x00});
    return code.bytes;
}
EndpointAddresses runStorageWorker(HANDLE process, const EndpointAllocator& allocator,
                                  const std::array<uint32_t, 7>& sizes, const EndpointAddresses& input, bool allocate) {
    BOOL wow64 = FALSE;
    if (!IsWow64Process(process, &wow64) || !wow64)
        throw std::runtime_error("endpoint allocator requires an x86 client");
    if (!allocator.object || !allocator.vectorAllocate || !allocator.vectorFree
        || !allocator.stringAllocate || !allocator.stringFree)
        throw std::runtime_error("endpoint allocator is not initialized");
    auto storage = VirtualAllocEx(process, nullptr, 2 * PAGE_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!storage) throw std::runtime_error(errorText("endpoint worker allocation"));
    HANDLE thread = nullptr;
    bool started = false, finished = false;
    const auto cleanup = [&] {
        if (thread) {
            const auto handle = thread; thread = nullptr;
            if (!CloseHandle(handle)) throw std::runtime_error(errorText("endpoint worker handle cleanup"));
        }
        // Never reclaim instructions from a worker whose termination is unconfirmed.
        if (storage && (!started || finished)) {
            const auto state = WaitForSingleObject(process, 0);
            if (state == WAIT_FAILED) throw std::runtime_error(errorText("endpoint worker cleanup status"));
            if (state != WAIT_OBJECT_0 && !VirtualFreeEx(process, storage, 0, MEM_RELEASE))
                throw std::runtime_error(errorText("endpoint worker memory cleanup"));
            storage = nullptr;
        }
    };
    EndpointAddresses output{};
    DWORD exitCode = 0;
    try {
        const auto address = reinterpret_cast<uintptr_t>(storage);
        if (address > UINT32_MAX - 2 * PAGE_SIZE) throw std::runtime_error("endpoint worker outside x86 address range");
        const auto result = static_cast<uint32_t>(address + PAGE_SIZE);
        const auto code = storageCode(result, allocator, sizes, allocate);
        if (code.size() > PAGE_SIZE) throw std::runtime_error("endpoint worker code exceeds page");
        writeClientMemory(process, address, code);
        const auto bytes = reinterpret_cast<const unsigned char*>(&input);
        writeClientMemory(process, result, Bytes(bytes, bytes + sizeof(input)));
        DWORD old = 0;
        if (!VirtualProtectEx(process, storage, PAGE_SIZE, PAGE_EXECUTE_READ, &old)
            || !FlushInstructionCache(process, storage, code.size()))
            throw std::runtime_error(errorText("endpoint worker code protection"));
        thread = CreateRemoteThread(process, nullptr, 0, reinterpret_cast<LPTHREAD_START_ROUTINE>(storage), nullptr, 0, nullptr);
        if (!thread) throw std::runtime_error(errorText("endpoint worker creation"));
        started = true;
        const auto state = WaitForSingleObject(thread, 10000);
        if (state == WAIT_FAILED) throw std::runtime_error(errorText("endpoint worker wait"));
        if (state != WAIT_OBJECT_0) throw std::runtime_error("endpoint worker did not finish; owned client must stop");
        finished = true;
        if (!GetExitCodeThread(thread, &exitCode)) throw std::runtime_error(errorText("endpoint worker exit status"));
        if (exitCode > 1) throw std::runtime_error("endpoint worker failed; code=" + std::to_string(exitCode));
        const auto returned = readClientMemory(process, result, sizeof(output));
        std::memcpy(&output, returned.data(), sizeof(output));
    } catch (...) { cleanupAndRethrow(std::current_exception(), cleanup); }
    cleanup();
    if (exitCode) throw std::runtime_error("client endpoint allocation failed; partial allocations released");
    return output;
}
uint32_t readWord(HANDLE process, uintptr_t address) {
    const auto bytes = readClientMemory(process, address, 4);
    uint32_t value = 0; std::memcpy(&value, bytes.data(), 4); return value;
}
}

EndpointAllocator clientEndpointAllocator(HANDLE process, uint32_t imageBase) {
    constexpr uint32_t OBJECT_RVA = 0x01C8E2BC, VTABLE_RVA = 0x0173432C;
    constexpr uint32_t ALLOCATE_RVA = 0x0007F990, FREE_RVA = 0x0007F9D0;
    if (!imageBase || imageBase > UINT32_MAX - OBJECT_RVA - 16)
        throw std::runtime_error("endpoint allocator image outside x86 range");
    const auto object = imageBase + OBJECT_RVA, vtable = imageBase + VTABLE_RVA;
    if (readWord(process, object) != vtable || readWord(process, object + 4) != 16
        || readWord(process, vtable + 4) != imageBase + ALLOCATE_RVA
        || readWord(process, vtable + 8) != imageBase + FREE_RVA)
        throw std::runtime_error("client endpoint allocator identity mismatch");
    return {object, imageBase + ALLOCATE_RVA, imageBase + FREE_RVA,
            readWord(process, imageBase + 0x016F67F8), readWord(process, imageBase + 0x016F67F4)};
}
EndpointAddresses allocateEndpointStorage(HANDLE process, const EndpointAllocator& allocator,
                                         const Config& config, unsigned selectorParity) {
    return runStorageWorker(process, allocator, endpointAllocationSizes(config, selectorParity), {}, true);
}
void freeEndpointStorage(HANDLE process, const EndpointAllocator& allocator, const EndpointAddresses& addresses) {
    if (WaitForSingleObject(process, 0) == WAIT_OBJECT_0) return;
    runStorageWorker(process, allocator, ENDPOINT_BLOCK_SIZES, addresses, false);
}
}
