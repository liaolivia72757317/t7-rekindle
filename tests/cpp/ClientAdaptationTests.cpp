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
        require(std::equal(nodeName.begin(), nodeName.end(), client.begin() + 0x2000), "behavior-tree name was scrubbed");
        std::cout << "Client memory patch atomicity and baseline rejection cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Client adaptation test failure: " << error.what() << '\n'; return false;
    }
}
