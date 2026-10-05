#include "ClientCode.h"
#include "Ap32.h"
#include <algorithm>

namespace t7 {
namespace {
struct CodeLayout {
    uint32_t rva;
    size_t rawOffset, packedSize, prefixSize, decodedSize, memorySize;
};
constexpr std::array<CodeLayout, 2> LAYOUT{{
    {0x00001000, 0x00000400, 0x0089BE00, 0, 0x016E7E00, 0x016E8000},
    {0x016E9000, 0x0089C200, 0x00004C00, 4, 0x0000C400, 0x0000D000}
}};
void checkCancelled(const std::function<bool()>& cancelled) {
    if (cancelled && cancelled()) throw std::runtime_error("client code preparation cancelled");
}
}

std::array<ClientCodeSection, 2> recoverClientCode(
    const Bytes& image, const std::function<bool()>& cancelled) {
    checkCancelled(cancelled);
    if (image.size() != 23042408 || sha256(image) != CLIENT_IMAGE_SHA256)
        throw std::runtime_error("unsupported client code baseline");
    std::array<ClientCodeSection, 2> result;
    for (size_t i = 0; i < LAYOUT.size(); ++i) {
        const auto& layout = LAYOUT[i];
        const Bytes packed(image.begin() + layout.rawOffset, image.begin() + layout.rawOffset + layout.packedSize);
        auto& section = result[i];
        section.rva = layout.rva;
        section.bytes.reserve(layout.memorySize);
        size_t cursor = layout.prefixSize;
        while (section.bytes.size() < layout.decodedSize) {
            checkCancelled(cancelled);
            const auto maximum = std::min<size_t>(0x20000, layout.decodedSize - section.bytes.size());
            const auto block = decodeAp32(packed, cursor, maximum);
            if (block.bytes.size() != maximum) throw std::runtime_error("client code block size mismatch");
            section.bytes.insert(section.bytes.end(), block.bytes.begin(), block.bytes.end());
            cursor += block.consumed;
        }
        if (std::any_of(packed.begin() + cursor, packed.end(), [](auto byte) { return byte != 0; }))
            throw std::runtime_error("unexpected client code section tail");
        section.bytes.resize(layout.memorySize, 0);
    }
    checkCancelled(cancelled);
    return result;
}
}
