#pragma once
#include "PythonHost.h"
#include "Journal.h"
#include "ServerTiming.h"
#include "RunDirectory.h"
#include <map>
#include <deque>
#include <atomic>
#include <memory>
#include <condition_variable>
#include <functional>

namespace t7 {
struct ConnectionSnapshot {
    uint64_t id, ageMs, idleMs, received, sent, sequence, receivedMessages, sentMessages, heartbeatAgeMs;
    size_t queuedSend;
    std::string role;
    bool authenticated;
};
struct ServerSnapshot {
    std::string status = "stopped", version, candidate, error, phase;
    size_t connections = 0, queuedEvents = 0, queuedBytes = 0, timers = 0;
    uint64_t started = 0, completedControls = 0, reloadMs = 0;
    uint64_t ioScans = 0, sendBlocked = 0, sendBudget = 0;
    std::vector<ConnectionSnapshot> clients;
};
class Server {
public:
    Server() = default;
    ~Server();
    void start(const Config& config, const fs::path& root, const fs::path& writableRoot = {}, const std::function<bool()>& cancelled = {});
    void stop();
    // start() waits for this condition before returning.  The method is kept
    // public for the bridge worker and tests that need an explicit readiness
    // assertion without touching socket or Python state.
    bool waitReady(std::chrono::milliseconds timeout, const std::function<bool()>& cancelled = {});
    Config boundConfig() const;
    bool running() const;
    void command(const std::string& name);
    ServerSnapshot snapshot();
    std::vector<std::string> lines();
private:
    using TimePoint = detail::TimePoint;
    struct PendingWrite { Bytes bytes; size_t offset = 0; std::string version, reason; };
    struct Session {
        SOCKET socket = INVALID_SOCKET; std::string role; bool authenticated = false;
        Bytes receive; std::deque<PendingWrite> send; size_t queued = 0;
        uint64_t accepted = 0, lastReceive = 0, received = 0, sent = 0, sequence = 0;
        uint64_t receivedMessages = 0, sentMessages = 0, heartbeatAt = 0;
        TimePoint blockedUntil{};
    };
    struct Timer { TimePoint due; uint64_t connection; std::string event; };
    struct QueuedEvent : Event { TimePoint queuedAt{}, due = TimePoint::max(); };
    using Timers = std::map<std::string, Timer>;
    Config config_;
    fs::path root_, writableRoot_, run_;
    std::unique_ptr<RunDirectory> runDirectory_;
    SOCKET listeners_[3]{INVALID_SOCKET, INVALID_SOCKET, INVALID_SOCKET};
    std::map<uint64_t, Session> sessions_;
    std::deque<QueuedEvent> events_;
    size_t eventBytes_ = 0;
    uint64_t nextId_ = 1, nextEvent_ = 1, lastFirst_ = 0;
    mutable std::mutex mutex_;
    std::condition_variable stateChanged_;
    std::atomic<bool> stopping_{true};
    bool ready_ = false;
    bool ioReady_ = false;
    std::string startError_;
    Config boundConfig_;
    detail::ServerClock clock_;
    std::unique_ptr<detail::ServerSignals> signals_;
    ServerSnapshot snapshot_;
    std::vector<std::string> lastLines_;
    std::unique_ptr<Journal> journal_;
    std::thread io_, business_;
    void networkLoop();
    void acceptConnections();
    bool receiveSession(uint64_t id, Session& session);
    void receiveFrame(uint64_t id, Session& session, const Bytes& frame);
    bool sendSession(uint64_t id, Session& session, TimePoint retryAt);
    bool scanNetwork(TimePoint retryAt);
    void businessLoop();
    bool nextEvent(Timers& timers, QueuedEvent& event, std::vector<uint64_t>& live);
    void processControl(PythonHost& python, const Event& event, std::string& state);
    void dispatch(PythonHost& python, const QueuedEvent& event, const std::vector<uint64_t>& live,
                  std::string& state, Timers& timers);
    void commit(Transition& result, const QueuedEvent& event, const std::string& version,
                std::string phase, TimePoint now, double callbackMs, std::string& state, Timers& timers, bool& committed);
    void fail(const std::exception& error);
    void queue(Event event, TimePoint due = TimePoint::max());
    void disconnect(uint64_t id, const std::string& reason);
};
}
