#include "../../src/Runtime/bridge/BattleSnapshotIngress.h"
#include <cmath>
#include <cstring>
#include <limits>
#include <stdexcept>

namespace {
using namespace t7r;
using namespace t7r::bridge;

void check(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}

uint64_t integer(const std::vector<uint8_t>& bytes, size_t at) {
    uint64_t value = 0;
    for (size_t i = 0; i < 8; ++i) value = (value << 8) | bytes.at(at + i);
    return value;
}

double real(const std::vector<uint8_t>& bytes, size_t at) {
    const auto bits = integer(bytes, at);
    double value;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

struct Sink final : BattleSnapshotSink {
    std::vector<uint8_t> contacts;
    std::vector<std::vector<uint8_t>> contactBatches;
    unsigned worlds = 0, motions = 0;
    int32_t submitWorld(const uint8_t*, uint32_t) override { ++worlds; return T7RGW_OK; }
    int32_t submitMotion(const T7RBattleMotion&) override { ++motions; return T7RGW_OK; }
    int32_t submitContacts(const uint8_t* data, uint32_t size) override {
        contacts.assign(data, data + size);
        contactBatches.push_back(contacts);
        return T7RGW_OK;
    }
};

T7RBattleConfig config() {
    T7RBattleConfig value{};
    value.abiVersion = T7R_BATTLE_CONFIG_ABI_VERSION;
    value.structSize = sizeof(value);
    value.epoch = 7; value.sourceId = 1; value.startedAtMs = 1000; value.memberCount = 2;
    for (uint32_t i = 0; i < 2; ++i) {
        value.members[i].actorId = i + 1;
        value.members[i].clientRid = static_cast<uint16_t>(i + 1);
        value.members[i].kind = 1;
    }
    return value;
}

WorldSnapshot sample(uint64_t shift = 0) {
    WorldSnapshot value{};
    value.ready = value.contactsReady = true;
    value.sampledAt = 65000 + shift; value.instanceStartedAt = 2000 + shift;
    value.contactBatchId = 1;
    for (uint32_t i = 0; i < 2; ++i) {
        value.actors[i].ready = true;
        value.actors[i].entity = 101 + i;
        value.actors[i].sequence = 1;
    }
    BladeContactEvent event{};
    event.actorId = 1; event.entity = 101; event.targetEntity = 102; event.sequence = 1;
    event.instanceStartedAt = value.instanceStartedAt;
    event.phaseAt = 64000 + shift; event.targetPhaseAt = 4001 + shift;
    event.occurredAt = 64010.25 + static_cast<double>(shift);
    event.sourceObservedAt = event.targetObservedAt = 65000 + shift;
    event.targetState = 1; event.hit.targetId = 2; event.hit.region = 2;
    event.hit.bodyIndex = 0; event.hit.contact.material = 0;
    value.contacts.push_back(event);
    return value;
}

void rejected(WorldSnapshot value, const char* field) {
    Sink sink;
    BattleSnapshotIngress ingress(config(), 2000, 10000, 1000, sink);
    std::string message;
    try { ingress.submit(value); }
    catch (const std::runtime_error& error) { message = error.what(); }
    check(!message.empty(), "invalid contact clock accepted");
    check(message.find(std::string("field=") + field) != std::string::npos, "contact clock field missing");
    for (const auto* part : {"epoch=7", "actor=1", "target=2", "nativeInstance=2000",
                              "nativeAnchorMs=10000", "matchAnchorMs=1000", "nativeAtMs=", "mappedAtMs="})
        check(message.find(part) != std::string::npos, "contact clock context missing");
    check(sink.worlds == 0 && sink.contacts.empty(), "invalid contact partially published");
}

void contactContinuityTests() {
    auto match = config();
    match.members[1].kind = 2;
    for (bool withContact : {false, true}) {
        for (bool resumeWorld : {false, true}) {
            Sink sink;
            BattleSnapshotIngress ingress(match, 2000, 10000, 1000, sink);
            auto first = sample(); first.contacts.clear();
            ingress.submit(first);

            auto expired = sample();
            expired.ready = false; expired.contactBatchId = 2; expired.sampledAt = 65020;
            expired.contacts[0].occurredAt = 65010.25;
            expired.contacts[0].sourceObservedAt = expired.contacts[0].targetObservedAt = 65300;
            if (!withContact) expired.contacts.clear();
            ingress.submit(expired);
            check(sink.worlds == 1 && sink.motions == 1, "expired pose drove world or AI motion");
            check(sink.contactBatches.size() == 1 && integer(sink.contacts, 36) == 1,
                  "expired contact batch did not retain the last accepted world binding");

            auto waiting = expired;
            waiting.contacts.clear(); waiting.sampledAt = 65100;
            ingress.submit(waiting);
            check(sink.contactBatches.size() == 1, "unchanged unready batch fabricated a watermark");

            auto next = waiting; next.contactBatchId = 3;
            ingress.submit(next);
            if (resumeWorld) {
                auto resumed = next; resumed.ready = true;
                resumed.contactBatchId = 4; resumed.sampledAt = 65400;
                ingress.submit(resumed);
            }
            ingress.flush();
            const auto worlds = resumeWorld ? 2u : 1u;
            check(sink.worlds == worlds && sink.motions == worlds, "expired samples changed world batch order");
            size_t contacts = 0;
            for (const auto& batch : sink.contactBatches) contacts += (batch.size() - 52) / 296;
            check(contacts == (withContact ? 1u : 0u), "expired contact evidence was lost or replayed");
            check(integer(sink.contacts, 36) == worlds, "final contact watermark lost its world binding");
            check(integer(sink.contacts, 28) == (resumeWorld ? 56400u : 56100u), "contact flush lost the sampled clock");
            if (withContact)
                check(real(sink.contacts, 100) == 56010.25, "expired contact timestamp was rewritten");
        }
    }
    for (bool ready : {false, true}) {
        for (unsigned failure = 0; failure != 4; ++failure) {
            Sink sink;
            BattleSnapshotIngress ingress(match, 2000, 10000, 1000, sink);
            auto first = sample(); first.contactBatchId = 2; first.contacts.clear();
            ingress.submit(first);
            auto invalid = first; invalid.ready = ready;
            invalid.contactBatchId = 3; invalid.sampledAt = 65020;
            const char* message = nullptr;
            switch (failure) {
            case 0: invalid.contactBatchId = 4; message = "native contact journal has a gap or dropped evidence"; break;
            case 1: invalid.contactBatchId = 1; message = "native contact journal moved backwards"; break;
            case 2: invalid.droppedContacts = 1; message = "native contact journal dropped evidence; battle incomplete"; break;
            case 3: invalid.instanceStartedAt = 2001; message = "native instance changed without a new match binding"; break;
            }
            std::string error;
            try { ingress.submit(invalid); }
            catch (const std::runtime_error& rejected) { error = rejected.what(); }
            check(error == message, "invalid contact continuity was accepted or misdiagnosed");
            check(sink.worlds == 1 && sink.contactBatches.empty(), "invalid journal was partially published");
        }
    }
}
}

void contactClockTests() {
    contactContinuityTests();
    for (uint64_t shift : {0ull, 70000ull}) {
        auto match = config(); match.epoch += shift;
        Sink sink;
        BattleSnapshotIngress ingress(match, 2000 + shift, 10000 + shift, 1000, sink);
        ingress.submit(sample(shift)); ingress.flush();
        check(sink.contacts.size() == 52 + 296 && sink.contacts[1] == 4, "contact v4 layout");
        check(integer(sink.contacts, 4) == match.epoch, "rematch epoch not retained");
        check(integer(sink.contacts, 84) == 55000, "source phase changed");
        check(real(sink.contacts, 92) == -4999., "prestart target phase not preserved");
        check(real(sink.contacts, 100) == 55010.25, "fractional contact time changed");
        check(integer(sink.contacts, 332) == 56000 && integer(sink.contacts, 340) == 56000,
              "observation clocks changed");
    }
    auto value = sample(); value.contacts[0].targetPhaseAt = 0;
    Sink emptyPhase;
    BattleSnapshotIngress ingress(config(), 2000, 10000, 1000, emptyPhase);
    ingress.submit(value); ingress.flush();
    check(real(emptyPhase.contacts, 92) == 0., "absent target phase sentinel changed");
    for (const double at : {8999.5, std::numeric_limits<double>::quiet_NaN(),
                            std::numeric_limits<double>::infinity(), 65000.5}) {
        value = sample(); value.contacts[0].occurredAt = at;
        rejected(value, "occurredAt");
    }
    value = sample(); value.contacts[0].phaseAt = 8000; rejected(value, "phaseAt");
    value = sample(); value.contacts[0].phaseAt = 65001; rejected(value, "phaseAt");
    value = sample(); value.contacts[0].targetPhaseAt = 65001; rejected(value, "targetPhaseAt");
    value = sample(); value.contacts[0].targetPhaseAt = UINT64_MAX; rejected(value, "targetPhaseAt");
    value = sample(); value.contacts[0].targetObservedAt = 0; rejected(value, "targetObservedAt");
    value = sample(); value.contacts[0].sourceObservedAt = 0; rejected(value, "sourceObservedAt");
}
