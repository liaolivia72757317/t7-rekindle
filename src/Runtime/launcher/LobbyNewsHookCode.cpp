#include "LobbyNews.h"
#include <cstring>

namespace t7 {
namespace {
void word(Bytes& code, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) code.push_back(static_cast<unsigned char>(value >> (i * 8)));
}
void jump(Bytes& code, uint32_t base, uint32_t target, unsigned char opcode = 0xE9) {
    code.push_back(opcode); word(code, target - (base + static_cast<uint32_t>(code.size()) + 4));
}
// EAX points to the view name; inspect only the exact, terminated sidebar names.
void match(Bytes& code, const char* name, uint32_t url, uint32_t length, bool constructor) {
    std::vector<size_t> misses;
    const auto count = std::strlen(name) + 1;
    for (size_t i = 0; i < count; ++i) {
        code.insert(code.end(), {0x80,0x78,static_cast<unsigned char>(i),static_cast<unsigned char>(name[i]),0x75,0});
        misses.push_back(code.size() - 1);
    }
    // pushfd + push eax precede this code: the first argument is at ESP+12.
    code.insert(code.end(), {0xC7,0x44,0x24,0x0C}); word(code, url);
    if (constructor) {
        code.insert(code.end(), {0xC7,0x44,0x24,0x10}); word(code, length);
        // The helper navigates only when the shared URL revision changes from zero.
        code.insert(code.end(), {0xC7,0x41,0xFC}); word(code, 1);
    }
    for (auto miss : misses) code[miss] = static_cast<unsigned char>(code.size() - miss - 1);
}
}
LobbyNewsHooks buildLobbyNewsHooks(uint32_t remoteBase, uint32_t imageBase, const std::string& address) {
    if (address.empty() || address.size() + 7 >= 128 || address.find('\0') != address.npos
        || remoteBase > UINT32_MAX - 4096 || imageBase > UINT32_MAX - 0x1220000)
        throw std::runtime_error("invalid lobby news hook address");
    LobbyNewsHooks result;
    auto& code = result.bytes;
    const uint32_t news = remoteBase + 512, banner = remoteBase + 640;
    result.constructor = remoteBase;
    code.insert(code.end(), {0x9C,0x50,0x8B,0xC7}); // EDI is the constructor's view name.
    match(code, "bbsMessage", news, static_cast<uint32_t>(address.size() + 5), true);
    match(code, "bannerMessage", banner, static_cast<uint32_t>(address.size() + 7), true);
    code.insert(code.end(), {0x58,0x9D});
    jump(code, remoteBase, imageBase + 0xC350); // Original std::string::assign.
    result.navigation = remoteBase + static_cast<uint32_t>(code.size());
    code.insert(code.end(), {0x9C,0x50,0x8D,0x41,0x04,0x83,0x79,0x18,0x10,0x72,0x02,0x8B,0x00});
    match(code, "bbsMessage", news, 0, false);
    match(code, "bannerMessage", banner, 0, false);
    code.insert(code.end(), {0x58,0x9D,0x55,0x8B,0xEC,0x53,0x8B,0xD9});
    jump(code, remoteBase, imageBase + 0x3EF876);
    if (code.size() > 512) throw std::runtime_error("lobby news hook code exceeds storage");
    code.resize(512, 0);
    const auto append = [&](const std::string& value) { code.insert(code.end(), value.begin(), value.end()); code.push_back(0); };
    append(address + "#news"); code.resize(640, 0); append(address + "#banner");
    code.resize(768, 0);
    result.initialize = remoteBase + 768;
    code.push_back(0x51);
    jump(code, remoteBase, imageBase + 0x3FCB60, 0xE8);
    code.insert(code.end(), {0x59,0x84,0xC0,0x74,0});
    const auto skip = code.size() - 1;
    code.insert(code.end(), {0x9C,0x60,0x8B,0xDC,0x81,0xEC,0x10,0x02,0,0,0x83,0xE4,0xF0,
                            0x0F,0xAE,0x04,0x24}); // Preserve caller FPU/SSE across browser initialization.
    jump(code, remoteBase, imageBase + 0x3FF860, 0xE8);
    code.insert(code.end(), {0x0F,0xAE,0x0C,0x24,0x8B,0xE3,0x61,0x9D});
    code[skip] = static_cast<unsigned char>(code.size() - skip - 1);
    code.push_back(0xC3);
    return result;
}
}
