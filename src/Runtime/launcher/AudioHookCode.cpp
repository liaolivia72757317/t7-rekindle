#include "AudioSettings.h"
#include <cstring>

namespace t7 {
namespace {
class Code {
public:
    Bytes bytes;
    void emit(std::initializer_list<unsigned char> values) { bytes.insert(bytes.end(), values); }
    void word(uint32_t v) { for (unsigned s = 0; s < 32; s += 8) bytes.push_back(static_cast<unsigned char>(v >> s)); }
    size_t branch(unsigned char condition = 0) {
        if (condition) emit({0x0F, condition}); else emit({0xE9});
        const auto position = bytes.size(); word(0); return position;
    }
    void bind(size_t position, size_t target) {
        const auto delta = static_cast<uint32_t>(target - position - 4);
        std::memcpy(bytes.data() + position, &delta, 4);
    }
    void call(uint32_t target) { emit({0xB8}); word(target); emit({0xFF, 0xD0}); }
    size_t localCall() { emit({0xE8}); const auto p = bytes.size(); word(0); return p; }
    void store(uint32_t address, uint32_t value) { emit({0xC7, 0x05}); word(address); word(value); }
};
}
Bytes buildAudioHook(uint32_t address, uint32_t imageBase, uint32_t threadId) {
    if (!address || address > UINT32_MAX - AUDIO_PAGE_SIZE || !imageBase
        || imageBase > UINT32_MAX - 0x2E35ACC || !threadId)
        throw std::runtime_error("audio hook outside x86 range");
    Code c;
    c.emit({0x9C, 0x60, 0x64, 0xA1}); c.word(0x24);
    c.emit({0x3D}); c.word(threadId); const auto wrongThread = c.branch(0x85);
    c.emit({0xA1}); c.word(address);
    c.emit({0x83, 0xF8, 0x01}); const auto idle = c.branch(0x82);
    c.emit({0x83, 0xF8, 0x02}); const auto reentrant = c.branch(0x87);
    c.emit({0x8B, 0xD8}); c.store(address, 3);
    c.emit({0x8B, 0xEC, 0x81, 0xEC}); c.word(0x210);
    c.emit({0x83, 0xE4, 0xF0, 0x0F, 0xAE, 0x04, 0x24, 0xFC});
    const auto readFirst = c.localCall();
    c.emit({0x85, 0xC0}); const auto unavailable = c.branch(0x84);
    c.emit({0x83, 0xFB, 0x02}); const auto readOnly = c.branch(0x85);
    c.emit({0xBE}); c.word(address + 0x10); c.emit({0xBF}); c.word(address + 0x20);
    c.emit({0xB9}); c.word(4); c.emit({0xF3, 0xA7}); const auto conflict = c.branch(0x85);
    // Original setters update both sound engines; the audio-only handler then saves.
    for (const auto pair : {std::pair<uint32_t, uint32_t>{0x30, AUDIO_MUSIC_RVA}, {0x38, AUDIO_EFFECTS_RVA}}) {
        c.emit({0xFF, 0x35}); c.word(address + pair.first + 4);
        c.emit({0xFF, 0x35}); c.word(address + pair.first);
        c.emit({0x8B, 0x0D}); c.word(imageBase + 0x2E35AC8); c.call(imageBase + pair.second);
    }
    c.emit({0x68}); c.word(address + 0x100);
    c.emit({0x8B, 0x0D}); c.word(imageBase + 0x2E35AC8); c.call(imageBase + 0x6EA040);
    const auto readAfter = c.localCall();
    c.emit({0x85, 0xC0}); const auto unavailableAfter = c.branch(0x84);
    c.bind(readOnly, c.bytes.size()); c.store(address + 4, 1); const auto success = c.branch();
    c.bind(conflict, c.bytes.size()); c.store(address + 4, 2); const auto conflicted = c.branch();
    c.bind(unavailable, c.bytes.size()); c.bind(unavailableAfter, c.bytes.size()); c.store(address + 4, 0);
    c.bind(success, c.bytes.size()); c.bind(conflicted, c.bytes.size());
    c.emit({0x0F, 0xAE, 0x0C, 0x24, 0x8B, 0xE5}); c.store(address, 0);
    for (auto p : {wrongThread, idle, reentrant}) c.bind(p, c.bytes.size());
    c.emit({0x61, 0x9D, 0xC3});

    c.bind(readFirst, c.bytes.size()); c.bind(readAfter, c.bytes.size());
    std::vector<size_t> invalid;
    for (const auto pair : {std::pair<uint32_t, uint32_t>{0x243B714, 0x17355AC},
            {0x2E24998, 0x17797B4}, {0x24396D0, 0x177A9F4}, {0x2E35AC8, 0x17A7710}}) {
        c.emit({0xA1}); c.word(imageBase + pair.first); c.emit({0x85, 0xC0}); invalid.push_back(c.branch(0x84));
        c.emit({0x81, 0x38}); c.word(imageBase + pair.second); invalid.push_back(c.branch(0x85));
        if (pair.first == 0x243B714) {
            c.emit({0x8B, 0x40, 0x28, 0x85, 0xC0}); invalid.push_back(c.branch(0x84));
            c.emit({0x81, 0x38}); c.word(imageBase + 0x1735544); invalid.push_back(c.branch(0x85));
        }
    }
    c.emit({0x8B, 0xD0});
    for (uint32_t i = 0; i < 4; ++i) {
        if (i % 2 == 0) c.emit({0x0F, 0xB6, 0x42, static_cast<unsigned char>(0x14 + i * 4)});
        else c.emit({0x8B, 0x42, static_cast<unsigned char>(0x14 + i * 4)});
        c.emit({0xA3}); c.word(address + 0x10 + i * 4);
    }
    c.emit({0xB8}); c.word(1); c.emit({0xC3});
    for (auto p : invalid) c.bind(p, c.bytes.size());
    c.emit({0x33, 0xC0, 0xC3});
    if (c.bytes.size() > AUDIO_PAGE_SIZE - AUDIO_CODE_OFFSET) throw std::runtime_error("audio hook overflow");
    Bytes page(AUDIO_PAGE_SIZE, 0);
    std::memcpy(page.data() + AUDIO_CODE_OFFSET, c.bytes.data(), c.bytes.size());
    return page;
}
}
