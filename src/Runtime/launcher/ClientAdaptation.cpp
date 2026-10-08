#include "ClientAdaptation.h"
#include <algorithm>
#include <cstring>

namespace t7 {
namespace {
void scrub(std::vector<MemoryPatch>& patches, const Bytes& image, const Bytes& token, size_t expectedCount) {
    size_t count = 0;
    auto cursor = image.begin();
    while ((cursor = std::search(cursor, image.end(), token.begin(), token.end())) != image.end()) {
        patches.push_back({static_cast<uint32_t>(cursor - image.begin()), token, Bytes(token.size(), 0)});
        cursor += token.size(); ++count;
    }
    if (count != expectedCount) throw std::runtime_error("client component name coverage mismatch");
}
Bytes ascii(const char* value) { return Bytes(value, value + std::strlen(value)); }
Bytes utf16(const char* value) {
    Bytes result;
    for (; *value; ++value) { result.push_back(static_cast<unsigned char>(*value)); result.push_back(0); }
    return result;
}
MemoryPatch padded(uint32_t rva, Bytes expected, Bytes replacement) {
    if (replacement.size() > expected.size()) throw std::runtime_error("invalid client patch size");
    replacement.resize(expected.size(), 0x90);
    return {rva, std::move(expected), std::move(replacement)};
}
MemoryPatch cameraWheelInputPatch() {
    Bytes expected{0x3C,0x01,0x75,0x13,0x8B,0x0D,0x68,0x53,0x23,0x03,0xE8,0x7D,0x71,0xFE,0xFF,
                   0xC6,0x45,0xB3,0x01,0x84,0xC0,0x74,0x04,0xC6,0x45,0xB3,0x00};
    auto replacement = expected;
    // UI receives the wheel but always returns false; still check the existing camera input gate.
    std::fill_n(replacement.begin(), 4, static_cast<unsigned char>(0x90));
    return {0x4CBAE4, std::move(expected), std::move(replacement)};
}
}

void applyMemoryPatches(Bytes& image, const std::vector<MemoryPatch>& patches) {
    for (size_t i = 0; i < patches.size(); ++i) {
        const auto& patch = patches[i];
        if (patch.expected.empty() || patch.expected.size() != patch.replacement.size()
            || patch.rva > image.size() || patch.expected.size() > image.size() - patch.rva)
            throw std::runtime_error("invalid client memory patch extent");
        if (!std::equal(patch.expected.begin(), patch.expected.end(), image.begin() + patch.rva))
            throw std::runtime_error("client memory patch signature mismatch at RVA=" + std::to_string(patch.rva));
        for (size_t j = 0; j < i; ++j) {
            if (patch.rva < patches[j].rva + patches[j].expected.size()
                && patches[j].rva < patch.rva + patch.expected.size())
                throw std::runtime_error("client memory patches overlap");
        }
    }
    for (const auto& patch : patches)
        std::copy(patch.replacement.begin(), patch.replacement.end(), image.begin() + patch.rva);
}

std::vector<MemoryPatch> clientMemoryPatches(const Bytes& image) {
    if (image.size() != 0x03B54000) throw std::runtime_error("unexpected client memory image size");
    std::vector<MemoryPatch> patches{
        padded(0x8D6C0, {0x55,0x8B,0xEC,0x81,0xEC,0x10,0x01,0,0,0xA1,0xFC,0xD9}, {0x33,0xC0,0xC3}),
        padded(0x8D800, {0x55,0x8B,0xEC,0x6A,0xFF,0x68,0x70,0x10,0x76,0x01,0x64,0xA1}, {0x33,0xC0,0xC2,0x08,0}),
        padded(0x8D910, {0x8B,0x49,0x10,0x8B,0x01,0xFF,0x60,0x04}, {0x33,0xC0,0xC3}),
        padded(0x8E36F, {0x8B,0x02,0x51,0x8B,0xCA,0xFF,0x50,0x08}, {0xB8,0x01,0,0,0}),
        padded(0x10730, {0xE8,0xFB,0,0,0,0x6A,0xFF,0x6A,0,0x68,0x04,0x08}, {0xC3}),
        {0x75ECF0, {0x55,0x8B,0xEC,0x83,0xEC,0x14,0xA1,0xFC,0xD9,0x82,0x02},
                    {0x8B,0x4C,0x24,0x04,0x33,0xC0,0x89,0x01,0xC2,0x04,0}},
        padded(0x890B1, {0xE8,0x8A,0x04,0,0,0x84,0xC0,0x75,0x18,0x6A,0,0x68,0xA0,0xCF,0xB2,1,
                   0x68,0x9C,0x4F,0xB3,1,0x6A,0,0xFF,0x15,0x90,0x6A,0xAF,1,0x32,0xC0,0x5E,0xC3},
                  {0xE8,0x8A,0x04,0,0,0x84,0xC0,0x75,0x18,0xB0,0x01,0x5E,0xC3}),
        cameraWheelInputPatch()
    };
    // Remove the corresponding dynamic names as well as their direct call paths.
    scrub(patches, image, ascii("TerSafe"), 2);
    scrub(patches, image, ascii("GeTssAntiService"), 5);
    // TCLS also occurs in behavior-tree registration names; those are not loader paths.
    scrub(patches, image, ascii("QQPCfix.dll"), 1);
    return patches;
}

std::vector<MemoryPatch> tenProxyMemoryPatches(const Bytes& original, uint32_t imageBase) {
    if (original.size() != 167576 || sha256(original) != TEN_PROXY_SHA256
        || !imageBase || imageBase > UINT32_MAX - TEN_PROXY_IMAGE_SIZE)
        throw std::runtime_error("unsupported protocol helper baseline or address");
    IMAGE_DOS_HEADER dos{}; std::memcpy(&dos, original.data(), sizeof(dos));
    IMAGE_NT_HEADERS32 headers{}; std::memcpy(&headers, original.data() + dos.e_lfanew, sizeof(headers));
    if (headers.OptionalHeader.AddressOfEntryPoint != TEN_PROXY_ENTRY_RVA
        || headers.OptionalHeader.SizeOfImage != TEN_PROXY_IMAGE_SIZE
        || headers.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_TLS].VirtualAddress
        || headers.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_TLS].Size)
        throw std::runtime_error("unsupported protocol helper initialization layout");
    const auto relocatedOperand = imageBase + 0x26398;
    Bytes call{0xFF,0x15,0,0,0,0};
    std::memcpy(call.data() + 2, &relocatedOperand, sizeof(relocatedOperand));
    std::vector<MemoryPatch> patches{
        {0x5FCB, {0x74,0x5A}, {0xEB,0x5A}},
        {0x61E4, call, {0x33,0xC0,0x83,0xC4,0x0C,0x90}},
        {0x7B30, {0x55,0x8B,0xEC}, {0x33,0xC0,0xC3}},
        {0x86D0, {0x55,0x8B,0xEC,0x83,0xEC,0x4C}, {0x33,0xC0,0xC2,0x08,0,0x90}}
    };
    // The hash-bound helper has matching raw/RVA offsets through .rdata.
    const Bytes names(original.begin() + 0x20000, original.begin() + 0x23000);
    std::vector<MemoryPatch> namePatches;
    scrub(namePatches, names, utf16("GbSpy"), 3);
    scrub(namePatches, names, utf16("TCLS"), 9);
    for (auto& patch : namePatches) { patch.rva += 0x20000; patches.push_back(std::move(patch)); }
    return patches;
}
}
