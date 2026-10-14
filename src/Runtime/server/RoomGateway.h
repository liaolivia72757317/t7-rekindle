#pragma once
#include "../core/Common.h"
#include <condition_variable>
#include <chrono>
#include <deque>
#include <functional>
#include <mutex>
#include <map>
#include <thread>
#include <atomic>

namespace t7 {
struct GatewayEffect {
    uint64_t connection = 0;
    std::string op, requestId, request;
};

// One original logic connection owns one server WebSocket connection.
class RoomGateway {
public:
    using Receiver = std::function<bool(Bytes)>;
    using Failure = std::function<void()>;
    RoomGateway(const RoomGatewaySettings& settings, Receiver receiver, Failure failure);
    ~RoomGateway();
    void requestStop();
    uint32_t latencyMs() const noexcept { return latencyMs_.load(); }
    struct Batch {
        RoomGateway& owner;
        std::unique_lock<std::mutex> lock;
        std::deque<GatewayEffect> sends;
        std::string acknowledge;
        void commit() noexcept;
    };
    Batch prepare(const std::vector<GatewayEffect>& effects);
    static std::shared_ptr<const RoomGatewaySettings> load(const fs::path& path);
private:
    RoomGatewaySettings settings_;
    Receiver receiver_;
    Failure failure_;
    std::mutex mutex_;
    std::condition_variable changed_;
    bool stopping_ = false;
    std::deque<GatewayEffect> sends_;
    struct PendingRequest {
        std::chrono::steady_clock::time_point sentAt;
        bool latencyProbe = false;
    };
    std::map<std::string, PendingRequest> active_;
    std::atomic<uint32_t> latencyMs_{UINT32_MAX};
    std::thread worker_;
    void run();
};
}
