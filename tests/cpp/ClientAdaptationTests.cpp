#include "../../src/Runtime/launcher/ClientAdaptation.h"
#include <iostream>
#include <algorithm>
#include <cstring>

namespace {
void require(bool result, const char* message) {
    if (!result) throw std::runtime_error(message);
}
void reject(const std::vector<t7::MemoryPatch>& patches) {
    t7::Bytes bytes(32, 0x55); const auto original = bytes;
    bool rejected = false;
    try { t7::applyMemoryPatches(bytes, patches); }
    catch (const std::runtime_error&) { rejected = true; }
    require(rejected && bytes == original, "memory patch rejection was not atomic");
}
}
bool verifyClientAdaptation() {
    try {
        t7::Bytes image(32, 0x55);
        t7::applyMemoryPatches(image, {{2, {0x55,0x55}, {0x33,0xC0}}, {4, {0x55}, {0xC3}}});
        require(image[1] == 0x55 && image[2] == 0x33 && image[3] == 0xC0
                && image[4] == 0xC3 && image[5] == 0x55, "memory patch roundtrip");
        reject({{1, {}, {}}}); reject({{1, {0x55}, {0x33,0xC0}}});
        reject({{UINT32_MAX, {0x55}, {0}}}); reject({{31, {0x55,0x55}, {0,0}}});
        reject({{1, {0x55}, {0}}, {2, {0x54}, {0}}});
        reject({{1, {0x55,0x55}, {0,0}}, {2, {0x55}, {0}}});
        bool rejected = false;
        try { t7::clientMemoryPatches(image); } catch (const std::runtime_error&) { rejected = true; }
        require(rejected, "unknown main image accepted");
        rejected = false;
        try { t7::tenProxyMemoryPatches(image, 0x10000000); } catch (const std::runtime_error&) { rejected = true; }
        require(rejected, "unknown protocol helper accepted");
        t7::Bytes client(0x03B54000, 0);
        size_t position = 0x1000;
        for (const auto* token : {"TerSafe", "TerSafe", "GeTssAntiService", "GeTssAntiService",
                                  "GeTssAntiService", "GeTssAntiService", "GeTssAntiService", "QQPCfix.dll"}) {
            std::copy_n(token, std::strlen(token), client.begin() + position); position += 32;
        }
        const t7::Bytes nodeName{'G','e','S','t','a','t','e','G','e','t','T','C','L','S','S','e','r','v','e','r'};
        std::copy(nodeName.begin(), nodeName.end(), client.begin() + 0x2000);
        const auto plan = t7::clientMemoryPatches(client);
        require(plan.size() == 16, "main adaptation coverage");
        for (const auto& patch : plan) std::copy(patch.expected.begin(), patch.expected.end(), client.begin() + patch.rva);
        t7::applyMemoryPatches(client, plan);
        const auto startup = std::find_if(plan.begin(), plan.end(), [](const auto& patch) { return patch.rva == 0x890B1; });
        require(startup != plan.end(), "startup initialization adaptation missing");
        const t7::Bytes dialogCall{0xFF,0x15,0x90,0x6A,0xAF,0x01};
        require(std::search(startup->replacement.begin(), startup->replacement.end(), dialogCall.begin(), dialogCall.end())
                    == startup->replacement.end(), "startup still depends on an ANSI dialog");
        require(std::equal(startup->expected.begin(), startup->expected.begin() + 9, startup->replacement.begin()),
                "successful startup initialization branch changed");
        const t7::Bytes returned{0xB0,0x01,0x5E,0xC3};
        require(std::equal(returned.begin(), returned.end(), startup->replacement.begin() + 9),
                "startup failure branch must return success without dialog arguments on the stack");
        require(std::equal(nodeName.begin(), nodeName.end(), client.begin() + 0x2000), "behavior-tree name was scrubbed");
        std::cout << "Client memory patch atomicity and baseline rejection cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Client adaptation test failure: " << error.what() << '\n'; return false;
    }
}
