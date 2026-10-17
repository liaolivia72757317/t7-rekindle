#include "../../src/Runtime/bridge/Session.h"
#include "../../src/Runtime/core/Common.h"
#include "../../src/BattleRust/ffi/include/t7r_runtime.h"
#include <atomic>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iostream>
#include <thread>
#include <vector>

namespace {
template<class Function>
Function symbol(HMODULE library, const char* name) {
    const auto address = GetProcAddress(library, name);
    if (!address) throw std::runtime_error(std::string("missing Rust fixture export: ") + name);
    return reinterpret_cast<Function>(address);
}

void put(std::vector<uint8_t>& bytes, uint64_t value, unsigned width) {
    for (unsigned shift = width * 8; shift; shift -= 8)
        bytes.push_back(static_cast<uint8_t>(value >> (shift - 8)));
}
void real(std::vector<uint8_t>& bytes, double value) {
    uint64_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    put(bytes, bits, 8);
}
std::vector<uint8_t> firstWorld(uint64_t epoch, uint64_t atMs) {
    std::vector<uint8_t> bytes;
    put(bytes, 1, 2); put(bytes, 0, 2);
    for (uint64_t value : {epoch, 1ull, 1ull, atMs}) put(bytes, value, 8);
    put(bytes, 1, 2); put(bytes, 0, 2);
    put(bytes, 1, 4); put(bytes, 1, 2); put(bytes, 1, 2);
    put(bytes, 101, 8);
    real(bytes, 1); real(bytes, 20); real(bytes, 0); real(bytes, 0);
    put(bytes, 0, 4); put(bytes, 0, 4);
    return bytes;
}

bool waitOperation(const std::shared_ptr<t7r::bridge::Session>& session, uint64_t id) {
    const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (std::chrono::steady_clock::now() < until) {
        t7r::bridge::Operation operation;
        if (session->operation(id, operation) != T7RNB_OK) return false;
        if (operation.status == T7RNB_OPERATION_SUCCEEDED) return true;
        if (operation.status == T7RNB_OPERATION_FAILED || operation.status == T7RNB_OPERATION_CANCELLED)
            return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return false;
}
}

bool verifySessionBattleAttachment(const t7r::fs::path& packageRoot) {
    wchar_t pathBuffer[32768]{};
    const auto length = GetEnvironmentVariableW(L"T7R_BATTLE_FFI_DLL", pathBuffer,
                                                static_cast<DWORD>(std::size(pathBuffer)));
    if (!length || length >= std::size(pathBuffer)) return false;
    const t7r::fs::path libraryPath(pathBuffer);
    HMODULE library = LoadLibraryExW(libraryPath.c_str(), nullptr, 0x100 | 0x800);
    if (!library) return false;
    uint64_t handle = 0;
    std::shared_ptr<t7r::bridge::Session> session;
    bool passed = false;
    try {
        const auto create = symbol<decltype(&t7r_gateway_create)>(library, "t7r_gateway_create");
        const auto start = symbol<decltype(&t7r_gateway_start)>(library, "t7r_gateway_start");
        const auto battleStart = symbol<decltype(&t7r_gateway_battle_start)>(library, "t7r_gateway_battle_start");
        const auto stagedStart = symbol<decltype(&t7r_gateway_battle_start_staged)>(
            library, "t7r_gateway_battle_start_staged");
        const auto battleStop = symbol<decltype(&t7r_gateway_battle_stop)>(library, "t7r_gateway_battle_stop");
        const auto battleClock = symbol<decltype(&t7r_gateway_battle_clock)>(library, "t7r_gateway_battle_clock");
        const auto bindClock = symbol<decltype(&t7r_gateway_battle_bind_client_clock)>(
            library, "t7r_gateway_battle_bind_client_clock");
        const auto bindServerClock = symbol<decltype(&t7r_gateway_battle_bind_server_clock)>(
            library, "t7r_gateway_battle_bind_server_clock");
        const auto submit = symbol<decltype(&t7r_gateway_battle_submit)>(library, "t7r_gateway_battle_submit");
        const auto release = symbol<decltype(&t7r_gateway_release)>(library, "t7r_gateway_release");
        std::ifstream input("tests/fixtures/resources/bundle-v1.json", std::ios::binary);
        const std::string bundle((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
        if (!input.is_open() || bundle.empty()
            || create(reinterpret_cast<const uint8_t*>(bundle.data()), static_cast<uint32_t>(bundle.size()), &handle) != T7RGW_OK
            || start(handle, 1) != T7RGW_OK)
            throw std::runtime_error("Rust gateway fixture did not start");

        T7RBattleConfig match{};
        match.abiVersion = T7R_BATTLE_CONFIG_ABI_VERSION; match.structSize = sizeof(match);
        match.epoch = 7; match.startedAtMs = 1000; match.durationMs = 120000;
        match.killTarget = 10; match.seed = 42; match.sourceId = 1; match.memberCount = 20;
        for (uint32_t index = 0; index != 20; ++index) {
            auto& member = match.members[index];
            member.actorId = index + 1; member.clientRid = static_cast<uint16_t>(index + 1);
            member.camp = index >= 1 && index <= 10 ? 2 : 1;
            member.kind = index == 0 ? 1 : 2;
            member.heroId = 1101; member.weaponId = 701; member.equipmentCount = 1;
            member.equipment[0].weaponId = 701; member.equipment[0].slot = 1;
        }
        if (battleStart(handle, &match) != T7RGW_OK)
            throw std::runtime_error("Rust battle fixture did not start");

        auto enabled = std::make_shared<std::atomic<bool>>(false);
        auto delivered = std::make_shared<std::atomic<bool>>(false);
        auto sampleBatch = std::make_shared<std::atomic<uint32_t>>(0);
        auto running = std::make_shared<std::atomic<bool>>(false);
        auto nativeInstance = std::make_shared<std::atomic<uint64_t>>(4400);
        auto droppedUnready = std::make_shared<std::atomic<bool>>(false);
        auto worldPolls = std::make_shared<std::atomic<unsigned>>(0);
        auto graphicsPolls = std::make_shared<std::atomic<unsigned>>(0);
        t7r::Bootstrap::TestAdapter adapter;
        adapter.launch = [running](const t7r::fs::path&, const t7r::Config&,
                                   const t7r::DiagnosticLogCallback&, const std::function<bool()>&,
                                   const std::function<void()>& adapting) {
            running->store(true);
            if (adapting) adapting();
        };
        adapter.running = [running] { return running->load(); };
        adapter.stop = [running] { running->store(false); };
        adapter.pollGraphics = [graphicsPolls](t7r::GraphicsValues&, uint32_t&, bool&) {
            ++*graphicsPolls;
            return false;
        };
        adapter.pollWorld = [enabled, delivered, sampleBatch, nativeInstance, droppedUnready, worldPolls](t7r::WorldSnapshot& world) {
            ++*worldPolls;
            if (!enabled->load()) return false;
            delivered->store(true);
            world = {};
            world.sampledAt = GetTickCount64(); world.instanceStartedAt = nativeInstance->load();
            world.contactsReady = true; world.contactBatchId = sampleBatch->fetch_add(1) + 1;
            if (droppedUnready->load()) {
                world.droppedContacts = 1;
                return true;
            }
            world.ready = true;
            world.actors[0].ready = true; world.actors[0].entity = 101;
            world.actors[0].sequence = 1; world.actors[0].position = {1.f, 20.f, 0.f};
            world.actors[1].ready = true; world.actors[1].entity = 102;
            world.actors[1].sequence = 1; world.actors[1].position = {2.f, 20.f, 0.f};
            world.actors[1].heading = 180.f; world.actors[1].forwardAttemptAt = world.sampledAt;
            return true;
        };
        session = t7r::bridge::Session::createForTest(t7r::utf8(packageRoot.wstring()), std::move(adapter));
        const auto clientDirectory = t7r::utf8((packageRoot / "synthetic-client" / "Bin").wstring());
        uint64_t startId = 0;
        const auto startStatus = session->submit(T7RNB_OPERATION_START, clientDirectory, startId, u8"新玩家", false, {1, 2, 3});
        if (startStatus != T7RNB_OK || !waitOperation(session, startId)) {
            t7r::bridge::Snapshot current;
            session->snapshot(current);
            std::string detail;
            uint32_t errorCode = 0;
            if (startId) session->error(startId, detail, errorCode);
            throw std::runtime_error("native fixture session did not start; submit="
                + std::to_string(startStatus) + " state=" + std::to_string(current.state)
                + " phase=" + current.phase + " error=" + std::to_string(current.errorCode)
                + " detail=" + detail);
        }
        if (session->attachBattle(match, libraryPath, handle) != T7RNB_OK
            || session->attachBattle(match, libraryPath, handle) != T7RNB_BUSY)
            throw std::runtime_error("native Rust battle binding was not exclusive");
        enabled->store(true);
        const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!session->battleIngressCreatedForTest() && std::chrono::steady_clock::now() < until)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        if (!delivered->load() || !session->battleIngressCreatedForTest()) {
            t7r::bridge::Snapshot current;
            session->snapshot(current);
            throw std::runtime_error("native battle snapshot was not submitted; delivered="
                + std::to_string(delivered->load()) + " state=" + std::to_string(current.state)
                + " phase=" + current.phase + " error=" + std::to_string(current.errorCode));
        }
        t7r::bridge::Snapshot state;
        if (session->snapshot(state) != T7RNB_OK || state.state != T7RNB_STATE_RUNNING)
            throw std::runtime_error("native battle snapshot failed");
        if (bindClock(handle, 7, INT64_MAX) != T7RGW_INVALID_STATE)
            throw std::runtime_error("native client clock was not bound by first world sample");
        if (bindServerClock(handle, 7, INT64_MAX) != T7RGW_INVALID_STATE)
            throw std::runtime_error("native server clock was not bound by first world sample");
        const auto worldStart = worldPolls->load(), graphicsStart = graphicsPolls->load();
        const auto pollingDeadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (graphicsPolls->load() < graphicsStart + 4 && std::chrono::steady_clock::now() < pollingDeadline)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        if (graphicsPolls->load() < graphicsStart + 4
            || worldPolls->load() - worldStart < 2 * (graphicsPolls->load() - graphicsStart))
            throw std::runtime_error("battle world readback is gated by the slow settings monitor");
        uint64_t nowMs = 0;
        if (battleClock(handle, &nowMs) != T7RGW_OK) throw std::runtime_error("battle clock stopped");
        const auto duplicate = firstWorld(7, nowMs);
        if (submit(handle, 6, duplicate.data(), static_cast<uint32_t>(duplicate.size())) != T7RGW_INVALID_ARGUMENT)
            throw std::runtime_error("native bridge did not commit world batch 1");
        enabled->store(false);
        if (session->detachBattle() != T7RNB_OK || session->detachBattle() != T7RNB_OK)
            throw std::runtime_error("native Rust battle detach failed");
        if (battleStop(handle) != T7RGW_OK) throw std::runtime_error("first Rust battle did not stop");
        match.epoch = 8;
        if (battleStart(handle, &match) != T7RGW_OK) throw std::runtime_error("Rust rematch did not start");
        if (bindClock(handle, 7, 0) != T7RGW_INVALID_STATE)
            throw std::runtime_error("old match client clock reached rematch");
        if (bindServerClock(handle, 7, 0) != T7RGW_INVALID_STATE)
            throw std::runtime_error("old match server clock reached rematch");
        nativeInstance->store(4401);
        sampleBatch->store(0);
        delivered->store(false);
        if (session->attachBattle(match, libraryPath, handle) != T7RNB_OK)
            throw std::runtime_error("native Rust rematch did not attach");
        enabled->store(true);
        const auto rematchUntil = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!session->battleIngressCreatedForTest() && std::chrono::steady_clock::now() < rematchUntil)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        if (!delivered->load() || !session->battleIngressCreatedForTest())
            throw std::runtime_error("native Rust rematch snapshot was not submitted");
        if (bindClock(handle, 8, INT64_MAX) != T7RGW_INVALID_STATE)
            throw std::runtime_error("native rematch client clock was not rebound");
        if (battleClock(handle, &nowMs) != T7RGW_OK) throw std::runtime_error("rematch clock stopped");
        const auto oldWorld = firstWorld(7, nowMs);
        if (submit(handle, 6, oldWorld.data(), static_cast<uint32_t>(oldWorld.size())) != T7RGW_INVALID_ARGUMENT)
            throw std::runtime_error("old match world reached rematch");
        const auto repeated = firstWorld(8, nowMs);
        if (submit(handle, 6, repeated.data(), static_cast<uint32_t>(repeated.size())) != T7RGW_INVALID_ARGUMENT)
            throw std::runtime_error("native rematch did not commit world batch 1");
        enabled->store(false);
        if (session->detachBattle() != T7RNB_OK || battleStop(handle) != T7RGW_OK)
            throw std::runtime_error("native rematch did not stop");
        match.epoch = 9;
        if (stagedStart(handle, &match) != T7RGW_OK
            || battleClock(handle, &nowMs) != T7RGW_EMPTY)
            throw std::runtime_error("staged match clock advanced before GAME");
        nativeInstance->store(4402);
        sampleBatch->store(0);
        delivered->store(false);
        if (session->attachBattle(match, libraryPath, handle) != T7RNB_OK)
            throw std::runtime_error("native staged match did not attach");
        enabled->store(true);
        const auto stagedUntil = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!delivered->load() && std::chrono::steady_clock::now() < stagedUntil)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
        if (!delivered->load() || session->battleIngressCreatedForTest()
            || session->snapshot(state) != T7RNB_OK || state.state != T7RNB_STATE_RUNNING)
            throw std::runtime_error("native staged capture failed before GAME");
        enabled->store(false);
        if (session->detachBattle() != T7RNB_OK || battleStop(handle) != T7RGW_OK)
            throw std::runtime_error("native staged match did not stop");
        uint64_t stopId = 0;
        if (session->submit(T7RNB_OPERATION_STOP, {}, stopId) != T7RNB_OK || !waitOperation(session, stopId))
            throw std::runtime_error("native fixture session did not stop");
        match.epoch = 10;
        if (battleStart(handle, &match) != T7RGW_OK)
            throw std::runtime_error("contact-loss fixture battle did not start");
        nativeInstance->store(4403);
        sampleBatch->store(0);
        delivered->store(false);
        droppedUnready->store(true);
        if (session->submit(T7RNB_OPERATION_START, clientDirectory, startId, u8"新玩家", false, {1, 2, 3}) != T7RNB_OK
            || !waitOperation(session, startId)
            || session->attachBattle(match, libraryPath, handle) != T7RNB_OK)
            throw std::runtime_error("contact-loss fixture did not attach");
        enabled->store(true);
        const auto lossUntil = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        do {
            session->snapshot(state);
            if (state.state == T7RNB_STATE_FAILED) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        } while (std::chrono::steady_clock::now() < lossUntil);
        if (!delivered->load() || state.state != T7RNB_STATE_FAILED
            || state.errorCode != T7RNB_ERROR_OPERATION || session->battleIngressCreatedForTest())
            throw std::runtime_error("unready native contact loss did not stop the attached session");
        enabled->store(false);
        if (battleStop(handle) != T7RGW_OK)
            throw std::runtime_error("contact-loss fixture battle did not stop");
        session->requestClose();
        if (!session->waitForWorkerForTest(std::chrono::seconds(5)))
            throw std::runtime_error("native fixture worker did not join");
        session.reset();
        if (release(handle) != T7RGW_OK) throw std::runtime_error("Rust gateway release failed");
        handle = 0;
        passed = true;
    } catch (const std::exception& error) {
        std::cerr << "session Rust attachment: " << error.what() << '\n';
    }
    if (session) {
        session->requestClose();
        session->waitForWorkerForTest(std::chrono::seconds(5));
    }
    if (handle) {
        const auto release = reinterpret_cast<decltype(&t7r_gateway_release)>(GetProcAddress(library, "t7r_gateway_release"));
        if (release) release(handle);
    }
    FreeLibrary(library);
    return passed;
}
