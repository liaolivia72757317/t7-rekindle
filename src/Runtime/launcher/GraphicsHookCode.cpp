#include "GraphicsSettings.h"
#include <cstring>

namespace t7 {
namespace {
class Code {
public:
    Bytes bytes;
    uint32_t base;
    explicit Code(uint32_t address) : base(address) {}
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
    void absoluteCall(uint32_t target) { emit({0xB8}); word(target); emit({0xFF, 0xD0}); }
    size_t localCall() { emit({0xE8}); const auto p = bytes.size(); word(0); return p; }
    void store(uint32_t address, uint32_t value) { emit({0xC7, 0x05}); word(address); word(value); }
};
struct Field { const char* key; unsigned char type; };
const std::array<Field, 9> FIELDS{{
    {"TargetSize", 7}, {"FullScreen", 1}, {"ConfigLevel", 2}, {"vSync", 1},
    {"enable_distance_fog", 1}, {"distance_fog_end", 4}, {"ragDollEnable", 1}, {"FPS", 2}, {"Swoosh", 2}
}};
constexpr uint32_t WINDOW_STYLE_OFFSET = 0x94, RENDERER_FULLSCREEN_OFFSET = 0x98;
constexpr uint32_t CLIENT_RVA = 0x2D379F0, CLIENT_VTABLE_RVA = 0x176F28C;
constexpr uint32_t RENDERER_RVA = 0x2D20008, RENDERER_VTABLE_RVA = 0x175B188;
constexpr uint32_t GET_WINDOW_LONG_IMPORT_RVA = 0x16F6A40, SHOW_WINDOW_IMPORT_RVA = 0x16F6A6C;
}

Bytes buildGraphicsHook(uint32_t address, uint32_t imageBase, uint32_t threadId, uint32_t audioEntry) {
    if (!address || address > UINT32_MAX - GRAPHICS_PAGE_SIZE || !imageBase
        || imageBase > UINT32_MAX - 0x2E37804 || !threadId)
        throw std::runtime_error("graphics hook outside x86 range");
    Bytes page(GRAPHICS_PAGE_SIZE, 0);
    for (size_t i = 0; i < FIELDS.size(); ++i) {
        const auto offset = GRAPHICS_KEYS_OFFSET + i * 64;
        const auto length = static_cast<uint32_t>(std::strlen(FIELDS[i].key));
        const uint32_t data = address + static_cast<uint32_t>(offset) + 24, capacity = 31;
        std::memcpy(page.data() + offset, &data, 4);
        std::memcpy(page.data() + offset + 16, &length, 4);
        std::memcpy(page.data() + offset + 20, &capacity, 4);
        std::memcpy(page.data() + offset + 24, FIELDS[i].key, length);
    }
    Code c(address + GRAPHICS_CODE_OFFSET);
    c.emit({0x9C, 0x60}); // Preserve the original tick's flags and registers.
    if (audioEntry) c.absoluteCall(audioEntry);
    c.emit({0x64, 0xA1}); c.word(0x24); // TEB.ClientId.UniqueThread
    c.emit({0x3D}); c.word(threadId); const auto wrongThread = c.branch(0x85);
    c.emit({0xA1}); c.word(address);
    c.emit({0x83, 0xF8, 0x01}); const auto idle = c.branch(0x82);
    c.emit({0x83, 0xF8, 0x02}); const auto reentrant = c.branch(0x87);
    c.emit({0x8B, 0xD8}); // EBX is the command; original client methods preserve it.
    c.store(address, 3);
    c.emit({0x8B, 0xEC, 0x81, 0xEC}); c.word(0x210);
    c.emit({0x83, 0xE4, 0xF0, 0x0F, 0xAE, 0x04, 0x24, 0xFC}); // aligned FXSAVE, CLD
    const auto readFirst = c.localCall();
    c.emit({0x85, 0xC0}); std::vector<size_t> unavailable{c.branch(0x84)};
    c.emit({0x83, 0xFB, 0x02}); const auto readOnly = c.branch(0x85);
    c.emit({0xBE}); c.word(address + 0x10);
    c.emit({0xBF}); c.word(address + 0x50);
    c.emit({0xB9}); c.word(16); c.emit({0xF3, 0xA7}); const auto conflict = c.branch(0x85);
    // Render changes and file saving run through the game's own settings handler.
    for (const auto& pair : std::array<std::pair<uint32_t, uint32_t>, 3>{{
        {0x2E35AC8, 0x17A7710}, {0x2D379F0, 0x176F28C}, {0x24396C8, 0x173642C}}}) {
        c.emit({0xA1}); c.word(imageBase + pair.first); c.emit({0x85, 0xC0}); unavailable.push_back(c.branch(0x84));
        c.emit({0x81, 0x38}); c.word(imageBase + pair.second); unavailable.push_back(c.branch(0x85));
    }
    // Alt-tab leaves exclusive fullscreen minimized. SW_SHOW in the original handler does not restore it.
    c.emit({0x83, 0x3D}); c.word(address + GRAPHICS_DISPLAY_REQUEST_OFFSET); c.emit({0});
    const auto keepFullscreen = c.branch(0x85);
    c.emit({0x83, 0x3D}); c.word(address + 0x30); c.emit({0});
    const auto alreadyWindowed = c.branch(0x84);
    c.emit({0x8B, 0x0D}); c.word(imageBase + CLIENT_RVA);
    c.emit({0x6A, SW_RESTORE, 0xFF, 0x71, 0x24, 0xFF, 0x15}); c.word(imageBase + SHOW_WINDOW_IMPORT_RVA);
    c.bind(keepFullscreen, c.bytes.size()); c.bind(alreadyWindowed, c.bytes.size());
    c.emit({0x68}); c.word(address + GRAPHICS_MAP_OFFSET);
    c.emit({0x8B, 0x0D}); c.word(imageBase + 0x2E35AC8);
    c.absoluteCall(imageBase + 0x6EA040);
    const auto readAfter = c.localCall();
    c.emit({0x85, 0xC0}); unavailable.push_back(c.branch(0x84));
    // Saving the config is not proof that the renderer reset and window transition succeeded.
    c.emit({0xA1}); c.word(address + GRAPHICS_DISPLAY_REQUEST_OFFSET);
    c.emit({0x3B, 0x05}); c.word(address + 0x30); unavailable.push_back(c.branch(0x85));
    c.emit({0x3B, 0x05}); c.word(address + RENDERER_FULLSCREEN_OFFSET); unavailable.push_back(c.branch(0x85));
    c.emit({0xA1}); c.word(address + WINDOW_STYLE_OFFSET); c.emit({0xC1, 0xE8, 0x1F});
    c.emit({0x3B, 0x05}); c.word(address + GRAPHICS_DISPLAY_REQUEST_OFFSET); unavailable.push_back(c.branch(0x85));
    c.emit({0x85, 0xC0}); const auto fullscreenApplied = c.branch(0x85);
    c.emit({0xA1}); c.word(address + WINDOW_STYLE_OFFSET);
    c.emit({0x25}); c.word(WS_CAPTION); c.emit({0x3D}); c.word(WS_CAPTION); unavailable.push_back(c.branch(0x85));
    c.emit({0x83, 0x3D}); c.word(address + 0x70); c.emit({0}); // expected snapshot's fullscreen mode
    const auto unchangedMode = c.branch(0x84);
    c.emit({0xF7, 0x05}); c.word(address + WINDOW_STYLE_OFFSET); c.word(WS_MINIMIZE);
    unavailable.push_back(c.branch(0x85));
    c.bind(fullscreenApplied, c.bytes.size()); c.bind(unchangedMode, c.bytes.size());
    c.bind(readOnly, c.bytes.size()); c.store(address + 4, 1); const auto success = c.branch();
    c.bind(conflict, c.bytes.size()); c.store(address + 4, 2); const auto conflicted = c.branch();
    for (auto p : unavailable) c.bind(p, c.bytes.size());
    c.store(address + 4, 0);
    c.bind(success, c.bytes.size()); c.bind(conflicted, c.bytes.size());
    c.emit({0x0F, 0xAE, 0x0C, 0x24, 0x8B, 0xE5}); // FXRSTOR; original saved stack
    c.store(address, 0); // Publish completion last; host never reuses an in-flight mailbox.
    for (auto p : {wrongThread, idle, reentrant}) c.bind(p, c.bytes.size());
    c.emit({0x61, 0x9D});
    c.bytes.insert(c.bytes.end(), GRAPHICS_TICK_ORIGINAL.begin(), GRAPHICS_TICK_ORIGINAL.end());
    c.emit({0xE9}); c.word(imageBase + GRAPHICS_TICK_RVA + 7 - (c.base + static_cast<uint32_t>(c.bytes.size()) + 4));

    const auto read = c.bytes.size(); c.bind(readFirst, read); c.bind(readAfter, read);
    c.emit({0x53, 0x56, 0x57}); // snapshot subroutine, preserves EBX/ESI/EDI
    c.emit({0xA1}); c.word(imageBase + 0x243B714);
    c.emit({0x85, 0xC0}); std::vector<size_t> invalid{c.branch(0x84)};
    c.emit({0x81, 0x38}); c.word(imageBase + 0x17355AC); invalid.push_back(c.branch(0x85));
    c.emit({0x8B, 0x58, 0x28, 0x85, 0xDB}); invalid.push_back(c.branch(0x84));
    c.emit({0x81, 0x3B}); c.word(imageBase + 0x1735544); invalid.push_back(c.branch(0x85));
    c.emit({0xBF}); c.word(address + 0x10); c.emit({0x33, 0xC0, 0xB9}); c.word(16); c.emit({0xF3, 0xAB});
    for (size_t i = 0; i < FIELDS.size(); ++i) {
        c.emit({0x68}); c.word(address + GRAPHICS_KEYS_OFFSET + static_cast<uint32_t>(i) * 64);
        c.emit({0x8B, 0xCB, 0x8B, 0x03, 0xFF, 0x50, 0x50, 0x85, 0xC0}); invalid.push_back(c.branch(0x84));
        size_t defaultSwoosh = 0;
        if (i == 8) {
            // Older/new profiles omit Swoosh; the game's GetInt default is zero.
            c.emit({0x83, 0x78, 0x08, 0x00}); defaultSwoosh = c.branch(0x84);
        }
        c.emit({0x83, 0x78, 0x08, FIELDS[i].type}); invalid.push_back(c.branch(0x85));
        if (i == 0) {
            c.emit({0x8B, 0x70, 0x0C, 0x85, 0xF6}); invalid.push_back(c.branch(0x84));
            c.emit({0x83, 0x7E, 0x10, 0x00}); invalid.push_back(c.branch(0x84));
            c.emit({0x83, 0x7E, 0x10, 0x1E}); invalid.push_back(c.branch(0x87));
            c.emit({0x83, 0x7E, 0x14, 0x10}); const auto inlineString = c.branch(0x82);
            c.emit({0x8B, 0x36, 0x85, 0xF6}); invalid.push_back(c.branch(0x84));
            c.bind(inlineString, c.bytes.size());
            c.emit({0xBF}); c.word(address + 0x10); c.emit({0xB9}); c.word(31);
            const auto loop = c.bytes.size(); c.emit({0xAC, 0xAA, 0x84, 0xC0});
            const auto ended = c.branch(0x84);
            c.emit({0x49}); const auto again = c.branch(0x85); c.bind(again, loop);
            invalid.push_back(c.branch()); c.bind(ended, c.bytes.size());
        } else {
            if (FIELDS[i].type == 1) c.emit({0x0F, 0xB6, 0x40, 0x0C});
            else c.emit({0x8B, 0x40, 0x0C});
            c.emit({0xA3}); c.word(address + 0x30 + (static_cast<uint32_t>(i) - 1) * 4);
        }
        if (defaultSwoosh) c.bind(defaultSwoosh, c.bytes.size());
    }
    // The renderer commits its display flags only after a successful D3D reset; config is written earlier.
    c.emit({0x8B, 0x35}); c.word(imageBase + CLIENT_RVA);
    c.emit({0x85, 0xF6}); invalid.push_back(c.branch(0x84));
    c.emit({0x81, 0x3E}); c.word(imageBase + CLIENT_VTABLE_RVA); invalid.push_back(c.branch(0x85));
    c.emit({0x8B, 0x3D}); c.word(imageBase + RENDERER_RVA);
    c.emit({0x85, 0xFF}); invalid.push_back(c.branch(0x84));
    c.emit({0x81, 0x3F}); c.word(imageBase + RENDERER_VTABLE_RVA); invalid.push_back(c.branch(0x85));
    c.emit({0x83, 0xBF}); c.word(0x11C); c.emit({0}); invalid.push_back(c.branch(0x84));
    c.emit({0x8B, 0x46, 0x24, 0x85, 0xC0}); invalid.push_back(c.branch(0x84));
    c.emit({0x6A, 0xF0, 0x50, 0xFF, 0x15}); c.word(imageBase + GET_WINDOW_LONG_IMPORT_RVA);
    c.emit({0x85, 0xC0}); invalid.push_back(c.branch(0x84));
    c.emit({0xA3}); c.word(address + WINDOW_STYLE_OFFSET);
    c.emit({0xC1, 0xE8, 0x1F, 0x8B, 0x97}); c.word(0x920); // WS_POPUP and effective renderer fullscreen flag
    c.emit({0xC1, 0xEA, 0x02, 0x83, 0xE2, 0x01, 0x89, 0x15}); c.word(address + RENDERER_FULLSCREEN_OFFSET);
    c.emit({0x0B, 0xC2, 0xA3}); c.word(address + 0x30);
    c.emit({0xB8}); c.word(1); const auto complete = c.branch();
    for (auto p : invalid) c.bind(p, c.bytes.size());
    c.emit({0x33, 0xC0}); c.bind(complete, c.bytes.size()); c.emit({0x5F, 0x5E, 0x5B, 0xC3});
    if (c.bytes.size() > GRAPHICS_PAGE_SIZE - GRAPHICS_CODE_OFFSET) throw std::runtime_error("graphics hook overflow");
    std::memcpy(page.data() + GRAPHICS_CODE_OFFSET, c.bytes.data(), c.bytes.size());
    return page;
}
}
