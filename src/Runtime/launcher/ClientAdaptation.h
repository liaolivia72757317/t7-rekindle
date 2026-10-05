#pragma once
#include "ClientImage.h"

namespace t7 {
struct MemoryPatch {
    uint32_t rva;
    Bytes expected;
    Bytes replacement;
};
inline constexpr char TEN_PROXY_SHA256[] = "27afa51752fd13ff0e940169c8386eed06b506c588dd5fdfad8057911b982ebb";
inline constexpr uint32_t TEN_PROXY_ENTRY_RVA = 0xD26D, TEN_PROXY_IMAGE_SIZE = 0x29000;

void applyMemoryPatches(Bytes& image, const std::vector<MemoryPatch>& patches);
std::vector<MemoryPatch> clientMemoryPatches(const Bytes& image);
std::vector<MemoryPatch> tenProxyMemoryPatches(const Bytes& original, uint32_t imageBase);
}
