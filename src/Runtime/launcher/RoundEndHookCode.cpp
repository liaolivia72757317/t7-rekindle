#include "RoundEndHookCode.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"

namespace t7r {
namespace {
constexpr uint32_t INSTANCE_RVA = 0x2E37804, ROOM_RVA = 0x2E3A4C4, SCENE_RVA = 0x2E3CB88;
constexpr uint32_t FILE_TELL_RVA = 0x71570, FILE_LENGTH_RVA = 0x71580, FILE_READ_RVA = 0x717C0;

std::vector<MemoryPatch> tutorialTabPatches() {
    // panel_newbie: use the native disabled state and retain the free-training tab.
    // Equal-length AVM2 edits preserve method, branch and exception-table offsets.
    std::vector<MemoryPatch> patches;
    struct ByteEdit { size_t offset; uint8_t value; };
    auto add = [&](uint32_t offset, Bytes before, std::initializer_list<ByteEdit> edits) {
        auto after = before;
        for (const auto& edit : edits) after.at(edit.offset) = edit.value;
        patches.push_back({offset, std::move(before), std::move(after)});
    };
    // initUILayout: show the free-training board, including saved tutorial selections.
    add(28595, {0xD0,0x66,0x2A,0x26,0x61,0xC6,1,0xD0,0x66,0x2B,0x27,0x61,0xC6,1,
                0xD0,0x66,0x2D,0x27,0x61,0xC6,1}, {{3,0x27},{17,0x26}});
    add(28703, {0xD0,0x66,0x28,0x26,0x61,0xE0,1}, {{2,0x29}});
    add(28742, {0xD0,0x66,0x26,0x26,0x61,0xE0,1}, {{2,0x29}});
    // onResetNavTab: returning from the lobby must not reselect a disabled tab.
    add(29206, {0xD0,0x30,0xD0,0x66,0x26,0x76,0x2A,0x12,8,0,0,0x29,0xD0,0x66,0x26,
                0x66,0xE0,1,0x96,0x12,7,0,0,0xD0,0x66,0x26,0x26,0x61,0xE0,1,0x47},
                {{4,0x29},{14,0x29},{25,0x29}});
    // Component initialization: basicTrainNavBtn.enabled = seniorTrainNavBtn.enabled = false.
    add(29299, {0xD0,0x66,0x26,0x26,0x61,0xCE,1}, {{3,0x27}});
    add(29459, {0xD0,0x66,0x28,0x26,0x61,0xCE,1}, {{3,0x27}});
    return patches;
}

WorldHookCode lobbyReturnHook(uint32_t address, uint32_t imageBase) {
    const auto code = address + LOBBY_RETURN_OFFSET;
    const auto error = address + ROUND_END_DATA_OFFSET + static_cast<uint32_t>(offsetof(RoundEndMailbox, lobbyUiError));
    constexpr uint32_t DATA_OFFSET = 0x240;
    // Zero-terminated resource rows: path, path length, file size, edit table.
    // Edit rows: file offset, length, original bytes; replacement bytes follow the originals.
    WorldHookCode data; data.bytes.resize(48, 0);
    auto writeWord = [&](size_t offset, uint32_t value) { std::memcpy(data.bytes.data() + offset, &value, 4); };
    auto resource = [&](size_t index, const std::string& path, uint32_t size, const std::vector<MemoryPatch>& patches) {
        const auto table = data.bytes.size();
        data.bytes.resize(table + (patches.size() + 1) * 12, 0);
        for (size_t i = 0; i < patches.size(); ++i) {
            const auto& patch = patches[i];
            writeWord(table + i * 12, patch.rva);
            writeWord(table + i * 12 + 4, static_cast<uint32_t>(patch.expected.size()));
            writeWord(table + i * 12 + 8, code + DATA_OFFSET + static_cast<uint32_t>(data.bytes.size()));
            data.bytes.insert(data.bytes.end(), patch.expected.begin(), patch.expected.end());
            data.bytes.insert(data.bytes.end(), patch.replacement.begin(), patch.replacement.end());
        }
        writeWord(index * 16, code + DATA_OFFSET + static_cast<uint32_t>(data.bytes.size()));
        writeWord(index * 16 + 4, static_cast<uint32_t>(path.size()));
        writeWord(index * 16 + 8, size);
        writeWord(index * 16 + 12, code + DATA_OFFSET + static_cast<uint32_t>(table));
        data.bytes.insert(data.bytes.end(), path.begin(), path.end()); data.bytes.push_back(0);
    };
    // GameFlow.onEnterLobby: replace trace("onEnterLobby") with this._enterUI = "".
    resource(0, "../data/ui/scripts_game.gfx", 902623, {{477100,
        {0xD0,0x30,0x5D,0x39,0x2C,0x42,0x46,0x39,1,0x29,0xD0,0x27,0x68,0x3A},
        {0xD0,0x30,0xD0,0x2C,3,0x68,0x89,1,2,2,0xD0,0x27,0x68,0x3A}}});
    resource(1, "../data/ui/panel_newbie.gfx", 39132, tutorialTabPatches());
    WorldHookCode c;
    c.emit({0x55,0x8B,0xEC,0x53,0x56,0x57,0x83,0xEC,16,0x8B,0xD9,0x8B,0x7B,8});
    c.emit({0xC7,0x45,0xF0,0,0,0,0,0x83,0xC8,0xFF,0x85,0xFF}); const auto noFile = c.branch(0x84);
    c.emit({0xBE}); c.word(code + DATA_OFFSET);
    const auto resourceLoop = c.bytes.size();
    c.emit({0x8B,0x4E,4,0x39,0x4B,0x1C});
    std::vector<size_t> native{c.branch(0x85)};
    c.emit({0x39,0x4B,0x20}); native.push_back(c.branch(0x82));
    c.emit({0x8B,0x53,0x0C,0x85,0xD2}); native.push_back(c.branch(0x84));
    c.emit({0x8B,0x3E});
    const auto pathLoop = c.bytes.size();
    c.emit({0x8A,2,0x3C,0x5C}); const auto notSlash = c.branch(0x85);
    c.emit({0xB0,0x2F}); c.bind(notSlash);
    c.emit({0x3C,0x41}); const auto below = c.branch(0x82);
    c.emit({0x3C,0x5A}); const auto above = c.branch(0x87);
    c.emit({0x0C,0x20}); c.bind(below); c.bind(above);
    c.emit({0x3A,7}); native.push_back(c.branch(0x85));
    c.emit({0x42,0x47,0x49}); c.bind(c.branch(0x85), pathLoop);
    c.emit({0x8B,0x7B,8,0x8B,0xCF}); c.call(imageBase + FILE_TELL_RVA); c.emit({0x89,0x45,0xEC});
    c.emit({0x8B,0xCF}); c.call(imageBase + FILE_LENGTH_RVA);
    c.emit({0x3B,0x46,8}); const auto knownSize = c.branch(0x84);
    c.store(error, 1); const auto invalidSize = c.jump();
    c.bind(knownSize); c.emit({0x8B,0x46,12,0x89,0x45,0xF0}); const auto matched = c.jump();
    for (auto branch : native) c.bind(branch);
    c.emit({0x83,0xC6,16,0x83,0x3E,0}); c.bind(c.branch(0x85), resourceLoop);
    c.bind(invalidSize); c.bind(matched); c.emit({0x8B,0x7B,8});
    c.emit({0xFF,0x75,0x0C,0xFF,0x75,8,0x8B,0xCF}); c.call(imageBase + FILE_READ_RVA);
    c.emit({0x89,0x45,0xE8,0x8B,0x5D,0xF0,0x85,0xDB}); std::vector<size_t> done{c.branch(0x84)};
    c.emit({0x85,0xC0}); done.push_back(c.branch(0x8E));
    const auto patchLoop = c.bytes.size();
    c.emit({0x8B,0x4B,4,0x85,0xC9}); done.push_back(c.branch(0x84));
    c.emit({0x8B,0x13,0x03,0xCA,0x39,0x4D,0xEC}); std::vector<size_t> next{c.branch(0x83)};
    c.emit({0x8B,0x45,0xEC,0x03,0x45,0xE8,0x3B,0xC2}); next.push_back(c.branch(0x86));
    // Intersect each edit with the bytes actually returned, including split/short reads.
    c.emit({0x3B,0x55,0xEC}); const auto startsInside = c.branch(0x83);
    c.emit({0x8B,0x55,0xEC}); c.bind(startsInside);
    c.emit({0x3B,0xC1}); const auto endsInside = c.branch(0x86);
    c.emit({0x8B,0xC1}); c.bind(endsInside);
    c.emit({0x8B,0xFA,0x2B,0x7D,0xEC,0x03,0x7D,8,0x8B,0xF2,0x2B,0x33,0x03,0x73,8});
    c.emit({0x8B,0xC8,0x2B,0xCA,0x89,0x4D,0xE4,0xFC,0xF3,0xA6});
    const auto mismatch = c.branch(0x85);
    c.emit({0x8B,0x4D,0xE4,0x2B,0xF9,0x2B,0xF1,0x03,0x73,4,0xF3,0xA4});
    for (auto branch : next) c.bind(branch);
    c.emit({0x83,0xC3,12}); c.bind(c.jump(), patchLoop);
    c.bind(mismatch); c.store(error, 1);
    for (auto branch : done) c.bind(branch);
    c.emit({0x8B,0x45,0xE8}); c.bind(noFile);
    c.emit({0x83,0xC4,16,0x5F,0x5E,0x5B,0x5D,0xC2,8,0});
    if (c.bytes.size() > DATA_OFFSET) throw std::runtime_error("lobby UI hook overflow");
    c.bytes.resize(DATA_OFFSET); c.bytes.insert(c.bytes.end(), data.bytes.begin(), data.bytes.end());
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

std::vector<std::pair<uint32_t, Bytes>> roundEndOriginals(uint32_t imageBase) {
    WorldHookCode leave;
    leave.emit({0x8B,0x0D}); leave.word(imageBase + ROOM_RVA);
    return {{FINISH_RESULT_RVA, {0x8B,0x4D,0xD0,0x8B,0x46,0x10}}, {NATIVE_LEAVE_RVA, leave.bytes},
            {GFX_READ_RVA, {0x55,0x8B,0xEC,0x8B,0x49,8}}};
}

std::vector<std::pair<uint32_t, Bytes>> roundEndJumps(uint32_t address, uint32_t imageBase) {
    std::vector<std::pair<uint32_t, Bytes>> result;
    for (auto item : {std::pair{FINISH_RESULT_RVA, FINISH_OBSERVER_OFFSET},
                     std::pair{NATIVE_LEAVE_RVA, LEAVE_OBSERVER_OFFSET},
                     std::pair{GFX_READ_RVA, LOBBY_RETURN_OFFSET}}) {
        WorldHookCode c;
        jumpTo(c, imageBase + item.first, address + item.second); c.emit({0x90});
        result.emplace_back(item.first, c.bytes);
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

Bytes buildRoundEndHooks(uint32_t address, uint32_t imageBase) {
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
    copy(lobbyReturnHook(address, imageBase), LOBBY_RETURN_OFFSET);
    return result;
}
}
