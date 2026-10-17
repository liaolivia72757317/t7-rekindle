#include "../../src/Runtime/launcher/ClientAdaptation.h"
#include <iostream>
#include <algorithm>
#include <cstring>

namespace {
void require(bool result, const char* message) {
    if (!result) throw std::runtime_error(message);
}
void reject(const std::vector<t7r::MemoryPatch>& patches) {
    t7r::Bytes bytes(32, 0x55); const auto original = bytes;
    bool rejected = false;
    try { t7r::applyMemoryPatches(bytes, patches); }
    catch (const std::runtime_error&) { rejected = true; }
    require(rejected && bytes == original, "memory patch rejection was not atomic");
}
}
bool verifyClientAdaptation() {
    try {
        t7r::Bytes image(32, 0x55);
        t7r::applyMemoryPatches(image, {{2, {0x55,0x55}, {0x33,0xC0}}, {4, {0x55}, {0xC3}}});
        require(image[1] == 0x55 && image[2] == 0x33 && image[3] == 0xC0
                && image[4] == 0xC3 && image[5] == 0x55, "memory patch roundtrip");
        reject({{1, {}, {}}}); reject({{1, {0x55}, {0x33,0xC0}}});
        reject({{UINT32_MAX, {0x55}, {0}}}); reject({{31, {0x55,0x55}, {0,0}}});
        reject({{1, {0x55}, {0}}, {2, {0x54}, {0}}});
        reject({{1, {0x55,0x55}, {0,0}}, {2, {0x55}, {0}}});
        bool rejected = false;
        try { t7r::clientMemoryPatches(image); } catch (const std::runtime_error&) { rejected = true; }
        require(rejected, "unknown main image accepted");
        rejected = false;
        try { t7r::tenProxyMemoryPatches(image, 0x10000000); } catch (const std::runtime_error&) { rejected = true; }
        require(rejected, "unknown protocol helper accepted");
        t7r::Bytes client(0x03B54000, 0);
        size_t position = 0x1000;
        for (const auto* token : {"TerSafe", "TerSafe", "GeTssAntiService", "GeTssAntiService",
                                  "GeTssAntiService", "GeTssAntiService", "GeTssAntiService", "QQPCfix.dll"}) {
            std::copy_n(token, std::strlen(token), client.begin() + position); position += 32;
        }
        const t7r::Bytes nodeName{'G','e','S','t','a','t','e','G','e','t','T','C','L','S','S','e','r','v','e','r'};
        std::copy(nodeName.begin(), nodeName.end(), client.begin() + 0x2000);
        const auto plan = t7r::clientMemoryPatches(client);
        require(plan.size() == 26, "main adaptation coverage");
        const auto trainingSite = std::find_if(plan.begin(), plan.end(), [](const auto& p) { return p.rva == 0x5A72F2; });
        const auto trainingStub = std::find_if(plan.begin(), plan.end(), [](const auto& p) { return p.rva == 0x16E8F40; });
        require(trainingSite != plan.end() && trainingStub != plan.end(), "training entry adaptation missing");
        require(trainingSite->expected == t7r::Bytes({0xFF,0x77,0xFC,0x8B,0xC8}), "training hook must stay in UI serialization");
        const auto& training = trainingStub->replacement;
        require(training[0] == 0x9C && training[7] == 4 && training[13] == 7
            && training[22] == 0x76 && training[23] == 0x17
            && training[34] == 0x79 && training[35] == 0x17,
            "training entry must match only the two map predecessor conditions");
        require(10 + training[9] == 45 && 16 + training[15] == 45
            && 28 + training[27] == 40 && 40 + training[39] == 45
            && training[40] == 0x9D && training[41] == 0x6A && training[42] == 0
            && training[45] == 0x9D && training[46] == 0xFF && training[48] == 0xFC,
            "training branch must retain flags, the level limit and unrelated row values");
        const auto spawnSite = std::find_if(plan.begin(), plan.end(), [](const auto& p) { return p.rva == 0x721296; });
        const auto spawnStub = std::find_if(plan.begin(), plan.end(), [](const auto& p) { return p.rva == 0x16E8F00; });
        require(spawnSite != plan.end() && spawnStub != plan.end(), "match spawn mode adaptation missing");
        require(spawnSite->expected == t7r::Bytes({0x8B,0x80,0xB8,0,0,0})
            && spawnStub->replacement == t7r::Bytes({
                0x81,0x38,0x06,0x04,0,0,0x75,0x06,0xB8,0x01,0,0,0,0xC3,
                0x8B,0x80,0xB8,0,0,0,0xC3}),
            "only map 1030 should use native system spawn; other maps keep their resource mode");
        int32_t spawnJump = 0;
        std::memcpy(&spawnJump, spawnSite->replacement.data() + 1, 4);
        require(spawnSite->replacement[0] == 0xE9 && spawnSite->replacement[5] == 0x90
            && spawnSite->rva + 5 + spawnJump == spawnStub->rva,
            "spawn mode jump must preserve the getter's null-resource branch");
        const auto mapSite = std::find_if(plan.begin(), plan.end(), [](const auto& p) { return p.rva == 0x53A001; });
        const auto mapStub = std::find_if(plan.begin(), plan.end(), [](const auto& p) { return p.rva == 0x16E8E00; });
        require(mapSite != plan.end() && mapStub != plan.end(), "map filter adaptation missing");
        require(mapSite->expected == t7r::Bytes({0xFF,0x77,0x08,0x8B,0xC8})
            && mapStub->replacement[0] == 0x9C && mapStub->replacement[1] == 0x83
            && mapStub->replacement[3] == 1 && mapStub->replacement[12] == 14
            && mapStub->replacement[24] == 6 && mapStub->replacement[102] == 83,
            "map filter must scope the original row and allow one of six team maps");
        const uint32_t teamValues[] = {6, 1003, 1030, 1021, 1001, 6003, 1034};
        for (size_t index = 0; index < 7; ++index) {
            const auto at = 30 + index * 10;
            uint32_t offset = 0, value = 0;
            std::memcpy(&offset, mapStub->replacement.data() + at + 2, 4);
            std::memcpy(&value, mapStub->replacement.data() + at + 6, 4);
            require(mapStub->replacement[at] == 0xC7 && mapStub->replacement[at + 1] == 0x87
                && offset == 0x4B2 + index * 4 && value == teamValues[index], "native team map catalogue differs");
        }
        require(6 + mapStub->replacement[5] == 105 && 18 + mapStub->replacement[17] == 30
            && 30 + mapStub->replacement[29] == 105 && mapStub->replacement[105] == 0x9D,
            "map row guards must restore flags and retain unrelated rows");
        for (const auto& patch : plan) std::copy(patch.expected.begin(), patch.expected.end(), client.begin() + patch.rva);
        t7r::applyMemoryPatches(client, plan);
        const auto startup = std::find_if(plan.begin(), plan.end(), [](const auto& patch) { return patch.rva == 0x890B1; });
        require(startup != plan.end(), "startup initialization adaptation missing");
        const t7r::Bytes dialogCall{0xFF,0x15,0x90,0x6A,0xAF,0x01};
        require(std::search(startup->replacement.begin(), startup->replacement.end(), dialogCall.begin(), dialogCall.end())
                    == startup->replacement.end(), "startup still depends on an ANSI dialog");
        require(std::equal(startup->expected.begin(), startup->expected.begin() + 9, startup->replacement.begin()),
                "successful startup initialization branch changed");
        const t7r::Bytes returned{0xB0,0x01,0x5E,0xC3};
        require(std::equal(returned.begin(), returned.end(), startup->replacement.begin() + 9),
                "startup failure branch must return success without dialog arguments on the stack");
        require(std::equal(nodeName.begin(), nodeName.end(), client.begin() + 0x2000), "behavior-tree name was scrubbed");
        std::cout << "Client memory patch atomicity and baseline rejection cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Client adaptation test failure: " << error.what() << '\n'; return false;
    }
}
