#pragma once
#include "../core/WorldSnapshot.h"
#include "../../BattleRust/ffi/include/t7r_runtime.h"
#include <array>
#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

namespace t7r::bridge {

// Original-client instance elapsed time = Rust match time + this offset.
int64_t clientTimeOffset(uint64_t nativeAnchorMs, uint64_t nativeInstanceStartedAt,
                         uint64_t matchAnchorMs);
// Original-server absolute uptime = Rust match time + this offset.
int64_t serverTimeOffset(uint64_t nativeAnchorMs, uint64_t matchAnchorMs);

class BattleSnapshotSink {
public:
    virtual ~BattleSnapshotSink() = default;
    virtual int32_t submitWorld(const uint8_t* data, uint32_t size) = 0;
    virtual int32_t submitMotion(const T7RBattleMotion& motion) = 0;
    virtual int32_t submitContacts(const uint8_t* data, uint32_t size) = 0;
    virtual std::string lastError() const { return {}; }
};

// One instance is bound to one match/source. Recreate it for every rematch.
class BattleSnapshotIngress {
public:
    BattleSnapshotIngress(const T7RBattleConfig& config, uint64_t nativeInstanceStartedAt,
                          uint64_t nativeAnchorMs, uint64_t matchAnchorMs, BattleSnapshotSink& sink);
    void submit(const WorldSnapshot& snapshot);
    void flush();

private:
    uint64_t clock(uint64_t nativeAt) const;
    double clock(double nativeAt) const;
    double targetPhaseClock(uint64_t nativeAt) const;
    uint32_t actorId(uint32_t clientRid) const;
    std::vector<uint8_t> worldPayload(const WorldSnapshot& snapshot, uint64_t batchId) const;
    struct ObservedContact {
        T7RBattleContact evidence;
        uint64_t targetObservedAtMs, sourceObservedAtMs;
    };
    ObservedContact contact(const BladeContactEvent& event) const;
    void publishContacts(uint64_t throughAtMs, std::chrono::steady_clock::time_point deadline);

    uint64_t epoch_, sourceId_, startedAtMs_, nativeAnchorMs_, matchAnchorMs_;
    uint64_t worldBatchId_ = 0, motionBatchId_ = 0, contactBatchId_ = 0;
    uint64_t semanticContactBatchId_ = 0, emittedContactThrough_ = 0, lastSampledAt_ = 0;
    std::vector<ObservedContact> pendingContacts_;
    std::array<uint16_t, MAX_WORLD_ACTORS + 1> rids_{};
    std::array<uint32_t, MAX_WORLD_ACTORS + 1> actorsByRid_{};
    std::array<uint8_t, MAX_WORLD_ACTORS + 1> kinds_{};
    uint64_t nativeInstance_;
    BattleSnapshotSink& sink_;
    bool failed_ = false;
};
}
