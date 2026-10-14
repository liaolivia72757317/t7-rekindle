#pragma once
#include "SprintHookFixture.h"
#include "../../src/Runtime/launcher/RoundEndHookCode.h"

namespace lobbyReturnFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t GFX = BASE + 0xE800, FILE = GFX + 0x40, STREAM = GFX + 0x80, TABLE = GFX + 0xC0;
constexpr uint32_t PATH = GFX + 0x100, SOURCE = GFX + 0x180, BUFFER = GFX + 0x200, OBSERVED = GFX + 0x280;
constexpr uint32_t READ_CALL = GFX + 0x300, STREAM_READ = GFX + 0x400;
constexpr uint32_t READ_RVA = 0x3F2B10, METHOD_OFFSET = 477100, RESOURCE_SIZE = 902623;
const t7::Bytes ORIGINAL{0xD0,0x30,0x5D,0x39,0x2C,0x42,0x46,0x39,1,0x29,0xD0,0x27,0x68,0x3A};
const t7::Bytes HOME{0xD0,0x30,0xD0,0x2C,3,0x68,0x89,1,2,2,0xD0,0x27,0x68,0x3A};

inline void setPath(HANDLE process, const std::string& path) {
    t7::Bytes bytes(path.begin(), path.end()); bytes.push_back(0);
    const bool heap = path.size() >= 16;
    t7::writeClientMemory(process, heap ? PATH : GFX + 0xC, bytes);
    if (heap) writeWord(process, GFX + 0xC, PATH);
    writeWord(process, GFX + 0x1C, static_cast<uint32_t>(path.size()));
    writeWord(process, GFX + 0x20, heap ? static_cast<uint32_t>(path.size()) : 15);
}

inline void prepare(HANDLE process) {
    setPath(process, "../data/ui/Scripts_Game.gfx");
    writeWord(process, GFX + 8, FILE); writeWord(process, FILE + 0x30, STREAM);
    writeWord(process, STREAM, TABLE); writeWord(process, STREAM + 8, RESOURCE_SIZE);
    writeWord(process, TABLE + 4, STREAM_READ + 0x80);
    writeWord(process, TABLE + 0x10, STREAM_READ + 0x90);
    writeWord(process, TABLE + 0x18, STREAM_READ);
    t7::writeClientMemory(process, STREAM_READ + 0x80, {0x8B,0x41,8,0xC3});
    t7::writeClientMemory(process, STREAM_READ + 0x90, {0x8B,0x41,4,0xC3});
    t7::writeClientMemory(process, BASE + 0x71570, {0x8B,0x49,0x30,0x85,0xC9,0x74,5,0x8B,1,0xFF,0x60,0x10,0x33,0xC0,0xC3});
    t7::writeClientMemory(process, BASE + 0x71580, {0x8B,0x49,0x30,0x85,0xC9,0x74,5,0x8B,1,0xFF,0x60,4,0x33,0xC0,0xC3});
    t7::writeClientMemory(process, BASE + 0x717C0,
        {0x55,0x8B,0xEC,0x8B,0x49,0x30,0x85,0xC9,0x74,6,0x8B,1,0x5D,0xFF,0x60,0x18,0x33,0xC0,0x5D,0xC2,8,0});
    t7::writeClientMemory(process, BASE + READ_RVA,
        {0x55,0x8B,0xEC,0x8B,0x49,8,0x85,0xC9,0x75,7,0x83,0xC8,0xFF,0x5D,0xC2,8,0,0x5D,0xE9,0x99,0xEC,0xC7,0xFF});

    t7::WorldHookCode c;
    c.emit({0x55,0x8B,0xEC,0x56,0x57,0x8B,0x41,0xC,0x85,0xC0}); const auto noData = c.branch(0x8E);
    c.emit({0x01,0x41,4,0x8B,0xC8,0xBE}); c.word(SOURCE);
    c.emit({0x8B,0x7D,8,0xFC,0xF3,0xA4}); c.bind(noData); c.emit({0x5F,0x5E,0x5D,0xC2,8,0});
    t7::writeClientMemory(process, STREAM_READ, c.bytes);
    c.bytes.clear(); c.emit({0x9C,0x60,0x89,0x25}); c.word(OBSERVED + 4);
    c.emit({0xBB}); c.word(0x11223344); c.emit({0xBE}); c.word(0x22334455);
    c.emit({0xBF}); c.word(0x33445566); c.emit({0xBD}); c.word(0x44556677);
    c.emit({0xFF,0x35}); c.word(OBSERVED + 28); c.emit({0x68}); c.word(BUFFER);
    c.emit({0xB9}); c.word(GFX); c.call(BASE + READ_RVA);
    c.emit({0xA3}); c.word(OBSERVED); c.emit({0x89,0x25}); c.word(OBSERVED + 8);
    for (const auto& reg : {std::pair{0x1D,12u}, std::pair{0x35,16u}, std::pair{0x3D,20u}, std::pair{0x2D,24u}}) {
        c.emit({0x89,static_cast<unsigned char>(reg.first)}); c.word(OBSERVED + reg.second);
    }
    c.emit({0x61,0x9D,0xC3}); t7::writeClientMemory(process, READ_CALL, c.bytes);
}

template<class Step>
void verify(HANDLE process, t7::ClientWorld& reader, uint32_t mailbox, Step step) {
    auto require = [](bool value, const char* message) { if (!value) throw std::runtime_error(message); };
    const auto error = mailbox + t7::ROUND_END_DATA_OFFSET + 16;
    auto read = [&](uint32_t offset, const t7::Bytes& source, int returned = -2) {
        const auto count = returned == -2 ? static_cast<int>(source.size()) : returned;
        writeWord(process, STREAM + 4, offset); writeWord(process, STREAM + 12, static_cast<uint32_t>(count));
        writeWord(process, OBSERVED + 28, static_cast<uint32_t>(source.size()));
        t7::writeClientMemory(process, SOURCE, source);
        t7::writeClientMemory(process, BUFFER, t7::Bytes(64, 0xCC));
        step(3);
        require(readWord(process, OBSERVED) == static_cast<uint32_t>(count), "lobby resource read changed native result");
        require(readWord(process, OBSERVED + 4) == readWord(process, OBSERVED + 8), "lobby resource read changed ESP");
        for (const auto& reg : {std::pair{12u,0x11223344u}, std::pair{16u,0x22334455u},
                               std::pair{20u,0x33445566u}, std::pair{24u,0x44556677u}})
            require(readWord(process, OBSERVED + reg.first) == reg.second, "lobby resource read changed nonvolatile register");
        return t7::readClientMemory(process, BUFFER, source.size());
    };
    for (const auto& path : {"../data/ui/Scripts_Game.gfx", "..\\DATA\\UI\\SCRIPTS_GAME.GFX"}) {
        setPath(process, path);
        t7::Bytes source(8, 0xAA), expected = source;
        source.insert(source.end(), ORIGINAL.begin(), ORIGINAL.end()); source.insert(source.end(), 8, 0xBB);
        expected.insert(expected.end(), HOME.begin(), HOME.end()); expected.insert(expected.end(), 8, 0xBB);
        require(read(METHOD_OFFSET - 8, source) == expected, "lobby entry retained custom-room return page");
        require(read(METHOD_OFFSET - 8, source) == expected, "re-reading lobby resource was not deterministic");
    }
    // Resource loaders may split the method at any byte boundary.
    for (size_t split = 1; split < ORIGINAL.size(); ++split) {
        t7::Bytes first(ORIGINAL.begin(), ORIGINAL.begin() + split), last(ORIGINAL.begin() + split, ORIGINAL.end());
        require(read(METHOD_OFFSET, first) == t7::Bytes(HOME.begin(), HOME.begin() + split), "lobby prefix short read");
        require(read(METHOD_OFFSET + static_cast<uint32_t>(split), last) == t7::Bytes(HOME.begin() + split, HOME.end()),
                "lobby suffix short read");
    }
    auto shortRead = read(METHOD_OFFSET, ORIGINAL, 6);
    require(t7::Bytes(shortRead.begin(), shortRead.begin() + 6) == t7::Bytes(HOME.begin(), HOME.begin() + 6)
        && t7::Bytes(shortRead.begin() + 6, shortRead.end()) == t7::Bytes(8, 0xCC), "lobby read used requested rather than actual length");
    for (const auto& path : {"../data/ui/panel_score.gfx", "../data/ui/scripts_game.gfx.bak", "short.gfx"}) {
        setPath(process, path);
        require(read(METHOD_OFFSET, ORIGINAL) == ORIGINAL, "lobby adaptation changed unrelated resource");
    }
    setPath(process, "../data/ui/scripts_game.gfx");
    require(read(0, ORIGINAL) == ORIGINAL && read(METHOD_OFFSET + 14, ORIGINAL) == ORIGINAL,
            "lobby adaptation changed unrelated bytecode");
    for (int result : {0, -1})
        require(read(METHOD_OFFSET, ORIGINAL, result) == t7::Bytes(ORIGINAL.size(), 0xCC), "lobby read failure changed buffer");
    writeWord(process, GFX + 8, 0);
    read(METHOD_OFFSET, ORIGINAL, -1); writeWord(process, GFX + 8, FILE);
    require(readWord(process, error) == 0, "valid lobby resource reported mismatch");
    auto rejected = [&] {
        require(readWord(process, error) == 1, "unknown lobby resource was silently accepted");
        const auto epoch = worldFixture::INSTANCE + 0x68;
        writeWord(process, epoch, readWord(process, epoch) + 1); step(0);
        require(readWord(process, error) == 1, "battle epoch cleared persistent lobby resource failure");
        bool reported = false;
        try { t7::WorldSnapshot value; reader.poll(value); } catch (const std::runtime_error&) { reported = true; }
        require(reported, "lobby resource failure was not reported");
        writeWord(process, error, 0);
    };
    writeWord(process, STREAM + 8, RESOURCE_SIZE + 1);
    require(read(METHOD_OFFSET, ORIGINAL) == ORIGINAL, "wrong-size lobby resource was modified"); rejected();
    writeWord(process, STREAM + 8, RESOURCE_SIZE);
    auto unknown = ORIGINAL; unknown.back() ^= 1;
    require(read(METHOD_OFFSET, unknown) == unknown, "unknown lobby bytecode was partially modified"); rejected();
    require(read(METHOD_OFFSET, ORIGINAL) == HOME, "lobby resource did not resume for valid input");
    constexpr uint32_t soloOffset = 276986;
    const t7::Bytes soloBefore{0x5D,0x18,0x66,0x18,0x46,0x08,0,0x24,1,0x46,0x1C,1,0x73,0xD5,0x5D,0x18};
    const t7::Bytes soloAfter{0x10,0x5B,0,0,2,2,2,2,2,2,2,2,2,2,2,2};
    require(read(soloOffset, soloBefore) == soloBefore, "offline lobby changed custom room team gate");
    t7::writeClientMemory(process, mailbox + t7::ROUND_END_CODE_OFFSET, t7::buildRoundEndHooks(mailbox, BASE, true));
    require(read(METHOD_OFFSET, ORIGINAL) == HOME, "custom room lost lobby return adaptation");
    require(read(soloOffset, soloBefore) == soloAfter, "custom room start did not reach server validation");
    for (size_t split = 1; split < soloBefore.size(); ++split) {
        t7::Bytes first(soloBefore.begin(), soloBefore.begin() + split), last(soloBefore.begin() + split, soloBefore.end());
        require(read(soloOffset, first) == t7::Bytes(soloAfter.begin(), soloAfter.begin() + split), "solo UI prefix short read");
        require(read(soloOffset + static_cast<uint32_t>(split), last) == t7::Bytes(soloAfter.begin() + split, soloAfter.end()),
                "solo UI suffix short read");
    }
    setPath(process, "../data/ui/panel_score.gfx");
    require(read(soloOffset, soloBefore) == soloBefore, "solo gate changed unrelated UI");
    setPath(process, "../data/ui/scripts_game.gfx");
    auto badSolo = soloBefore; badSolo.back() ^= 1;
    require(read(soloOffset, badSolo) == badSolo, "unknown solo gate was partially rewritten"); rejected();

    // The login UI receives the committed server identity; before identification it uses native data.
    constexpr uint32_t loginObject = GFX + 0x500, loginTable = loginObject + 0x40, getId = loginObject + 0x80;
    writeWord(process, loginObject, loginTable); writeWord(process, loginTable + 0x28, getId);
    t7::WorldHookCode code; code.emit({0xB8}); code.word(1234); code.emit({0xC3});
    t7::writeClientMemory(process, getId, code.bytes);
    for (const auto& site : t7::LOBBY_IDENTITY_SITES) {
        t7::writeClientMemory(process, BASE + site.first, {0x8B,0x17,0x8B,0xCF,0x8B,0xF0,0xFF,0x52,0x28,0xC3});
        for (const auto& patch : t7::roundEndJumps(mailbox, BASE, true))
            if (patch.first == site.first) t7::writeClientMemory(process, BASE + patch.first, patch.second);
        code.bytes.clear(); code.emit({0x9C,0x60,0xBF}); code.word(loginObject);
        code.emit({0xB8}); code.word(0x12345678);
        code.emit({0xB9}); code.word(BASE + site.first); code.emit({0xFF,0xD1});
        code.emit({0xA3}); code.word(OBSERVED);
        code.emit({0x89,0x35}); code.word(OBSERVED + 4);
        code.emit({0x61,0x9D,0xC3}); t7::writeClientMemory(process, READ_CALL, code.bytes);
        writeWord(process, mailbox + t7::LOBBY_IDENTITY_DATA_OFFSET, 0);
        step(3); require(readWord(process, OBSERVED) == 1234, "unidentified UI lost its native identity");
        require(readWord(process, OBSERVED + 4) == 0x12345678, "native UI identity lost its data record pointer");
        writeWord(process, mailbox + t7::LOBBY_IDENTITY_DATA_OFFSET, 700);
        step(3); require(readWord(process, OBSERVED) == 700, "UI room owner differs from committed server user ID");
        require(readWord(process, OBSERVED + 4) == 0x12345678, "server UI identity lost its data record pointer");
        require(readWord(process, mailbox + t7::LOBBY_IDENTITY_DATA_OFFSET + 4) == 700,
                "observed UI identity differs from adapter output");
    }
    // Execute the HUD serialization entry, including its two original stack arguments.
    code.bytes.clear();
    code.emit({0x58,0xA3}); code.word(OBSERVED);
    code.emit({0x58,0xA3}); code.word(OBSERVED + 4);
    code.emit({0x89,0x1D}); code.word(OBSERVED + 8);
    code.emit({0x89,0x35}); code.word(OBSERVED + 12);
    code.emit({0x9C,0x58,0xA3}); code.word(OBSERVED + 16);
    code.emit({0xC3});
    t7::writeClientMemory(process, BASE + t7::NETWORK_HUD_RVA + 5, code.bytes);
    for (const auto& patch : t7::roundEndJumps(mailbox, BASE, true))
        if (patch.first == t7::NETWORK_HUD_RVA)
            t7::writeClientMemory(process, BASE + patch.first, patch.second);
    code.bytes.clear(); code.emit({0x9C,0x60,0xBD}); code.word(GFX + 0x700);
    code.emit({0xBB}); code.word(333); code.emit({0xBE}); code.word(444);
    code.emit({0xF9}); code.call(BASE + t7::NETWORK_HUD_RVA);
    code.emit({0x61,0x9D,0xC3});
    t7::writeClientMemory(process, READ_CALL, code.bytes);
    for (uint32_t latency : {17u, 53u, UINT32_MAX}) {
        writeWord(process, mailbox + t7::NETWORK_LATENCY_DATA_OFFSET, latency);
        step(3);
        require(readWord(process, OBSERVED) == GFX + 0x700 - 0x28, "network HUD lost its native argument pointer");
        require(readWord(process, OBSERVED + 4) == UINT32_MAX && readWord(process, OBSERVED + 8) == UINT32_MAX,
                "network HUD did not hide the duplicate legacy logic field");
        require(readWord(process, OBSERVED + 12) == latency, "network HUD did not consume the measured server RTT");
        require((readWord(process, OBSERVED + 16) & 1) != 0, "network HUD changed caller flags");
        require(readWord(process, mailbox + t7::NETWORK_LATENCY_DATA_OFFSET + 4) == latency,
                "network HUD observation differs from its displayed RTT");
    }
    t7::writeClientMemory(process, mailbox + t7::ROUND_END_CODE_OFFSET, t7::buildRoundEndHooks(mailbox, BASE));
}
}
