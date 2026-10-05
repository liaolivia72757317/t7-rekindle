#include "MovementHookCode.h"
#include "MovementResources.h"
#include "StartupAnimation.h"
#include <cstring>

namespace t7 {
namespace {
class Code {
public:
    Bytes bytes;
    uint32_t base;
    explicit Code(uint32_t address) : base(address) {}
    void emit(std::initializer_list<unsigned char> values) { bytes.insert(bytes.end(), values); }
    void word(uint32_t value) {
        for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<unsigned char>(value >> shift));
    }
    size_t branch(unsigned char condition = 0) {
        if (condition) emit({0x0F, condition}); else emit({0xE9});
        const auto field = bytes.size(); word(0); return field;
    }
    void bind(size_t field, size_t target) {
        const auto displacement = static_cast<uint32_t>(target) - static_cast<uint32_t>(field + 4);
        std::memcpy(bytes.data() + field, &displacement, 4);
    }
    void jump(uint32_t target) {
        const auto instruction = movementJump(base + static_cast<uint32_t>(bytes.size()), target);
        bytes.insert(bytes.end(), instruction.begin(), instruction.end());
    }
};
struct Path { uint32_t address, length; };

// EDX is an x86 std::string. All matching paths exceed the SSO capacity.
// Registers and flags are saved by the caller; matching never changes the path.
std::vector<size_t> matchPaths(Code& code, const std::vector<Path>& paths, std::vector<size_t>& misses) {
    code.emit({0x85, 0xD2}); misses.push_back(code.branch(0x84));
    std::vector<size_t> matches;
    for (const auto& path : paths) {
        std::vector<size_t> next;
        code.emit({0x81, 0x7A, 0x10}); code.word(path.length); next.push_back(code.branch(0x85));
        code.emit({0x81, 0x7A, 0x14}); code.word(path.length); next.push_back(code.branch(0x82));
        code.emit({0x8B, 0x32, 0x85, 0xF6}); next.push_back(code.branch(0x84)); // data
        code.emit({0xBF}); code.word(path.address);
        code.emit({0xB9}); code.word(path.length);
        const auto loop = code.bytes.size();
        code.emit({0x8A, 0x06, 0x3C, 0x5C}); // mov al,[esi]; cmp al,'\'
        const auto notSlash = code.branch(0x85);
        code.emit({0xB0, 0x2F}); code.bind(notSlash, code.bytes.size());
        code.emit({0x3C, 0x41}); const auto below = code.branch(0x82);
        code.emit({0x3C, 0x5A}); const auto above = code.branch(0x87);
        code.emit({0x0C, 0x20}); // ASCII lowercase
        code.bind(below, code.bytes.size()); code.bind(above, code.bytes.size());
        code.emit({0x3A, 0x07}); next.push_back(code.branch(0x85));
        code.emit({0x46, 0x47, 0x49}); // inc esi; inc edi; dec ecx
        const auto repeat = code.branch(0x85); code.bind(repeat, loop);
        matches.push_back(code.branch());
        for (auto field : next) code.bind(field, code.bytes.size());
    }
    misses.push_back(code.branch());
    return matches;
}
void bindAll(Code& code, const std::vector<size_t>& fields) {
    for (auto field : fields) code.bind(field, code.bytes.size());
}
}
Bytes movementJump(uint32_t source, uint32_t destination) {
    // x86 EIP arithmetic wraps at 32 bits, including remote pages above 2 GiB.
    Code code(source); code.emit({0xE9}); code.word(destination - source - 5); return code.bytes;
}
MovementHookPage buildMovementHooks(uint32_t remoteBase, uint32_t imageBase, bool skipStartupAnimation) {
    if (!remoteBase || remoteBase > UINT32_MAX - MOVEMENT_HOOK_PAGE_SIZE
        || !imageBase || imageBase > UINT32_MAX - MOVEMENT_HOOK_SITES.back().rva - 5)
        throw std::runtime_error("movement hook address outside x86 range");
    MovementHookPage page; page.bytes.resize(MOVEMENT_HOOK_PAGE_SIZE, 0);
    size_t data = 0;
    auto putString = [&](const std::string& value) {
        if (data + value.size() + 1 > 0x200) throw std::runtime_error("movement hook string storage overflow");
        const Path path{remoteBase + static_cast<uint32_t>(data), static_cast<uint32_t>(value.size())};
        std::memcpy(page.bytes.data() + data, value.data(), value.size()); data += value.size() + 1; return path;
    };
    const auto infantry = putString(movementResourcePath(MovementResource::Infantry));
    const auto router = putString(movementResourcePath(MovementResource::Router));
    const auto birth = putString(movementResourcePath(MovementResource::Birth));
    const auto offline = putString(movementOfflineTreePath());
    std::vector<Path> xmlPaths{infantry, router, birth};
    if (skipStartupAnimation) xmlPaths.push_back(putString(startupAnimationResourcePath()));
    const uint32_t replacementObject = remoteBase + 0x200;
    std::memcpy(page.bytes.data() + 0x200, &offline.address, 4);
    std::memcpy(page.bytes.data() + 0x210, &offline.length, 4);
    std::memcpy(page.bytes.data() + 0x214, &offline.length, 4);
    size_t offset = 0x300;
    for (size_t i = 0; i < MOVEMENT_HOOK_SITES.size(); ++i) {
        Code code(remoteBase + static_cast<uint32_t>(offset)); page.entries[i] = code.base;
        const auto& site = MOVEMENT_HOOK_SITES[i];
        if (i == 1) code.bytes.insert(code.bytes.end(), site.original.begin(), site.original.end());
        if (i == 2) code.emit({0xC6, 0x04, 0x3E, 0x00}); // original NUL terminator
        code.emit({0x9C, 0x60}); // pushfd; pushad
        std::vector<size_t> misses;
        if (i == 0) {
            code.emit({0x83, 0x7C, 0x24, 0x2C, 0x00}); // open flags must be read-only
            misses.push_back(code.branch(0x85));
            code.emit({0x8B, 0x54, 0x24, 0x28}); // original first argument
        } else code.emit({0x8B, 0x55, 0x08}); // XML loader's unchanged filename
        const auto matches = matchPaths(code, i == 0 ? std::vector<Path>{router} : xmlPaths, misses);
        bindAll(code, matches);
        if (i == 0) { code.emit({0xC7, 0x44, 0x24, 0x28}); code.word(replacementObject); }
        if (i == 1) { code.emit({0x81, 0x44, 0x24, 0x18}); code.word(static_cast<uint32_t>(MOVEMENT_XML_RESERVE)); }
        if (i == 2) {
            code.emit({0x61, 0x9D}); // expose original frame to the owned debugger
            page.breakpoint = code.base + static_cast<uint32_t>(code.bytes.size());
            code.emit({0xCC, 0x56}); // transform, then original push esi
            code.jump(imageBase + site.rva + 5);
        }
        bindAll(code, misses);
        code.emit({0x61, 0x9D});
        if (i == 0) code.bytes.insert(code.bytes.end(), site.original.begin(), site.original.end());
        if (i == 2) code.emit({0x56});
        code.jump(imageBase + site.rva + 5);
        if (offset + code.bytes.size() > page.bytes.size()) throw std::runtime_error("movement hook page overflow");
        std::memcpy(page.bytes.data() + offset, code.bytes.data(), code.bytes.size());
        offset = (offset + code.bytes.size() + 15) & ~size_t(15);
    }
    return page;
}
}
