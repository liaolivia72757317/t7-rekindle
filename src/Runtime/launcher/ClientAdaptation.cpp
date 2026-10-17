#include "ClientAdaptation.h"
#include <algorithm>
#include <cstring>

namespace t7r {
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
MemoryPatch defenseAssistLevelPatch() {
    Bytes expected{0x3B,0x9E,0xA8,0x02,0,0,0x7F,0x21};
    auto replacement = expected;
    // The local player exceeds the map's beginner level cap; keep the other initialization checks.
    replacement[6] = 0x90; replacement[7] = 0x90;
    return {0x495DA6, std::move(expected), std::move(replacement)};
}
MemoryPatch defenseAssistSettingsPatch() {
    // UpdataSettings refreshes listeners and the packet flag, but must also update the input mode.
    return {0x4810E0,
        {0x56,0x8B,0xF1,0x8B,0x0D,0xC8,0x5A,0x23,0x03,0xE8,0x92,0xA8,0x26,0,
         0x8B,0xCE,0x84,0xC0,0x74,0x12,0xE8,0xB7,0,0,0,0x8B,0xCE,0xE8,0x20,0,0,0,
         0xC6,0x46,0x54,0x01,0x5E,0xC3,0xE8,0xA5,0,0,0,0xC6,0x46,0x54,0,0x5E,0xC3},
        {0x56,0x8B,0xF1,0x8B,0x0D,0xC8,0x5A,0x23,0x03,0xE8,0x92,0xA8,0x26,0,
         0x0F,0xB6,0xC0,0x50,0x8B,0xCE,0xE8,0xB7,0,0,0,0x80,0x3C,0x24,0,0x74,0x07,
         0x8B,0xCE,0xE8,0x1A,0,0,0,0x58,0x88,0x46,0x54,0x01,0xC0,0x89,0x46,0x20,0x5E,0xC3}};
}
MemoryPatch hudPingPatch() {
    // Read the native heartbeat RTT, including business processing, instead of transport Ping.
    // Skip the transport-name temporary before construction; preserve HUD locals and diagnostics.
    return padded(0x795285,
        {0x68,0x70,0x4F,0xB3,0x01,0x8D,0x4D,0xD8,0xE8,0x8E,0x6E,0x87,0xFF,0xC6,0x45,0xFC,
         0x02,0x8B,0x0D,0xCC,0x96,0x83,0x02,0x8D,0x55,0xD8,0x8B,0x01,0x52,0x8B,0x40,0x44,
         0xFF,0xD0,0x88,0x45,0xA7},
        {0x8B,0x0D,0x9C,0xC5,0x1E,0x03,0x85,0xC9,0x0F,0x84,0x6E,0x01,0,0,
         0x80,0x79,0x1C,0,0x0F,0x84,0x64,0x01,0,0,0xE8,0xEE,0x3D,0xC2,0xFF,0x8B,0xF0,
         0xE9,0x3E,0x01,0,0});
}
std::vector<MemoryPatch> matchMapFilterPatches() {
    // Restrict the original small-battlefield row before it builds the dialog.
    // Native filter synchronization and matchmaking then consume the same six IDs.
    constexpr uint32_t SITE = 0x53A001, STUB = 0x16E8E00;
    Bytes code{0x9C,0x83,0x3F,0x01,0x75,0,
        0x81,0xBF,0xB2,0x04,0,0,0x0E,0,0,0,0x74,0x0C,
        0x81,0xBF,0xB2,0x04,0,0,0x06,0,0,0,0x75,0};
    const auto append = [&](uint32_t value) {
        for (unsigned i = 0; i < 4; ++i) code.push_back(static_cast<unsigned char>(value >> (8 * i)));
    };
    uint32_t offset = 0x4B2;
    for (uint32_t value : {6u, 1003u, 1030u, 1021u, 1001u, 6003u, 1034u}) {
        code.insert(code.end(), {0xC7,0x87}); append(offset); append(value); offset += 4;
    }
    // ceil(6 * .83) permits five exclusions, so at least one checkbox stays selected.
    code.insert(code.end(), {0x9D,0x6A,0x53,0xEB,0x04});
    const auto original = code.size();
    code[5] = static_cast<unsigned char>(original - 6);
    code[29] = static_cast<unsigned char>(original - 30);
    code.insert(code.end(), {0x9D,0xFF,0x77,0x08,0x8B,0xC8,0xE9});
    const auto resume = SITE + 5 - (STUB + static_cast<uint32_t>(code.size()) + 4);
    for (unsigned i = 0; i < 4; ++i) code.push_back(static_cast<unsigned char>(resume >> (8 * i)));
    Bytes jump{0xE9};
    const auto relative = STUB - SITE - 5;
    for (unsigned i = 0; i < 4; ++i) jump.push_back(static_cast<unsigned char>(relative >> (8 * i)));
    // This zero-filled tail is part of the existing executable .text allocation.
    return {{SITE, {0xFF,0x77,0x08,0x8B,0xC8}, jump}, {STUB, Bytes(code.size(), 0), code}};
}
std::vector<MemoryPatch> matchSpawnModePatches() {
    // Kill-score matches assign native spawn points on the server. Map 1030 normally
    // requires an area selection; use the client's existing SYSTEM initialization.
    // Other maps and the getter's null-resource branch retain their original behavior.
    constexpr uint32_t SITE = 0x721296, STUB = 0x16E8F00;
    const Bytes code{0x81,0x38,0x06,0x04,0,0,0x75,0x06,0xB8,0x01,0,0,0,0xC3,
                     0x8B,0x80,0xB8,0,0,0,0xC3};
    Bytes jump{0xE9};
    const auto relative = STUB - SITE - 5;
    for (unsigned i = 0; i < 4; ++i) jump.push_back(static_cast<unsigned char>(relative >> (8 * i)));
    return {padded(SITE, {0x8B,0x80,0xB8,0,0,0}, jump), {STUB, Bytes(code.size(), 0), code}};
}
std::vector<MemoryPatch> trainingEntryPatches() {
    // Only the UI predecessor field for these two map rows is relaxed. Keep the
    // level condition, other entries, resource tables and saved progress intact.
    constexpr uint32_t SITE = 0x5A72F2, STUB = 0x16E8F40;
    Bytes code{0x9C,0x83,0xBB,0x84,0,0,0,0x04,0x75,0,
        0x83,0x7F,0xF8,0x07,0x75,0,
        0x81,0xBB,0x8A,0,0,0,0x76,0x17,0,0,0x74,0,
        0x81,0xBB,0x8A,0,0,0,0x79,0x17,0,0,0x75,0};
    const auto unlocked = code.size();
    code.insert(code.end(), {0x9D,0x6A,0,0xEB,0});
    const auto original = code.size();
    code.insert(code.end(), {0x9D,0xFF,0x77,0xFC});
    const auto tail = code.size();
    code[9] = static_cast<unsigned char>(original - 10);
    code[15] = static_cast<unsigned char>(original - 16);
    code[27] = static_cast<unsigned char>(unlocked - 28);
    code[39] = static_cast<unsigned char>(original - 40);
    code[unlocked + 4] = static_cast<unsigned char>(tail - unlocked - 5);
    code.insert(code.end(), {0x8B,0xC8,0xE9});
    const auto resume = SITE + 5 - (STUB + static_cast<uint32_t>(code.size()) + 4);
    for (unsigned i = 0; i < 4; ++i) code.push_back(static_cast<unsigned char>(resume >> (8 * i)));
    Bytes jump{0xE9};
    const auto relative = STUB - SITE - 5;
    for (unsigned i = 0; i < 4; ++i) jump.push_back(static_cast<unsigned char>(relative >> (8 * i)));
    return {{SITE, {0xFF,0x77,0xFC,0x8B,0xC8}, jump}, {STUB, Bytes(code.size(), 0), code}};
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
        cameraWheelInputPatch(),
        defenseAssistLevelPatch(),
        defenseAssistSettingsPatch(),
        hudPingPatch(),
        // Zero is a completed RTT sample; only negative pending samples should be skipped.
        {0x3B90A8, {0x0F,0x2F,0xC1,0x77,0x09}, {0x0F,0x2F,0xC1,0x73,0x09}}
    };
    for (auto& patch : matchMapFilterPatches()) patches.push_back(std::move(patch));
    for (auto& patch : matchSpawnModePatches()) patches.push_back(std::move(patch));
    for (auto& patch : trainingEntryPatches()) patches.push_back(std::move(patch));
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
