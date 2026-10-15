#pragma once
#include "../../src/Runtime/launcher/TitleSettings.h"
#include "../../src/Runtime/launcher/RemoteImage.h"
#include <algorithm>
#include <cstring>

namespace titleFixture {
constexpr uint32_t BASE = 0x400000, OWNER = BASE + 0x28000, ROWS = BASE + 0x29000, CALLS = OWNER + 0x40;
constexpr uint32_t INDEX_ROWS = CALLS + 4, INDEX_COUNT = CALLS + 8, INDEX_CALLS = CALLS + 12;
inline void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
inline void word(t7r::Bytes& bytes, uint32_t v) {
    for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<uint8_t>(v >> shift));
}
inline void put(HANDLE process, uint32_t address, uint32_t value) {
    t7r::Bytes bytes; word(bytes, value); t7r::writeClientMemory(process, address, bytes);
}
inline uint32_t get(HANDLE process, uint32_t address) {
    auto bytes = t7r::readClientMemory(process, address, 4);
    uint32_t result; std::memcpy(&result, bytes.data(), 4); return result;
}
inline void text(t7r::Bytes& bytes, size_t at, size_t width, const char* value) {
    require(std::strlen(value) < width, "fixture title text overflow");
    std::fill_n(bytes.begin() + at, width, uint8_t{0});
    std::copy_n(value, std::strlen(value), bytes.begin() + at);
}
inline t7r::Bytes catalogue(const char* description = "A", const char* name = "Builder") {
    t7r::Bytes result; word(result, 1); word(result, 3);
    for (const auto item : {std::pair<uint32_t, const char*>{1007, "Veteran"}, {1008, "Elite"}, {1000000, name}}) {
        const auto start = result.size(); word(result, item.first); result.resize(start + 164, 0);
        text(result, start + 4, 32, item.second); text(result, start + 36, 128, description);
    }
    return result;
}
inline void prepare(HANDLE process) {
    put(process, BASE + 0x2440AD0, OWNER); put(process, OWNER, BASE + 0x17302A4);
    put(process, OWNER + 8, ROWS); put(process, OWNER + 12, 3);
    put(process, OWNER + 0x28, OWNER + 0x80);
    put(process, BASE + 0x2E249B8, OWNER);
    t7r::writeClientMemory(process, ROWS, t7r::Bytes(548 * 3, 0x5A));
    put(process, ROWS, 1008); put(process, ROWS + 548, 999); put(process, ROWS + 1096, 1007);
    for (const auto item : {std::pair<uint32_t, const char*>{0, "Elite"}, {548, "Other"}, {1096, "Veteran"}}) {
        t7r::Bytes name(32); text(name, 0, 32, item.second);
        t7r::writeClientMemory(process, ROWS + item.first + 4, name);
    }
    t7r::Bytes reindex{0x55,0x8B,0xEC,0x83,0xEC,0x14,0x8B,0x41,4,0xA3}; word(reindex, INDEX_ROWS);
    reindex.insert(reindex.end(), {0x8B,0x41,8,0xA3}); word(reindex, INDEX_COUNT);
    reindex.insert(reindex.end(), {0xFF,0x05}); word(reindex, INDEX_CALLS);
    reindex.insert(reindex.end(), {0x8B,0xE5,0x5D,0xC3});
    t7r::writeClientMemory(process, BASE + t7r::TITLE_REINDEX_RVA, reindex);
    t7r::Bytes refresh{0x55,0x8B,0xEC,0x6A,0xFF,0x68}; word(refresh, 0);
    refresh.insert(refresh.end(), {0xFF,0x05}); word(refresh, CALLS);
    refresh.insert(refresh.end(), {0xD9,0xEE,0x0F,0x57,0xC0,0x8B,0xE5,0x5D,0xC3});
    t7r::writeClientMemory(process, BASE + t7r::TITLE_REFRESH_RVA, refresh);
}
inline void verify(HANDLE process, t7r::TitleSettings& settings) {
    auto descriptions = catalogue();
    require(t7r::validTitleCatalogue(descriptions), "valid title catalogue rejected");
    require(!t7r::validTitleCatalogue(t7r::Bytes(256, 0)), "obsolete title mailbox accepted");
    require(!t7r::validTitleCatalogue(t7r::Bytes(0)), "empty native catalogue accepted");
    auto badPadding = descriptions; badPadding[40] = 1;
    require(!t7r::validTitleCatalogue(badPadding), "title padding accepted hidden data");
    auto invalid = descriptions; std::fill_n(invalid.begin() + 12, 32, 'X');
    require(!t7r::validTitleCatalogue(invalid), "unterminated title name accepted");
    invalid = descriptions; std::copy_n(invalid.begin() + 8, 4, invalid.begin() + 8 + 164);
    require(!t7r::validTitleCatalogue(invalid), "duplicate title ID accepted");
    invalid = descriptions; invalid[4] = 65;
    require(!t7r::validTitleCatalogue(invalid), "excessive catalogue accepted");
    const auto before = t7r::readClientMemory(process, ROWS, 548 * 3);
    put(process, OWNER, 0);
    settings.update(descriptions); settings.poll(); Sleep(50); settings.poll();
    require(get(process, CALLS) == 0 && t7r::readClientMemory(process, ROWS, before.size()) == before,
        "unready original table was edited");
    put(process, OWNER, BASE + 0x17302A4);
    auto awaitCalls = [&](uint32_t count) {
        const auto deadline = GetTickCount64() + 4000;
        while (get(process, CALLS) < count || !settings.applied()) {
            settings.poll(); Sleep(5); require(GetTickCount64() < deadline, "title frame response timeout");
        }
        settings.poll();
    };
    awaitCalls(1);
    auto expected = before;
    text(expected, 1096 + 36, 128, "A"); text(expected, 36, 128, "A");
    const auto merged = get(process, INDEX_ROWS);
    require(merged != ROWS && get(process, INDEX_COUNT) == 4 && get(process, INDEX_CALLS) == 1,
        "original index builder did not receive all original and custom rows");
    require(t7r::readClientMemory(process, merged, expected.size()) == expected,
        "catalogue lost original IDs, names, attributes or unrelated rows");
    require(t7r::readClientMemory(process, ROWS, before.size()) == before
        && get(process, OWNER + 8) == ROWS && get(process, OWNER + 12) == 3,
        "original resource allocation/ownership was changed");
    auto custom = t7r::readClientMemory(process, merged + 3 * 548, 548);
    require(get(process, merged + 3 * 548) == 1000000
        && std::equal(descriptions.begin() + 8 + 2 * 164, descriptions.end(), custom.begin())
        && std::all_of(custom.begin() + 164, custom.end(), [](auto b) { return b == 0; }),
        "custom title acquired original gameplay attributes");
    settings.update(descriptions); settings.poll(); Sleep(30); settings.poll();
    require(get(process, CALLS) == 1, "unchanged title table rebuilt every frame");
    descriptions = catalogue("C"); settings.update(descriptions); settings.poll();
    descriptions = catalogue("D", "Contributor"); settings.update(descriptions);
    awaitCalls(3);
    require(t7r::readClientMemory(process, merged + 1096 + 36, 1)[0] == 'D', "pending title update lost newest description");
    auto maximum = descriptions;
    for (uint32_t id = 1000001; id <= 1000061; ++id) {
        const auto start = maximum.size(); word(maximum, id); maximum.resize(start + 164, 0);
        text(maximum, start + 4, 32, "Community");
    }
    maximum[4] = 64;
    require(t7r::validTitleCatalogue(maximum), "maximum native catalogue rejected");
    settings.update(maximum); awaitCalls(4);
    require(get(process, INDEX_COUNT) == 65 && get(process, merged + 64 * 548) == 1000061,
        "maximum catalogue lost custom or unrelated original rows");
    settings.update(descriptions); awaitCalls(5);
    require(get(process, INDEX_COUNT) == 4, "smaller snapshot retained stale title index entries");
    const auto active = t7r::readClientMemory(process, merged, 4 * 548);
    auto renamedOriginal = descriptions; text(renamedOriginal, 12, 32, "Renamed");
    settings.update(renamedOriginal);
    bool rejected = false; const auto deadline = GetTickCount64() + 4000;
    while (!rejected && GetTickCount64() < deadline) {
        try { settings.poll(); } catch (const std::runtime_error&) { rejected = true; }
        Sleep(5);
    }
    require(rejected && get(process, CALLS) == 5 && t7r::readClientMemory(process, merged, active.size()) == active,
        "rejected title changed the active catalogue");
    settings.update(descriptions); settings.poll();
}
}
