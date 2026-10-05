#include "ClientImports.h"
#include <algorithm>

namespace t7 {
namespace {
void put32(Bytes& bytes, size_t offset, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) bytes.at(offset + i) = static_cast<unsigned char>(value >> (8 * i));
}
bool asciiName(const std::string& value) {
    return !value.empty() && std::all_of(value.begin(), value.end(), [](unsigned char c) { return c >= 33 && c <= 126; });
}
struct ImportTable {
    Bytes bytes;
    uint32_t base, capacity;
    size_t allocate(size_t size, size_t alignment) {
        const auto offset = (bytes.size() + alignment - 1) & ~(alignment - 1);
        if (offset > capacity || size > capacity - offset) throw std::runtime_error("client import table exceeds capacity");
        bytes.resize(offset + size, 0);
        return offset;
    }
    uint32_t name(const std::string& value, bool hint) {
        if (!asciiName(value)) throw std::runtime_error("invalid client import name");
        const auto prefix = hint ? 2u : 0u;
        if (value.size() > capacity) throw std::runtime_error("client import name exceeds capacity");
        const auto offset = allocate(prefix + value.size() + 1, hint ? 2 : 1);
        std::copy(value.begin(), value.end(), bytes.begin() + offset + prefix);
        return base + static_cast<uint32_t>(offset);
    }
};
}

const std::vector<ClientImportModule>& clientImportModules() {
    static const std::vector<ClientImportModule> modules{
#include "ClientImportTable.inc"
    };
    return modules;
}

ClientImportDirectory writeClientImports(Bytes& image, uint32_t tableRva, uint32_t capacity,
                                        const std::vector<ClientImportModule>& modules) {
    if (image.size() > UINT32_MAX || tableRva % 4 || tableRva > image.size()
        || capacity > image.size() - tableRva || capacity < 40 || modules.empty()
        || modules.size() > capacity / 20 - 1)
        throw std::runtime_error("invalid client import directory extent");
    const auto descriptorSize = static_cast<uint32_t>((modules.size() + 1) * 20);
    ImportTable table{Bytes(descriptorSize, 0), tableRva, capacity};
    struct IatWrite { uint32_t rva; Bytes bytes; };
    std::vector<IatWrite> writes;
    uint32_t firstIat = UINT32_MAX, lastIat = 0;
    size_t symbolCount = 0;
    for (size_t index = 0; index < modules.size(); ++index) {
        const auto& module = modules[index];
        if (!asciiName(module.dll) || module.dll.find_first_of("/\\:") != std::string::npos
            || module.iatRva % 4 || module.iatRva > image.size() || module.symbols.empty()
            || (image.size() - module.iatRva) / 4 < 2
            || module.symbols.size() > (image.size() - module.iatRva) / 4 - 1)
            throw std::runtime_error("invalid client import module");
        const auto iatSize = static_cast<uint32_t>((module.symbols.size() + 1) * 4);
        const auto iatEnd = module.iatRva + iatSize;
        if (module.iatRva < tableRva + capacity && tableRva < iatEnd)
            throw std::runtime_error("client import table overlaps IAT");
        for (const auto& previous : writes) {
            if (module.iatRva < previous.rva + previous.bytes.size() && previous.rva < iatEnd)
                throw std::runtime_error("client IAT groups overlap");
        }
        const auto dllName = table.name(module.dll, false);
        const auto lookup = table.allocate(iatSize, 4);
        Bytes iat(iatSize, 0);
        for (size_t i = 0; i < module.symbols.size(); ++i) {
            const auto nameRva = table.name(module.symbols[i], true);
            put32(table.bytes, lookup + i * 4, nameRva);
            put32(iat, i * 4, nameRva);
        }
        const auto descriptor = index * 20;
        put32(table.bytes, descriptor, tableRva + static_cast<uint32_t>(lookup));
        put32(table.bytes, descriptor + 12, dllName);
        put32(table.bytes, descriptor + 16, module.iatRva);
        firstIat = std::min(firstIat, module.iatRva); lastIat = std::max(lastIat, iatEnd);
        symbolCount += module.symbols.size();
        writes.push_back({module.iatRva, std::move(iat)});
    }
    std::copy(table.bytes.begin(), table.bytes.end(), image.begin() + tableRva);
    for (const auto& write : writes)
        std::copy(write.bytes.begin(), write.bytes.end(), image.begin() + write.rva);
    return {tableRva, descriptorSize, static_cast<uint32_t>(table.bytes.size()), firstIat, lastIat - firstIat, symbolCount};
}
}
