#pragma once
#include "BattleSnapshotIngress.h"
#include <filesystem>
#include <optional>
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

namespace t7r::bridge {

struct BattleClockAnchor {
    uint64_t nativeMs;
    uint64_t matchMs;

    static BattleClockAnchor fromPreciseUptime(uint64_t before100ns, uint64_t after100ns,
                                             uint64_t matchMs);
};

// Holds the same Rust DLL loaded by the managed gateway owner, not a second
// statically linked Rust registry. The caller keeps the gateway handle alive.
class BattleGatewaySink final : public BattleSnapshotSink {
public:
    BattleGatewaySink(const std::filesystem::path& libraryPath, uint64_t gatewayHandle);
    ~BattleGatewaySink() override;
    BattleGatewaySink(const BattleGatewaySink&) = delete;
    BattleGatewaySink& operator=(const BattleGatewaySink&) = delete;

    int32_t submitWorld(const uint8_t* data, uint32_t size) override;
    int32_t submitMotion(const T7RBattleMotion& motion) override;
    int32_t submitContacts(const uint8_t* data, uint32_t size) override;
    std::string lastError() const override;
    uint64_t clockMs() const;
    std::optional<uint64_t> tryClockMs() const;
    std::optional<BattleClockAnchor> tryClockAnchor() const;
    void bindClientClock(uint64_t epoch, int64_t offsetMs);
    void bindServerClock(uint64_t epoch, int64_t offsetMs);

private:
    HMODULE library_ = nullptr;
    uint64_t handle_ = 0;
    decltype(&t7r_gateway_battle_submit) submit_ = nullptr;
    decltype(&t7r_gateway_battle_submit_motion) motion_ = nullptr;
    decltype(&t7r_gateway_error) error_ = nullptr;
    decltype(&t7r_gateway_battle_clock) clock_ = nullptr;
    decltype(&t7r_gateway_battle_bind_client_clock) bindClock_ = nullptr;
    decltype(&t7r_gateway_battle_bind_server_clock) bindServerClock_ = nullptr;
};
}
