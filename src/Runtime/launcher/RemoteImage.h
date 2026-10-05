#pragma once
#include "ClientAdaptation.h"

namespace t7 {
Bytes readClientMemory(HANDLE process, uintptr_t address, size_t size);
void writeClientMemory(HANDLE process, uintptr_t address, const Bytes& bytes);
void applyRemotePatches(HANDLE process, uintptr_t base, const std::vector<MemoryPatch>& patches);
void installClientImage(HANDLE process, HANDLE thread, const ClientImage& image,
                        const std::function<bool()>& cancelled = {});
}
