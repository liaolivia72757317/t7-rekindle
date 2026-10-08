#pragma once
#include "ClientAdaptation.h"

namespace t7 {
struct OutputDevice {
    uint32_t ordinal = 0;
    GUID identifier{};
    std::string name;
    bool selected = false;
};
struct OutputDeviceHooks {
    Bytes code;
    std::vector<MemoryPatch> patches;
};
OutputDevice resolveOutputDevice(const GUID& identifier);
OutputDeviceHooks buildOutputDeviceHooks(uint32_t remoteBase, uint32_t imageBase, uint32_t ordinal);
void installOutputDevice(HANDLE process, uint32_t imageBase, const OutputDevice& device);
void verifyOutputDevice(HANDLE process, uint32_t imageBase, const OutputDevice& device);
}
