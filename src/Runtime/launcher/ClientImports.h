#pragma once
#include "../core/Common.h"

namespace t7 {
struct ClientImportModule {
    std::string dll;
    uint32_t iatRva = 0;
    std::vector<std::string> symbols;
};
struct ClientImportDirectory {
    uint32_t rva = 0, size = 0, usedSize = 0, iatRva = 0, iatSize = 0;
    size_t symbolCount = 0;
};
const std::vector<ClientImportModule>& clientImportModules();
ClientImportDirectory writeClientImports(Bytes& image, uint32_t tableRva, uint32_t capacity,
                                        const std::vector<ClientImportModule>& modules);
}
