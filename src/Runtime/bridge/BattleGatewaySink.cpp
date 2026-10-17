#include "BattleGatewaySink.h"
#include <stdexcept>
#include <string>
#include <realtimeapiset.h>

#pragma comment(lib, "mincore.lib")

namespace t7r::bridge {
namespace {
template <typename Function>
Function required(HMODULE library, const char* name) {
    const auto address = GetProcAddress(library, name);
    if (!address) throw std::runtime_error(std::string("missing Rust battle export: ") + name);
    return reinterpret_cast<Function>(address);
}
}

BattleClockAnchor BattleClockAnchor::fromPreciseUptime(uint64_t before100ns, uint64_t after100ns,
                                                      uint64_t matchMs) {
    if (!before100ns || after100ns < before100ns || after100ns - before100ns > 500000)
        throw std::runtime_error("Rust battle clock anchor is invalid");
    // The end of the precise bracket, rounded up, never places a sampled tick
    // ahead of the match clock. A GetTickCount64 midpoint loses the tick fraction.
    return {after100ns / 10000 + (after100ns % 10000 != 0), matchMs};
}

BattleGatewaySink::BattleGatewaySink(const std::filesystem::path& libraryPath, uint64_t gatewayHandle)
    : handle_(gatewayHandle) {
    if (!libraryPath.is_absolute() || libraryPath.empty() || gatewayHandle == 0)
        throw std::runtime_error("invalid Rust battle gateway binding");
    library_ = LoadLibraryExW(libraryPath.c_str(), nullptr, 0x100 | 0x800);
    if (!library_)
        throw std::runtime_error("loading Rust battle gateway failed: " + std::to_string(GetLastError()));
    try {
        const auto version = required<decltype(&t7r_runtime_abi_version)>(library_, "t7r_runtime_abi_version");
        if (version() != T7R_RUNTIME_ABI_VERSION)
            throw std::runtime_error("Rust battle gateway ABI mismatch");
        const auto snapshot = required<decltype(&t7r_gateway_snapshot)>(library_, "t7r_gateway_snapshot");
        T7RGatewaySnapshot state{};
        state.abiVersion = T7R_RUNTIME_ABI_VERSION;
        state.structSize = sizeof(state);
        if (snapshot(handle_, &state) != T7RGW_OK || state.state != 1)
            throw std::runtime_error("Rust battle gateway handle is not running in this module");
        submit_ = required<decltype(submit_)>(library_, "t7r_gateway_battle_submit");
        motion_ = required<decltype(motion_)>(library_, "t7r_gateway_battle_submit_motion");
        error_ = required<decltype(error_)>(library_, "t7r_gateway_error");
        clock_ = required<decltype(clock_)>(library_, "t7r_gateway_battle_clock");
        bindClock_ = required<decltype(bindClock_)>(library_, "t7r_gateway_battle_bind_client_clock");
        bindServerClock_ = required<decltype(bindServerClock_)>(library_, "t7r_gateway_battle_bind_server_clock");
    } catch (...) {
        FreeLibrary(library_);
        library_ = nullptr;
        throw;
    }
}

BattleGatewaySink::~BattleGatewaySink() {
    if (library_) FreeLibrary(library_);
}

int32_t BattleGatewaySink::submitWorld(const uint8_t* data, uint32_t size) {
    return submit_(handle_, 6, data, size);
}
int32_t BattleGatewaySink::submitMotion(const T7RBattleMotion& motion) {
    return motion_(handle_, &motion);
}
int32_t BattleGatewaySink::submitContacts(const uint8_t* data, uint32_t size) {
    return submit_(handle_, 14, data, size);
}
std::string BattleGatewaySink::lastError() const {
    std::array<uint8_t, 4096> bytes{};
    uint32_t length = 0;
    const auto status = error_(handle_, bytes.data(), static_cast<uint32_t>(bytes.size()), &length);
    if (status != T7RGW_OK || length > bytes.size())
        return "gateway diagnostic read failed: status=" + std::to_string(status);
    return std::string(reinterpret_cast<const char*>(bytes.data()), length);
}
uint64_t BattleGatewaySink::clockMs() const {
    const auto value = tryClockMs();
    if (!value) throw std::runtime_error("Rust battle clock has not started");
    return *value;
}
std::optional<uint64_t> BattleGatewaySink::tryClockMs() const {
    uint64_t value = 0;
    const auto status = clock_(handle_, &value);
    if (status == T7RGW_EMPTY) return std::nullopt;
    if (status != T7RGW_OK)
        throw std::runtime_error("Rust battle clock unavailable");
    return value;
}
std::optional<BattleClockAnchor> BattleGatewaySink::tryClockAnchor() const {
    ULONGLONG before = 0, after = 0;
    QueryInterruptTimePrecise(&before);
    const auto matchNow = tryClockMs();
    QueryInterruptTimePrecise(&after);
    if (!matchNow) return std::nullopt;
    return BattleClockAnchor::fromPreciseUptime(before, after, *matchNow);
}
void BattleGatewaySink::bindClientClock(uint64_t epoch, int64_t offsetMs) {
    if (bindClock_(handle_, epoch, offsetMs) != T7RGW_OK)
        throw std::runtime_error("Rust battle client clock binding failed");
}
void BattleGatewaySink::bindServerClock(uint64_t epoch, int64_t offsetMs) {
    if (bindServerClock_(handle_, epoch, offsetMs) != T7RGW_OK)
        throw std::runtime_error("Rust battle server clock binding failed");
}
}
