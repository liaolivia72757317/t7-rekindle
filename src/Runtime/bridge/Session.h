#pragma once

#include "../launcher/Bootstrap.h"
#include "../server/Server.h"
#include "T7NativeBridge.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace t7::bridge {

struct Snapshot {
    uint32_t state = T7NB_STATE_IDLE;
    uint32_t operation = T7NB_OPERATION_NONE;
    uint64_t operationId = 0;
    uint64_t revision = 0;
    uint16_t ports[3] = {};
    uint32_t errorCode = 0;
    uint32_t flags = 1;
    uint64_t logCursor = 0;
    std::string phase;
};

struct Operation {
    uint64_t id = 0;
    uint32_t kind = T7NB_OPERATION_NONE;
    uint32_t status = T7NB_OPERATION_UNKNOWN;
    uint32_t errorCode = 0;
    std::string error;
};

struct LogRecord {
    uint64_t cursor = 0;
    std::string text;
};

class Session final : public std::enable_shared_from_this<Session> {
public:
    static std::shared_ptr<Session> create(std::string packageRoot);
#if defined(T7_NATIVE_BRIDGE_TESTING)
    // Internal test seam. It is compiled only into native test hosts and is
    // deliberately absent from the exported C ABI.
    static std::shared_ptr<Session> createForTest(
        std::string packageRoot, Bootstrap::TestAdapter adapter,
        std::function<void()> afterOperation = {});
    bool waitForWorkerForTest(std::chrono::milliseconds timeout);
#endif
    ~Session();
    void requestClose() noexcept;

    int32_t submit(uint32_t kind, std::string clientDirectory, uint64_t& operationId,
                   std::string playerName = u8"吃我一记流星锤");
    int32_t cancel(uint64_t operationId);
    int32_t snapshot(Snapshot& result) const;
    int32_t operation(uint64_t operationId, Operation& result) const;
    int32_t error(uint64_t operationId, std::string& result, uint32_t& errorCode) const;
    int32_t readLogs(uint64_t& cursor, uint8_t* buffer, uint32_t capacity,
                     uint32_t& required, uint32_t& flags);

private:
    struct Command { uint64_t id; uint32_t kind; std::string clientDirectory; std::string playerName; };

    explicit Session(std::string packageRoot, Bootstrap::TestAdapter adapter = {});
    void startWorker();
    void workerLoop();
    void finishOperationLocked(uint64_t operationId, uint32_t status);
    void execute(const Command& command);
    void executeCheck(const Command& command);
    void executeStart(const Command& command);
    void executeStop(const Command& command);
    void monitorClient();
    void setFailure(uint64_t operationId, uint32_t code, const std::string& message);
    std::string cleanup() noexcept;
    void log(const std::string& text, const char* level = "INFO");
    bool cancelled(uint64_t operationId) const;
    void setState(uint32_t state, uint32_t operation, uint64_t operationId, const std::string& phase);

    const std::string packageRoot_;
    mutable std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<Command> commands_;
    std::map<uint64_t, Operation> operations_;
    std::deque<LogRecord> logs_;
    uint64_t nextOperation_ = 1;
    uint64_t nextLog_ = 1;
    uint64_t earliestLog_ = 1;
    uint64_t revisionCounter_ = 0;
    bool closing_ = false;
    bool workerExited_ = false;
#if defined(T7_NATIVE_BRIDGE_TESTING)
    std::function<void()> afterOperationForTest_;
#endif
    Snapshot snapshot_;
    std::atomic<uint64_t> cancelOperation_{0};
    std::unique_ptr<Server> server_;
    Bootstrap bootstrap_;
    bool winsockStarted_ = false;
};

}
