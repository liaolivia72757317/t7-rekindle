#include "Session.h"
#include "../core/Common.h"
#include "../core/DiagnosticLog.h"
#include "../core/PlayerName.h"
#include <algorithm>
#include <cstring>
#include <fstream>
#include <winsock2.h>

namespace t7r::bridge {
namespace {
constexpr size_t MAX_LOG_RECORDS = 512;
constexpr size_t MAX_LOG_BYTES = 1024 * 1024;

fs::path localDataRoot() {
    wchar_t buffer[32768]{};
    auto length = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer, static_cast<DWORD>(std::size(buffer)));
    if (length && length < std::size(buffer)) return fs::path(buffer) / L"T7-Rekindle";
    wchar_t temporary[MAX_PATH]{};
    auto temporaryLength = GetTempPathW(static_cast<DWORD>(std::size(temporary)), temporary);
    if (temporaryLength && temporaryLength < std::size(temporary)) return fs::path(temporary) / L"T7-Rekindle";
    throw std::runtime_error("writable session root unavailable");
}

uint32_t errorCodeFor(const std::exception&) { return T7RNB_ERROR_OPERATION; }
fs::path requestedCapturePath() {
    wchar_t value[32768]{};
    const auto length = GetEnvironmentVariableW(L"T7R_BATTLE_CAPTURE_PATH", value, static_cast<DWORD>(std::size(value)));
    if (!length) return {};
    if (length >= std::size(value)) throw std::runtime_error("battle capture path exceeds limit");
    return fs::path(value);
}

}

std::shared_ptr<Session> Session::create(std::string packageRoot) {
    auto result = std::shared_ptr<Session>(new Session(std::move(packageRoot)));
    result->startWorker();
    return result;
}

#if defined(T7R_NATIVE_BRIDGE_TESTING)
std::shared_ptr<Session> Session::createForTest(
    std::string packageRoot, Bootstrap::TestAdapter adapter, std::function<void()> afterOperation) {
    auto result = std::shared_ptr<Session>(
        new Session(std::move(packageRoot), std::move(adapter)));
    result->afterOperationForTest_ = std::move(afterOperation);
    result->startWorker();
    return result;
}
#endif

Session::Session(std::string packageRoot, Bootstrap::TestAdapter adapter)
    : packageRoot_(std::move(packageRoot)), bootstrap_(std::move(adapter)) {
    if (packageRoot_.empty()) throw std::runtime_error("package root is empty");
    auto path = fs::path(wide(packageRoot_));
    if (!path.is_absolute() || path.native().find(L'\0') != std::wstring::npos)
        throw std::runtime_error("package root must be an absolute UTF-8 path");
    snapshot_.phase = "idle";
}

#if defined(T7R_NATIVE_BRIDGE_TESTING)
bool Session::waitForWorkerForTest(std::chrono::milliseconds timeout) {
    std::unique_lock<std::mutex> lock(mutex_);
    return changed_.wait_for(lock, timeout, [this] { return workerExited_; });
}
bool Session::battleIngressCreatedForTest() {
    std::lock_guard<std::mutex> lock(battleMutex_);
    return battleAttachment_ && battleAttachment_->ingress;
}
#endif

Session::~Session() {
    requestClose();
}

void Session::requestClose() noexcept {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        closing_ = true;
        // A zero operation id is the idle sentinel.  Keep the atomic at zero
        // when there is no active command so a future worker iteration cannot
        // accidentally treat an unrelated command as cancelled.
        cancelOperation_.store(snapshot_.operation == T7RNB_OPERATION_NONE ? 0 : snapshot_.operationId,
                               std::memory_order_release);
        for (const auto& command : commands_) {
            auto found = operations_.find(command.id);
            if (found != operations_.end() && found->second.status == T7RNB_OPERATION_QUEUED) {
                found->second.status = T7RNB_OPERATION_CANCELLED;
                found->second.errorCode = T7RNB_ERROR_CANCELLED;
                found->second.error = "session released";
            }
        }
        commands_.clear();
    }
    changed_.notify_all();
    // The worker is detached but keeps a shared reference to this object.  It
    // owns all blocking cleanup and releases the final reference itself.
}

void Session::startWorker() {
    auto self = shared_from_this();
    std::thread([self] { self->workerLoop(); }).detach();
}

int32_t Session::setLogLevel(uint32_t level) {
    if (level > static_cast<uint32_t>(DiagnosticLogLevel::Error)) return T7RNB_INVALID_ARGUMENT;
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7RNB_INVALID_HANDLE;
    logFilter_->minimum.store(static_cast<DiagnosticLogLevel>(level));
    return T7RNB_OK;
}

int32_t Session::setGuardDebug(bool enabled) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7RNB_INVALID_HANDLE;
    showGuardDebug_.store(enabled);
    return T7RNB_OK;
}

int32_t Session::setOutputDevice(const GUID& identifier) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7RNB_INVALID_HANDLE;
    if (snapshot_.operation != T7RNB_OPERATION_NONE || !commands_.empty()) return T7RNB_BUSY;
    outputDevice_ = identifier;
    return T7RNB_OK;
}

int32_t Session::submit(uint32_t kind, std::string clientDirectory, uint64_t& operationId,
                        std::string playerName, bool skipStartupAnimation,
                        std::array<uint16_t, 3> gatewayPorts,
                        std::vector<CombatProfile> profiles) {
    if (kind != T7RNB_OPERATION_CHECK && kind != T7RNB_OPERATION_START && kind != T7RNB_OPERATION_STOP)
        return T7RNB_INVALID_ARGUMENT;
    if (kind == T7RNB_OPERATION_START && !validPlayerName(playerName)) return T7RNB_INVALID_ARGUMENT;
    const bool externalGateway = gatewayPorts[0] || gatewayPorts[1] || gatewayPorts[2];
    if (externalGateway && (kind != T7RNB_OPERATION_START || !gatewayPorts[0] || !gatewayPorts[1]
        || !gatewayPorts[2] || gatewayPorts[0] == gatewayPorts[1]
        || gatewayPorts[0] == gatewayPorts[2] || gatewayPorts[1] == gatewayPorts[2]))
        return T7RNB_INVALID_ARGUMENT;
    if (kind == T7RNB_OPERATION_START && !externalGateway) return T7RNB_INVALID_ARGUMENT;
    if (!profiles.empty() && !externalGateway) return T7RNB_INVALID_ARGUMENT;
    if (kind != T7RNB_OPERATION_STOP) {
        try {
            auto path = fs::path(wide(clientDirectory));
            if (clientDirectory.empty() || !path.is_absolute() || clientDirectory.size() > 32768)
                return T7RNB_INVALID_ARGUMENT;
        } catch (...) { return T7RNB_INVALID_ARGUMENT; }
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7RNB_INVALID_HANDLE;
    if (snapshot_.state == T7RNB_STATE_FAILED_CLEANING && kind != T7RNB_OPERATION_STOP)
        return T7RNB_BUSY;
    // Reserve the first queued command in the snapshot before notifying the
    // worker.  Without this reservation two callers can both observe an idle
    // snapshot during the small enqueue/dequeue window and submit concurrent
    // lifecycle operations even though the worker is strictly serial.
    const bool active = snapshot_.operation != T7RNB_OPERATION_NONE;
    const bool queued = !commands_.empty();
    if (kind == T7RNB_OPERATION_START || kind == T7RNB_OPERATION_CHECK) {
        if ((snapshot_.state != T7RNB_STATE_IDLE && snapshot_.state != T7RNB_STATE_FAILED) || active || queued)
            return T7RNB_BUSY;
    }
    if (kind == T7RNB_OPERATION_STOP && queued) {
        // A Stop already waiting behind an active operation is enough; do not
        // create an unbounded stop queue from repeated UI clicks.
        for (const auto& command : commands_)
            if (command.kind == T7RNB_OPERATION_STOP) return T7RNB_BUSY;
    }
    if (kind == T7RNB_OPERATION_STOP && active && snapshot_.operation == T7RNB_OPERATION_STOP)
        return T7RNB_BUSY;
    const bool retryingCleanup = snapshot_.state == T7RNB_STATE_FAILED_CLEANING;
    operationId = nextOperation_++;
    if (kind == T7RNB_OPERATION_STOP && active) {
        cancelOperation_.store(snapshot_.operationId, std::memory_order_release);
        snapshot_.state = T7RNB_STATE_CANCELLING;
        snapshot_.phase = "cancelling-before-stop";
    }
    operations_[operationId] = {operationId, kind, T7RNB_OPERATION_QUEUED, 0, {}};
    commands_.push_back({operationId, kind, std::move(clientDirectory), std::move(playerName),
                         skipStartupAnimation, outputDevice_, gatewayPorts, std::move(profiles)});
    if (!active) {
        snapshot_.operation = kind;
        snapshot_.operationId = operationId;
        if (!retryingCleanup) snapshot_.errorCode = 0;
        snapshot_.flags &= ~1u;
        snapshot_.state = kind == T7RNB_OPERATION_CHECK ? T7RNB_STATE_CHECKING
            : kind == T7RNB_OPERATION_START ? T7RNB_STATE_STARTING_RUNTIME : T7RNB_STATE_STOPPING_CLIENT;
        snapshot_.phase = retryingCleanup ? "cleanup-retry-queued" : "queued";
    }
    changed_.notify_all();
    return T7RNB_OK;
}

int32_t Session::cancel(uint64_t operationId) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto found = operations_.find(operationId);
    if (found == operations_.end()) return T7RNB_NOT_FOUND;
    if (found->second.status != T7RNB_OPERATION_QUEUED && found->second.status != T7RNB_OPERATION_RUNNING)
        return T7RNB_NOT_READY;
    if (found->second.status == T7RNB_OPERATION_QUEUED) {
        found->second.status = T7RNB_OPERATION_CANCELLED;
        found->second.errorCode = T7RNB_ERROR_CANCELLED;
        found->second.error = "operation cancelled before execution";
        commands_.erase(std::remove_if(commands_.begin(), commands_.end(),
            [operationId](const Command& command) { return command.id == operationId; }), commands_.end());
        if (snapshot_.operationId == operationId && snapshot_.operation == found->second.kind) {
            const bool cleanupPending = snapshot_.errorCode == T7RNB_ERROR_CLEANUP;
            snapshot_.operation = T7RNB_OPERATION_NONE;
            if (cleanupPending) {
                snapshot_.state = T7RNB_STATE_FAILED_CLEANING;
                snapshot_.flags &= ~1u;
                snapshot_.errorCode = T7RNB_ERROR_CLEANUP;
                snapshot_.phase = "failed-cleaning";
            } else {
                snapshot_.state = T7RNB_STATE_IDLE;
                snapshot_.errorCode = T7RNB_ERROR_CANCELLED;
                snapshot_.flags |= 1u;
                snapshot_.phase = "idle";
            }
        }
        changed_.notify_all();
        return T7RNB_OK;
    }
    cancelOperation_.store(operationId, std::memory_order_release);
    if (snapshot_.operationId == operationId) {
        snapshot_.state = T7RNB_STATE_CANCELLING;
        snapshot_.phase = "cancelling";
    }
    changed_.notify_all();
    return T7RNB_OK;
}

int32_t Session::snapshot(Snapshot& result) const {
    std::lock_guard<std::mutex> lock(mutex_);
    result = snapshot_;
    return T7RNB_OK;
}

int32_t Session::operation(uint64_t operationId, Operation& result) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto found = operations_.find(operationId);
    if (found == operations_.end()) return T7RNB_NOT_FOUND;
    result = found->second;
    return T7RNB_OK;
}

int32_t Session::error(uint64_t operationId, std::string& result, uint32_t& errorCode) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto found = operations_.find(operationId);
    if (found == operations_.end()) return T7RNB_NOT_FOUND;
    result = found->second.error;
    errorCode = found->second.errorCode;
    return T7RNB_OK;
}

int32_t Session::readLogs(uint64_t& cursor, uint8_t* buffer, uint32_t capacity,
                          uint32_t& required, uint32_t& flags) {
    if (capacity && !buffer) return T7RNB_INVALID_ARGUMENT;
    std::lock_guard<std::mutex> lock(mutex_);
    flags = 0;
    uint64_t readCursor = cursor == 0 ? earliestLog_ : cursor;
    if (readCursor < earliestLog_) {
        flags |= 1u; // gap: caller's cursor was rotated out
        readCursor = earliestLog_;
    }
    std::string output;
    for (const auto& item : logs_) {
        if (item.cursor < readCursor) continue;
        output += std::to_string(item.cursor);
        output.push_back('\t');
        output += item.text;
        output.push_back('\n');
    }
    required = static_cast<uint32_t>(output.size());
    if (required > capacity) {
        // A short buffer must never consume the caller's cursor.  The gap
        // flag is repeated on the retry, where the full record set can be
        // copied and the cursor advances atomically.
        return T7RNB_BUFFER_TOO_SMALL;
    }
    if (required) std::memcpy(buffer, output.data(), required);
    cursor = nextLog_;
    return T7RNB_OK;
}

int32_t Session::attachBattle(const T7RBattleConfig& config, const std::filesystem::path& rustLibrary,
                              uint64_t gatewayHandle) {
    if (config.abiVersion != T7R_BATTLE_CONFIG_ABI_VERSION || config.structSize != sizeof(config)
        || config.memberCount == 0 || config.memberCount > MAX_WORLD_ACTORS || !rustLibrary.is_absolute() || !gatewayHandle)
        return T7RNB_INVALID_ARGUMENT;
    for (uint32_t i = 0; i < config.memberCount; ++i) {
        const auto& member = config.members[i];
        if (member.actorId == 0 || member.actorId > MAX_WORLD_ACTORS || member.clientRid == 0
            || member.clientRid > MAX_WORLD_ACTORS || member.camp < 1 || member.camp > 2)
            return T7RNB_INVALID_ARGUMENT;
    }
    {
        std::lock_guard<std::mutex> stateLock(mutex_);
        if (closing_ || snapshot_.state != T7RNB_STATE_RUNNING || snapshot_.operation != T7RNB_OPERATION_NONE)
            return T7RNB_NOT_READY;
        std::lock_guard<std::mutex> battleLock(battleMutex_);
        if (battleAttachment_) return T7RNB_BUSY;
    }
    std::unique_ptr<BattleGatewaySink> sink;
    try { sink = std::make_unique<BattleGatewaySink>(rustLibrary, gatewayHandle); }
    catch (const std::exception& error) {
        log(std::string("Rust battle attachment failed: ") + error.what(), DiagnosticLogLevel::Error);
        return T7RNB_FAILED;
    }
    std::lock_guard<std::mutex> stateLock(mutex_);
    if (closing_ || snapshot_.state != T7RNB_STATE_RUNNING || snapshot_.operation != T7RNB_OPERATION_NONE)
        return T7RNB_NOT_READY;
    std::lock_guard<std::mutex> battleLock(battleMutex_);
    if (battleAttachment_) return T7RNB_BUSY;
    auto attachment = std::make_unique<BattleAttachment>();
    attachment->config = config;
    attachment->sink = std::move(sink);
    battleAttachment_ = std::move(attachment);
    return T7RNB_OK;
}

int32_t Session::detachBattle() {
    std::unique_ptr<BattleAttachment> attachment;
    {
        std::lock_guard<std::mutex> lock(battleMutex_);
        attachment = std::move(battleAttachment_);
    }
    if (!attachment || !attachment->ingress) return T7RNB_OK;
    try { attachment->ingress->flush(); }
    catch (const std::exception& error) {
        log(std::string("Rust battle final watermark failed: ") + error.what(), DiagnosticLogLevel::Error);
        return T7RNB_FAILED;
    }
    return T7RNB_OK;
}

void Session::workerLoop() {
    while (true) {
        try {
            auto nextMonitor = std::chrono::steady_clock::now();
            while (true) {
                Command command{};
                bool hasCommand = false;
                {
                    std::unique_lock<std::mutex> lock(mutex_);
                    const auto interval = snapshot_.state == T7RNB_STATE_RUNNING ? 5 : 50;
                    hasCommand = changed_.wait_for(lock, std::chrono::milliseconds(interval),
                        [this] { return closing_ || !commands_.empty(); });
                    if (!hasCommand && snapshot_.state == T7RNB_STATE_RUNNING && !closing_) {
                        lock.unlock();
                        const auto now = std::chrono::steady_clock::now();
                        if (now >= nextMonitor) {
                            monitorClient();
                            monitorTitles();
                            nextMonitor = now + std::chrono::milliseconds(50);
                        }
                        monitorWorld();
                        continue;
                    }
                    // wait_for returns false on a timeout.  There is no command
                    // to move in that case; remain idle instead of touching an
                    // empty deque.
                    if (!hasCommand) continue;
                    if (closing_ && commands_.empty()) break;
                    command = std::move(commands_.front());
                    commands_.pop_front();
                    auto& operation = operations_.at(command.id);
                    operation.status = T7RNB_OPERATION_RUNNING;
                    snapshot_.operation = command.kind;
                    snapshot_.operationId = command.id;
                    snapshot_.errorCode = 0;
                    snapshot_.flags &= ~1u;
                    snapshot_.state = command.kind == T7RNB_OPERATION_CHECK ? T7RNB_STATE_CHECKING
                        : command.kind == T7RNB_OPERATION_START ? T7RNB_STATE_STARTING_RUNTIME : T7RNB_STATE_STOPPING_CLIENT;
                    snapshot_.phase = "queued";
                }
                try {
                    execute(command);
                    bool cancellationWon;
                    {
                        // cancel() uses the same lock: accepting cancellation and
                        // publishing success must have one atomic ordering.
                        std::lock_guard<std::mutex> lock(mutex_);
                        cancellationWon = cancelled(command.id) && command.kind != T7RNB_OPERATION_STOP;
                        if (!cancellationWon) finishOperationLocked(command.id, T7RNB_OPERATION_SUCCEEDED);
                    }
                    if (cancellationWon) {
                        // Cancellation can race with the final instruction of a
                        // command.  Route that race through the same cleanup
                        // path as an in-flight cancellation; otherwise a Start
                        // could be reported cancelled while its client remains
                        // running.
                        setFailure(command.id, T7RNB_ERROR_CANCELLED, "operation cancelled");
                    }
                } catch (const std::exception& error) {
                    setFailure(command.id, errorCodeFor(error), error.what());
                } catch (...) {
                    setFailure(command.id, T7RNB_INTERNAL_ERROR, "non-standard native exception");
                }
#if defined(T7R_NATIVE_BRIDGE_TESTING)
                if (afterOperationForTest_) afterOperationForTest_();
#endif
            }
        } catch (const std::exception& error) {
            failWorker(std::string("session worker failed: ") + error.what());
            continue;
        } catch (...) {
            failWorker("session worker failed: non-standard native exception");
            continue;
        }
        break;
    }
    cleanup();
    {
        std::lock_guard<std::mutex> lock(mutex_);
        workerExited_ = true;
    }
    changed_.notify_all();
}

void Session::finishOperationLocked(uint64_t operationId, uint32_t status) {
    // Caller holds mutex_: terminal results and the idle operation marker
    // must become visible together before another command can be submitted.
    auto& operation = operations_.at(operationId);
    operation.status = status;
    snapshot_.operation = T7RNB_OPERATION_NONE;
    snapshot_.operationId = operationId;
    if (snapshot_.state != T7RNB_STATE_FAILED && snapshot_.state != T7RNB_STATE_FAILED_CLEANING) {
        snapshot_.state = (operation.kind == T7RNB_OPERATION_STOP || operation.kind == T7RNB_OPERATION_CHECK)
            ? T7RNB_STATE_IDLE : snapshot_.state;
        snapshot_.phase = snapshot_.state == T7RNB_STATE_RUNNING ? "running" : "idle";
    }
    if (snapshot_.state == T7RNB_STATE_RUNNING || snapshot_.state == T7RNB_STATE_FAILED_CLEANING)
        snapshot_.flags &= ~1u;
    else snapshot_.flags |= 1u;
    auto expectedCancel = operationId;
    cancelOperation_.compare_exchange_strong(expectedCancel, 0, std::memory_order_acq_rel);
    changed_.notify_all();
}

void Session::execute(const Command& command) {
    if (command.kind == T7RNB_OPERATION_CHECK) executeCheck(command);
    else if (command.kind == T7RNB_OPERATION_START) executeStart(command);
    else executeStop(command);
}

void Session::executeCheck(const Command& command) {
    if (cancelled(command.id)) throw std::runtime_error("check cancelled");
    Config config;
    config.outputDevice = command.outputDevice;
    config.ports[0] = 1; config.ports[1] = 2; config.ports[2] = 3;
    bootstrap_.check(fs::path(wide(command.clientDirectory)), config, [this](DiagnosticLogLevel level, std::string line) { log(line, level); });
    if (cancelled(command.id)) throw std::runtime_error("check cancelled");
    log("client preflight passed");
}

void Session::executeStart(const Command& command) {
    if (cancelled(command.id)) throw std::runtime_error("start cancelled");
    {
        std::lock_guard<std::mutex> lock(mutex_);
        graphics_.state = T7RNB_GRAPHICS_UNAVAILABLE;
        ++graphics_.revision; graphicsQueued_ = false;
        audio_.state = T7RNB_AUDIO_UNAVAILABLE;
        ++audio_.revision; audioQueued_ = false;
        titleUpdates_.clear(); titleRequest_ = {}; titleError_.clear();
    }
    if (!winsockStarted_) {
        WSADATA data{};
        if (WSAStartup(MAKEWORD(2, 2), &data) != 0) throw std::runtime_error("WSAStartup failed");
        winsockStarted_ = true;
    }
    Config config;
    config.bindAddress = "127.0.0.1";
    config.advertisedAddress = "127.0.0.1";
    config.playerName = command.playerName;
    config.clientDirectory = fs::path(wide(command.clientDirectory));
    config.skipStartupAnimation = command.skipStartupAnimation;
    config.outputDevice = command.outputDevice;
    const auto packageRoot = fs::path(wide(packageRoot_));
    config.lobbyNewsPath = packageRoot / "news/index.html";
    for (size_t index = 0; index < command.gatewayPorts.size(); ++index)
        config.ports[index] = command.gatewayPorts[index];
    config.combatProfiles = command.profiles;
    windowProbeFailureSince_.reset();
    captureDisabled_ = false;
    try {
        const auto capturePath = requestedCapturePath();
        if (!capturePath.empty())
            capture_ = std::make_unique<BattleCapture>(capturePath, config.clientDirectory, packageRoot);
    } catch (const std::exception& error) {
        log(std::string("battle capture disabled: ") + error.what(), DiagnosticLogLevel::Warn);
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7RNB_STATE_STARTING_RUNTIME;
        snapshot_.phase = "runtime-ready-wait";
    }
    if (cancelled(command.id)) throw std::runtime_error("start cancelled");
    const auto& actual = config;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.ports[0] = actual.ports[0]; snapshot_.ports[1] = actual.ports[1]; snapshot_.ports[2] = actual.ports[2];
        snapshot_.state = T7RNB_STATE_STARTING_CLIENT;
        snapshot_.phase = "client-start";
    }
    bootstrap_.launch(fs::path(wide(command.clientDirectory)), actual,
        [this](DiagnosticLogLevel level, std::string line) { log(line, level); },
        [this, id = command.id] { return cancelled(id); },
        [this, id = command.id] {
            setState(T7RNB_STATE_ADAPTING_CLIENT, T7RNB_OPERATION_START, id, "client-adaptation");
        },
        [this](std::string line) { log(line, DiagnosticLogLevel::Warn); }, logFilter_->allows(DiagnosticLogLevel::Debug));
    if (cancelled(command.id)) throw std::runtime_error("start cancelled");
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7RNB_STATE_RUNNING;
        snapshot_.phase = "running";
        snapshot_.revision = ++revisionCounter_;
    }
    log("session running with Rust gateway");
}

void Session::executeStop(const Command& command) {
    (void)command;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7RNB_STATE_STOPPING_CLIENT;
        snapshot_.phase = "client-stop";
    }
    bootstrap_.stop();
    stopCapture();
    if (detachBattle() != T7RNB_OK)
        throw std::runtime_error("Rust battle final watermark failed");
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7RNB_STATE_STOPPING_RUNTIME;
        snapshot_.phase = "runtime-stop";
    }
    if (winsockStarted_) { WSACleanup(); winsockStarted_ = false; }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.ports[0] = snapshot_.ports[1] = snapshot_.ports[2] = 0;
    }
}

void Session::setFailure(uint64_t operationId, uint32_t code, const std::string& message) {
    const bool wasCancelled = cancelled(operationId);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.errorCode = wasCancelled ? T7RNB_ERROR_CANCELLED : code;
        snapshot_.state = wasCancelled ? T7RNB_STATE_CANCELLING : T7RNB_STATE_FAILED_CLEANING;
        snapshot_.flags &= ~1u;
        snapshot_.phase = wasCancelled ? "cancelling" : "failed-cleaning";
    }
    log(wasCancelled ? "operation cancelled: " + message : "operation failed: " + message, wasCancelled ? DiagnosticLogLevel::Info : DiagnosticLogLevel::Error);
    auto cleanupError = cleanup();
    if (!cleanupError.empty()) log("session cleanup failed: " + cleanupError, DiagnosticLogLevel::Error);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto found = operations_.find(operationId);
        if (found != operations_.end()) {
            found->second.errorCode = cleanupError.empty()
                ? (wasCancelled ? T7RNB_ERROR_CANCELLED : code) : T7RNB_ERROR_CLEANUP;
            found->second.error = message;
            if (!cleanupError.empty()) found->second.error += "; cleanup=" + cleanupError;
        }
        if (wasCancelled && cleanupError.empty()) {
            snapshot_.state = T7RNB_STATE_IDLE;
            snapshot_.errorCode = T7RNB_ERROR_CANCELLED;
            snapshot_.flags |= 1u;
            snapshot_.phase = "idle";
        } else if (cleanupError.empty()) {
            snapshot_.state = T7RNB_STATE_FAILED;
            snapshot_.flags |= 1u;
            snapshot_.phase = "failed";
        } else {
            snapshot_.state = T7RNB_STATE_FAILED_CLEANING;
            snapshot_.flags &= ~1u;
            snapshot_.errorCode = T7RNB_ERROR_CLEANUP;
            snapshot_.phase = "failed-cleaning";
        }
        if (found != operations_.end())
            finishOperationLocked(operationId, wasCancelled ? T7RNB_OPERATION_CANCELLED : T7RNB_OPERATION_FAILED);
    }
}

std::string Session::cleanup() noexcept {
    std::string errors;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7RNB_STATE_STOPPING_CLIENT;
        snapshot_.phase = "client-stop";
    }
    try { bootstrap_.stop(); }
    catch (const std::exception& error) { errors = error.what(); }
    catch (...) { errors = "non-standard client cleanup error"; }
    stopCapture();
    try { if (detachBattle() != T7RNB_OK) throw std::runtime_error("Rust battle final watermark failed"); }
    catch (const std::exception& error) { if (!errors.empty()) errors += "; "; errors += error.what(); }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7RNB_STATE_STOPPING_RUNTIME;
        snapshot_.phase = "runtime-stop";
    }
    if (winsockStarted_) { WSACleanup(); winsockStarted_ = false; }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.ports[0] = snapshot_.ports[1] = snapshot_.ports[2] = 0;
    }
    return errors;
}

void Session::log(const std::string& text, DiagnosticLogLevel level) {
    if (!logFilter_->allows(level)) return;
    logRecord(formatDiagnosticRecord(text, level, "NativeBridge"));
}

void Session::logRecord(const std::string& record) {
    if (!logFilter_->allows(diagnosticRecordLevel(record))) return;
    std::string writeFailure;
    try {
        appendNativeLog(localDataRoot() / "logs", record);
    } catch (const std::exception& error) {
        writeFailure = formatDiagnosticRecord(std::string("日志文件写入失败：") + error.what(), DiagnosticLogLevel::Error, "NativeBridge");
    }
    std::lock_guard<std::mutex> lock(mutex_);
    logs_.push_back({nextLog_++, boundedDiagnosticRecord(record)});
    if (!writeFailure.empty()) logs_.push_back({nextLog_++, boundedDiagnosticRecord(writeFailure)});
    while (logs_.size() > MAX_LOG_RECORDS) { logs_.pop_front(); earliestLog_ = logs_.front().cursor; }
    size_t bytes = 0; for (const auto& item : logs_) bytes += item.text.size() + 24;
    while (bytes > MAX_LOG_BYTES && !logs_.empty()) {
        logs_.pop_front(); earliestLog_ = logs_.empty() ? nextLog_ : logs_.front().cursor;
        bytes = 0; for (const auto& item : logs_) bytes += item.text.size() + 24;
    }
    snapshot_.logCursor = nextLog_ - 1;
}

bool Session::cancelled(uint64_t operationId) const {
    return operationId != 0 && cancelOperation_.load(std::memory_order_acquire) == operationId;
}

void Session::setState(uint32_t state, uint32_t operation, uint64_t operationId, const std::string& phase) {
    std::lock_guard<std::mutex> lock(mutex_);
    snapshot_.state = state; snapshot_.operation = operation; snapshot_.operationId = operationId; snapshot_.phase = phase;
}

}
