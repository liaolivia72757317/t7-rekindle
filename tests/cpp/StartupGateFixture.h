#pragma once
#include "../../src/Runtime/launcher/RemoteImage.h"

namespace startupFixture {
constexpr uint32_t BASE = 0x400000, CONTROL = BASE + 0x3000, NETWORK = BASE + 0x3100;
constexpr uint32_t SELECTOR = BASE + 0x892B0, SERVICE = BASE + 0x24396CC, VTABLE = BASE + 0x1734E64;
enum Control : uint32_t {
    CodePage = 0, DialogCalls = 4, Selections = 8, Exit = 12, Delay = 16, InitializationSuccess = 20,
    StackValid = 24, InitializationResult = 28, RegisterValid = 32, Peer = 36, PeerReady = 40,
    AllocationFailure = 44, Error = 48, StackBefore = 52
};
void require(bool condition, const char* message);
uint32_t readWord(HANDLE process, uint32_t address);
void writeWord(HANDLE process, uint32_t address, uint32_t value);
t7::MemoryPatch initializationPatch();
t7::fs::path create(const t7::fs::path& root, const std::string& name, const std::string& codePage = {});
}
