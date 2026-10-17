#include "GraphicsSettings.h"
#include "RemoteImage.h"
#include <cstring>

namespace t7r {
namespace {
void write(HANDLE process, uint32_t address, const void* data, size_t size) {
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(address)), data, size, &actual)
        || actual != size) throw std::runtime_error(errorText("graphics mailbox write"));
    const auto checked = readClientMemory(process, address, size);
    if (std::memcmp(data, checked.data(), size)) throw std::runtime_error("graphics mailbox readback mismatch");
}
}
void GraphicsSettings::attach(HANDLE process, uint32_t address, uint32_t imageBase) {
    if (process_ || !process || !address) throw std::runtime_error("graphics mailbox attachment state");
    process_ = process; remote_ = address; imageBase_ = imageBase;
}
void GraphicsSettings::clear() {
    process_ = nullptr; remote_ = imageBase_ = pending_ = 0; requestedAt_ = 0; hasValues_ = false; lastRaw_ = {};
}
void GraphicsSettings::request(uint32_t command) {
    // A request becomes visible only after its immutable payload has been written.
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process_, reinterpret_cast<void*>(static_cast<uintptr_t>(remote_)), &command, 4, &actual) || actual != 4)
        throw std::runtime_error(errorText("graphics request publication"));
    pending_ = command; requestedAt_ = GetTickCount64();
}
bool GraphicsSettings::poll(GraphicsValues& values, uint32_t& result, bool& applied) {
    if (!process_) return false;
    if (!pending_) { request(1); return false; }
    const auto bytes = readClientMemory(process_, remote_, 0x50);
    uint32_t command = 0; std::memcpy(&command, bytes.data(), 4);
    if (command != 0) {
        if (GetTickCount64() - requestedAt_ > 10000) throw std::runtime_error("graphics frame thread response timed out");
        return false;
    }
    applied = pending_ == 2; pending_ = 0;
    std::memcpy(&result, bytes.data() + 4, 4);
    hasValues_ = result == 1 || result == 2;
    if (hasValues_) {
        std::memcpy(&lastRaw_, bytes.data() + 0x10, sizeof(lastRaw_));
        values = decodeGraphicsValues(lastRaw_);
    }
    return true;
}
void GraphicsSettings::apply(const GraphicsValues& values) {
    if (!process_ || pending_ || !hasValues_) throw std::runtime_error("graphics bridge is not ready to apply");
    const auto map = buildGraphicsMap(remote_ + GRAPHICS_MAP_OFFSET, imageBase_, values);
    write(process_, remote_ + GRAPHICS_MAP_OFFSET, map.data(), map.size());
    write(process_, remote_ + 0x50, &lastRaw_, sizeof(lastRaw_));
    write(process_, remote_ + GRAPHICS_DISPLAY_REQUEST_OFFSET, &values.fullScreen, sizeof(values.fullScreen));
    request(2);
}
}
