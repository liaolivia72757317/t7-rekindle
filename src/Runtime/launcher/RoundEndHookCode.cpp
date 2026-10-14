#include "RoundEndHookCode.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"

namespace t7 {
namespace {
constexpr uint32_t INSTANCE_RVA = 0x2E37804, ROOM_RVA = 0x2E3A4C4, SCENE_RVA = 0x2E3CB88;
constexpr uint32_t FILE_TELL_RVA = 0x71570, FILE_LENGTH_RVA = 0x71580, FILE_READ_RVA = 0x717C0;
constexpr uint32_t LOBBY_METHOD_OFFSET = 477100, LOBBY_RESOURCE_SIZE = 902623;

WorldHookCode lobbyReturnHook(uint32_t address, uint32_t imageBase, bool customLobby) {
    const auto code = address + LOBBY_RETURN_OFFSET;
    const auto error = address + ROUND_END_DATA_OFFSET + static_cast<uint32_t>(offsetof(RoundEndMailbox, lobbyUiError));
    const uint32_t original = code + 0x380, path = code + 0x3A0;
    const std::string resource = "../data/ui/scripts_game.gfx";
    // GameFlow.onEnterLobby: replace trace("onEnterLobby") with this._enterUI = "".
    // Equal-length AVM2 instructions keep ABC offsets and the settlement flow intact.
    const Bytes before{0xD0,0x30,0x5D,0x39,0x2C,0x42,0x46,0x39,1,0x29,0xD0,0x27,0x68,0x3A};
    const Bytes after {0xD0,0x30,0xD0,0x2C,3,0x68,0x89,1,2,2,0xD0,0x27,0x68,0x3A};
    WorldHookCode c;
    c.emit({0x55,0x8B,0xEC,0x53,0x56,0x57,0x83,0xEC,8,0x8B,0xF1,0x8B,0x7E,8});
    c.emit({0xC7,0x45,0xF0,0,0,0,0,0x83,0xC8,0xFF,0x85,0xFF}); const auto noFile = c.branch(0x84);
    c.emit({0x81,0x7E,0x1C}); c.word(static_cast<uint32_t>(resource.size()));
    std::vector<size_t> native{c.branch(0x85)};
    c.emit({0x81,0x7E,0x20}); c.word(static_cast<uint32_t>(resource.size())); native.push_back(c.branch(0x82));
    c.emit({0x8B,0x76,0x0C,0x85,0xF6}); native.push_back(c.branch(0x84));
    c.emit({0xBA}); c.word(path); c.emit({0xB9}); c.word(static_cast<uint32_t>(resource.size()));
    const auto pathLoop = c.bytes.size();
    c.emit({0x8A,6,0x3C,0x5C}); const auto notSlash = c.branch(0x85);
    c.emit({0xB0,0x2F}); c.bind(notSlash);
    c.emit({0x3C,0x41}); const auto below = c.branch(0x82);
    c.emit({0x3C,0x5A}); const auto above = c.branch(0x87);
    c.emit({0x0C,0x20}); c.bind(below); c.bind(above);
    c.emit({0x3A,2}); native.push_back(c.branch(0x85));
    c.emit({0x46,0x42,0x49}); c.bind(c.branch(0x85), pathLoop);
    c.emit({0x8B,0xCF}); c.call(imageBase + FILE_TELL_RVA); c.emit({0x8B,0xD8});
    c.emit({0x8B,0xCF}); c.call(imageBase + FILE_LENGTH_RVA);
    c.emit({0x3D}); c.word(LOBBY_RESOURCE_SIZE); const auto knownSize = c.branch(0x84);
    c.store(error, 1); native.push_back(c.jump());
    c.bind(knownSize); c.emit({0xC7,0x45,0xF0,1,0,0,0});
    for (auto branch : native) c.bind(branch);
    c.emit({0xFF,0x75,0x0C,0xFF,0x75,8,0x8B,0xCF}); c.call(imageBase + FILE_READ_RVA);
    c.emit({0x89,0x45,0xEC,0x83,0x7D,0xF0,0}); std::vector<size_t> done{c.branch(0x84)};
    c.emit({0x85,0xC0}); done.push_back(c.branch(0x8E));
    if (customLobby) c.call(address + SOLO_ROOM_UI_OFFSET);
    c.emit({0x8B,0x45,0xEC});
    c.emit({0x81,0xFB}); c.word(LOBBY_METHOD_OFFSET + 14); done.push_back(c.branch(0x83));
    c.emit({0x03,0xC3,0x3D}); c.word(LOBBY_METHOD_OFFSET); done.push_back(c.branch(0x86));
    // Intersect the returned bytes with the method prefix, including split/short reads.
    c.emit({0x8B,0xD3,0x81,0xFA}); c.word(LOBBY_METHOD_OFFSET); const auto startsInside = c.branch(0x83);
    c.emit({0xBA}); c.word(LOBBY_METHOD_OFFSET); c.bind(startsInside);
    c.emit({0x3D}); c.word(LOBBY_METHOD_OFFSET + 14); const auto endsInside = c.branch(0x86);
    c.emit({0xB8}); c.word(LOBBY_METHOD_OFFSET + 14); c.bind(endsInside);
    c.emit({0x2B,0xC2,0x8B,0xC8,0x2B,0xDA,0xF7,0xDB,0x03,0x5D,8,0x8B,0xFB});
    c.emit({0x81,0xEA}); c.word(LOBBY_METHOD_OFFSET);
    c.emit({0x8D,0xB2}); c.word(original); c.emit({0x8B,0xD9,0xFC,0xF3,0xA6});
    const auto mismatch = c.branch(0x85);
    c.emit({0x2B,0xFB,0x2B,0xF3,0x83,0xC6,14,0x8B,0xCB,0xF3,0xA4}); done.push_back(c.jump());
    c.bind(mismatch); c.store(error, 1);
    for (auto branch : done) c.bind(branch);
    c.emit({0x8B,0x45,0xEC}); c.bind(noFile);
    c.emit({0x83,0xC4,8,0x5F,0x5E,0x5B,0x5D,0xC2,8,0});
    if (c.bytes.size() > 0x380) throw std::runtime_error("lobby return hook overflow");
    c.bytes.resize(0x380); c.bytes.insert(c.bytes.end(), before.begin(), before.end());
    c.bytes.insert(c.bytes.end(), after.begin(), after.end());
    c.bytes.resize(0x3A0); c.bytes.insert(c.bytes.end(), resource.begin(), resource.end());
    return c;
}

WorldHookCode soloRoomUiHook(uint32_t address) {
    constexpr uint32_t offset = 276986;
    const auto code = address + SOLO_ROOM_UI_OFFSET;
    const auto original = code + 0x200;
    const auto error = address + ROUND_END_DATA_OFFSET + static_cast<uint32_t>(offsetof(RoundEndMailbox, lobbyUiError));
    const Bytes before{0x5D,0x18,0x66,0x18,0x46,0x08,0,0x24,1,0x46,0x1C,1,0x73,0xD5,0x5D,0x18};
    // Keep formation selection and the room check; Rust validates roster and team rules.
    const Bytes after{0x10,0x5B,0,0,2,2,2,2,2,2,2,2,2,2,2,2};
    WorldHookCode c;
    c.emit({0x9C,0x60,0x81,0xFB}); c.word(offset + 16);
    std::vector<size_t> done{c.branch(0x83)};
    c.emit({0x8B,0x45,0xEC,0x03,0xC3,0x3D}); c.word(offset); done.push_back(c.branch(0x86));
    c.emit({0x8B,0xD3,0x81,0xFA}); c.word(offset); const auto startsInside = c.branch(0x83);
    c.emit({0xBA}); c.word(offset); c.bind(startsInside);
    c.emit({0x3D}); c.word(offset + 16); const auto endsInside = c.branch(0x86);
    c.emit({0xB8}); c.word(offset + 16); c.bind(endsInside);
    c.emit({0x2B,0xC2,0x8B,0xC8,0x2B,0xDA,0xF7,0xDB,0x03,0x5D,8,0x8B,0xFB});
    c.emit({0x81,0xEA}); c.word(offset);
    c.emit({0x8D,0xB2}); c.word(original); c.emit({0x8B,0xD9,0xFC,0xF3,0xA6});
    const auto mismatch = c.branch(0x85);
    c.emit({0x2B,0xFB,0x2B,0xF3,0x83,0xC6,16,0x8B,0xCB,0xF3,0xA4}); done.push_back(c.jump());
    c.bind(mismatch); c.store(error, 1);
    for (auto branch : done) c.bind(branch);
    c.emit({0x61,0x9D,0xC3});
    if (c.bytes.size() > 0x200) throw std::runtime_error("solo room UI hook overflow");
    c.bytes.resize(0x200); c.bytes.insert(c.bytes.end(), before.begin(), before.end());
    c.bytes.insert(c.bytes.end(), after.begin(), after.end());
    return c;
}

void epoch(WorldHookCode& c, uint32_t data) {
    // ESI is the live instance. A missing actor or a recreated service is not a new battle.
    c.emit({0x8B,0x46,0x68,0x8B,0x56,0x6C,0x3B,0x05}); c.word(data);
    const auto changed = c.branch(0x85);
    c.emit({0x3B,0x15}); c.word(data + 4); const auto same = c.branch(0x84);
    c.bind(changed);
    c.emit({0xA3}); c.word(data); c.emit({0x89,0x15}); c.word(data + 4);
    c.store(data + 8, 0); c.store(data + 12, 0);
    c.bind(same);
}

void jumpTo(WorldHookCode& c, uint32_t origin, uint32_t target) {
    c.emit({0xE9}); c.word(target - (origin + static_cast<uint32_t>(c.bytes.size()) + 4));
}
}

std::vector<std::pair<uint32_t, Bytes>> roundEndOriginals(uint32_t imageBase, bool customLobby) {
    WorldHookCode leave;
    leave.emit({0x8B,0x0D}); leave.word(imageBase + ROOM_RVA);
    std::vector<std::pair<uint32_t, Bytes>> result{{FINISH_RESULT_RVA, {0x8B,0x4D,0xD0,0x8B,0x46,0x10}}, {NATIVE_LEAVE_RVA, leave.bytes},
            {GFX_READ_RVA, {0x55,0x8B,0xEC,0x8B,0x49,8}}};
    if (customLobby) {
        for (const auto& site : LOBBY_IDENTITY_SITES)
            result.emplace_back(site.first, Bytes{0x8B,0x17,0x8B,0xCF,0x8B,0xF0,0xFF,0x52,0x28});
        result.emplace_back(NETWORK_HUD_RVA, NETWORK_HUD_ORIGINAL);
    }
    return result;
}

std::vector<std::pair<uint32_t, Bytes>> roundEndJumps(uint32_t address, uint32_t imageBase, bool customLobby) {
    std::vector<std::pair<uint32_t, Bytes>> result;
    for (auto item : {std::pair{FINISH_RESULT_RVA, FINISH_OBSERVER_OFFSET},
                     std::pair{NATIVE_LEAVE_RVA, LEAVE_OBSERVER_OFFSET},
                     std::pair{GFX_READ_RVA, LOBBY_RETURN_OFFSET}}) {
        WorldHookCode c;
        jumpTo(c, imageBase + item.first, address + item.second); c.emit({0x90});
        result.emplace_back(item.first, c.bytes);
    }
    if (customLobby) {
        WorldHookCode hud;
        jumpTo(hud, imageBase + NETWORK_HUD_RVA, address + NETWORK_HUD_OFFSET);
        result.emplace_back(NETWORK_HUD_RVA, hud.bytes);
        for (const auto& site : LOBBY_IDENTITY_SITES) {
            WorldHookCode c;
            jumpTo(c, imageBase + site.first, address + site.second);
            c.emit({0x90,0x90,0x90,0x90}); result.emplace_back(site.first, c.bytes);
        }
    }
    return result;
}

void verifyRoundEndFunctions(HANDLE process, uint32_t imageBase) {
    auto signatures = roundEndOriginals(imageBase);
    signatures.push_back({FINISH_RESULT_RVA + 6, {0x56,0x89,0x41,0x4C,0xE8,0xEE,0xD9,0xFF,0xFF}});
    WorldHookCode leave;
    leave.emit({0x6A,0,0xE8,0x73,0x2C,0xE8,0xFF,0xA1}); leave.word(imageBase + SCENE_RVA);
    leave.emit({0x83,0x78,0x24,0,0x74,5,0xE8,0x93,0x11,0xE0,0xFF,0xB8,1,0,0,0,0xC2,0x10,0});
    signatures.push_back({NATIVE_LEAVE_RVA + 6, leave.bytes});
    signatures.push_back({GFX_READ_RVA + 6, {0x85,0xC9,0x75,7,0x83,0xC8,0xFF,0x5D,0xC2,8,0,0x5D,0xE9,0x99,0xEC,0xC7,0xFF}});
    signatures.push_back({FILE_READ_RVA, {0x55,0x8B,0xEC,0x8B,0x49,0x30,0x85,0xC9,0x74,6,0x8B,1,0x5D,0xFF,0x60,0x18,0x33,0xC0,0x5D,0xC2,8,0}});
    for (const auto& getter : {std::pair{FILE_TELL_RVA, 0x10}, std::pair{FILE_LENGTH_RVA, 4}})
        signatures.push_back({getter.first, {0x8B,0x49,0x30,0x85,0xC9,0x74,5,0x8B,1,0xFF,0x60,
                                            static_cast<unsigned char>(getter.second),0x33,0xC0,0xC3}});
    for (const auto& entry : signatures)
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("round end native function signature mismatch");
}

Bytes buildRoundEndHooks(uint32_t address, uint32_t imageBase, bool customLobby) {
    const auto data = address + ROUND_END_DATA_OFFSET;
    Bytes result(0x1000, 0);
    auto copy = [&](const WorldHookCode& c, uint32_t offset) {
        if (c.bytes.size() > 0x400) throw std::runtime_error("round end hook overflow");
        std::memcpy(result.data() + offset - ROUND_END_CODE_OFFSET, c.bytes.data(), c.bytes.size());
    };
    WorldHookCode c;
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    c.emit({0x8B,0x35}); c.word(imageBase + INSTANCE_RVA);
    c.emit({0x85,0xF6}); std::vector<size_t> done{c.branch(0x84)};
    epoch(c, data);
    c.emit({0x83,0x7E,0x7C,5}); done.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(data + 8); c.emit({0}); done.push_back(c.branch(0x85));
    c.emit({0xA1}); c.word(data + 12); c.emit({0x48,0x83,0xF8,2}); done.push_back(c.branch(0x87));
    std::vector<size_t> failed;
    for (auto rva : {ROOM_RVA, SCENE_RVA}) {
        c.emit({0x83,0x3D}); c.word(imageBase + rva); c.emit({0}); failed.push_back(c.branch(0x84));
    }
    c.store(data + 8, 1); // Mark before entering the original UI leave action.
    c.emit({0x6A,0,0x6A,0,0x6A,0,0x6A,0}); c.call(imageBase + NATIVE_LEAVE_RVA);
    c.emit({0x84,0xC0}); done.push_back(c.branch(0x85));
    for (auto branch : failed) c.bind(branch);
    c.store(data + 8, 2);
    for (auto branch : done) c.bind(branch);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D,0xC3});
    copy(c, ROUND_END_CODE_OFFSET);
    if (c.bytes.size() > 0x300) throw std::runtime_error("round end hook overlaps lobby identity");
    if (customLobby) {
        c.bytes.clear();
        // The gateway has one connection; hide the duplicate legacy logic field.
        c.emit({0x8B,0x1D}); c.word(address + NETWORK_LATENCY_DATA_OFFSET);
        c.emit({0x89,0x1D}); c.word(address + NETWORK_LATENCY_DATA_OFFSET + offsetof(NetworkLatencyMailbox, displayedMs));
        c.emit({0x9C,0xFF,0x05}); c.word(address + NETWORK_LATENCY_DATA_OFFSET + offsetof(NetworkLatencyMailbox, reads));
        c.emit({0x9D});
        c.emit({0x8B,0xF3});
        c.emit({0xBB}); c.word(UINT32_MAX);
        c.bytes.insert(c.bytes.end(), NETWORK_HUD_ORIGINAL.begin(), NETWORK_HUD_ORIGINAL.end());
        jumpTo(c, address + NETWORK_HUD_OFFSET, imageBase + NETWORK_HUD_RVA + 5);
        if (c.bytes.size() > 0x80) throw std::runtime_error("network HUD hook overlaps finish observer");
        copy(c, NETWORK_HUD_OFFSET);
        for (const auto& site : LOBBY_IDENTITY_SITES) {
            c.bytes.clear(); c.emit({0x8B,0x17,0x8B,0xCF,0x8B,0xF0,0xA1}); c.word(address + LOBBY_IDENTITY_DATA_OFFSET);
            c.emit({0x85,0xC0}); const auto identified = c.branch(0x85);
            c.emit({0xFF,0x52,0x28}); c.bind(identified);
            c.emit({0xA3}); c.word(address + LOBBY_IDENTITY_DATA_OFFSET + offsetof(LobbyIdentityMailbox, uiPlayerId));
            c.emit({0x9C,0xFF,0x05});
            c.word(address + LOBBY_IDENTITY_DATA_OFFSET + (site.first == LOBBY_IDENTITY_RVA
                    ? offsetof(LobbyIdentityMailbox, createReads) : offsetof(LobbyIdentityMailbox, loginReads)));
            c.emit({0x9D});
            jumpTo(c, address + site.second, imageBase + site.first + 9);
            if (c.bytes.size() > 0x40) throw std::runtime_error("lobby identity hook overflow");
            copy(c, site.second);
        }
    }

    // Observe actual FINISH consumption, not a result field left over from the previous battle.
    c.bytes.clear(); c.emit({0x9C,0x60,0x8B,0xDE,0x8B,0x75,0xD0});
    epoch(c, data);
    c.emit({0x8B,0x43,0x10,0xA3}); c.word(data + 12);
    c.emit({0x61,0x9D,0x8B,0x4D,0xD0,0x8B,0x46,0x10});
    jumpTo(c, address + FINISH_OBSERVER_OFFSET, imageBase + FINISH_RESULT_RVA + 6);
    copy(c, FINISH_OBSERVER_OFFSET);

    // A manual leave uses the same entry and suppresses a later automatic request.
    c.bytes.clear(); c.emit({0x9C,0x60,0x8B,0x35}); c.word(imageBase + INSTANCE_RVA);
    c.emit({0x85,0xF6}); const auto noInstance = c.branch(0x84);
    epoch(c, data); c.store(data + 8, 1);
    c.bind(noInstance); c.emit({0x61,0x9D,0x8B,0x0D}); c.word(imageBase + ROOM_RVA);
    jumpTo(c, address + LEAVE_OBSERVER_OFFSET, imageBase + NATIVE_LEAVE_RVA + 6);
    copy(c, LEAVE_OBSERVER_OFFSET);
    if (c.bytes.size() > 0x100) throw std::runtime_error("leave observer overlaps solo room UI hook");
    if (customLobby) copy(soloRoomUiHook(address), SOLO_ROOM_UI_OFFSET);
    copy(lobbyReturnHook(address, imageBase, customLobby), LOBBY_RETURN_OFFSET);
    return result;
}
}
