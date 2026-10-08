#include "GraphicsSettings.h"
#include <cmath>
#include <cstring>
#include <cstdio>
#include <functional>

namespace t7 {
bool validGraphicsValues(const GraphicsValues& v) {
    return v.width >= 640 && v.width <= 7680 && v.height >= 480 && v.height <= 4320
        && v.fullScreen <= 1 && v.quality <= 4 && v.verticalSync <= 1 && v.fog <= 1
        && v.viewDistance >= 32 && v.viewDistance <= 1024 && v.ragDoll <= 1
        && v.frameLimit <= 1 && v.swoosh <= 2;
}
bool sameGraphicsValues(const GraphicsValues& a, const GraphicsValues& b) {
    return a.width == b.width && a.height == b.height && a.fullScreen == b.fullScreen
        && a.quality == b.quality && a.verticalSync == b.verticalSync && a.fog == b.fog
        && a.viewDistance == b.viewDistance && a.ragDoll == b.ragDoll
        && a.frameLimit == b.frameLimit && a.swoosh == b.swoosh;
}
GraphicsValues decodeGraphicsValues(const GraphicsRawValues& raw) {
    GraphicsValues v;
    char extra = 0;
    if (!std::memchr(raw.resolution, 0, sizeof(raw.resolution))
        || sscanf_s(raw.resolution, "%ux%u%c", &v.width, &v.height, &extra, 1u) != 2
        || !std::isfinite(raw.viewDistance) || raw.viewDistance < 32 || raw.viewDistance > 1024
        || std::floor(raw.viewDistance) != raw.viewDistance || raw.configLevel > 4
        || (raw.fps != 60 && raw.fps != 200))
        throw std::runtime_error("invalid graphics configuration snapshot");
    v.fullScreen = raw.fullScreen; v.quality = 4 - raw.configLevel;
    v.verticalSync = raw.verticalSync; v.fog = raw.fog;
    v.viewDistance = static_cast<uint32_t>(raw.viewDistance);
    v.ragDoll = raw.ragDoll; v.frameLimit = raw.fps == 60; v.swoosh = raw.swoosh;
    if (!validGraphicsValues(v)) throw std::runtime_error("graphics configuration outside supported range");
    return v;
}

namespace {
struct Field { const char* key; uint32_t type, value; };
Bytes buildMap(uint32_t address, uint32_t imageBase, const std::vector<Field>& fields) {
    // Read-only MSVC x86 map<string, DVariant>, including its sentinel node.
    Bytes bytes(0x640, 0);
    auto put = [&](size_t offset, uint32_t word) { std::memcpy(bytes.data() + offset, &word, 4); };
    const uint32_t head = address + 0x20;
    auto node = [&](size_t i) { return address + 0x80 + static_cast<uint32_t>(i) * 0x60; };
    put(0, head); put(4, static_cast<uint32_t>(fields.size()));
    bytes[0x2C] = 1; bytes[0x2D] = 1;
    std::function<uint32_t(size_t, size_t, uint32_t)> tree = [&](size_t first, size_t end, uint32_t parent) {
        if (first == end) return head;
        const auto i = (first + end) / 2, offset = static_cast<size_t>(node(i) - address);
        put(offset, tree(first, i, node(i))); put(offset + 4, parent);
        put(offset + 8, tree(i + 1, end, node(i))); bytes[offset + 0xC] = 1;
        const auto length = std::strlen(fields[i].key);
        std::memcpy(bytes.data() + offset + 0x10, fields[i].key, length);
        put(offset + 0x20, static_cast<uint32_t>(length)); put(offset + 0x24, 15);
        put(offset + 0x28, imageBase + 0x172E7A8); put(offset + 0x2C, 0x1FFFF);
        put(offset + 0x30, fields[i].type); put(offset + 0x34, fields[i].value);
        return node(i);
    };
    put(0x20, node(0)); put(0x24, tree(0, fields.size(), head)); put(0x28, node(fields.size() - 1));
    return bytes;
}
}
Bytes buildGraphicsMap(uint32_t address, uint32_t imageBase, const GraphicsValues& v) {
    if (!validGraphicsValues(v)) throw std::runtime_error("invalid graphics settings");
    auto bytes = buildMap(address, imageBase, {
        {"AudioChanged", 1, 0}, {"FrameOpen", 1, v.frameLimit}, {"NetChanged", 1, 0},
        {"OperChanged", 1, 0}, {"PicChanged", 1, 1}, {"Swoosh", 2, v.swoosh},
        {"fogEnable", 1, v.fog}, {"quality", 2, v.quality}, {"ragDollEnable", 1, v.ragDoll},
        {"resolution", 7, address + 0x600}, {"senseRange", 2, v.viewDistance},
        {"vSyncEnabled", 1, v.verticalSync}, {"windowsMode", 1, 1 - v.fullScreen}
    });
    const auto resolution = std::to_string(v.width) + "x" + std::to_string(v.height);
    std::memcpy(bytes.data() + 0x600, resolution.c_str(), resolution.size() + 1);
    // DVariant's string payload points to a std::string, not its character data.
    const auto length = static_cast<uint32_t>(resolution.size()); const uint32_t capacity = 15;
    std::memcpy(bytes.data() + 0x610, &length, 4); std::memcpy(bytes.data() + 0x614, &capacity, 4);
    return bytes;
}
Bytes buildAudioMap(uint32_t address, uint32_t imageBase) {
    return buildMap(address, imageBase, {{"AudioChanged", 1, 1}, {"NetChanged", 1, 0},
        {"OperChanged", 1, 0}, {"PicChanged", 1, 0}});
}
}
