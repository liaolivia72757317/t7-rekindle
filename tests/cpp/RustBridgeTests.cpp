#include "../../src/BattleRust/ffi/include/t7r_runtime.h"
#include "../../src/Runtime/core/Protocol.h"
#include "../../src/Runtime/bridge/BattleSnapshotIngress.h"
#include "../../src/Runtime/bridge/BattleGatewaySink.h"
#include <array>
#include <fstream>
#include <iostream>
#include <sstream>
#include <cstddef>
#include <cstring>
#include <vector>
#include <chrono>
#include <thread>

namespace {
static_assert(sizeof(T7RGatewayTitleRequest) == 32, "title request ABI size");
static_assert(offsetof(T7RGatewayTitleRequest, epoch) == 16, "title request ABI epoch");
t7r::Bytes unhex(const std::string& text) {
    if (text.size() % 2) throw std::runtime_error("odd hex length");
    t7r::Bytes result;
    for (size_t index = 0; index < text.size(); index += 2)
        result.push_back(static_cast<unsigned char>(std::stoul(text.substr(index, 2), nullptr, 16)));
    return result;
}

void bigEndian(t7r::Bytes& bytes, uint64_t value, unsigned width) {
    for (unsigned shift = width * 8; shift != 0; shift -= 8)
        bytes.push_back(static_cast<unsigned char>(value >> (shift - 8)));
}
void number(t7r::Bytes& bytes, double value) {
    uint64_t bits = 0; std::memcpy(&bits, &value, sizeof(bits)); bigEndian(bytes, bits, 8);
}
t7r::Bytes worldSample(uint64_t epoch) {
    t7r::Bytes bytes;
    bigEndian(bytes, 1, 2); bigEndian(bytes, 0, 2);
    for (auto value : {epoch, 1ull, 1ull, 1000ull}) bigEndian(bytes, value, 8);
    bigEndian(bytes, 2, 2); bigEndian(bytes, 0, 2);
    for (uint32_t actor = 1; actor <= 2; ++actor) {
        bigEndian(bytes, actor, 4); bigEndian(bytes, actor, 2); bigEndian(bytes, 1, 2);
        bigEndian(bytes, actor + 100, 8);
        number(bytes, static_cast<double>(actor)); number(bytes, 20); number(bytes, 0);
        number(bytes, actor == 1 ? 0 : 180);
        bigEndian(bytes, 0, 4); bigEndian(bytes, 0, 4);
    }
    return bytes;
}
t7r::Bytes playerIntent(uint64_t epoch) {
    t7r::Bytes bytes;
    bigEndian(bytes, 1, 2); bigEndian(bytes, 0, 2);
    for (auto value : {epoch, 1ull, 1ull, 1000ull}) bigEndian(bytes, value, 8);
    bigEndian(bytes, 1, 4); bigEndian(bytes, 1, 2);
    bytes.push_back(1); bytes.push_back(0);
    return bytes;
}

uint64_t readBig(const t7r::Bytes& bytes, size_t offset, size_t width) {
    uint64_t value = 0;
    for (size_t index = 0; index < width; ++index) value = (value << 8) | bytes.at(offset + index);
    return value;
}

struct RecordingSink final : t7r::bridge::BattleSnapshotSink {
    std::vector<t7r::Bytes> worlds;
    std::vector<T7RBattleMotion> motions;
    std::vector<T7RBattleContacts> watermarks;
    std::vector<T7RBattleContact> contacts;
    std::vector<uint64_t> observations, sourceObservations;
    std::vector<t7r::Bytes> contactPayloads;
    bool rejectMotion = false;
    unsigned worldBusy = 0, motionBusy = 0, contactBusy = 0;
    t7r::Bytes retryWorld, retryContact;
    uint64_t retryMotionBatch = 0;
    int32_t submitWorld(const uint8_t* data, uint32_t size) override {
        const t7r::Bytes payload(data, data + size);
        if (!retryWorld.empty() && payload != retryWorld) throw std::runtime_error("BUSY world changed");
        if (worldBusy) { --worldBusy; retryWorld = payload; return T7RGW_BUSY; }
        retryWorld.clear();
        worlds.emplace_back(data, data + size);
        return T7RGW_OK;
    }
    int32_t submitMotion(const T7RBattleMotion& motion) override {
        if (retryMotionBatch && motion.batchId != retryMotionBatch) throw std::runtime_error("BUSY motion changed");
        if (motionBusy) { --motionBusy; retryMotionBatch = motion.batchId; return T7RGW_BUSY; }
        retryMotionBatch = 0;
        if (rejectMotion) return T7RGW_BUFFER_TOO_SMALL;
        motions.push_back(motion);
        return T7RGW_OK;
    }
    int32_t submitContacts(const uint8_t* data, uint32_t size) override {
        const t7r::Bytes bytes(data, data + size);
        if (!retryContact.empty() && bytes != retryContact) throw std::runtime_error("BUSY contact changed");
        if (contactBusy) { --contactBusy; retryContact = bytes; return T7RGW_BUSY; }
        retryContact.clear();
        contactPayloads.push_back(bytes);
        if (readBig(bytes, 0, 2) != 5) throw std::runtime_error("contact v5 required");
        T7RBattleContacts batch{};
        batch.throughAtMs = readBig(bytes, 28, 8);
        batch.count = static_cast<uint32_t>(readBig(bytes, 48, 2));
        if (size != 52 + batch.count * 296) throw std::runtime_error("contact v3 length");
        watermarks.push_back(batch);
        for (size_t i = 0; i < batch.count; ++i) {
            const auto at = 52 + 296 * i;
            T7RBattleContact e{};
            e.actorId = static_cast<uint32_t>(readBig(bytes, at, 4));
            e.targetId = static_cast<uint32_t>(readBig(bytes, at + 4, 4));
            e.targetClientRid = static_cast<uint16_t>(readBig(bytes, at + 10, 2));
            e.phaseAtMs = readBig(bytes, at + 32, 8);
            e.targetIntent = bytes.at(at + 30); e.targetPhase = bytes.at(at + 31);
            e.defenceWeaponId = static_cast<uint32_t>(readBig(bytes, at + 64, 4));
            e.flags = bytes.at(at + 71);
            e.region = bytes.at(at + 68); e.bodyIndex = static_cast<int8_t>(bytes.at(at + 69));
            const auto occurred = readBig(bytes, at + 48, 8);
            const auto phase = readBig(bytes, at + 40, 8);
            std::memcpy(&e.occurredAtMs, &occurred, sizeof(occurred));
            std::memcpy(&e.targetPhaseAtMs, &phase, sizeof(phase));
            contacts.push_back(e);
            observations.push_back(readBig(bytes, at + 280, 8));
            sourceObservations.push_back(readBig(bytes, at + 288, 8));
        }
        return T7RGW_OK;
    }
};

struct GatewaySink final : t7r::bridge::BattleSnapshotSink {
    uint64_t handle;
    explicit GatewaySink(uint64_t value) : handle(value) {}
    int32_t submitWorld(const uint8_t* data, uint32_t size) override {
        const auto result = t7r_gateway_battle_submit(handle, 6, data, size);
        if (result != T7RGW_OK) std::cerr << "gateway world status=" << result << '\n';
        return result;
    }
    int32_t submitMotion(const T7RBattleMotion& motion) override {
        return t7r_gateway_battle_submit_motion(handle, &motion);
    }
    int32_t submitContacts(const uint8_t* data, uint32_t size) override {
        const auto result = t7r_gateway_battle_submit(handle, 14, data, size);
        if (result != T7RGW_OK) std::cerr << "gateway contacts status=" << result << '\n';
        return result;
    }
};

t7r::WorldSnapshot nativeSnapshot() {
    t7r::WorldSnapshot sample{};
    sample.ready = true; sample.sampledAt = 5000; sample.instanceStartedAt = 4400;
    sample.contactsReady = true; sample.contactBatchId = 1;
    for (uint32_t index = 0; index != 2; ++index) {
        auto& actor = sample.actors[index];
        actor.ready = true; actor.entity = 101 + index; actor.sequence = 1;
        actor.position = {static_cast<float>(index + 1), 20.f, 0.f};
        actor.heading = index ? 180.f : 0.f;
    }
    sample.actors[1].forwardAttemptAt = 4999;
    t7r::BladeContactEvent event{};
    event.actorId = 1; event.entity = 101; event.sequence = 1;
    event.instanceStartedAt = 4400; event.phaseAt = 5000;
    event.targetPhaseAt = 5000; event.occurredAt = 5000; event.targetObservedAt = 5000; event.sourceObservedAt = 5000;
    event.hit.contact.ready = true; event.hit.contact.hit = true;
    event.hit.contact.position = {1, 20, 0};
    event.hit.contact.normal = {0, 0, 1}; event.hit.contact.direction = {1, 0, 0};
    event.hit.along = .5f; event.hit.bodyIndex = -1;
    event.sourcePosition = {1, 20, 0}; event.targetPosition = {1, 20, 0};
    sample.contacts.push_back(event);
    return sample;
}

void nativeRosterIngress(const T7RBattleConfig& templateConfig) {
    for (const uint32_t count : {10u, 60u}) {
        auto config = templateConfig;
        config.memberCount = count;
        for (uint32_t i = 0; i < count; ++i) {
            config.members[i] = templateConfig.members[i == 0 ? 0 : 1];
            config.members[i].actorId = i + 1;
            config.members[i].clientRid = static_cast<uint16_t>(i + 1);
        }
        auto sample = nativeSnapshot();
        for (uint32_t i = 1; i < t7r::MAX_WORLD_ACTORS; ++i) {
            sample.actors[i] = sample.actors[0];
            sample.actors[i].entity = 101 + i;
        }
        sample.actors[0].targetId = count;
        sample.actors[0].targetTerrain[count - 1].ready = true;
        sample.contacts[0].hit.targetId = count;
        sample.contacts[0].targetEntity = 100 + count;
        RecordingSink sink;
        t7r::bridge::BattleSnapshotIngress ingress(config, 4400, 5000, 1000, sink);
        ingress.submit(sample);
        ingress.flush();
        if (sink.worlds.size() != 1 || readBig(sink.worlds[0], 36, 2) != count
            || sink.motions.size() != count - 1 || sink.motions.back().actorId != count
            || readBig(sink.worlds[0], 88, 4) != count
            || sink.contacts.size() != 1 || sink.contacts[0].targetId != count)
            throw std::runtime_error("native roster dropped or invented a bound actor");
    }
}

void backpressureDeadline(const T7RBattleConfig& match) {
    auto check = [](bool value) { if (!value) throw std::runtime_error("native backpressure deadline failed"); };
    {
        RecordingSink busy;
        busy.worldBusy = UINT_MAX;
        t7r::bridge::BattleSnapshotIngress ingress(match, 4400, 5000, 1000, busy);
        const auto before = GetTickCount64();
        std::string error;
        try { ingress.submit(nativeSnapshot()); }
        catch (const std::runtime_error& failure) { error = failure.what(); }
        check(error.find("backpressure exceeded 5 seconds") != std::string::npos
            && GetTickCount64() - before >= 5000 && GetTickCount64() - before < 8000 && busy.worlds.empty());
        busy.worldBusy = 0;
        try { ingress.submit(nativeSnapshot()); check(false); }
        catch (const std::runtime_error& failure) { check(std::string(failure.what()).find("requires a new match") != std::string::npos); }
    }
}

void snapshotIngress(const T7RBattleConfig& match, uint64_t handle) {
    nativeRosterIngress(match);
    auto check = [](bool value) { if (!value) throw std::runtime_error("native snapshot ingress failed"); };
    check(t7r::bridge::clientTimeOffset(5010, 4400, 1010) == -400);
    check(t7r::bridge::clientTimeOffset(2000, 1000, 500) == 500);
    check(t7r::bridge::clientTimeOffset(1, 1, uint64_t{INT64_MAX} + 1) == INT64_MIN);
    check(t7r::bridge::serverTimeOffset(5010, 1010) == 4000);
    check(t7r::bridge::serverTimeOffset(500, 1000) == -500);
    check(t7r::bridge::serverTimeOffset(1, uint64_t{INT64_MAX} + 2) == INT64_MIN);
    for (const auto& clocks : {std::array<uint64_t, 3>{1, 0, 0}, {1, 2, 0},
                               {UINT64_MAX, 1, 0}, {1, 1, UINT64_MAX}}) {
        bool rejected = false;
        try { (void)t7r::bridge::clientTimeOffset(clocks[0], clocks[1], clocks[2]); }
        catch (const std::runtime_error&) { rejected = true; }
        check(rejected);
    }
    for (const auto& clocks : {std::array<uint64_t, 2>{0, 1}, {UINT64_MAX, 0}, {1, UINT64_MAX}}) {
        bool rejected = false;
        try { (void)t7r::bridge::serverTimeOffset(clocks[0], clocks[1]); }
        catch (const std::runtime_error&) { rejected = true; }
        check(rejected);
    }
    auto sample = nativeSnapshot();
    RecordingSink afterSample;
    afterSample.worldBusy = afterSample.motionBusy = afterSample.contactBusy = 2;
    t7r::bridge::BattleSnapshotIngress anchoredAfterSample(match, 4400, 5010, 1010, afterSample);
    anchoredAfterSample.submit(sample);
    check(afterSample.worlds.size() == 1 && readBig(afterSample.worlds[0], 28, 8) == 1000
          && afterSample.motions.size() == 1 && afterSample.motions[0].attemptedForwardAtMs == 999);
    anchoredAfterSample.flush();
    check(afterSample.contacts.size() == 1 && afterSample.contacts[0].occurredAtMs == 1000);
    RecordingSink observationSink;
    t7r::bridge::BattleSnapshotIngress observed(match, 4400, 5010, 1010, observationSink);
    auto observedSample = sample;
    observedSample.contacts[0].targetPhaseAt = 5001;
    observedSample.contacts[0].phaseAt = 5001;
    observedSample.contacts[0].sourceObservedAt = 5017;
    observedSample.contacts[0].targetObservedAt = 5016;
    observed.submit(observedSample); observed.flush();
    check(observationSink.contacts[0].occurredAtMs == 1000
          && observationSink.contacts[0].targetPhaseAtMs == 1001
          && observationSink.observations[0] == 1016
          && observationSink.contacts[0].phaseAtMs == 1001
          && observationSink.sourceObservations[0] == 1017
          && observationSink.watermarks[0].throughAtMs == 1000);
    RecordingSink underflow;
    t7r::bridge::BattleSnapshotIngress invalidClock(match, 4400, 5010, 5, underflow);
    try { invalidClock.submit(sample); throw std::runtime_error("clock underflow accepted"); }
    catch (const std::runtime_error& error) { check(std::string(error.what()) != "clock underflow accepted"); }
    check(underflow.worlds.empty());
    RecordingSink recorded;
    t7r::bridge::BattleSnapshotIngress ingress(match, 4400, 4999, 999, recorded);
    sample.ready = false;
    ingress.submit(sample);
    check(recorded.worlds.empty() && recorded.watermarks.empty());
    RecordingSink droppedSink;
    t7r::bridge::BattleSnapshotIngress droppedIngress(match, 4400, 4999, 999, droppedSink);
    auto droppedSample = sample;
    droppedSample.contacts.clear();
    droppedSample.droppedContacts = 1;
    try { droppedIngress.submit(droppedSample); throw std::runtime_error("unready contact loss accepted"); }
    catch (const std::runtime_error& error) { check(std::string(error.what()) != "unready contact loss accepted"); }
    droppedSample.droppedContacts = 0;
    droppedSample.ready = true;
    try { droppedIngress.submit(droppedSample); throw std::runtime_error("contact-loss ingress reused"); }
    catch (const std::runtime_error& error) { check(std::string(error.what()) != "contact-loss ingress reused"); }
    try { droppedIngress.flush(); throw std::runtime_error("contact-loss ingress flushed"); }
    catch (const std::runtime_error& error) { check(std::string(error.what()) != "contact-loss ingress flushed"); }
    check(droppedSink.worlds.empty() && droppedSink.watermarks.empty());
    RecordingSink pendingLossSink;
    t7r::bridge::BattleSnapshotIngress pendingLoss(match, 4400, 4999, 999, pendingLossSink);
    auto pendingSample = nativeSnapshot();
    pendingLoss.submit(pendingSample);
    pendingSample.ready = false;
    pendingSample.contacts.clear();
    pendingSample.droppedContacts = 1;
    try { pendingLoss.submit(pendingSample); throw std::runtime_error("mid-battle contact loss accepted"); }
    catch (const std::runtime_error& error) { check(std::string(error.what()) != "mid-battle contact loss accepted"); }
    try { pendingLoss.flush(); throw std::runtime_error("partial evidence flushed after contact loss"); }
    catch (const std::runtime_error& error) { check(std::string(error.what()) != "partial evidence flushed after contact loss"); }
    check(pendingLossSink.worlds.size() == 1 && pendingLossSink.watermarks.empty());
    sample.ready = true;
    ingress.submit(sample);
    check(recorded.worlds.size() == 1 && recorded.motions.size() == 1 && recorded.watermarks.empty());
    check(recorded.worlds[0].size() == 152 && readBig(recorded.worlds[0], 4, 8) == match.epoch
          && readBig(recorded.worlds[0], 28, 8) == 1000
          && readBig(recorded.worlds[0], 44, 2) == 1);
    check(recorded.worlds[0] == worldSample(match.epoch));
    check(recorded.motions[0].actorId == 2 && recorded.motions[0].attemptedForwardAtMs == 999);
    sample.sampledAt = 5010; sample.contacts.clear(); sample.actors[1].forwardAttemptAt = 0;
    ingress.submit(sample);
    check(recorded.worlds.size() == 2 && recorded.motions.size() == 2
          && recorded.motions[1].attemptedForwardAtMs == 0 && recorded.watermarks.empty());
    sample.sampledAt = 5200;
    sample.contactBatchId = 2;
    sample.actors[1].forwardAttemptAt = 4500;
    auto delayed = nativeSnapshot().contacts[0];
    delayed.occurredAt = 5010; delayed.targetObservedAt = 5016; delayed.sourceObservedAt = 5016;
    sample.contacts.push_back(delayed);
    ingress.submit(sample);
    check(recorded.watermarks.empty()
          && recorded.motions.size() == 3 && recorded.motions[2].attemptedForwardAtMs == 0);
    sample.sampledAt = 5300; sample.contactBatchId = 3; sample.contacts.clear();
    ingress.submit(sample);
    check(recorded.watermarks.size() == 1 && recorded.watermarks[0].throughAtMs == 1100
          && recorded.contacts.size() == 2 && recorded.contacts[0].actorId == 1
          && recorded.contacts[0].occurredAtMs == 1000 && recorded.contacts[1].occurredAtMs == 1010);
    ingress.flush();
    check(recorded.watermarks.size() == 2 && recorded.watermarks[1].count == 0
          && recorded.watermarks[1].throughAtMs == 1300);
    sample.contactBatchId = 5;
    try { ingress.submit(sample); throw std::runtime_error("gap accepted"); }
    catch (const std::runtime_error& error) { check(std::string(error.what()) != "gap accepted"); }
    sample = nativeSnapshot();
    sample.contacts[0].hit.targetId = 2;
    sample.contacts[0].hit.region = 2;
    sample.contacts[0].targetEntity = 102;
    sample.contacts[0].targetState = 225;
    auto remapped = match;
    remapped.members[0].clientRid = 2;
    remapped.members[1].clientRid = 1;
    RecordingSink rebound;
    t7r::bridge::BattleSnapshotIngress remappedIngress(remapped, 4400, 4999, 999, rebound);
    remappedIngress.submit(sample);
    remappedIngress.flush();
    check(readBig(rebound.worlds[0], 40, 4) == 2 && readBig(rebound.worlds[0], 44, 2) == 1
          && rebound.motions.size() == 1 && rebound.motions[0].actorId == 2
          && rebound.motions[0].clientRid == 1 && rebound.contacts[0].actorId == 2
          && rebound.contacts[0].targetId == 1 && rebound.contacts[0].targetClientRid == 2
          && rebound.contacts[0].targetIntent == 7 && rebound.contacts[0].targetPhase == 2);
    sample.contacts[0].targetState = 41;
    RecordingSink brokenShield;
    t7r::bridge::BattleSnapshotIngress brokenShieldIngress(match, 4400, 4999, 999, brokenShield);
    brokenShieldIngress.submit(sample);
    brokenShieldIngress.flush();
    check(brokenShield.contacts.size() == 1 && brokenShield.contacts[0].targetIntent == 0
          && brokenShield.contacts[0].targetPhase == 9);
    sample.instanceStartedAt = 4401; sample.contactBatchId = 2; sample.contacts.clear();
    try { remappedIngress.submit(sample); throw std::runtime_error("old instance accepted"); }
    catch (const std::runtime_error& error) { check(std::string(error.what()) != "old instance accepted"); }
    sample = nativeSnapshot();
    RecordingSink blocked;
    blocked.rejectMotion = true;
    t7r::bridge::BattleSnapshotIngress failedIngress(match, 4400, 4999, 999, blocked);
    try { failedIngress.submit(sample); throw std::runtime_error("backpressure accepted"); }
    catch (const std::runtime_error& error) {
        const std::string message = error.what();
        check(message.find("Rust battle rejected native bot motion: status=") == 0
              && message.find("; actor=2; batch=1; worldBatch=1; matchAtMs=1000") != std::string::npos);
    }
    check(blocked.worlds.size() == 1 && blocked.watermarks.empty());
    try { failedIngress.submit(sample); throw std::runtime_error("failed ingress reused"); }
    catch (const std::runtime_error& error) { check(std::string(error.what()) != "failed ingress reused"); }
    check(blocked.worlds.size() == 1);
    GatewaySink gateway(handle);
    t7r::bridge::BattleSnapshotIngress live(match, 4400, 4999, 999, gateway);
    sample = nativeSnapshot();
    auto observedEvent = sample.contacts[0];
    observedEvent.targetPhaseAt = 5001; observedEvent.targetObservedAt = 5016;
    observedEvent.phaseAt = 5001; observedEvent.sourceObservedAt = 5017;
    observedEvent.hit.targetId = 2; observedEvent.targetEntity = 102;
    observedEvent.hit.region = 2; observedEvent.targetState = 225;
    // More than 4096 bytes exercises the bounded kind-14 payload route.
    sample.contacts.assign(20, observedEvent);
    live.submit(sample);
    std::this_thread::sleep_for(std::chrono::milliseconds(25));
    sample.sampledAt = 5010; sample.contactBatchId = 2; sample.contacts.clear();
    sample.actors[1].forwardAttemptAt = 0;
    sample.ready = false;
    observedEvent.occurredAt = 5009.5;
    sample.contacts.push_back(observedEvent);
    live.submit(sample);
    live.flush();
    sample.ready = true; sample.sampledAt = 5020; sample.contactBatchId = 3;
    sample.contacts.clear();
    live.submit(sample);
    live.flush();
}

void protocolCorpus() {
    std::ifstream input("tests/fixtures/protocol/method3-v1.txt");
    if (!input) throw std::runtime_error("run from repository root: protocol corpus not found");
    std::string line; size_t count = 0;
    while (std::getline(input, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream row(line);
        std::string clear, frame;
        if (!(row >> clear >> frame)) throw std::runtime_error("invalid corpus row");
        if (t7r::decryptMethod3(unhex(frame)) != unhex(clear))
            throw std::runtime_error("native method-3 differs from shared reference vector");
        ++count;
    }
    if (!input.eof() || count != 32) throw std::runtime_error("incomplete protocol corpus");
}

void gatewayLifecycle() {
    static_assert(sizeof(T7RGatewaySnapshot) == 56, "gateway ABI size");
    static_assert(sizeof(T7RBattleEquipment) == 8 && sizeof(T7RBattleMember) == 52, "battle member ABI size");
    static_assert(sizeof(T7RBattleConfig) == 3176, "battle config ABI size");
    static_assert(sizeof(T7RBattleMotion) == 104, "battle motion ABI size");
    static_assert(sizeof(T7RBattleContact) == 288, "battle contact ABI size");
    static_assert(sizeof(T7RBattleContacts) == 64, "battle contacts ABI size");
    static_assert(offsetof(T7RBattleConfig, members) == 56, "battle member ABI alignment");
    static_assert(offsetof(T7RBattleContact, phaseAtMs) == 40, "battle contact ABI alignment");
    static_assert(offsetof(T7RBattleContact, sourcePosition) == 80, "battle contact geometry alignment");
    static_assert(offsetof(T7RGatewaySnapshot, epoch) == 8, "gateway epoch alignment");
    static_assert(offsetof(T7RGatewaySnapshot, rulesVersion) == 40, "gateway resource layout");
    std::ifstream input("tests/fixtures/resources/bundle-v1.json", std::ios::binary);
    if (!input) throw std::runtime_error("resource fixture not found");
    const std::string bytes((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    uint64_t handle = 0;
    auto check = [](bool success) { if (!success) throw std::runtime_error("gateway C ABI lifecycle failed"); };
    check(t7r_gateway_create(nullptr, 0, &handle) == T7RGW_INVALID_ARGUMENT && handle == 0);
    check(t7r_gateway_create(reinterpret_cast<const uint8_t*>(bytes.data()), static_cast<uint32_t>(bytes.size()), &handle) == T7RGW_OK);
    try {
        T7RGatewaySnapshot state{}; state.abiVersion = 1; state.structSize = sizeof(state);
        check(t7r_gateway_snapshot(handle, &state) == T7RGW_OK && state.state == 0 && state.mapId == 1028);
        check(t7r_gateway_start(handle, 1) == T7RGW_OK);
        check(t7r_gateway_start(handle, 2) == T7RGW_INVALID_STATE);
        check(t7r_gateway_snapshot(handle, &state) == T7RGW_OK && state.state == 1 && state.epoch == 1);
        check(state.loginPort != 0 && state.logicPort != 0 && state.instancePort != 0);
        uint64_t battleClock = 99;
        check(t7r_gateway_battle_clock(handle, nullptr) == T7RGW_INVALID_ARGUMENT);
        check(t7r_gateway_battle_clock(handle, &battleClock) == T7RGW_INVALID_STATE && battleClock == 0);
        check(t7r_gateway_battle_dispatch_next(handle) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_bind_client_clock(handle, 7, -1000) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_bind_server_clock(handle, 7, 4000) == T7RGW_INVALID_STATE);
        T7RBattleConfig match{};
        match.abiVersion = T7R_BATTLE_CONFIG_ABI_VERSION; match.structSize = sizeof(match);
        match.epoch = 7; match.startedAtMs = 1000; match.durationMs = 120000;
        match.killTarget = 10; match.seed = 42; match.sourceId = 1; match.memberCount = 20;
        for (uint32_t i = 0; i < 20; ++i) {
            auto& member = match.members[i];
            member.actorId = i + 1; member.clientRid = static_cast<uint16_t>(i + 1);
            member.camp = i >= 1 && i <= 10 ? 2 : 1; member.kind = i == 0 ? 1 : 2;
            member.heroId = 1101; member.weaponId = 701; member.equipmentCount = 1;
            member.equipment[0].weaponId = 701; member.equipment[0].slot = 1;
        }
        check(t7r_gateway_battle_start(handle, nullptr) == T7RGW_INVALID_ARGUMENT);
        match.abiVersion = 1; match.structSize = 1096;
        check(t7r_gateway_battle_start(handle, &match) == T7RGW_INVALID_ARGUMENT);
        check(t7r_gateway_battle_start_staged(handle, &match) == T7RGW_INVALID_ARGUMENT);
        match.abiVersion = T7R_BATTLE_CONFIG_ABI_VERSION; match.structSize = sizeof(match);
        match.members[1].clientRid = match.members[0].clientRid;
        check(t7r_gateway_battle_start(handle, &match) == T7RGW_INVALID_ARGUMENT);
        match.members[1].clientRid = 2;
        check(t7r_gateway_battle_start(handle, &match) == T7RGW_OK);
        check(t7r_gateway_battle_clock(handle, &battleClock) == T7RGW_OK && battleClock >= match.startedAtMs);
        check(t7r_gateway_battle_bind_client_clock(handle, 6, -1000) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_bind_client_clock(handle, 7, -1000) == T7RGW_OK);
        check(t7r_gateway_battle_bind_client_clock(handle, 7, -1000) == T7RGW_OK);
        check(t7r_gateway_battle_bind_client_clock(handle, 7, -999) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_bind_server_clock(handle, 6, 4000) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_bind_server_clock(handle, 7, 4000) == T7RGW_OK);
        check(t7r_gateway_battle_bind_server_clock(handle, 7, 4000) == T7RGW_OK);
        check(t7r_gateway_battle_bind_server_clock(handle, 7, 4001) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_start(handle, &match) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_submit(handle, 6, nullptr, 0) == T7RGW_INVALID_ARGUMENT);
        const auto sample = worldSample(7);
        check(sample.size() == 152 && t7r_gateway_battle_submit(handle, 6, sample.data(), static_cast<uint32_t>(sample.size())) == T7RGW_OK);
        bool dispatched = false;
        const auto dispatchDeadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (!dispatched && std::chrono::steady_clock::now() < dispatchDeadline) {
            const auto result = t7r_gateway_battle_dispatch_next(handle);
            check(result == T7RGW_OK || result == T7RGW_EMPTY);
            dispatched = result == T7RGW_OK;
            if (!dispatched) std::this_thread::sleep_for(std::chrono::milliseconds(5));
        }
        check(dispatched);
        check(t7r_gateway_battle_submit(handle, 6, sample.data(), static_cast<uint32_t>(sample.size())) == T7RGW_INVALID_ARGUMENT);
        T7RBattleMotion motion{};
        motion.abiVersion = 1; motion.structSize = sizeof(motion);
        motion.epoch = 7; motion.sourceId = 1; motion.batchId = 1; motion.worldBatchId = 1;
        motion.sampledAtMs = 1000; motion.actorId = 2; motion.clientRid = 2; motion.entity = 102;
        motion.position[0] = 2; motion.position[1] = 20; motion.heading = 180;
        motion.attemptedForwardAtMs = 999;
        check(t7r_gateway_battle_submit_motion(handle, nullptr) == T7RGW_INVALID_ARGUMENT);
        motion.clientRid = 1;
        check(t7r_gateway_battle_submit_motion(handle, &motion) == T7RGW_INVALID_ARGUMENT);
        motion.clientRid = 2;
        check(t7r_gateway_battle_submit_motion(handle, &motion) == T7RGW_OK);
        check(t7r_gateway_battle_submit_motion(handle, &motion) == T7RGW_INVALID_ARGUMENT);
        T7RBattleContact contact{};
        contact.actorId = 1; contact.clientRid = 1; contact.sourceEntity = 101;
        contact.sequence = 1; contact.phaseAtMs = 1000;
        contact.targetPhaseAtMs = 1000; contact.occurredAtMs = 1000; contact.along = .5;
        contact.bodyIndex = -1; contact.sourcePosition[0] = 1; contact.sourcePosition[1] = 20;
        contact.targetPosition[0] = 1; contact.targetPosition[1] = 20;
        contact.position[0] = 1; contact.position[1] = 20;
        contact.normal[2] = 1; contact.direction[0] = 1;
        std::array<T7RBattleContact, 3> events{contact, contact, contact};
        auto& weapon = events[1];
        weapon.targetId = 2; weapon.targetClientRid = 2; weapon.targetEntity = 102;
        weapon.targetPosition[0] = 2; weapon.targetIntent = 5; weapon.targetPhase = 2;
        weapon.defenceWeaponId = 701;
        events[2] = weapon; events[2].defenceWeaponId = 0; events[2].region = 2;
        T7RBattleContacts contacts{};
        contacts.abiVersion = 1; contacts.structSize = sizeof(contacts);
        contacts.epoch = 7; contacts.sourceId = 1; contacts.batchId = 1;
        contacts.worldBatchId = 1; contacts.throughAtMs = 1000;
        contacts.count = static_cast<uint32_t>(events.size()); contacts.events = events.data();
        check(t7r_gateway_battle_submit_contacts(handle, nullptr) == T7RGW_INVALID_ARGUMENT);
        contacts.dropped = 1;
        check(t7r_gateway_battle_submit_contacts(handle, &contacts) == T7RGW_INVALID_ARGUMENT);
        contacts.dropped = 0;
        weapon.defenceWeaponId = 0;
        check(t7r_gateway_battle_submit_contacts(handle, &contacts) == T7RGW_INVALID_ARGUMENT);
        weapon.defenceWeaponId = 701;
        weapon.targetPhaseAtMs = -4999;
        check(t7r_gateway_battle_submit_contacts(handle, &contacts) == T7RGW_INVALID_ARGUMENT);
        weapon.targetPhaseAtMs = 1000;
        check(t7r_gateway_battle_submit_contacts(handle, &contacts) == T7RGW_OK);
        check(t7r_gateway_battle_submit_contacts(handle, &contacts) == T7RGW_INVALID_ARGUMENT);
        const auto intent = playerIntent(7);
        check(intent.size() == 44 && t7r_gateway_battle_submit(handle, 11, intent.data(), static_cast<uint32_t>(intent.size())) == T7RGW_OK);
        check(t7r_gateway_battle_submit(handle, 11, intent.data(), static_cast<uint32_t>(intent.size())) == T7RGW_INVALID_ARGUMENT);
        check(t7r_gateway_battle_submit(handle, 99, sample.data(), static_cast<uint32_t>(sample.size())) == T7RGW_INVALID_ARGUMENT);
        check(t7r_gateway_battle_stop(handle) == T7RGW_OK);
        check(t7r_gateway_battle_dispatch_next(handle) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_clock(handle, &battleClock) == T7RGW_INVALID_STATE && battleClock == 0);
        check(t7r_gateway_battle_bind_client_clock(handle, 7, -1000) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_bind_server_clock(handle, 7, 4000) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_submit_motion(handle, &motion) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_submit_contacts(handle, &contacts) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_start(handle, &match) == T7RGW_INVALID_STATE);
        match.epoch = 8;
        check(t7r_gateway_battle_start(handle, &match) == T7RGW_OK);
        check(t7r_gateway_battle_bind_client_clock(handle, 7, -1000) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_bind_client_clock(handle, 8, 6000) == T7RGW_OK);
        check(t7r_gateway_battle_bind_server_clock(handle, 7, 4000) == T7RGW_INVALID_STATE);
        check(t7r_gateway_battle_bind_server_clock(handle, 8, 4000) == T7RGW_OK);
        snapshotIngress(match, handle);
        check(t7r_gateway_battle_submit(handle, 6, sample.data(), static_cast<uint32_t>(sample.size())) == T7RGW_INVALID_ARGUMENT);
        check(t7r_gateway_battle_submit_motion(handle, &motion) == T7RGW_INVALID_ARGUMENT);
        check(t7r_gateway_battle_submit_contacts(handle, &contacts) == T7RGW_INVALID_ARGUMENT);
        uint32_t required = 99;
        check(t7r_gateway_read_request(handle, nullptr, 0, &required) == T7RGW_EMPTY && required == 0);
        check(t7r_gateway_battle_stop(handle) == T7RGW_OK);
        backpressureDeadline(match);
        match.epoch = 9;
        const auto stagedStatus = t7r_gateway_battle_start_staged(handle, &match);
        if (stagedStatus != T7RGW_OK)
            throw std::runtime_error("staged battle start status=" + std::to_string(stagedStatus));
        battleClock = 99;
        const auto stagedClockStatus = t7r_gateway_battle_clock(handle, &battleClock);
        if (stagedClockStatus != T7RGW_EMPTY || battleClock != 0)
            throw std::runtime_error("staged battle clock status=" + std::to_string(stagedClockStatus));
        check(t7r_gateway_battle_stop(handle) == T7RGW_OK);
        check(t7r_gateway_stop(handle) == T7RGW_OK);
        check(t7r_gateway_stop(handle) == T7RGW_OK);
        check(t7r_gateway_start(handle, 1) == T7RGW_INVALID_STATE);
        check(t7r_gateway_start(handle, 2) == T7RGW_OK);
    } catch (...) { (void)t7r_gateway_release(handle); throw; }
    check(t7r_gateway_release(handle) == T7RGW_OK);
    check(t7r_gateway_start(handle, 3) == T7RGW_INVALID_HANDLE);
    check(t7r_gateway_release(handle) == T7RGW_INVALID_HANDLE);
}

void dynamicGatewayIngress() {
    wchar_t pathBuffer[32768]{};
    const auto length = GetEnvironmentVariableW(L"T7R_BATTLE_FFI_DLL", pathBuffer,
                                                static_cast<DWORD>(std::size(pathBuffer)));
    if (!length) return;
    if (length >= std::size(pathBuffer)) throw std::runtime_error("dynamic Rust DLL path is too long");
    const std::filesystem::path path(pathBuffer);
    HMODULE library = LoadLibraryExW(path.c_str(), nullptr, 0x100 | 0x800);
    if (!library) throw std::runtime_error("loading dynamic Rust test library failed");
    uint64_t handle = 0;
    try {
        const auto symbol = [library](const char* name) {
            const auto address = GetProcAddress(library, name);
            if (!address) throw std::runtime_error(std::string("missing dynamic test export: ") + name);
            return address;
        };
        const auto create = reinterpret_cast<decltype(&t7r_gateway_create)>(symbol("t7r_gateway_create"));
        const auto start = reinterpret_cast<decltype(&t7r_gateway_start)>(symbol("t7r_gateway_start"));
        const auto stop = reinterpret_cast<decltype(&t7r_gateway_stop)>(symbol("t7r_gateway_stop"));
        const auto release = reinterpret_cast<decltype(&t7r_gateway_release)>(symbol("t7r_gateway_release"));
        const auto battleStart = reinterpret_cast<decltype(&t7r_gateway_battle_start)>(symbol("t7r_gateway_battle_start"));
        const auto battleStop = reinterpret_cast<decltype(&t7r_gateway_battle_stop)>(symbol("t7r_gateway_battle_stop"));
        std::ifstream input("tests/fixtures/resources/bundle-v1.json", std::ios::binary);
        if (!input) throw std::runtime_error("dynamic Rust resource fixture not found");
        const std::string bundle((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
        if (create(reinterpret_cast<const uint8_t*>(bundle.data()), static_cast<uint32_t>(bundle.size()), &handle)
            != T7RGW_OK || !handle || start(handle, 1) != T7RGW_OK)
            throw std::runtime_error("dynamic Rust gateway did not start");
        T7RGatewaySnapshot staticState{};
        staticState.abiVersion = 1; staticState.structSize = sizeof(staticState);
        if (t7r_gateway_snapshot(handle, &staticState) != T7RGW_INVALID_HANDLE)
            throw std::runtime_error("dynamic gateway unexpectedly shares the static test registry");
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
            throw std::runtime_error("dynamic Rust battle did not start");
        {
            t7r::bridge::BattleGatewaySink sink(path, handle);
            if (sink.clockMs() < match.startedAtMs)
                throw std::runtime_error("dynamic Rust battle clock precedes match start");
            sink.bindClientClock(match.epoch, -1000);
            sink.bindServerClock(match.epoch, 4000);
            t7r::bridge::BattleSnapshotIngress ingress(match, 4400, 4999, 999, sink);
            auto sample = nativeSnapshot();
            auto weapon = sample.contacts.front();
            weapon.hit.targetId = 2; weapon.targetEntity = 102; weapon.targetState = 223;
            weapon.targetPosition = sample.actors[1].position;
            weapon.hit.defenceWeaponId = 701; weapon.hit.guardVolume = true;
            sample.contacts.push_back(weapon);
            auto body = weapon; body.hit.guardVolume = false; body.hit.defenceWeaponId = 0; body.hit.region = 2;
            sample.contacts.push_back(body);
            RecordingSink recorded;
            t7r::bridge::BattleSnapshotIngress recording(match, 4400, 4999, 999, recorded);
            recording.submit(sample); recording.flush();
            if (recorded.contacts.size() != 3 || recorded.contacts[1].defenceWeaponId != 701
                || recorded.contacts[1].region != 0 || recorded.contacts[1].bodyIndex != -1 || (recorded.contacts[1].flags & 8) == 0
                || recorded.contacts[2].defenceWeaponId != 0 || recorded.contacts[2].region != 2)
                throw std::runtime_error("native ingress changed weapon/body contact identity");
            ingress.submit(sample);
            std::this_thread::sleep_for(std::chrono::milliseconds(15));
            sample.sampledAt = 5010; sample.contactBatchId = 2;
            sample.contacts.clear(); sample.actors[1].forwardAttemptAt = 0;
            ingress.submit(sample);
            ingress.flush();
            T7RBattleMotion badMotion{};
            badMotion.abiVersion = 1; badMotion.structSize = sizeof(badMotion);
            badMotion.epoch = match.epoch; badMotion.sourceId = match.sourceId;
            badMotion.batchId = 3; badMotion.worldBatchId = 2; badMotion.sampledAtMs = 1010;
            badMotion.actorId = 2; badMotion.clientRid = 2; badMotion.entity = 999;
            badMotion.position[0] = 2; badMotion.position[1] = 20; badMotion.heading = 180;
            if (sink.submitMotion(badMotion) != T7RGW_INVALID_ARGUMENT
                || sink.lastError() != "AI motion differs from sampled world")
                throw std::runtime_error("dynamic Rust motion error lost its diagnostic cause");
            auto invalid = recorded.contactPayloads.front();
            // Remove the weapon ID without inventing a body region.
            for (size_t index = 0; index != 4; ++index) invalid.at(52 + 296 + 64 + index) = 0;
            if (sink.submitContacts(invalid.data(), static_cast<uint32_t>(invalid.size())) != T7RGW_INVALID_ARGUMENT)
                throw std::runtime_error("dynamic Rust accepted a body contact without a region");
            const auto cause = sink.lastError();
            if (cause.find("contact payload decode failed") == std::string::npos
                || cause.find("batchId=1") == std::string::npos || cause.find("region") == std::string::npos)
                throw std::runtime_error("dynamic Rust contact error lost its decode details");
            if (sink.submitMotion(badMotion) != T7RGW_INVALID_ARGUMENT
                || sink.lastError().find(cause) == std::string::npos)
                throw std::runtime_error("followup error replaced the contact decode cause");
        }
        if (battleStop(handle) != T7RGW_OK)
            throw std::runtime_error("dynamic clock fixture did not stop");
        match.epoch = 8;
        if (battleStart(handle, &match) != T7RGW_OK)
            throw std::runtime_error("dynamic clock fixture did not restart");
        {
            t7r::bridge::BattleGatewaySink sink(path, handle);
            std::this_thread::sleep_for(std::chrono::milliseconds(40));
            const auto anchor = sink.tryClockAnchor();
            if (!anchor) throw std::runtime_error("running match has no clock anchor");
            auto sample = nativeSnapshot();
            sample.contacts.clear(); sample.actors[1].forwardAttemptAt = 0;
            t7r::bridge::BattleSnapshotIngress ingress(match, sample.instanceStartedAt,
                anchor->nativeMs, anchor->matchMs, sink);
            // No socket consumer in this fixture: keep publications within its bounded queue.
            for (uint32_t batch = 1; batch <= 16; ++batch) {
                sample.sampledAt = GetTickCount64(); sample.contactBatchId = batch;
                ingress.submit(sample);
                std::this_thread::sleep_for(std::chrono::milliseconds(2));
            }
            ingress.flush();
        }
        if (battleStop(handle) != T7RGW_OK || stop(handle) != T7RGW_OK || release(handle) != T7RGW_OK)
            throw std::runtime_error("dynamic Rust gateway did not stop cleanly");
        handle = 0;
    } catch (...) {
        if (handle) {
            const auto release = reinterpret_cast<decltype(&t7r_gateway_release)>(GetProcAddress(library, "t7r_gateway_release"));
            if (release) (void)release(handle);
        }
        FreeLibrary(library);
        throw;
    }
    FreeLibrary(library);
}

void preciseClockAnchors() {
    using t7r::bridge::BattleClockAnchor;
    auto check = [](bool value) { if (!value) throw std::runtime_error("precise battle anchor failed"); };
    check(BattleClockAnchor::fromPreciseUptime(10000000, 10000000, 1000).nativeMs == 1000);
    check(BattleClockAnchor::fromPreciseUptime(10000000, 10000001, 1000).nativeMs == 1001);
    check(BattleClockAnchor::fromPreciseUptime(UINT64_MAX, UINT64_MAX, 1000).nativeMs
          == UINT64_MAX / 10000 + 1);
    for (const auto& clocks : {std::array<uint64_t, 2>{0, 1}, {100, 99}, {1, 500002}}) {
        bool rejected = false;
        try { (void)BattleClockAnchor::fromPreciseUptime(clocks[0], clocks[1], 1000); }
        catch (const std::runtime_error&) { rejected = true; }
        check(rejected);
    }
    // Sweep every fractional position within a 15.625 ms native tick.
    for (uint64_t phase = 0; phase < 156250; phase += 100) {
        const auto before = 50000000 + phase;
        const auto anchor = BattleClockAnchor::fromPreciseUptime(before, before + 123, 1000);
        for (uint64_t elapsed = 1000; elapsed < 1000000; elapsed += 10000) {
            const auto sampled = ((before + elapsed) / 156250) * 156250 / 10000;
            const auto mapped = static_cast<int64_t>(anchor.matchMs) + static_cast<int64_t>(sampled)
                              - static_cast<int64_t>(anchor.nativeMs);
            check(mapped <= static_cast<int64_t>(1000 + elapsed / 10000));
        }
    }
}
}

void contactClockTests();

int main() {
    static_assert(sizeof(uint32_t) == 4, "Rust ABI requires 32-bit version fields");
    if (t7r_runtime_abi_version() != T7R_RUNTIME_ABI_VERSION) {
        std::cerr << "Rust static library ABI version mismatch\n";
        return 1;
    }
    try { contactClockTests(); protocolCorpus(); preciseClockAnchors(); gatewayLifecycle(); dynamicGatewayIngress(); }
    catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    std::cout << "Rust C ABI lifecycle passed; native method-3 matched 32 shared vectors\n";
    return 0;
}
