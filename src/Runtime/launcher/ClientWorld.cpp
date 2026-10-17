#include "ClientWorld.h"
#include "RemoteImage.h"
#include "RoundEndHookCode.h"
#include <cmath>
#include <cstring>
#include <realtimeapiset.h>

#pragma comment(lib, "mincore.lib")

namespace t7r {
namespace {
bool validPosition(const std::array<float, 3>& position) {
    for (auto coordinate : position)
        if (!std::isfinite(coordinate) || std::abs(coordinate) >= 100000) return false;
    return true;
}
float degrees(float yaw) {
    return std::remainder(yaw, 6.283185307179586f) * 57.29577951308232f;
}
uint64_t sampleTime(uint32_t tick, uint64_t now) {
    const auto age = static_cast<uint32_t>(now) - tick; // Native GetTickCount wraps every 49.7 days.
    return age <= now && age < 0x80000000u ? now - age : 0;
}
TerrainContact terrainContact(const TerrainMailbox& raw) {
    TerrainContact value;
    if ((raw.status != 1 && raw.status != 2) || !validPosition(raw.from) || !validPosition(raw.to)) return value;
    if (raw.status == 1) { value.ready = true; return value; }
    if (!validPosition(raw.normal) || !std::isfinite(raw.fraction) || raw.fraction < 0 || raw.fraction > 1
        || raw.material < -1 || raw.material > 7) return value;
    std::array<float, 3> delta{};
    for (size_t i = 0; i < 3; ++i) delta[i] = raw.to[i] - raw.from[i];
    const auto length = std::hypot(delta[0], delta[1], delta[2]);
    const auto normalLength = std::hypot(raw.normal[0], raw.normal[1], raw.normal[2]);
    if (length < .0001f || normalLength < .9f || normalLength > 1.1f) return value;
    value.ready = value.hit = true; value.material = raw.material;
    for (size_t i = 0; i < 3; ++i) {
        value.position[i] = raw.from[i] + delta[i] * raw.fraction;
        value.normal[i] = raw.normal[i] / normalLength;
        value.direction[i] = delta[i] / length;
    }
    return value;
}
BladeTrace bladeTrace(const BladeMailbox& raw) {
    BladeTrace value;
    if (raw.poseReady != 1 || raw.traceReady != 1 || raw.spanMs > 100
        || !validPosition(raw.base) || !validPosition(raw.tip)) return value;
    const auto length = std::hypot(raw.tip[0] - raw.base[0], raw.tip[1] - raw.base[1], raw.tip[2] - raw.base[2]);
    if (length < .01f || length > 8.f) return value;
    value.ready = true; value.base = raw.base; value.tip = raw.tip;
    value.length = length; value.spanMs = raw.spanMs;
    if (raw.sweepSteps <= 201 && validPosition(raw.sweepBase) && validPosition(raw.sweepTip)) {
        value.sweepSteps = raw.sweepSteps; value.sweepBase = raw.sweepBase; value.sweepTip = raw.sweepTip;
    }
    for (size_t i = 0; i < raw.contacts.size(); ++i) {
        const auto& source = raw.contacts[i];
        if (source.contact.status == 0) continue;
        auto& hit = value.contacts[i];
        hit.contact = terrainContact(source.contact);
        if (!hit.contact.ready || !hit.contact.hit || !std::isfinite(source.time) || !std::isfinite(source.along)
            || source.time < 0 || source.time > 1 || source.along < 0 || source.along > 1) return {};
        hit.targetId = static_cast<uint32_t>(i == GROUND_CONTACT_SLOT ? 0 :
            i > GROUND_CONTACT_SLOT ? i - DEFENCE_CONTACT_OFFSET : i);
        hit.time = source.time; hit.along = source.along;
        hit.bodyIndex = source.bodyIndex; hit.region = source.region;
        hit.defenceWeaponId = source.defenceWeaponId; hit.guardVolume = source.guardVolume != 0;
    }
    return value;
}
}
void ClientWorld::attach(HANDLE process, uint32_t address) {
    if (process_ || !process || !address) throw std::runtime_error("world mailbox attachment state");
    process_ = process; remote_ = address;
}
void ClientWorld::clear() {
    if (process_ && WaitForSingleObject(process_, 0) != WAIT_OBJECT_0)
        throw std::runtime_error("world bridge cleanup requires terminated owned client");
    process_ = nullptr; remote_ = 0; requestedAt_ = 0; pending_ = false; targets_ = {}; targetMasks_ = {};
}
void ClientWorld::request() {
    const uint32_t command = 1;
    SIZE_T actual = 0;
    for (size_t i = 0; i < targets_.size(); ++i) {
        const auto slot = remote_ + ACTORS_DATA_OFFSET + i * sizeof(ActorMailbox);
        if (!WriteProcessMemory(process_, reinterpret_cast<void*>(static_cast<uintptr_t>(slot + offsetof(ActorMailbox, targetId))),
                                &targets_[i], sizeof(uint32_t), &actual) || actual != sizeof(uint32_t)
            || !WriteProcessMemory(process_, reinterpret_cast<void*>(static_cast<uintptr_t>(slot + offsetof(ActorMailbox, targetMask))),
                                   &targetMasks_[i], sizeof(uint64_t), &actual) || actual != sizeof(uint64_t))
            throw std::runtime_error(errorText("world target publication"));
    }
    requestedAt_ = GetTickCount64();
    if (!WriteProcessMemory(process_, reinterpret_cast<void*>(static_cast<uintptr_t>(remote_)),
                            &command, sizeof(command), &actual) || actual != sizeof(command))
        throw std::runtime_error(errorText("world request publication"));
    pending_ = true;
}
bool ClientWorld::poll(WorldSnapshot& value, const std::array<uint8_t, MAX_WORLD_ACTORS>& camps) {
    if (!process_) return false;
    const auto roundEnd = readClientMemory(process_, remote_ + ROUND_END_DATA_OFFSET, sizeof(RoundEndMailbox));
    RoundEndMailbox handoff;
    std::memcpy(&handoff, roundEnd.data(), sizeof(handoff));
    if (handoff.lobbyUiError) throw std::runtime_error("lobby UI resource signature mismatch; supported client required");
    if (handoff.status == 2) throw std::runtime_error("round end native logout handoff failed; new client session required");
    if (!pending_) { request(); return false; }
    const auto bytes = readClientMemory(process_, remote_, sizeof(WorldMailbox));
    WorldMailbox raw;
    std::memcpy(&raw, bytes.data(), sizeof(raw));
    if (raw.command != 0) return false;
    value = {};
    const auto now = GetTickCount64();
    const auto frameBytes = readClientMemory(process_, remote_ + FRAME_DATA_OFFSET + sizeof(ContactFrameMailbox), sizeof(ContactFrameMailbox));
    ContactFrameMailbox frame;
    std::memcpy(&frame, frameBytes.data(), sizeof(frame));
    if (frame.count > MAX_FRAME_CONTACTS) throw std::runtime_error("native contact journal count exceeds capacity");
    value.sampledAt = sampleTime(frame.tick, now);
    value.instanceStartedAt = raw.instanceStartedAt;
    value.contactsReady = true; // Capability is independent of pose freshness or a temporarily missing scene.
    value.contactFrames = frame.frames; value.droppedContacts = frame.dropped;
    value.contactBatchId = frame.batchId;
    if (raw.ready == 1 && raw.instanceStartedAt != 0 && frame.count) {
        const auto events = readClientMemory(process_, remote_ + CONTACTS_DATA_OFFSET, frame.count * sizeof(ContactEventMailbox));
        ULONGLONG readCompleted = 0;
        QueryInterruptTimePrecise(&readCompleted);
        const uint64_t observedAt = readCompleted / 10000;
        for (uint32_t i = 0; i < frame.count; ++i) {
            ContactEventMailbox source;
            std::memcpy(&source, events.data() + i * sizeof(source), sizeof(source));
            if (source.epoch != raw.instanceStartedAt) continue;
            const auto contact = terrainContact(source.hit.contact);
            if (!contact.hit || source.actorId < 1 || source.actorId > MAX_WORLD_ACTORS
                || source.targetId > MAX_WORLD_ACTORS || source.actorId == source.targetId || source.spanMs > 100
                || !std::isfinite(source.hit.time) || source.hit.time < 0 || source.hit.time > 1
                || !std::isfinite(source.hit.along) || source.hit.along < 0 || source.hit.along > 1
                || !validPosition(source.sourcePosition) || !validPosition(source.targetPosition)
                || !std::isfinite(source.sourceYaw) || !std::isfinite(source.targetYaw)
                || source.hit.guardVolume > 1
                || source.hit.guardVolume && (source.targetId == 0 || source.hit.defenceWeaponId == 0 || source.hit.region != 0 || source.hit.bodyIndex != -1)
                || source.hit.region > 3 || source.hit.bodyIndex < -1 || source.hit.bodyIndex > 127
                || source.sourceVelocityReady > 1 || source.targetVelocityReady > 1 || source.airborne > 1 || source.targetAirborne > 1)
                throw std::runtime_error("invalid native contact journal entry");
            BladeContactEvent event;
            event.hit = {contact, source.targetId, source.hit.time, source.hit.along,
                         source.hit.bodyIndex, source.hit.region, source.hit.defenceWeaponId, source.hit.guardVolume != 0};
            event.actorId = source.actorId; event.entity = source.entity; event.targetEntity = source.targetEntity;
            event.state = source.state; event.sequence = source.sequence; event.instanceStartedAt = source.epoch;
            event.targetState = source.targetState; event.targetSequence = source.targetSequence;
            event.sourceObservedAt = observedAt; event.targetObservedAt = observedAt;
            event.phaseAt = sampleTime(source.phaseTick, observedAt); event.targetPhaseAt = sampleTime(source.targetPhaseTick, observedAt);
            if ((source.phaseTick != 0 && event.phaseAt == 0)
                || (source.targetPhaseTick != 0 && event.targetPhaseAt == 0))
                throw std::runtime_error("native contact phase exceeds observation clock range");
            event.occurredAt = static_cast<double>(sampleTime(source.tick, observedAt)) - source.spanMs * (1. - source.hit.time);
            event.sourcePosition = source.sourcePosition; event.targetPosition = source.targetPosition;
            event.sourceHeading = degrees(source.sourceYaw); event.targetHeading = degrees(source.targetYaw);
            event.airborne = source.airborne != 0;
            event.targetAirborne = source.targetAirborne != 0;
            event.velocityReady = source.spanMs > 0 && source.sourceVelocityReady == 1 && source.targetVelocityReady == 1;
            if (event.velocityReady) {
                if (!validPosition(source.sourceVelocity) || !validPosition(source.targetVelocity)
                    || !validPosition(source.baseVelocity) || !validPosition(source.tipVelocity))
                    throw std::runtime_error("invalid native contact velocity");
                event.sourceVelocity = source.sourceVelocity; event.targetVelocity = source.targetVelocity;
                for (size_t axis = 0; axis < 3; ++axis)
                    event.weaponVelocity[axis] = source.baseVelocity[axis]
                        + (source.tipVelocity[axis] - source.baseVelocity[axis]) * source.hit.along;
            }
            value.contacts.push_back(event);
        }
    }
    const auto actorBytes = readClientMemory(process_, remote_ + ACTORS_DATA_OFFSET,
                                             MAX_WORLD_ACTORS * sizeof(ActorMailbox));
    std::vector<ActorMailbox> entries(MAX_WORLD_ACTORS);
    std::memcpy(entries.data(), actorBytes.data(), actorBytes.size());
    value.ready = raw.ready == 1 && raw.instanceStartedAt != 0 && now - value.sampledAt <= 250;
    for (size_t i = 0; i < entries.size(); ++i) {
        const auto& source = entries[i];
        auto& actor = value.actors[i];
        const auto heading = degrees(source.yaw);
        actor.ready = value.ready && source.ready == 1 && validPosition(source.position) && std::isfinite(heading);
        actor.entity = source.blade.actorEntity; actor.state = source.blade.state; actor.sequence = source.blade.sequence;
        actor.phaseAt = sampleTime(source.blade.phaseTick, now);
        actor.forwardAttemptAt = source.forwardAttemptTick ? sampleTime(source.forwardAttemptTick, now) : 0;
        if (!actor.ready) continue;
        actor.guard = source.blade.guard; actor.guardWeaponId = source.blade.guardWeaponId;
        actor.position = source.position; actor.heading = heading; actor.targetId = source.targetId;
        actor.blade = bladeTrace(source.blade);
        for (size_t probe = 0; probe < 2; ++probe) actor.terrain[probe] = terrainContact(source.terrain[probe]);
        for (size_t target = 0; target < entries.size(); ++target)
            if (entries[target].ready == 1) actor.targetTerrain[target] = terrainContact(source.targetTerrain[target]);
    }
    // Existing single-bot rounds still consume the pair fields after a hot reload.
    value.player = value.actors[0].position; value.bot = value.actors[1].position;
    value.playerHeading = value.actors[0].heading; value.botHeading = value.actors[1].heading;
    value.terrain = {value.actors[0].terrain, value.actors[1].terrain};
    // Query every enemy in melee reach, never reuse the nearest enemy's ray for another target.
    for (size_t i = 0; i < entries.size(); ++i) {
        targets_[i] = 0;
        targetMasks_[i] = 0;
        if (!value.actors[i].ready) continue;
        float closest = 3.f;
        for (size_t j = 0; j < entries.size(); ++j) {
            if (i == j || (camps[i] != 0 && camps[i] == camps[j]) || !value.actors[j].ready) continue;
            const auto& from = value.actors[i].position;
            const auto& to = value.actors[j].position;
            const auto dx = to[0] - from[0], dy = to[1] - from[1];
            const auto distance = std::hypot(dx, dy);
            const auto yaw = entries[i].yaw;
            if (std::abs(to[2] - from[2]) > 1.f || distance > 2.2f
                || (distance > .01f && (dx * std::cos(yaw) - dy * std::sin(yaw)) / distance < .5f)) continue;
            targetMasks_[i] |= uint64_t{1} << j;
            const auto rank = std::hypot(distance, to[2] - from[2]);
            if (rank >= closest) continue;
            closest = rank; targets_[i] = static_cast<uint32_t>(j + 1);
        }
    }
    pending_ = false;
    return true;
}
}
