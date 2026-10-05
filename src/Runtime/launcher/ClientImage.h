#pragma once
#include "ClientCode.h"
#include "ClientImports.h"

namespace t7 {
struct ClientMemoryRegion {
    uint32_t rva = 0, size = 0;
    DWORD protection = PAGE_NOACCESS;
};
struct ClientImage {
    uint32_t imageBase = 0, originalEntryRva = 0, entryRva = 0;
    Bytes bytes;
    std::vector<ClientMemoryRegion> regions;
    ClientImportDirectory imports;
};

// Reconstructs data for an existing image mapping; this is not an executable file.
ClientImage recoverClientImage(const Bytes& original, const std::function<bool()>& cancelled = {});
}
