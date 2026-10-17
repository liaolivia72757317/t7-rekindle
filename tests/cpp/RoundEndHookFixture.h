#pragma once
#include "SprintHookFixture.h"
#include "LobbyReturnHookFixture.h"
#include "../../src/Runtime/launcher/RoundEndHookCode.h"

namespace roundEndFixture {
using worldFixture::BASE;
using worldFixture::INSTANCE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t DATA = BASE + 0xE000, COMMAND = DATA, CALLS = DATA + 4, PACKET = DATA + 0x40;
constexpr uint32_t ROOM = DATA + 0x80, SCENE = DATA + 0xC0, DISPATCH = DATA + 0x400;

inline void prepare(HANDLE process) {
    lobbyReturnFixture::prepare(process);
    writeWord(process, BASE + 0x2E3A4C4, ROOM); writeWord(process, BASE + 0x2E3CB88, SCENE);
    for (const auto& entry : t7r::roundEndOriginals(BASE))
        t7r::writeClientMemory(process, BASE + entry.first, entry.second);
    t7r::WorldHookCode c;
    c.emit({0x6A,0,0xE8,0x73,0x2C,0xE8,0xFF,0xA1}); c.word(BASE + 0x2E3CB88);
    c.emit({0x83,0x78,0x24,0,0x74,5,0xE8,0x93,0x11,0xE0,0xFF,0xB8,1,0,0,0,0xC2,0x10,0});
    t7r::writeClientMemory(process, BASE + t7r::NATIVE_LEAVE_RVA + 6, c.bytes);
    c.bytes.clear(); c.emit({0xFF,0x05}); c.word(CALLS);
    c.emit({0x0F,0x57,0xC0,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x61F410, c.bytes);
    t7r::writeClientMemory(process, BASE + t7r::FINISH_RESULT_RVA + 6,
        {0x56,0x89,0x41,0x4C,0xE8,0xEE,0xD9,0xFF,0xFF,0x5E,0x8B,0xE5,0x5D,0xC3});
    t7r::writeClientMemory(process, BASE + 0x6A5570, {0xC2,4,0});
    c.bytes.clear(); c.emit({0x55,0x8B,0xEC,0x83,0xEC,0x30,0x56,0xC7,0x45,0xD0}); c.word(INSTANCE);
    c.emit({0xBE}); c.word(PACKET); c.emit({0xB8}); c.word(BASE + t7r::FINISH_RESULT_RVA); c.emit({0xFF,0xE0});
    t7r::writeClientMemory(process, BASE + 0x6A7A60, c.bytes);
    c.bytes.clear(); c.emit({0x9C,0x60,0xA1}); c.word(COMMAND); c.store(COMMAND, 0);
    c.emit({0x83,0xF8,1}); const auto notFinish = c.branch(0x85);
    c.call(BASE + 0x6A7A60); const auto done = c.jump();
    c.bind(notFinish); c.emit({0x83,0xF8,2}); const auto notLeave = c.branch(0x85);
    c.emit({0x6A,0,0x6A,0,0x6A,0,0x6A,0}); c.call(BASE + t7r::NATIVE_LEAVE_RVA);
    const auto left = c.jump();
    c.bind(notLeave); c.emit({0x83,0xF8,3}); const auto notRead = c.branch(0x85);
    c.call(lobbyReturnFixture::READ_CALL);
    c.bind(notRead); c.bind(left); c.bind(done); c.emit({0x61,0x9D,0xC3});
    t7r::writeClientMemory(process, DISPATCH, c.bytes);

    t7r::verifyRoundEndFunctions(process, BASE);
    for (const auto& entry : t7r::roundEndOriginals(BASE)) {
        t7r::writeClientMemory(process, BASE + entry.first, {0xCC});
        bool rejected = false;
        try { t7r::verifyRoundEndFunctions(process, BASE); } catch (const std::runtime_error&) { rejected = true; }
        if (!rejected) throw std::runtime_error("round end accepted an unknown native function");
        t7r::writeClientMemory(process, BASE + entry.first, entry.second);
    }
}

inline void verify(HANDLE process, t7r::ClientWorld& reader, uint32_t mailbox) {
    auto require = [](bool value, const char* message) { if (!value) throw std::runtime_error(message); };
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    auto step = [&](uint32_t command = 0) {
        writeWord(process, COMMAND, command); worldFixture::requestFrame(process);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, worldFixture::FRAME_GATE) != 1) {
            require(GetTickCount64() < deadline, "round end frame timeout"); Sleep(1);
        }
    };
    lobbyReturnFixture::verify(process, reader, mailbox, step);
    uint32_t epoch = readWord(process, INSTANCE + 0x68), calls = readWord(process, CALLS);
    auto newRound = [&] {
        writeWord(process, INSTANCE + 0x68, ++epoch);
        writeWord(process, INSTANCE + 0x7C, 4); step();
    };
    const auto status = mailbox + t7r::ROUND_END_DATA_OFFSET + 8;
    for (uint32_t result : {1u, 2u, 3u}) {
        newRound();
        writeWord(process, INSTANCE + 0x4C, result); // A stale field is not FINISH consumption.
        writeWord(process, INSTANCE + 0x7C, 5); step();
        require(readWord(process, CALLS) == calls, "END alone triggered native logout");
        writeWord(process, PACKET + 0x10, result); step(1);
        require(readWord(process, CALLS) == ++calls && readWord(process, status) == 1,
                "END and FINISH did not request native logout");
        step(); step(1);
        require(readWord(process, CALLS) == calls, "duplicate END/FINISH replayed native logout");
    }
    newRound(); writeWord(process, PACKET + 0x10, 1); step(1);
    require(readWord(process, CALLS) == calls, "FINISH before END triggered native logout");
    writeWord(process, INSTANCE + 0x7C, 5); step();
    require(readWord(process, CALLS) == ++calls, "FINISH-first delivery was lost");

    newRound(); step(2); // The same entry is used by the original manual leave action.
    require(readWord(process, CALLS) == ++calls, "manual native leave did not run");
    writeWord(process, INSTANCE + 0x7C, 5); step(1); step();
    require(readWord(process, CALLS) == calls, "automatic leave re-entered a manual leave");

    newRound(); writeWord(process, INSTANCE + 0x7C, 5); writeWord(process, PACKET + 0x10, 0); step(1);
    require(readWord(process, CALLS) == calls, "incomplete battle requested automatic logout");
    for (uint32_t state : {1u, 3u, 0u}) {
        newRound();
        writeWord(process, worldFixture::STATE + 0x38, state);
        if (!state) writeWord(process, BASE + 0x2E405F8, 0);
        writeWord(process, INSTANCE + 0x7C, 5); writeWord(process, PACKET + 0x10, 3); step(1);
        require(readWord(process, CALLS) == ++calls, "missing/dead/choosing actor blocked settlement");
        writeWord(process, BASE + 0x2E405F8, worldFixture::STATE);
        writeWord(process, worldFixture::STATE + 0x38, 4);
    }
    // High epoch bits also identify a new instance.
    writeWord(process, INSTANCE + 0x6C, readWord(process, INSTANCE + 0x6C) + 1); step(1);
    require(readWord(process, CALLS) == ++calls, "round end truncated the instance epoch");
    newRound(); writeWord(process, BASE + 0x2E3A4C4, 0);
    writeWord(process, INSTANCE + 0x7C, 5); step(1);
    require(readWord(process, CALLS) == calls && readWord(process, status) == 2,
            "missing native service was silently accepted");
    bool failed = false;
    try { t7r::WorldSnapshot value; reader.poll(value); } catch (const std::runtime_error&) { failed = true; }
    require(failed, "native handoff failure was not reported");
    writeWord(process, BASE + 0x2E3A4C4, ROOM); step();
    require(readWord(process, CALLS) == calls, "failed native handoff retried every frame");
    newRound(); writeWord(process, INSTANCE + 0x7C, 0); step();
    writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
