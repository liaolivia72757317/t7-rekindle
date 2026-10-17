#pragma once
#include "../../../src/Runtime/core/Common.h"
#include <chrono>
#include <algorithm>

namespace t7r::detail {
using Clock = std::chrono::steady_clock;
using TimePoint = Clock::time_point;
inline std::string timerName(const std::string& id) {
    const auto colon = id.find(':', id.rfind("match:", 0) == 0 ? 6 : 0);
    return id.substr(colon == std::string::npos ? 0 : colon + 1);
}
inline bool animationTimer(const std::string& id) {
    const auto name = timerName(id);
    for (const auto* prefix : {"battle-action-phase", "bot-action-phase", "bot-release", "combat-hurt-"})
        if (name.rfind(prefix, 0) == 0) return true;
    return false;
}
inline bool decisionTimer(const std::string& id) {
    const auto name = timerName(id);
    return name == "actors-pulse" || name == "actors-decision"
        || name == "bot-think" || name.rfind("bot-think-", 0) == 0;
}
inline TimePoint timerDeadline(const std::string& id, TimePoint dispatched, TimePoint committed, int64_t delay) {
    return (animationTimer(id) ? committed : dispatched) + std::chrono::milliseconds(delay);
}

class ServerClock {
public:
    ServerClock() : ServerClock(GetTickCount64(), Clock::now()) {}
    ServerClock(uint64_t uptime, TimePoint origin) : uptime_(uptime), origin_(origin) {}
    uint64_t millis(TimePoint now = Clock::now()) const {
        if (now < origin_) throw std::runtime_error("monotonic clock moved backwards");
        return uptime_ + static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(now - origin_).count());
    }
private:
    uint64_t uptime_;
    TimePoint origin_;
};
inline TimePoint nextPoll(TimePoint deadline, TimePoint now) {
    constexpr auto period = std::chrono::milliseconds(5);
    return deadline > now ? deadline : deadline + period * ((now - deadline) / period + 1);
}
inline int64_t relativeWait(TimePoint deadline, TimePoint now) {
    auto ticks = std::chrono::ceil<std::chrono::duration<int64_t, std::ratio<1, 10000000>>>(deadline - now).count();
    return -(std::max)(int64_t{1}, ticks);
}

class ServerSignals {
public:
    ServerSignals() {
        try {
            handles_[0] = CreateEventW(nullptr, TRUE, FALSE, nullptr);
            check(handles_[0] != nullptr, "create stop event");
            for (size_t i = 1; i <= 2; ++i) {
                handles_[i] = CreateEventW(nullptr, FALSE, FALSE, nullptr);
                check(handles_[i] != nullptr, "create work event");
                handles_[i + 2] = CreateWaitableTimerExW(nullptr, nullptr,
                    CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
                check(handles_[i + 2] != nullptr, "create high resolution timer");
            }
        } catch (...) { close(); throw; }
    }
    ~ServerSignals() { close(); }
    ServerSignals(const ServerSignals&) = delete;
    ServerSignals& operator=(const ServerSignals&) = delete;
    void stop() { signal(0); }
    void business() { signal(1); }
    void io() { signal(2); }
    bool waitBusiness(TimePoint deadline) { return wait(1, deadline); }
    bool waitIo(TimePoint deadline) { return wait(2, deadline); }
private:
    HANDLE handles_[5]{};
    static void check(bool success, const char* operation) {
        if (!success) throw std::runtime_error(std::string(operation) + " Win32=" + std::to_string(GetLastError()));
    }
    void close() noexcept { for (auto& handle : handles_) { if (handle) CloseHandle(handle); handle = nullptr; } }
    void signal(size_t index) { check(SetEvent(handles_[index]) != FALSE, "signal server event"); }
    bool wait(size_t index, TimePoint deadline) {
        auto timer = handles_[index + 2];
        if (deadline == TimePoint::max()) check(CancelWaitableTimer(timer) != FALSE, "cancel server timer");
        else {
            LARGE_INTEGER due{}; due.QuadPart = relativeWait(deadline, Clock::now());
            check(SetWaitableTimer(timer, &due, 0, nullptr, nullptr, FALSE) != FALSE, "arm server timer");
        }
        HANDLE waits[]{handles_[0], handles_[index], timer};
        auto result = WaitForMultipleObjects(3, waits, FALSE, INFINITE);
        check(result < WAIT_OBJECT_0 + 3, "wait for server work");
        return result != WAIT_OBJECT_0;
    }
};
}
