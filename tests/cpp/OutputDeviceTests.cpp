#include "../../src/Runtime/launcher/OutputDevice.h"
#include <algorithm>
#include <cstring>
#include <iostream>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
uint32_t word(const t7::Bytes& bytes, size_t offset) {
    uint32_t value = 0; std::memcpy(&value, bytes.data() + offset, 4); return value;
}
}
bool verifyOutputDevice() {
    try {
        constexpr uint32_t remote = 0x12000000, base = 0x400000, ordinal = 257;
        const auto hooks = t7::buildOutputDeviceHooks(remote, base, ordinal);
        require(hooks.code.size() == 46 && hooks.patches.size() == 2, "output device hook layout");
        require(word(hooks.code, 6) == ordinal && word(hooks.code, 35) == ordinal, "adapter ordinal truncated or resolution source differs");
        require(remote + 18 + word(hooks.code, 14) == base + 0x2364E6, "renderer initialization return target");
        require(remote + 46 + word(hooks.code, 42) == base + 0x33B44A, "resolution enumeration return target");
        t7::Bytes image(0x340000, 0xCC);
        for (size_t i = 0; i < hooks.patches.size(); ++i) {
            const auto& patch = hooks.patches[i];
            require(patch.replacement.size() == 6 && patch.replacement[0] == 0xE9 && patch.replacement[5] == 0x90, "output device entry size");
            require(base + patch.rva + 5 + word(patch.replacement, 1) == remote + i * 32, "output device entry target");
            std::copy(patch.expected.begin(), patch.expected.end(), image.begin() + patch.rva);
        }
        t7::applyMemoryPatches(image, hooks.patches);
        require(image[0x2364E0] == 0xE9 && image[0x33B444] == 0xE9, "output device hooks not installed together");
        // Default selection leaves the client untouched, including when no process is provided.
        t7::installOutputDevice(nullptr, base, {});
        std::cout << "Output device selection and resolution hooks passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n'; return false;
    }
}
