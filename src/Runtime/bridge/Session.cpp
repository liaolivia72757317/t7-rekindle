#include "Session.h"
#include "../core/Common.h"
#include "../core/DiagnosticLog.h"
#include "../core/PlayerName.h"
#include <algorithm>
#include <cstring>
#include <fstream>
#include <winsock2.h>

namespace t7::bridge {
namespace {
constexpr size_t MAX_LOG_RECORDS = 512;
constexpr size_t MAX_LOG_BYTES = 1024 * 1024;
// The supported client also returns 0x1234 after its exit confirmation.
constexpr DWORD CLIENT_NORMAL_EXIT_CODE = 0x1234;

fs::path localDataRoot() {
    wchar_t buffer[32768]{};
    auto length = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer, static_cast<DWORD>(std::size(buffer)));
    if (length && length < std::size(buffer)) return fs::path(buffer) / L"T7-Rekindle";
    wchar_t temporary[MAX_PATH]{};
    auto temporaryLength = GetTempPathW(static_cast<DWORD>(std::size(temporary)), temporary);
    if (temporaryLength && temporaryLength < std::size(temporary)) return fs::path(temporary) / L"T7-Rekindle";
    throw std::runtime_error("writable session root unavailable");
}

uint32_t errorCodeFor(const std::exception&) { return T7NB_ERROR_OPERATION; }
}

std::shared_ptr<Session> Session::create(std::string packageRoot) {
    auto result = std::shared_ptr<Session>(new Session(std::move(packageRoot)));
    result->startWorker();
    return result;
}

#if defined(T7_NATIVE_BRIDGE_TESTING)
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

#if defined(T7_NATIVE_BRIDGE_TESTING)
bool Session::waitForWorkerForTest(std::chrono::milliseconds timeout) {
    std::unique_lock<std::mutex> lock(mutex_);
    return changed_.wait_for(lock, timeout, [this] { return workerExited_; });
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
        cancelOperation_.store(snapshot_.operation == T7NB_OPERATION_NONE ? 0 : snapshot_.operationId,
                               std::memory_order_release);
        for (const auto& command : commands_) {
            auto found = operations_.find(command.id);
            if (found != operations_.end() && found->second.status == T7NB_OPERATION_QUEUED) {
                found->second.status = T7NB_OPERATION_CANCELLED;
                found->second.errorCode = T7NB_ERROR_CANCELLED;
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

int32_t Session::setOutputDevice(const GUID& identifier) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7NB_INVALID_HANDLE;
    if (snapshot_.operation != T7NB_OPERATION_NONE || !commands_.empty()) return T7NB_BUSY;
    outputDevice_ = identifier;
    return T7NB_OK;
}

int32_t Session::submit(uint32_t kind, std::string clientDirectory, uint64_t& operationId,
                        std::string playerName, bool skipStartupAnimation) {
    if (kind != T7NB_OPERATION_CHECK && kind != T7NB_OPERATION_START && kind != T7NB_OPERATION_STOP)
        return T7NB_INVALID_ARGUMENT;
    if (kind == T7NB_OPERATION_START && !validPlayerName(playerName)) return T7NB_INVALID_ARGUMENT;
    if (kind != T7NB_OPERATION_STOP) {
        try {
            auto path = fs::path(wide(clientDirectory));
            if (clientDirectory.empty() || !path.is_absolute() || clientDirectory.size() > 32768)
                return T7NB_INVALID_ARGUMENT;
        } catch (...) { return T7NB_INVALID_ARGUMENT; }
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7NB_INVALID_HANDLE;
    if (snapshot_.state == T7NB_STATE_FAILED_CLEANING && kind != T7NB_OPERATION_STOP)
        return T7NB_BUSY;
    // Reserve the first queued command in the snapshot before notifying the
    // worker.  Without this reservation two callers can both observe an idle
    // snapshot during the small enqueue/dequeue window and submit concurrent
    // lifecycle operations even though the worker is strictly serial.
    const bool active = snapshot_.operation != T7NB_OPERATION_NONE;
    const bool queued = !commands_.empty();
    if (kind == T7NB_OPERATION_START || kind == T7NB_OPERATION_CHECK) {
        if (snapshot_.state == T7NB_STATE_RUNNING || active || queued) return T7NB_BUSY;
    }
    if (kind == T7NB_OPERATION_STOP && queued) {
        // A Stop already waiting behind an active operation is enough; do not
        // create an unbounded stop queue from repeated UI clicks.
        for (const auto& command : commands_)
            if (command.kind == T7NB_OPERATION_STOP) return T7NB_BUSY;
    }
    if (kind == T7NB_OPERATION_STOP && active && snapshot_.operation == T7NB_OPERATION_STOP)
        return T7NB_BUSY;
    const bool retryingCleanup = snapshot_.state == T7NB_STATE_FAILED_CLEANING;
    operationId = nextOperation_++;
    if (kind == T7NB_OPERATION_STOP && active) {
        cancelOperation_.store(snapshot_.operationId, std::memory_order_release);
        snapshot_.state = T7NB_STATE_CANCELLING;
        snapshot_.phase = "cancelling-before-stop";
    }
    operations_[operationId] = {operationId, kind, T7NB_OPERATION_QUEUED, 0, {}};
    commands_.push_back({operationId, kind, std::move(clientDirectory), std::move(playerName), skipStartupAnimation, outputDevice_});
    if (!active) {
        snapshot_.operation = kind;
        snapshot_.operationId = operationId;
        if (!retryingCleanup) snapshot_.errorCode = 0;
        snapshot_.flags &= ~1u;
        snapshot_.state = kind == T7NB_OPERATION_CHECK ? T7NB_STATE_CHECKING
            : kind == T7NB_OPERATION_START ? T7NB_STATE_STARTING_RUNTIME : T7NB_STATE_STOPPING_CLIENT;
        snapshot_.phase = retryingCleanup ? "cleanup-retry-queued" : "queued";
    }
    changed_.notify_all();
    return T7NB_OK;
}

int32_t Session::cancel(uint64_t operationId) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto found = operations_.find(operationId);
    if (found == operations_.end()) return T7NB_NOT_FOUND;
    if (found->second.status != T7NB_OPERATION_QUEUED && found->second.status != T7NB_OPERATION_RUNNING)
        return T7NB_NOT_READY;
    if (found->second.status == T7NB_OPERATION_QUEUED) {
        found->second.status = T7NB_OPERATION_CANCELLED;
        found->second.errorCode = T7NB_ERROR_CANCELLED;
        found->second.error = "operation cancelled before execution";
        commands_.erase(std::remove_if(commands_.begin(), commands_.end(),
            [operationId](const Command& command) { return command.id == operationId; }), commands_.end());
        if (snapshot_.operationId == operationId && snapshot_.operation == found->second.kind) {
            const bool cleanupPending = snapshot_.errorCode == T7NB_ERROR_CLEANUP;
            snapshot_.operation = T7NB_OPERATION_NONE;
            if (cleanupPending) {
                snapshot_.state = T7NB_STATE_FAILED_CLEANING;
                snapshot_.flags &= ~1u;
                snapshot_.errorCode = T7NB_ERROR_CLEANUP;
                snapshot_.phase = "failed-cleaning";
            } else {
                snapshot_.state = T7NB_STATE_IDLE;
                snapshot_.errorCode = T7NB_ERROR_CANCELLED;
                snapshot_.flags |= 1u;
                snapshot_.phase = "idle";
            }
        }
        changed_.notify_all();
        return T7NB_OK;
    }
    cancelOperation_.store(operationId, std::memory_order_release);
    if (snapshot_.operationId == operationId) {
        snapshot_.state = T7NB_STATE_CANCELLING;
        snapshot_.phase = "cancelling";
    }
    changed_.notify_all();
    return T7NB_OK;
}

int32_t Session::snapshot(Snapshot& result) const {
    std::lock_guard<std::mutex> lock(mutex_);
    result = snapshot_;
    return T7NB_OK;
}

int32_t Session::operation(uint64_t operationId, Operation& result) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto found = operations_.find(operationId);
    if (found == operations_.end()) return T7NB_NOT_FOUND;
    result = found->second;
    return T7NB_OK;
}

int32_t Session::error(uint64_t operationId, std::string& result, uint32_t& errorCode) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto found = operations_.find(operationId);
    if (found == operations_.end()) return T7NB_NOT_FOUND;
    result = found->second.error;
    errorCode = found->second.errorCode;
    return T7NB_OK;
}

int32_t Session::readLogs(uint64_t& cursor, uint8_t* buffer, uint32_t capacity,
                          uint32_t& required, uint32_t& flags) {
    if (capacity && !buffer) return T7NB_INVALID_ARGUMENT;
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
        return T7NB_BUFFER_TOO_SMALL;
    }
    if (required) std::memcpy(buffer, output.data(), required);
    cursor = nextLog_;
    return T7NB_OK;
}

void Session::workerLoop() {
    try {
        while (true) {
            Command command{};
            bool hasCommand = false;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                hasCommand = changed_.wait_for(lock, std::chrono::milliseconds(100),
                    [this] { return closing_ || !commands_.empty(); });
                if (!hasCommand && snapshot_.state == T7NB_STATE_RUNNING && !closing_) {
                    lock.unlock();
                    monitorClient();
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
                operation.status = T7NB_OPERATION_RUNNING;
                snapshot_.operation = command.kind;
                snapshot_.operationId = command.id;
                snapshot_.errorCode = 0;
                snapshot_.flags &= ~1u;
                snapshot_.state = command.kind == T7NB_OPERATION_CHECK ? T7NB_STATE_CHECKING
                    : command.kind == T7NB_OPERATION_START ? T7NB_STATE_STARTING_RUNTIME : T7NB_STATE_STOPPING_CLIENT;
                snapshot_.phase = "queued";
            }
            try {
                execute(command);
                bool cancellationWon;
                {
                    // cancel() uses the same lock: accepting cancellation and
                    // publishing success must have one atomic ordering.
                    std::lock_guard<std::mutex> lock(mutex_);
                    cancellationWon = cancelled(command.id) && command.kind != T7NB_OPERATION_STOP;
                    if (!cancellationWon) finishOperationLocked(command.id, T7NB_OPERATION_SUCCEEDED);
                }
                if (cancellationWon) {
                    // Cancellation can race with the final instruction of a
                    // command.  Route that race through the same cleanup
                    // path as an in-flight cancellation; otherwise a Start
                    // could be reported cancelled while its client remains
                    // running.
                    setFailure(command.id, T7NB_ERROR_CANCELLED, "operation cancelled");
                }
            } catch (const std::exception& error) {
                setFailure(command.id, errorCodeFor(error), error.what());
            } catch (...) {
                setFailure(command.id, T7NB_INTERNAL_ERROR, "non-standard native exception");
            }
#if defined(T7_NATIVE_BRIDGE_TESTING)
            if (afterOperationForTest_) afterOperationForTest_();
#endif
        }
    } catch (...) {
        cleanup();
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
    snapshot_.operation = T7NB_OPERATION_NONE;
    snapshot_.operationId = operationId;
    if (snapshot_.state != T7NB_STATE_FAILED && snapshot_.state != T7NB_STATE_FAILED_CLEANING) {
        snapshot_.state = (operation.kind == T7NB_OPERATION_STOP || operation.kind == T7NB_OPERATION_CHECK)
            ? T7NB_STATE_IDLE : snapshot_.state;
        snapshot_.phase = snapshot_.state == T7NB_STATE_RUNNING ? "running" : "idle";
    }
    if (snapshot_.state == T7NB_STATE_RUNNING || snapshot_.state == T7NB_STATE_FAILED_CLEANING)
        snapshot_.flags &= ~1u;
    else snapshot_.flags |= 1u;
    auto expectedCancel = operationId;
    cancelOperation_.compare_exchange_strong(expectedCancel, 0, std::memory_order_acq_rel);
    changed_.notify_all();
}

void Session::monitorClient() {
    if (server_ && !server_->running()) {
        std::string detail = "runtime stopped unexpectedly";
        try {
            auto runtime = server_->snapshot();
            if (!runtime.error.empty()) detail += ": " + runtime.error;
        } catch (const std::exception& error) {
            detail += ": " + std::string(error.what());
        }
        setFailure(0, T7NB_ERROR_OPERATION, detail);
        return;
    }
    bool alive = true;
    try { alive = bootstrap_.running(); }
    catch (const std::exception& error) {
        setFailure(0, T7NB_ERROR_OPERATION, std::string("owned client status failed: ") + error.what());
        return;
    }
    if (alive) { monitorGraphics(); monitorAudio(); return; }
    DWORD exitCode = 0;
    try { exitCode = bootstrap_.exitCode(); }
    catch (const std::exception& error) {
        setFailure(0, T7NB_ERROR_OPERATION, error.what());
        return;
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7NB_STATE_STOPPING_CLIENT;
        snapshot_.phase = "client-exited";
    }
    auto cleanupError = cleanup();
    const bool normalExit = exitCode == 0 || exitCode == CLIENT_NORMAL_EXIT_CODE;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (cleanupError.empty()) {
            snapshot_.state = normalExit ? T7NB_STATE_IDLE : T7NB_STATE_FAILED;
            snapshot_.errorCode = normalExit ? 0 : T7NB_ERROR_CLIENT_EXIT;
            snapshot_.flags |= 1u;
            snapshot_.phase = normalExit ? "client-exited" : "client-exited-error";
        } else {
            snapshot_.state = T7NB_STATE_FAILED_CLEANING;
            snapshot_.flags &= ~1u;
            snapshot_.errorCode = T7NB_ERROR_CLEANUP;
            snapshot_.phase = "failed-cleaning";
        }
    }
    log(cleanupError.empty() ? "owned client exited; exitCode=" + std::to_string(exitCode) + "; runtime stopped"
                             : "owned client exited; cleanup=" + cleanupError,
        cleanupError.empty() && normalExit ? "INFO" : "ERROR");
}

void Session::execute(const Command& command) {
    if (command.kind == T7NB_OPERATION_CHECK) executeCheck(command);
    else if (command.kind == T7NB_OPERATION_START) executeStart(command);
    else executeStop(command);
}

void Session::executeCheck(const Command& command) {
    if (cancelled(command.id)) throw std::runtime_error("check cancelled");
    Config config;
    config.outputDevice = command.outputDevice;
    config.ports[0] = 1; config.ports[1] = 2; config.ports[2] = 3;
    bootstrap_.check(fs::path(wide(command.clientDirectory)), config, [this](std::string line) { log(line); });
    log("client preflight passed");
}

void Session::executeStart(const Command& command) {
    if (cancelled(command.id)) throw std::runtime_error("start cancelled");
    {
        std::lock_guard<std::mutex> lock(mutex_);
        graphics_.state = T7NB_GRAPHICS_UNAVAILABLE;
        ++graphics_.revision; graphicsQueued_ = false;
        audio_.state = T7NB_AUDIO_UNAVAILABLE;
        ++audio_.revision; audioQueued_ = false;
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
    config.skipStartupAnimation = command.skipStartupAnimation;
    config.outputDevice = command.outputDevice;
    config.ports[0] = config.ports[1] = config.ports[2] = 0;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7NB_STATE_STARTING_RUNTIME;
        snapshot_.phase = "runtime-ready-wait";
    }
    server_ = std::make_unique<Server>();
    const auto packageRoot = fs::path(wide(packageRoot_));
    server_->start(config, packageRoot, localDataRoot(), [this, id = command.id] { return cancelled(id); });
    if (cancelled(command.id)) throw std::runtime_error("start cancelled");
    auto actual = server_->boundConfig();
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.ports[0] = actual.ports[0]; snapshot_.ports[1] = actual.ports[1]; snapshot_.ports[2] = actual.ports[2];
        snapshot_.state = T7NB_STATE_STARTING_CLIENT;
        snapshot_.phase = "client-start";
    }
    bootstrap_.launch(fs::path(wide(command.clientDirectory)), actual,
        [this](std::string line) { log(line); },
        [this, id = command.id] { return cancelled(id); },
        [this, id = command.id] {
            setState(T7NB_STATE_ADAPTING_CLIENT, T7NB_OPERATION_START, id, "client-adaptation");
        },
        [this](std::string line) { log(line, "WARNING"); });
    if (cancelled(command.id)) throw std::runtime_error("start cancelled");
    if (!server_->running()) {
        auto runtime = server_->snapshot();
        throw std::runtime_error(runtime.error.empty() ? "runtime stopped during client launch" : runtime.error);
    }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7NB_STATE_RUNNING;
        snapshot_.phase = "running";
        snapshot_.revision = ++revisionCounter_;
    }
    log("session running");
}

void Session::executeStop(const Command& command) {
    (void)command;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7NB_STATE_STOPPING_CLIENT;
        snapshot_.phase = "client-stop";
    }
    bootstrap_.stop();
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7NB_STATE_STOPPING_RUNTIME;
        snapshot_.phase = "runtime-stop";
    }
    std::vector<std::string> journalLines;
    if (server_) {
        server_->stop();
        journalLines = server_->lines();
        server_.reset();
    }
    if (winsockStarted_) { WSACleanup(); winsockStarted_ = false; }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.ports[0] = snapshot_.ports[1] = snapshot_.ports[2] = 0;
    }
    for (const auto& line : journalLines) logRecord(line);
}

void Session::setFailure(uint64_t operationId, uint32_t code, const std::string& message) {
    const bool wasCancelled = cancelled(operationId);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.errorCode = wasCancelled ? T7NB_ERROR_CANCELLED : code;
        snapshot_.state = wasCancelled ? T7NB_STATE_CANCELLING : T7NB_STATE_FAILED_CLEANING;
        snapshot_.flags &= ~1u;
        snapshot_.phase = wasCancelled ? "cancelling" : "failed-cleaning";
    }
    log(wasCancelled ? "operation cancelled: " + message : "operation failed: " + message, wasCancelled ? "INFO" : "ERROR");
    auto cleanupError = cleanup();
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto found = operations_.find(operationId);
        if (found != operations_.end()) {
            found->second.errorCode = cleanupError.empty()
                ? (wasCancelled ? T7NB_ERROR_CANCELLED : code) : T7NB_ERROR_CLEANUP;
            found->second.error = message;
            if (!cleanupError.empty()) found->second.error += "; cleanup=" + cleanupError;
        }
        if (wasCancelled && cleanupError.empty()) {
            snapshot_.state = T7NB_STATE_IDLE;
            snapshot_.errorCode = T7NB_ERROR_CANCELLED;
            snapshot_.flags |= 1u;
            snapshot_.phase = "idle";
        } else if (cleanupError.empty()) {
            snapshot_.state = T7NB_STATE_FAILED;
            snapshot_.flags |= 1u;
            snapshot_.phase = "failed";
        } else {
            snapshot_.state = T7NB_STATE_FAILED_CLEANING;
            snapshot_.flags &= ~1u;
            snapshot_.errorCode = T7NB_ERROR_CLEANUP;
            snapshot_.phase = "failed-cleaning";
        }
        if (found != operations_.end())
            finishOperationLocked(operationId, wasCancelled ? T7NB_OPERATION_CANCELLED : T7NB_OPERATION_FAILED);
    }
}

std::string Session::cleanup() noexcept {
    std::string errors;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7NB_STATE_STOPPING_CLIENT;
        snapshot_.phase = "client-stop";
    }
    try { bootstrap_.stop(); }
    catch (const std::exception& error) { errors = error.what(); }
    catch (...) { errors = "non-standard client cleanup error"; }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.state = T7NB_STATE_STOPPING_RUNTIME;
        snapshot_.phase = "runtime-stop";
    }
    try { if (server_) { server_->stop(); server_.reset(); } }
    catch (const std::exception& error) { if (!errors.empty()) errors += "; "; errors += error.what(); }
    catch (...) { if (!errors.empty()) errors += "; "; errors += "non-standard server cleanup error"; }
    if (winsockStarted_) { WSACleanup(); winsockStarted_ = false; }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.ports[0] = snapshot_.ports[1] = snapshot_.ports[2] = 0;
    }
    return errors;
}

void Session::log(const std::string& text, const char* level) {
    logRecord(formatDiagnosticRecord(text, level, "NativeBridge"));
}

void Session::logRecord(const std::string& record) {
    std::string writeFailure;
    try {
        appendNativeLog(localDataRoot() / "logs", record);
    } catch (const std::exception& error) {
        writeFailure = formatDiagnosticRecord(std::string("日志文件写入失败：") + error.what(), "WARNING", "NativeBridge");
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
