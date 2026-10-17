#include "BattleSnapshotIngress.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <chrono>
#include <thread>

namespace t7r::bridge {
static_assert(MAX_WORLD_ACTORS == T7R_BATTLE_MAX_ACTORS, "battle sampling and FFI capacity must agree");
namespace {
template <typename Submit>
int32_t submitWithRetry(Submit submit, std::chrono::steady_clock::time_point deadline) {
    while (true) {
        const auto status = submit();
        if (status != T7RGW_BUSY) return status;
        if (std::chrono::steady_clock::now() >= deadline)
            throw std::runtime_error("Rust battle input backpressure exceeded 5 seconds; request not committed");
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
}
void put(std::vector<uint8_t>& bytes, uint64_t value, unsigned width) {
    for (unsigned shift = width * 8; shift != 0; shift -= 8)
        bytes.push_back(static_cast<uint8_t>(value >> (shift - 8)));
}

int64_t clockOffset(uint64_t nativeValue, uint64_t matchValue) {
    if (nativeValue >= matchValue) {
        const auto difference = nativeValue - matchValue;
        if (difference > static_cast<uint64_t>(INT64_MAX))
            throw std::runtime_error("battle clock offset exceeds int64");
        return static_cast<int64_t>(difference);
    }
    const auto difference = matchValue - nativeValue;
    if (difference > static_cast<uint64_t>(INT64_MAX) + 1)
        throw std::runtime_error("battle clock offset exceeds int64");
    return difference == static_cast<uint64_t>(INT64_MAX) + 1
        ? INT64_MIN : -static_cast<int64_t>(difference);
}

void real(std::vector<uint8_t>& bytes, double value) {
    uint64_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    put(bytes, bits, 8);
}
void point(double (&output)[3], const std::array<float, 3>& source) {
    for (size_t axis = 0; axis != 3; ++axis) output[axis] = source[axis];
}
void point(std::vector<uint8_t>& bytes, const std::array<float, 3>& source) {
    for (float coordinate : source) real(bytes, coordinate);
}
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
// Original battle animation state chains, in semantic intent/phase order.
std::pair<int, int> actionState(uint32_t state) {
    constexpr uint32_t attack[4][4] = {
        {235, 237, 238, 239}, {241, 243, 244, 245},
        {247, 249, 250, 251}, {253, 255, 256, 257}
    };
    constexpr uint32_t guard[4][2] = {{305, 223}, {308, 224}, {307, 225}, {306, 226}};
    for (uint8_t direction = 0; direction != 4; ++direction) {
        for (uint8_t phase = 0; phase != 4; ++phase)
            if (state == attack[direction][phase])
                return {static_cast<uint8_t>(direction + 1), static_cast<uint8_t>(phase == 0 ? 1 : phase == 1 ? 2 : phase == 2 ? 3 : 4)};
        if (state == guard[direction][0]) return {static_cast<uint8_t>(direction + 5), 1};
        if (state == guard[direction][1]) return {static_cast<uint8_t>(direction + 5), 2};
    }
    constexpr uint32_t parried[4] = {240, 246, 252, 258};
    constexpr uint32_t terrain[4] = {466, 468, 467, 469};
    constexpr uint32_t parrySuccess[4] = {231, 232, 233, 234};
    for (uint8_t direction = 0; direction != 4; ++direction) {
        if (state == parried[direction]) return {static_cast<uint8_t>(direction + 1), 6};
        if (state == terrain[direction]) return {static_cast<uint8_t>(direction + 1), 7};
        if (state == parrySuccess[direction]) return {static_cast<uint8_t>(direction + 5), 8};
    }
    if (state == 95) return {9, 1};
    if (state == 309) return {9, 2};
    if (state == 111) return {9, 8};
    if (state == 438) return {10, 1};
    if (state == 439) return {10, 3};
    if (state == 443) return {10, 6};
    if (state == 1) return {0, 5};
    if (state == 35 || state == 40 || state == 41 || state == 598) return {0, 9};
    return {0, 0};
}
}

int64_t clientTimeOffset(uint64_t nativeAnchorMs, uint64_t nativeInstanceStartedAt,
                         uint64_t matchAnchorMs) {
    if (!nativeInstanceStartedAt || nativeAnchorMs < nativeInstanceStartedAt)
        throw std::runtime_error("native instance clock is not ready");
    return clockOffset(nativeAnchorMs - nativeInstanceStartedAt, matchAnchorMs);
}

int64_t serverTimeOffset(uint64_t nativeAnchorMs, uint64_t matchAnchorMs) {
    if (!nativeAnchorMs) throw std::runtime_error("native server clock is not ready");
    return clockOffset(nativeAnchorMs, matchAnchorMs);
}

BattleSnapshotIngress::BattleSnapshotIngress(const T7RBattleConfig& config, uint64_t nativeInstanceStartedAt,
                                             uint64_t nativeAnchorMs, uint64_t matchAnchorMs,
                                             BattleSnapshotSink& sink)
    : epoch_(config.epoch), sourceId_(config.sourceId), startedAtMs_(config.startedAtMs),
      nativeAnchorMs_(nativeAnchorMs),
      matchAnchorMs_(matchAnchorMs), nativeInstance_(nativeInstanceStartedAt), sink_(sink) {
    require(config.abiVersion == T7R_BATTLE_CONFIG_ABI_VERSION && config.structSize == sizeof(config) && config.epoch != 0
            && config.sourceId != 0 && config.memberCount >= 1 && config.memberCount <= MAX_WORLD_ACTORS && nativeAnchorMs != 0
            && nativeInstanceStartedAt != 0,
            "invalid native battle ingress binding");
    for (uint32_t index = 0; index != config.memberCount; ++index) {
        const auto& member = config.members[index];
        require(member.actorId >= 1 && member.actorId <= MAX_WORLD_ACTORS
                && member.clientRid >= 1 && member.clientRid <= MAX_WORLD_ACTORS
                && (member.kind == 1 || member.kind == 2)
                && !rids_[member.actorId] && !actorsByRid_[member.clientRid],
                "invalid or duplicate native battle RID binding");
        rids_[member.actorId] = member.clientRid;
        actorsByRid_[member.clientRid] = member.actorId;
        kinds_[member.actorId] = member.kind;
    }
    require(rids_[1] != 0 && kinds_[1] == 1, "native battle requires the local player");
}

uint64_t BattleSnapshotIngress::clock(uint64_t nativeAt) const {
    if (nativeAt == 0) return 0;
    if (nativeAt < nativeAnchorMs_) {
        const auto before = nativeAnchorMs_ - nativeAt;
        require(before <= matchAnchorMs_, "native event precedes match clock");
        return matchAnchorMs_ - before;
    }
    const auto after = nativeAt - nativeAnchorMs_;
    require(after <= UINT64_MAX - matchAnchorMs_, "native event exceeds match clock");
    return matchAnchorMs_ + after;
}

double BattleSnapshotIngress::clock(double nativeAt) const {
    require(std::isfinite(nativeAt) && nativeAt >= 0., "invalid native fractional event time");
    const auto mapped = static_cast<double>(matchAnchorMs_) + nativeAt - static_cast<double>(nativeAnchorMs_);
    require(std::isfinite(mapped) && mapped >= 0. && mapped < std::ldexp(1., 64),
            "native fractional event outside match clock");
    return mapped;
}

double BattleSnapshotIngress::targetPhaseClock(uint64_t nativeAt) const {
    if (nativeAt == 0) return 0.;
    // A target can retain a preparation-stage action after the match clock starts.
    const auto mapped = nativeAt < nativeAnchorMs_
        ? static_cast<double>(matchAnchorMs_) - static_cast<double>(nativeAnchorMs_ - nativeAt)
        : static_cast<double>(matchAnchorMs_) + static_cast<double>(nativeAt - nativeAnchorMs_);
    require(std::isfinite(mapped) && mapped < std::ldexp(1., 64), "native target phase outside match clock");
    return mapped;
}

uint32_t BattleSnapshotIngress::actorId(uint32_t clientRid) const {
    require(clientRid <= MAX_WORLD_ACTORS && actorsByRid_[clientRid] != 0, "unbound native client RID");
    return actorsByRid_[clientRid];
}

std::vector<uint8_t> BattleSnapshotIngress::worldPayload(const WorldSnapshot& snapshot, uint64_t batchId) const {
    std::vector<uint8_t> bytes;
    bytes.reserve(40 + MAX_WORLD_ACTORS * 56);
    put(bytes, 1, 2); put(bytes, 0, 2);
    put(bytes, epoch_, 8); put(bytes, sourceId_, 8);
    put(bytes, batchId, 8); put(bytes, clock(snapshot.sampledAt), 8);
    uint16_t count = 0;
    for (size_t i = 0; i < snapshot.actors.size(); ++i)
        if (actorsByRid_[i + 1] && snapshot.actors[i].ready) ++count;
    put(bytes, count, 2); put(bytes, 0, 2);
    for (uint32_t index = 0; index != MAX_WORLD_ACTORS; ++index) {
        const auto& actor = snapshot.actors[index];
        if (!actor.ready || !actorsByRid_[index + 1]) continue;
        const auto id = actorId(index + 1);
        require(actor.entity != 0 && actor.sequence <= UINT16_MAX && std::isfinite(actor.heading),
                "invalid native actor pose");
        put(bytes, id, 4); put(bytes, index + 1, 2); put(bytes, actor.sequence, 2);
        put(bytes, actor.entity, 8); point(bytes, actor.position); real(bytes, actor.heading);
        uint32_t targetId = actor.targetId;
        uint8_t contactState = 0;
        if (targetId != 0 && targetId <= MAX_WORLD_ACTORS && !actorsByRid_[targetId]) targetId = 0;
        if (targetId != 0) {
            require(targetId <= MAX_WORLD_ACTORS && targetId != index + 1, "invalid native contact target");
            const auto& probe = actor.targetTerrain[targetId - 1];
            if (probe.ready) contactState = probe.hit ? 1 : 2;
            targetId = actorId(targetId);
        }
        put(bytes, targetId, 4); put(bytes, contactState, 1); put(bytes, 0, 3);
    }
    return bytes;
}

BattleSnapshotIngress::ObservedContact BattleSnapshotIngress::contact(const BladeContactEvent& event) const {
    const auto timeContext = [&](const char* field, double nativeAt) {
        return std::string("; field=") + field + "; nativeAtMs=" + std::to_string(nativeAt)
            + "; mappedAtMs=" + std::to_string(static_cast<double>(matchAnchorMs_) + nativeAt - static_cast<double>(nativeAnchorMs_))
            + "; nativeAnchorMs=" + std::to_string(nativeAnchorMs_) + "; matchAnchorMs=" + std::to_string(matchAnchorMs_)
            + "; epoch=" + std::to_string(epoch_) + "; nativeInstance=" + std::to_string(nativeInstance_)
            + "; actor=" + std::to_string(event.actorId) + "; target=" + std::to_string(event.hit.targetId);
    };
    const auto mapTime = [&](const char* field, auto nativeAt, auto convert) {
        try { return convert(); }
        catch (const std::exception& error) {
            throw std::runtime_error(error.what() + timeContext(field, static_cast<double>(nativeAt)));
        }
    };
    T7RBattleContact value{};
    require(event.sequence > 0 && event.sequence <= UINT16_MAX && event.instanceStartedAt != 0
            && event.hit.targetId <= MAX_WORLD_ACTORS && event.actorId != event.hit.targetId
            && event.hit.region <= 3 && event.hit.bodyIndex >= -1 && event.hit.bodyIndex <= 127
            && event.hit.contact.material >= -1 && event.hit.contact.material <= 7,
            "invalid native contact evidence");
    value.actorId = actorId(event.actorId); value.clientRid = static_cast<uint16_t>(event.actorId);
    value.targetId = event.hit.targetId ? actorId(event.hit.targetId) : 0;
    value.targetClientRid = static_cast<uint16_t>(event.hit.targetId);
    value.sourceEntity = event.entity; value.targetEntity = event.targetEntity;
    value.sequence = static_cast<uint16_t>(event.sequence);
    const auto [intent, phase] = actionState(event.targetState);
    value.targetIntent = static_cast<uint8_t>(intent); value.targetPhase = static_cast<uint8_t>(phase);
    value.phaseAtMs = mapTime("phaseAt", event.phaseAt, [&] { return clock(event.phaseAt); });
    value.targetPhaseAtMs = mapTime("targetPhaseAt", event.targetPhaseAt, [&] { return targetPhaseClock(event.targetPhaseAt); });
    value.occurredAtMs = mapTime("occurredAt", event.occurredAt, [&] { return clock(event.occurredAt); });
    value.along = event.hit.along; value.defenceWeaponId = event.hit.defenceWeaponId;
    value.region = static_cast<uint8_t>(event.hit.region);
    value.bodyIndex = static_cast<int8_t>(event.hit.bodyIndex);
    value.material = static_cast<int8_t>(event.hit.contact.material);
    value.flags = static_cast<uint8_t>((event.velocityReady ? 1 : 0) | (event.airborne ? 2 : 0)
                                      | (event.targetAirborne ? 4 : 0));
    point(value.sourcePosition, event.sourcePosition); point(value.targetPosition, event.targetPosition);
    point(value.sourceVelocity, event.sourceVelocity); point(value.targetVelocity, event.targetVelocity);
    point(value.weaponVelocity, event.weaponVelocity); point(value.position, event.hit.contact.position);
    point(value.normal, event.hit.contact.normal); point(value.direction, event.hit.contact.direction);
    value.sourceHeading = event.sourceHeading; value.targetHeading = event.targetHeading;
    const auto targetObserved = mapTime("targetObservedAt", event.targetObservedAt, [&] {
        require(event.targetObservedAt != 0, "native contact lacks target observation time");
        return clock(event.targetObservedAt);
    });
    const auto sourceObserved = mapTime("sourceObservedAt", event.sourceObservedAt, [&] {
        require(event.sourceObservedAt != 0, "native contact lacks source observation time");
        return clock(event.sourceObservedAt);
    });
    if (value.occurredAtMs > static_cast<double>(targetObserved) || value.occurredAtMs > static_cast<double>(sourceObserved))
        throw std::runtime_error("native contact exceeds observation time" + timeContext("occurredAt", event.occurredAt));
    if (value.targetPhaseAtMs > static_cast<double>(targetObserved))
        throw std::runtime_error("native target phase exceeds observation time" + timeContext("targetPhaseAt", static_cast<double>(event.targetPhaseAt)));
    if (value.phaseAtMs > sourceObserved)
        throw std::runtime_error("native source phase exceeds observation time" + timeContext("phaseAt", static_cast<double>(event.phaseAt)));
    return {value, targetObserved, sourceObserved};
}

void BattleSnapshotIngress::submit(const WorldSnapshot& snapshot) {
    require(!failed_, "native battle ingress requires a new match after submit failure");
    if (snapshot.droppedContacts) {
        failed_ = true;
        throw std::runtime_error("native contact journal dropped evidence; battle incomplete");
    }
    if (!snapshot.ready && worldBatchId_ == 0) return;
    const bool newContacts = snapshot.contactsReady && snapshot.contactBatchId > contactBatchId_;
    if (newContacts) {
        require(snapshot.contacts.size() <= 8192
                && (contactBatchId_ == 0 || snapshot.contactBatchId == contactBatchId_ + 1),
                "native contact journal has a gap or dropped evidence");
    } else {
        require(snapshot.contacts.empty(),
                "native contact evidence lacks a fresh batch");
        require(!snapshot.contactsReady || snapshot.contactBatchId >= contactBatchId_,
                "native contact journal moved backwards");
    }
    if (!snapshot.ready && !newContacts) return;
    require(snapshot.actors.size() == MAX_WORLD_ACTORS, "invalid native actor sample capacity");
    require(snapshot.instanceStartedAt != 0 && snapshot.sampledAt != 0
            && clock(snapshot.sampledAt) >= startedAtMs_
            && worldBatchId_ != UINT64_MAX && motionBatchId_ <= UINT64_MAX - MAX_WORLD_ACTORS,
            "invalid native battle sample clock or batch");
    require(nativeInstance_ == snapshot.instanceStartedAt,
            "native instance changed without a new match binding");
    require(lastSampledAt_ == 0 || snapshot.sampledAt >= lastSampledAt_,
            "native contact watermark moved backwards");
    const uint64_t nextWorldBatch = worldBatchId_ + 1;
    const auto payload = snapshot.ready ? worldPayload(snapshot, nextWorldBatch) : std::vector<uint8_t>{};
    std::vector<ObservedContact> events;
    if (newContacts) {
        events.reserve(snapshot.contacts.size());
        for (const auto& evidence : snapshot.contacts) {
            require(evidence.instanceStartedAt == snapshot.instanceStartedAt,
                    "contact evidence belongs to another native instance");
            require(evidence.actorId >= 1 && evidence.actorId <= MAX_WORLD_ACTORS
                    && evidence.hit.targetId <= MAX_WORLD_ACTORS, "invalid native contact RID");
            if (!actorsByRid_[evidence.actorId]
                || (evidence.hit.targetId && !actorsByRid_[evidence.hit.targetId])) continue;
            events.push_back(contact(evidence));
        }
    }
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    try {
        // Expired poses cannot drive AI, but their frozen contact journal remains evidence.
        if (snapshot.ready) {
            const auto worldStatus = submitWithRetry([&] { return sink_.submitWorld(payload.data(), static_cast<uint32_t>(payload.size())); }, deadline);
            if (worldStatus != T7RGW_OK)
                throw std::runtime_error("Rust battle rejected native world sample: status="
                    + std::to_string(worldStatus) + "; batch=" + std::to_string(nextWorldBatch)
                    + "; nativeAtMs=" + std::to_string(snapshot.sampledAt)
                    + "; matchAtMs=" + std::to_string(clock(snapshot.sampledAt)) + "; " + sink_.lastError());
            worldBatchId_ = nextWorldBatch;
            for (uint32_t index = 0; index != MAX_WORLD_ACTORS; ++index) {
                const auto& actor = snapshot.actors[index];
                if (!actor.ready || !actorsByRid_[index + 1]) continue;
                const auto id = actorId(index + 1);
                if (kinds_[id] != 2) continue;
                T7RBattleMotion motion{};
                motion.abiVersion = 1; motion.structSize = sizeof(motion);
                motion.epoch = epoch_; motion.sourceId = sourceId_;
                motion.batchId = ++motionBatchId_; motion.worldBatchId = nextWorldBatch;
                motion.sampledAtMs = clock(snapshot.sampledAt);
                motion.actorId = id; motion.clientRid = static_cast<uint16_t>(index + 1);
                motion.entity = actor.entity; point(motion.position, actor.position);
                motion.heading = actor.heading;
                if (actor.forwardAttemptAt && actor.forwardAttemptAt <= snapshot.sampledAt
                    && snapshot.sampledAt - actor.forwardAttemptAt <= 250)
                    motion.attemptedForwardAtMs = clock(actor.forwardAttemptAt);
                const auto motionStatus = submitWithRetry([&] { return sink_.submitMotion(motion); }, deadline);
                if (motionStatus != T7RGW_OK)
                    throw std::runtime_error("Rust battle rejected native bot motion: status="
                        + std::to_string(motionStatus) + "; actor=" + std::to_string(id)
                        + "; batch=" + std::to_string(motion.batchId)
                        + "; worldBatch=" + std::to_string(nextWorldBatch)
                        + "; matchAtMs=" + std::to_string(motion.sampledAtMs) + "; " + sink_.lastError());
            }
        }
        if (newContacts) {
            require(pendingContacts_.size() <= 8192 - events.size(),
                    "native contact evidence exceeds pending capacity");
            pendingContacts_.insert(pendingContacts_.end(), events.begin(), events.end());
            contactBatchId_ = snapshot.contactBatchId;
            // This batch can contain several native frames. Future batches may
            // still report sweeps starting 100 ms before the previous frame tick.
            if (lastSampledAt_) {
                const auto previousAt = clock(lastSampledAt_);
                if (previousAt >= startedAtMs_ && previousAt - startedAtMs_ >= 100
                    && previousAt - 100 > emittedContactThrough_)
                    publishContacts(previousAt - 100, deadline);
            }
        }
        lastSampledAt_ = snapshot.sampledAt;
    } catch (...) {
        failed_ = true;
        throw;
    }
}

void BattleSnapshotIngress::publishContacts(uint64_t throughAtMs, std::chrono::steady_clock::time_point deadline) {
    require(semanticContactBatchId_ != UINT64_MAX && throughAtMs > emittedContactThrough_,
            "invalid native contact watermark publication");
    std::vector<ObservedContact> ready, later;
    ready.reserve(pendingContacts_.size());
    later.reserve(pendingContacts_.size());
    for (const auto& event : pendingContacts_)
        (event.evidence.occurredAtMs <= static_cast<double>(throughAtMs) ? ready : later).push_back(event);
    const auto batchId = semanticContactBatchId_ + 1;
    std::vector<uint8_t> payload;
    payload.reserve(52 + ready.size() * 296);
    put(payload, 4, 2); put(payload, 0, 2);
    put(payload, epoch_, 8); put(payload, sourceId_, 8);
    put(payload, batchId, 8); put(payload, throughAtMs, 8);
    put(payload, worldBatchId_, 8); put(payload, 0, 4);
    put(payload, ready.size(), 2); put(payload, 0, 2);
    for (const auto& contact : ready) {
        const auto& e = contact.evidence;
        put(payload, e.actorId, 4); put(payload, e.targetId, 4);
        put(payload, e.clientRid, 2); put(payload, e.targetClientRid, 2);
        put(payload, e.sourceEntity, 8); put(payload, e.targetEntity, 8);
        put(payload, e.sequence, 2); put(payload, e.targetIntent, 1); put(payload, e.targetPhase, 1);
        put(payload, e.phaseAtMs, 8); real(payload, e.targetPhaseAtMs);
        real(payload, e.occurredAtMs); real(payload, e.along); put(payload, e.defenceWeaponId, 4);
        put(payload, e.region, 1); put(payload, static_cast<uint8_t>(e.bodyIndex), 1);
        put(payload, static_cast<uint8_t>(e.material), 1); put(payload, e.flags, 1);
        for (const auto* point : {e.sourcePosition, e.targetPosition, e.sourceVelocity,
                                 e.targetVelocity, e.weaponVelocity, e.position, e.normal, e.direction})
            for (size_t axis = 0; axis != 3; ++axis) real(payload, point[axis]);
        real(payload, e.sourceHeading); real(payload, e.targetHeading);
        put(payload, contact.targetObservedAtMs, 8);
        put(payload, contact.sourceObservedAtMs, 8);
    }
    const auto contactStatus = submitWithRetry([&] { return sink_.submitContacts(payload.data(), static_cast<uint32_t>(payload.size())); }, deadline);
    if (contactStatus != T7RGW_OK)
        throw std::runtime_error("Rust battle rejected native contact watermark: status="
            + std::to_string(contactStatus) + "; batch=" + std::to_string(batchId)
            + "; throughAtMs=" + std::to_string(throughAtMs) + "; " + sink_.lastError());
    pendingContacts_.swap(later);
    semanticContactBatchId_ = batchId;
    emittedContactThrough_ = throughAtMs;
}

void BattleSnapshotIngress::flush() {
    require(!failed_, "native battle ingress requires a new match after submit failure");
    if (!contactBatchId_) return;
    try {
        const auto through = clock(lastSampledAt_);
        if (through > emittedContactThrough_)
            publishContacts(through, std::chrono::steady_clock::now() + std::chrono::seconds(5));
        require(pendingContacts_.empty(), "native contact evidence remains after final watermark");
    } catch (...) {
        failed_ = true;
        throw;
    }
}
}
