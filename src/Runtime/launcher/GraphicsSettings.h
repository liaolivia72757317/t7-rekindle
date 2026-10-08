#pragma once
#include "../core/Common.h"
#include "AudioSettings.h"
#include <array>

namespace t7 {
struct GraphicsValues {
    uint32_t width = 1920, height = 1080;
    uint32_t fullScreen = 0, quality = 4, verticalSync = 0, fog = 0;
    uint32_t viewDistance = 128, ragDoll = 1, frameLimit = 0, swoosh = 0;
};
bool validGraphicsValues(const GraphicsValues& value);
bool sameGraphicsValues(const GraphicsValues& left, const GraphicsValues& right);

// The mailbox is consumed only on the client's original frame thread.
struct GraphicsRawValues {
    char resolution[32]{};
    uint32_t fullScreen = 0, configLevel = 0, verticalSync = 0, fog = 0;
    float viewDistance = 0;
    uint32_t ragDoll = 0, fps = 0, swoosh = 0;
};
static_assert(sizeof(GraphicsRawValues) == 64, "graphics mailbox layout");
GraphicsValues decodeGraphicsValues(const GraphicsRawValues& raw);
constexpr uint32_t GRAPHICS_TICK_RVA = 0x9EEB0;
constexpr uint32_t GRAPHICS_CODE_OFFSET = 0x1000, GRAPHICS_PAGE_SIZE = 0x2000;
constexpr uint32_t GRAPHICS_MAP_OFFSET = 0x100, GRAPHICS_KEYS_OFFSET = 0x900;
constexpr uint32_t GRAPHICS_DISPLAY_REQUEST_OFFSET = 0x90;
inline const Bytes GRAPHICS_TICK_ORIGINAL{0x56, 0x8B, 0xF1, 0x80, 0x7E, 0x7C, 0x00};
Bytes buildGraphicsMap(uint32_t address, uint32_t imageBase, const GraphicsValues& values);
Bytes buildGraphicsHook(uint32_t address, uint32_t imageBase, uint32_t threadId, uint32_t audioEntry = 0);

class GraphicsSettings final {
public:
    void install(HANDLE process, uint32_t imageBase, DWORD threadId);
    void clear(); // Called after the owned process has exited.
    // false means no completed read yet; result: 0 not ready, 1 ready, 2 conflict.
    bool poll(GraphicsValues& values, uint32_t& result, bool& applied);
    void apply(const GraphicsValues& values);
    AudioSettings& audio() { return audio_; }
private:
    AudioSettings audio_;
    void request(uint32_t command);
    HANDLE process_ = nullptr;
    uint32_t remote_ = 0, imageBase_ = 0, pending_ = 0;
    ULONGLONG requestedAt_ = 0;
    GraphicsRawValues lastRaw_{};
    bool hasValues_ = false;
};
}
