#include "../../src/Runtime/launcher/ClientImports.h"
#include <algorithm>
#include <iostream>
#include <functional>
#include <cstring>

namespace {
uint32_t get32(const t7::Bytes& bytes, size_t offset) {
    uint32_t result = 0;
    for (unsigned i = 0; i < 4; ++i) result |= static_cast<uint32_t>(bytes.at(offset + i)) << (8 * i);
    return result;
}
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
void reject(uint32_t table, uint32_t capacity, const std::vector<t7::ClientImportModule>& modules) {
    t7::Bytes image(4096, 0xA5);
    const auto original = image;
    bool rejected = false;
    try { t7::writeClientImports(image, table, capacity, modules); }
    catch (const std::runtime_error&) { rejected = true; }
    require(rejected && image == original, "invalid imports accepted or partially published");
}
}

bool verifyClientImports() {
    try {
        const std::vector<t7::ClientImportModule> modules{{"First.dll", 0x100, {"One", "Two"}},
                                                        {"Second.dll", 0x200, {"Three"}}};
        t7::Bytes image(4096, 0xA5);
        const auto directory = t7::writeClientImports(image, 0x400, 0x800, modules);
        require(directory.rva == 0x400 && directory.size == 60 && directory.symbolCount == 3, "import directory metadata");
        require(directory.iatRva == 0x100 && directory.iatSize == 0x108, "IAT metadata");
        for (size_t i = 0; i < modules.size(); ++i) {
            const auto descriptor = 0x400 + i * 20;
            require(get32(image, descriptor + 16) == modules[i].iatRva, "IAT slot moved");
            const auto dll = get32(image, descriptor + 12);
            require(!std::strcmp(reinterpret_cast<const char*>(image.data() + dll), modules[i].dll.c_str()), "DLL name mismatch");
            const auto lookup = get32(image, descriptor);
            for (size_t symbol = 0; symbol < modules[i].symbols.size(); ++symbol) {
                const auto name = get32(image, lookup + symbol * 4);
                require(name == get32(image, modules[i].iatRva + symbol * 4), "ILT/IAT mismatch");
                require(image[name] == 0 && image[name + 1] == 0, "unexpected import hint");
                require(!std::strcmp(reinterpret_cast<const char*>(image.data() + name + 2), modules[i].symbols[symbol].c_str()), "symbol mismatch");
            }
            require(get32(image, lookup + modules[i].symbols.size() * 4) == 0, "ILT terminator");
            require(get32(image, modules[i].iatRva + modules[i].symbols.size() * 4) == 0, "IAT terminator");
        }
        for (size_t i = 0; i < 20; ++i) require(image[0x428 + i] == 0, "descriptor terminator");
        require(image[0] == 0xA5 && image[0x400 + directory.usedSize] == 0xA5, "unrelated bytes changed");
        reject(UINT32_MAX, 0x800, modules); reject(0x401, 0x800, modules);
        reject(0x400, UINT32_MAX, modules); reject(0x400, 20, modules);
        reject(0x400, 60, modules); reject(0x400, 0x800, {});
        auto bad = modules; bad[1].iatRva = 0x104; reject(0x400, 0x800, bad);
        bad = modules; bad[1].iatRva = 0x400; reject(0x400, 0x800, bad);
        bad = modules; bad[1].iatRva = 0x201; reject(0x400, 0x800, bad);
        bad = modules; bad[1].iatRva = 4092; reject(0x400, 0x800, bad);
        bad = modules; bad[1].symbols.clear(); reject(0x400, 0x800, bad);
        bad = modules; bad[1].symbols = {std::string("Bad\0Name", 8)}; reject(0x400, 0x800, bad);
        bad = modules; bad[1].symbols = {""}; reject(0x400, 0x800, bad);
        bad = modules; bad[1].symbols = {std::string(4096, 'A')}; reject(0x400, 0x800, bad);
        bad = modules; bad[1].dll = "../Second.dll"; reject(0x400, 0x800, bad);
        bad = modules; bad[1].dll = "Second dll"; reject(0x400, 0x800, bad);
        const auto& fixed = t7::clientImportModules();
        size_t count = 0;
        for (const auto& module : fixed) count += module.symbols.size();
        require(fixed.size() == 19 && count == 794, "client import binding coverage");
        const auto& kernel = fixed.at(5);
        require(kernel.symbols.at((0x016F6274 - kernel.iatRva) / 4) == "WriteFile", "write binding reversed");
        require(kernel.symbols.at((0x016F6278 - kernel.iatRva) / 4) == "ReadFile", "read binding reversed");
        const auto d3dx = std::find_if(fixed.begin(), fixed.end(), [](const t7::ClientImportModule& module) {
            return module.dll == "d3dx9_43.dll";
        });
        require(d3dx != fixed.end(), "D3DX module missing");
        require(d3dx->symbols.at((0x016F6BBC - d3dx->iatRva) / 4) == "D3DXCreateBuffer", "D3DX buffer allocation binding");
        require(d3dx->symbols.at((0x016F6BFC - d3dx->iatRva) / 4) == "D3DXGetShaderConstantTable", "D3DX shader constant table binding");
        require(d3dx->symbols.at((0x016F6C38 - d3dx->iatRva) / 4) == "D3DXVec3Transform", "D3DX homogeneous vector transform binding");
        std::cout << "Client import layout and transactional rejection cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Client import test failure: " << error.what() << '\n';
        return false;
    }
}
