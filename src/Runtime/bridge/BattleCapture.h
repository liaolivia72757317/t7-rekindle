#pragma once
#include "../core/WorldSnapshot.h"
#include "../core/Common.h"
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>
#include <atomic>

namespace t7r::bridge {

// Opt-in development trace. The client and its resource directory are never output targets.
class BattleCapture final {
public:
    BattleCapture(const fs::path& output, const fs::path& clientDirectory, const fs::path& packageRoot);
    ~BattleCapture();
    BattleCapture(const BattleCapture&) = delete;
    BattleCapture& operator=(const BattleCapture&) = delete;
    void record(const WorldSnapshot& world);
    void disable();
    void stop();

private:
    struct Batch {
        WorldSnapshot world;
        uint64_t worldBatchId = 0, capturedAt = 0;
        size_t bytes = 0;
    };
    HANDLE file_ = INVALID_HANDLE_VALUE;
    std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<Batch> pending_;
    std::thread thread_;
    size_t queuedBytes_ = 0;
    uint64_t epoch_ = 0, batchId_ = 0;
    bool stopping_ = false;
    std::atomic<bool> coverageLost_{false};
    std::string failure_;
    void run() noexcept;
    void write(const std::string& line);
};

}
