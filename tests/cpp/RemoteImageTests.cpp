#include "../../src/Runtime/launcher/RemoteImage.h"
#include <iostream>

namespace {
void require(bool result, const char* message) { if (!result) throw std::runtime_error(message); }
}
bool verifyRemoteImage() {
    void* page = nullptr;
    try {
        page = VirtualAlloc(nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        require(page != nullptr, "test page allocation");
        const auto address = reinterpret_cast<uintptr_t>(page);
        const auto process = GetCurrentProcess(); DWORD old = 0;
        require(VirtualProtect(page, 4096, PAGE_READONLY, &old), "test page protection");
        t7::applyRemotePatches(process, address, {{1, {0,0}, {0x33,0xC0}}, {3, {0}, {0xC3}}});
        require(t7::readClientMemory(process, address, 5) == t7::Bytes({0,0x33,0xC0,0xC3,0}), "remote patch roundtrip");
        MEMORY_BASIC_INFORMATION region{};
        require(VirtualQuery(page, &region, sizeof(region)) == sizeof(region)
                && region.Protect == PAGE_READONLY, "remote patch protection not restored");
        bool rejected = false;
        try { t7::applyRemotePatches(process, address, {{1, {0x33}, {1}}, {2, {0xFF}, {1}}}); }
        catch (const std::runtime_error&) { rejected = true; }
        require(rejected && t7::readClientMemory(process, address + 1, 1) == t7::Bytes({0x33}), "signature mismatch partially published");
        rejected = false;
        try { t7::writeClientMemory(process, address + 4095, {1,2}); }
        catch (const std::runtime_error&) { rejected = true; }
        require(rejected, "cross-region write accepted");
        rejected = false;
        try { t7::installClientImage(nullptr, nullptr, {}, [] { return true; }); }
        catch (const std::runtime_error& error) { rejected = std::string(error.what()).find("cancelled") != std::string::npos; }
        require(rejected, "early installation cancellation");
        require(VirtualFree(page, 0, MEM_RELEASE), "test page release"); page = nullptr;
        std::cout << "Remote image memory readback, protection, rejection and cancellation cases passed\n";
        return true;
    } catch (const std::exception& error) {
        if (page && !VirtualFree(page, 0, MEM_RELEASE)) std::cerr << "Remote image test page cleanup failed\n";
        std::cerr << "Remote image test failure: " << error.what() << '\n'; return false;
    }
}
