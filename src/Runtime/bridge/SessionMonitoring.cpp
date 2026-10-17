#include "Session.h"

namespace t7r::bridge {
namespace {
// The supported client also returns 0x1234 after its exit confirmation.
constexpr DWORD CLIENT_NORMAL_EXIT_CODE = 0x1234;
}

void Session::monitorClient() {
    bool alive = true, windowClosed = false;
    try {
        alive = bootstrap_.running();
    } catch (const std::exception& error) {
        setFailure(0, T7RNB_ERROR_OPERATION, std::string("owned client status failed: ") + error.what());
        return;
    }
    if (alive) {
        try {
            windowClosed = bootstrap_.windowClosed();
            windowProbeFailureSince_.reset();
        } catch (const std::exception& error) {
            const auto now = std::chrono::steady_clock::now();
            if (!windowProbeFailureSince_) {
                windowProbeFailureSince_ = now;
                log(std::string("owned game window probe retrying: ") + error.what(), DiagnosticLogLevel::Warn);
            } else if (now - *windowProbeFailureSince_ >= std::chrono::seconds(5)) {
                setFailure(0, T7RNB_ERROR_OPERATION, std::string("owned game window probe failed for 5 seconds: ") + error.what());
                return;
            }
        }
        if (windowClosed) {
            // Preserve a natural exit or debug failure that raced the window observation.
            try { alive = bootstrap_.running(); }
            catch (const std::exception& error) {
                setFailure(0, T7RNB_ERROR_OPERATION, std::string("owned client status failed: ") + error.what());
                return;
            }
            windowClosed = alive;
        }
    }
    if (alive && !windowClosed) {
        monitorGraphics(); monitorAudio();
        return;
    }
    DWORD exitCode = 0;
    try { if (!windowClosed) exitCode = bootstrap_.exitCode(); }
    catch (const std::exception& error) {
        setFailure(0, T7RNB_ERROR_OPERATION, error.what());
        return;
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7RNB_STATE_STOPPING_CLIENT;
        snapshot_.phase = windowClosed ? "client-window-closed" : "client-exited";
    }
    if (windowClosed) log("owned game window closed; stopping remaining session processes");
    auto cleanupError = cleanup();
    const bool normalExit = windowClosed || exitCode == 0 || exitCode == CLIENT_NORMAL_EXIT_CODE;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (cleanupError.empty()) {
            snapshot_.state = normalExit ? T7RNB_STATE_IDLE : T7RNB_STATE_FAILED;
            snapshot_.errorCode = normalExit ? 0 : T7RNB_ERROR_CLIENT_EXIT;
            snapshot_.flags |= 1u;
            snapshot_.phase = windowClosed ? "client-window-closed" : normalExit ? "client-exited" : "client-exited-error";
        } else {
            snapshot_.state = T7RNB_STATE_FAILED_CLEANING;
            snapshot_.flags &= ~1u;
            snapshot_.errorCode = T7RNB_ERROR_CLEANUP;
            snapshot_.phase = "failed-cleaning";
        }
    }
    const auto reason = windowClosed ? std::string("owned game window closed")
                                    : "owned client exited; exitCode=" + std::to_string(exitCode);
    log(reason + (cleanupError.empty() ? "; runtime stopped" : "; cleanup=" + cleanupError),
        cleanupError.empty() && normalExit ? DiagnosticLogLevel::Info : DiagnosticLogLevel::Error);
}

void Session::monitorWorld() {
    bool battleNeedsWorld = false;
    std::array<uint8_t, MAX_WORLD_ACTORS> camps{};
    {
        std::lock_guard<std::mutex> lock(battleMutex_);
        battleNeedsWorld = battleAttachment_ != nullptr;
        if (battleAttachment_) {
            const auto& config = battleAttachment_->config;
            for (uint32_t i = 0; i < config.memberCount; ++i) {
                const auto& member = config.members[i];
                camps[member.clientRid - 1] = member.camp;
            }
        }
    }
    if (!battleNeedsWorld && (!capture_ || captureDisabled_)) return;
    try {
        WorldSnapshot world;
        if (bootstrap_.pollWorld(world, camps)) {
            if (capture_ && !captureDisabled_) {
                try { capture_->record(world); }
                catch (const std::exception& error) {
                    captureDisabled_ = true;
                    capture_->disable();
                    log(std::string("battle capture disabled: ") + error.what(), DiagnosticLogLevel::Warn);
                }
            }
            std::string clockBinding;
            {
                std::lock_guard<std::mutex> lock(battleMutex_);
                if (battleAttachment_) {
                    auto& attachment = *battleAttachment_;
                    if (!attachment.ingress && world.droppedContacts)
                        throw std::runtime_error("native contact journal dropped evidence; battle incomplete");
                    if (world.ready && !attachment.ingress) {
                        const auto anchor = attachment.sink->tryClockAnchor();
                        if (anchor) {
                            const auto nativeNow = anchor->nativeMs;
                            const auto matchNow = anchor->matchMs;
                            if (!world.sampledAt || nativeNow < world.sampledAt)
                                throw std::runtime_error("Rust battle clock anchor is invalid");
                            if (matchNow < attachment.config.startedAtMs)
                                throw std::runtime_error("Rust battle clock precedes match start");
                            if (matchNow - attachment.config.startedAtMs >= nativeNow - world.sampledAt) {
                                attachment.ingress = std::make_unique<BattleSnapshotIngress>(
                                    attachment.config, world.instanceStartedAt, nativeNow, matchNow, *attachment.sink);
                                attachment.sink->bindClientClock(attachment.config.epoch,
                                    clientTimeOffset(nativeNow, world.instanceStartedAt, matchNow));
                                attachment.sink->bindServerClock(attachment.config.epoch,
                                    serverTimeOffset(nativeNow, matchNow));
                                clockBinding = "Rust battle clock anchored; epoch=" + std::to_string(attachment.config.epoch)
                                    + "; source=" + std::to_string(attachment.config.sourceId)
                                    + "; nativeInstance=" + std::to_string(world.instanceStartedAt)
                                    + "; nativeAnchorMs=" + std::to_string(nativeNow)
                                    + "; matchAnchorMs=" + std::to_string(matchNow);
                            }
                        }
                    }
                    if (attachment.ingress) attachment.ingress->submit(world);
                }
            }
            if (!clockBinding.empty()) log(clockBinding);
        }
    } catch (const std::exception& error) {
        try {
            if (!bootstrap_.running()) { monitorClient(); return; }
        } catch (const std::exception& statusError) {
            setFailure(0, T7RNB_ERROR_OPERATION, std::string("owned client status failed: ") + statusError.what());
            return;
        }
        setFailure(0, T7RNB_ERROR_OPERATION, std::string("world snapshot failed: ") + error.what());
    }
}

void Session::stopCapture() {
    if (!capture_) return;
    auto capture = std::move(capture_);
    try { capture->stop(); }
    catch (const std::exception& error) {
        log(std::string("battle capture incomplete: ") + error.what(), DiagnosticLogLevel::Warn);
    }
    catch (...) { log("battle capture incomplete: non-standard capture error", DiagnosticLogLevel::Warn); }
}

void Session::failWorker(const std::string& message) {
    uint64_t operationId = 0;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (snapshot_.operation != T7RNB_OPERATION_NONE) operationId = snapshot_.operationId;
        for (const auto& command : commands_) {
            auto& operation = operations_.at(command.id);
            operation.status = T7RNB_OPERATION_FAILED;
            operation.errorCode = T7RNB_INTERNAL_ERROR;
            operation.error = message;
        }
        commands_.clear();
    }
    setFailure(operationId, T7RNB_INTERNAL_ERROR, message);
}

}
