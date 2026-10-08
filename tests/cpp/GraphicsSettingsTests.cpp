#include "../../src/Runtime/launcher/GraphicsSettings.h"
#include "../../src/Runtime/bridge/T7NativeBridge.h"
#include <cstring>
#include <iostream>
#include <limits>

namespace {
void require(bool condition, const char* message) { if (!condition) throw std::runtime_error(message); }
uint32_t word(const t7::Bytes& bytes, size_t offset) {
    require(offset + 4 <= bytes.size(), "map pointer outside mailbox");
    uint32_t v = 0; std::memcpy(&v, bytes.data() + offset, 4); return v;
}
}
bool verifyGraphicsSettings() {
    try {
        require(sizeof(T7NativeGraphicsSnapshot) == 64 && offsetof(T7NativeGraphicsSnapshot, values) == 20, "graphics ABI");
        t7::GraphicsValues values;
        require(t7::validGraphicsValues(values), "default graphics values");
        auto invalid = values; invalid.quality = 5; require(!t7::validGraphicsValues(invalid), "quality range");
        invalid = values; invalid.fullScreen = 2; require(!t7::validGraphicsValues(invalid), "boolean range");
        invalid = values; invalid.width = 0; require(!t7::validGraphicsValues(invalid), "resolution range");
        invalid = values; invalid.swoosh = 3; require(!t7::validGraphicsValues(invalid), "swoosh range");
        t7::GraphicsRawValues raw{}; strcpy_s(raw.resolution, "1920x1080");
        raw.configLevel = 0; raw.viewDistance = 159; raw.ragDoll = 1; raw.fps = 200; raw.swoosh = 1;
        const auto parsed = t7::decodeGraphicsValues(raw);
        require(parsed.quality == 4 && parsed.viewDistance == 159 && parsed.frameLimit == 0 && parsed.swoosh == 1, "game field mapping");
        raw.configLevel = 4; raw.fps = 60;
        require(t7::decodeGraphicsValues(raw).quality == 0 && t7::decodeGraphicsValues(raw).frameLimit == 1, "reverse quality/FPS mapping");
        raw.viewDistance = std::numeric_limits<float>::quiet_NaN();
        bool rejected = false; try { t7::decodeGraphicsValues(raw); } catch (const std::exception&) { rejected = true; }
        require(rejected, "non-finite distance accepted");
        constexpr uint32_t BASE = 0x81230000;
        const auto map = t7::buildGraphicsMap(BASE, 0x400000, parsed);
        const auto head = word(map, 0); require(word(map, 4) == 13 && map[head - BASE + 13] == 1, "map sentinel");
        auto find = [&](const char* key, uint32_t type, uint32_t expected) {
            auto node = word(map, head - BASE + 4);
            for (unsigned i = 0; i < 14 && node != head; ++i) {
                const auto offset = node - BASE;
                const std::string current(reinterpret_cast<const char*>(map.data() + offset + 16), word(map, offset + 32));
                const auto comparison = current.compare(key);
                if (!comparison) return word(map, offset + 0x30) == type && word(map, offset + 0x34) == expected;
                node = word(map, offset + (comparison < 0 ? 8 : 0));
            }
            return false;
        };
        require(find("PicChanged", 1, 1) && find("OperChanged", 1, 0) && find("AudioChanged", 1, 0)
            && find("NetChanged", 1, 0), "graphics-only apply flags");
        require(find("quality", 2, 4) && find("senseRange", 2, 159) && find("windowsMode", 1, 1)
            && find("FrameOpen", 1, 0) && find("Swoosh", 2, 1), "apply map values");
        require(find("resolution", 7, BASE + 0x600) && std::string(reinterpret_cast<const char*>(map.data() + 0x600)) == "1920x1080", "variant string");
        require(word(map, 0x610) == 9 && word(map, 0x614) == 15, "resolution variant must own an x86 std::string, not a C string");
        const auto hook = t7::buildGraphicsHook(BASE, 0x400000, 123);
        require(hook.size() == t7::GRAPHICS_PAGE_SIZE && hook[t7::GRAPHICS_CODE_OFFSET] == 0x9C, "hook layout");
        require(sizeof(T7NativeAudioSnapshot) == 40 && offsetof(T7NativeAudioSnapshot, values) == 20, "audio ABI");
        t7::AudioValues audio;
        require(t7::validAudioValues(audio), "default audio values");
        audio.musicVolume = 0; audio.effectsVolume = 1;
        require(t7::validAudioValues(audio), "audio endpoints");
        audio.musicVolume = std::numeric_limits<float>::quiet_NaN();
        require(!t7::validAudioValues(audio), "audio NaN accepted");
        audio.musicVolume = 1.01f; require(!t7::validAudioValues(audio), "audio upper bound");
        audio.musicVolume = -0.01f; require(!t7::validAudioValues(audio), "audio lower bound");
        audio.musicVolume = 1; audio.musicMuted = 2; require(!t7::validAudioValues(audio), "audio mute flag");
        const auto audioMap = t7::buildAudioMap(BASE, 0x400000);
        require(word(audioMap, 4) == 4, "audio-only map count");
        for (size_t i = 0; i < 4; ++i) {
            const auto offset = 0x80 + i * 0x60;
            require(word(audioMap, offset + 0x30) == 1 && word(audioMap, offset + 0x34) == (i == 0 ? 1u : 0u),
                "audio save enables an unrelated settings category");
        }
        std::cout << "Graphics value validation, game mappings, read-only settings map and ABI passed\n";
        return true;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return false; }
}
