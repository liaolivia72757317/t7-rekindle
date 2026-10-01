#include "Server.h"
#include "SendQueue.h"
#include "../core/Protocol.h"
#include <algorithm>

namespace t7 {
namespace {
double elapsedMs(detail::TimePoint start, detail::TimePoint end) {
    return std::chrono::duration<double, std::milli>(end - start).count();
}

fs::path defaultWritableRoot() {
    wchar_t buffer[32768]{};
    auto length = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer, static_cast<DWORD>(std::size(buffer)));
    if (length && length < std::size(buffer)) return fs::path(buffer) / L"T7-Rekindle";
    wchar_t temporary[MAX_PATH]{};
    auto temporaryLength = GetTempPathW(static_cast<DWORD>(std::size(temporary)), temporary);
    if (!temporaryLength || temporaryLength >= std::size(temporary))
        throw std::runtime_error("writable session root unavailable");
    return fs::path(temporary) / L"T7-Rekindle";
}
}
Server::~Server() { stop(); }
void Server::start(const Config& config, const fs::path& root, const fs::path& writableRoot, const std::function<bool()>& cancelled) {
    if (!stopping_) throw std::runtime_error("server is already running");
    stop();
    {
        std::lock_guard<std::mutex> lock(mutex_);
        lastLines_.clear();
    }
    validateConfig(config); config_ = config; boundConfig_ = config; root_ = root;
    // The package root may be installed read-only.  Keep generated revisions
    // and wire journals outside it unless the caller explicitly supplies a
    // writable session root.
    writableRoot_ = writableRoot.empty() ? defaultWritableRoot() : writableRoot;
    try {
        runDirectory_ = std::make_unique<RunDirectory>(writableRoot_ / "data");
        run_ = runDirectory_->path();
        RunDirectory::prune(run_.parent_path());
        journal_ = std::make_unique<Journal>(run_ / "wire");
        signals_ = std::make_unique<detail::ServerSignals>();
        for (int i = 0; i < 3; ++i) {
            auto socket = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
            if (socket == INVALID_SOCKET) throw std::runtime_error("socket creation failed");
            listeners_[i] = socket;
            BOOL exclusive = TRUE;
            if (setsockopt(socket, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<char*>(&exclusive), sizeof(exclusive)))
                throw std::runtime_error("exclusive bind setup failed");
            sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(config.ports[i]);
            inet_pton(AF_INET, config.bindAddress.c_str(), &address.sin_addr);
            if (bind(socket, reinterpret_cast<sockaddr*>(&address), sizeof(address)) || listen(socket, 16))
                throw std::runtime_error("listen failed on port " + std::to_string(config.ports[i]) + " WSA=" + std::to_string(WSAGetLastError()));
            sockaddr_in actual{}; int actualSize = sizeof(actual);
            if (getsockname(socket, reinterpret_cast<sockaddr*>(&actual), &actualSize) != 0 || !ntohs(actual.sin_port))
                throw std::runtime_error("getsockname failed after listener bind WSA=" + std::to_string(WSAGetLastError()));
            boundConfig_.ports[i] = ntohs(actual.sin_port);
            u_long nonblocking = 1;
            if (ioctlsocket(socket, FIONBIO, &nonblocking)) throw std::runtime_error("nonblocking setup failed");
        }
        { std::lock_guard<std::mutex> lock(mutex_); snapshot_ = {}; snapshot_.status = "starting"; snapshot_.started = clock_.millis(); ready_ = false; ioReady_ = false; startError_.clear(); }
        stopping_ = false;
        io_ = std::thread(&Server::networkLoop, this);
        business_ = std::thread(&Server::businessLoop, this);
        if (!waitReady(std::chrono::seconds(30), cancelled)) {
            std::string error;
            { std::lock_guard<std::mutex> lock(mutex_); error = startError_.empty() ? "runtime readiness timeout" : startError_; }
            stop();
            throw std::runtime_error(error);
        }
    } catch (...) { stop(); throw; }
}
void Server::stop() {
    { std::lock_guard<std::mutex> lock(mutex_); stopping_ = true; ready_ = false; ioReady_ = false; snapshot_.status = "stopping"; stateChanged_.notify_all(); }
    if (signals_) {
        try { signals_->stop(); }
        catch (const std::exception& e) { std::lock_guard<std::mutex> lock(mutex_); snapshot_.error = e.what(); }
    }
    if (business_.joinable()) business_.join();
    if (io_.joinable()) io_.join();
    for (auto& socket : listeners_) { if (socket != INVALID_SOCKET) closesocket(socket); socket = INVALID_SOCKET; }
    std::unique_ptr<Journal> journal;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto& item : sessions_) closesocket(item.second.socket);
        sessions_.clear(); events_.clear(); eventBytes_ = 0; snapshot_.status = "stopped";
        // Do not leave callers with ports from a listener that has already
        // been closed (including a partially failed start transaction).
        boundConfig_.ports[0] = boundConfig_.ports[1] = boundConfig_.ports[2] = 0;
        stateChanged_.notify_all();
        journal = std::move(journal_);
        signals_.reset();
    }
    if (journal) {
        auto lines = journal->lines();
        journal.reset();
        std::lock_guard<std::mutex> lock(mutex_);
        lastLines_ = std::move(lines);
    }
    if (runDirectory_) {
        try { runDirectory_->finish(); }
        catch (const std::exception& error) {
            std::lock_guard<std::mutex> lock(mutex_);
            snapshot_.error = std::string("history retention failed: ") + error.what();
            lastLines_.push_back(snapshot_.error);
            if (lastLines_.size() > 1000) lastLines_.erase(lastLines_.begin());
        }
        runDirectory_.reset();
    }
}
bool Server::waitReady(std::chrono::milliseconds timeout, const std::function<bool()>& cancelled) {
    std::unique_lock<std::mutex> lock(mutex_);
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while ((!ready_ || !ioReady_) && startError_.empty() && !stopping_) {
        if (cancelled && cancelled()) {
            startError_ = "runtime readiness cancelled";
            stateChanged_.notify_all();
            return false;
        }
        const auto now = std::chrono::steady_clock::now();
        if (now >= deadline) break;
        const auto next = (std::min)(deadline, now + std::chrono::milliseconds(50));
        stateChanged_.wait_until(lock, next);
    }
    return ready_ && ioReady_;
}
Config Server::boundConfig() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return boundConfig_;
}
bool Server::running() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return ready_ && ioReady_ && !stopping_ && snapshot_.status == "running";
}
void Server::fail(const std::exception& error) {
    std::lock_guard<std::mutex> lock(mutex_);
    stopping_ = true;
    snapshot_.error = error.what(); snapshot_.status = "failed"; startError_ = error.what(); ready_ = false;
    stateChanged_.notify_all();
    try { if (signals_) signals_->stop(); }
    catch (const std::exception& e) { snapshot_.error += std::string("; ") + e.what(); }
}
void Server::queue(Event event, TimePoint due) {
    // 调用者持有 mutex；读取 socket 前应用背压。
    event.id = nextEvent_++;
    QueuedEvent queued; static_cast<Event&>(queued) = std::move(event);
    queued.queuedAt = detail::Clock::now(); queued.due = due;
    auto bytes = queued.body.size() + queued.serialized.size() + 256;
    events_.push_back(std::move(queued)); eventBytes_ += bytes;
    signals_->business();
}
void Server::command(const std::string& name) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (stopping_ || snapshot_.status != "running") throw std::runtime_error("server is not running");
    if (events_.size() >= 1024) throw std::runtime_error("event queue full");
    Event event; event.type = "control"; event.name = name;
    if (name.rfind("diagnostic:", 0) == 0) {
        event.type = "operator"; event.name = name.substr(10);
        if (sessions_.empty()) throw std::runtime_error("diagnostic command requires a live session");
        event.connection = sessions_.begin()->first;
    }
    queue(std::move(event));
}
ServerSnapshot Server::snapshot() {
    std::lock_guard<std::mutex> lock(mutex_); auto result = snapshot_;
    result.connections = sessions_.size(); result.queuedEvents = events_.size(); result.queuedBytes = eventBytes_;
    auto now = clock_.millis();
    for (const auto& pair : sessions_) {
        const auto& s = pair.second;
        result.clients.push_back({pair.first, now - s.accepted, now - s.lastReceive, s.received, s.sent, s.sequence,
            s.receivedMessages, s.sentMessages, s.heartbeatAt ? now - s.heartbeatAt : UINT64_MAX, s.queued, s.role, s.authenticated});
    }
    return result;
}
std::vector<std::string> Server::lines() {
    std::lock_guard<std::mutex> lock(mutex_);
    return journal_ ? journal_->lines() : lastLines_;
}
void Server::disconnect(uint64_t id, const std::string& reason) {
    auto it = sessions_.find(id); if (it == sessions_.end()) return;
    closesocket(it->second.socket); sessions_.erase(it);
    Event event; event.type = "closed"; event.connection = id; queue(std::move(event));
    journal_->add("CONTROL", id, snapshot_.version, "connection-closed: " + reason);
}
void Server::acceptConnections() {
    const char* roles[] = {"login", "logic", "instance"};
    for (int i = 0; i < 3 && sessions_.size() < 32 && events_.size() < 990; ++i) {
        SOCKET socket = accept(listeners_[i], nullptr, nullptr);
        if (socket == INVALID_SOCKET) {
            if (WSAGetLastError() != WSAEWOULDBLOCK) throw std::runtime_error("socket accept failed WSA=" + std::to_string(WSAGetLastError()));
            continue;
        }
        u_long nonblocking = 1; BOOL noDelay = TRUE;
        if (ioctlsocket(socket, FIONBIO, &nonblocking) ||
            setsockopt(socket, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<char*>(&noDelay), sizeof(noDelay))) {
            auto error = WSAGetLastError(); closesocket(socket);
            journal_->add("ERROR", 0, "native", "accepted socket setup failed WSA=" + std::to_string(error)); continue;
        }
        uint64_t id = nextId_++;
        Session session; session.socket = socket; session.role = roles[i]; session.accepted = session.lastReceive = clock_.millis();
        sessions_.emplace(id, std::move(session));
        Event event; event.type = "connected"; event.role = roles[i]; event.connection = id; queue(std::move(event));
    }
}
void Server::receiveFrame(uint64_t id, Session& session, const Bytes& frame) {
    Event event; event.connection = id; event.role = session.role;
    if (!session.authenticated) {
        if (frame[2] != 3) throw std::runtime_error("connection must begin with AUTH");
        session.authenticated = true; auto reply = changeKeyFrame();
        session.queued += reply.size(); session.send.push_back({std::move(reply), 0, "native", "method3-key"});
        event.type = "authenticated";
    } else {
        if (frame[2] == 13) throw std::runtime_error("client CLOSE");
        auto clear = decryptMethod3(frame);
        if (clear.size() < 14) throw std::runtime_error("short SH_PACKAGE");
        event.type = "message"; event.sequence = be32(clear.data()); session.sequence = event.sequence;
        event.command = static_cast<uint16_t>(clear[4] * 256 + clear[5]); event.serverTime = be64(clear.data() + 6);
        ++session.receivedMessages;
        if (event.command == 0x1D) session.heartbeatAt = clock_.millis();
        event.body.assign(clear.begin() + 14, clear.end());
    }
    queue(std::move(event));
}
bool Server::receiveSession(uint64_t id, Session& session) {
    if (!session.authenticated && clock_.millis() - session.accepted > 10000) throw std::runtime_error("AUTH timeout");
    if (events_.size() >= 1024 || eventBytes_ >= 8 * 1024 * 1024) return false;
    unsigned char bytes[16384];
    int received = recv(session.socket, reinterpret_cast<char*>(bytes), sizeof(bytes), 0);
    if (!received) throw std::runtime_error("peer EOF");
    if (received < 0 && WSAGetLastError() != WSAEWOULDBLOCK) throw std::runtime_error("socket read error");
    if (received > 0) {
        session.received += received; session.lastReceive = clock_.millis();
        Bytes raw(bytes, bytes + received);
        session.receive.insert(session.receive.end(), raw.begin(), raw.end());
        journal_->add("C2S", id, snapshot_.version, "socket-read", std::move(raw), config_.captureWire);
        if (session.receive.size() > MAX_FRAME * 2) throw std::runtime_error("receive buffer overflow");
    }
    Bytes frame; size_t count = 0;
    while (count < 32 && events_.size() < 1024 && eventBytes_ < 8 * 1024 * 1024 && takeFrame(session.receive, frame)) {
        receiveFrame(id, session, frame); ++count;
    }
    return count == 32 && !session.receive.empty();
}
bool Server::sendSession(uint64_t id, Session& session, TimePoint retryAt) {
    if (detail::Clock::now() < session.blockedUntil) return false;
    auto result = detail::drainWrites(session.send, [&](const unsigned char* bytes, size_t size) {
        auto sent = ::send(session.socket, reinterpret_cast<const char*>(bytes), static_cast<int>(size), 0);
        return detail::SendAttempt{sent, sent < 0 ? WSAGetLastError() : 0};
    }, [&](const PendingWrite& pending, size_t sent) {
        Bytes raw(pending.bytes.begin() + pending.offset, pending.bytes.begin() + pending.offset + sent);
        journal_->add("S2C", id, pending.version, pending.reason, std::move(raw), config_.captureWire);
        session.queued -= sent; session.sent += sent;
        if (pending.offset + sent == pending.bytes.size()) ++session.sentMessages;
    });
    if (result == detail::DrainResult::Blocked) { session.blockedUntil = retryAt; ++snapshot_.sendBlocked; }
    if (result == detail::DrainResult::Budget) { ++snapshot_.sendBudget; return true; }
    return false;
}
bool Server::scanNetwork(TimePoint retryAt) {
    std::lock_guard<std::mutex> lock(mutex_);
    ++snapshot_.ioScans; acceptConnections();
    std::vector<uint64_t> order;
    for (const auto& pair : sessions_) order.push_back(pair.first);
    auto first = std::upper_bound(order.begin(), order.end(), lastFirst_);
    std::rotate(order.begin(), first, order.end());
    if (!order.empty()) lastFirst_ = order.front();
    std::vector<std::pair<uint64_t, std::string>> closed; bool again = false;
    for (auto id : order) {
        auto& session = sessions_.at(id);
        try {
            again = receiveSession(id, session) || again;
            again = sendSession(id, session, retryAt) || again;
        } catch (const std::exception& e) { closed.emplace_back(id, e.what()); }
    }
    for (const auto& pair : closed) disconnect(pair.first, pair.second);
    return again;
}
void Server::networkLoop() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        ioReady_ = true;
        stateChanged_.notify_all();
    }
    try {
        auto deadline = detail::Clock::now();
        while (!stopping_) {
            deadline = detail::nextPoll(deadline, detail::Clock::now());
            if (scanNetwork(deadline)) signals_->io();
            if (!signals_->waitIo(deadline)) break;
        }
    } catch (const std::exception& e) { fail(e); }
    {
        std::lock_guard<std::mutex> lock(mutex_);
        ioReady_ = false;
        stateChanged_.notify_all();
    }
}
bool Server::nextEvent(Timers& timers, QueuedEvent& event, std::vector<uint64_t>& live) {
    while (!stopping_) {
        auto deadline = TimePoint::max();
        {
            std::lock_guard<std::mutex> lock(mutex_);
            auto now = detail::Clock::now();
            for (auto it = timers.begin(); it != timers.end();) {
                if (it->second.due <= now && events_.size() < 1024 && eventBytes_ < 8 * 1024 * 1024) {
                    Event due; due.type = "timer"; due.connection = it->second.connection; due.serialized = it->second.event;
                    queue(std::move(due), it->second.due); it = timers.erase(it);
                } else { deadline = (std::min)(deadline, it->second.due); ++it; }
            }
            if (!events_.empty()) {
                bool pressured = events_.size() >= 1024 || eventBytes_ >= 8 * 1024 * 1024;
                event = std::move(events_.front()); events_.pop_front();
                eventBytes_ -= event.body.size() + event.serialized.size() + 256;
                for (const auto& pair : sessions_) live.push_back(pair.first);
                if (pressured) signals_->io();
                return true;
            }
        }
        if (!signals_->waitBusiness(deadline)) return false;
    }
    return false;
}
void Server::processControl(PythonHost& python, const Event& event, std::string& state) {
    auto started = clock_.millis();
    if (event.name == "check") {
        auto candidate = python.prepare(); std::lock_guard<std::mutex> lock(mutex_); snapshot_.candidate = candidate;
    } else if (event.name == "reload" || event.name == "rollback") {
        state = python.switchVersion(state, event.name == "rollback");
        auto version = python.version(); std::lock_guard<std::mutex> lock(mutex_);
        snapshot_.version = version; if (event.name == "reload") snapshot_.candidate.clear();
    } else throw std::runtime_error("unknown native control command");
    auto elapsed = clock_.millis() - started;
    { std::lock_guard<std::mutex> lock(mutex_); ++snapshot_.completedControls; snapshot_.reloadMs = elapsed; snapshot_.error.clear(); }
    journal_->add("CONTROL", 0, python.version(), "event=" + std::to_string(event.id) + " " + event.name + " passed in " + std::to_string(elapsed) + " ms");
    for (const auto& warning : python.diagnostics()) journal_->add("LOG-ERROR", 0, python.version(), warning);
}
void Server::commit(Transition& result, const QueuedEvent& event, const std::string& version,
                    std::string phase, TimePoint now, double callbackMs, std::string& state, Timers& timers, bool& committed) {
    std::vector<PendingWrite> writes;
    auto label = "event=" + std::to_string(event.id) + " ";
    for (const auto& message : result.send) writes.push_back({plainFrame(message.command, 0, message.body), 0, version, label + message.reason});
    std::lock_guard<std::mutex> lock(mutex_);
    if (stopping_) throw std::runtime_error("transition cancelled: server stopping");
    std::map<uint64_t, size_t> totals;
    for (size_t i = 0; i < result.send.size(); ++i) totals[result.send[i].connection] += writes[i].bytes.size();
    for (const auto& total : totals) {
        auto found = sessions_.find(total.first);
        if (found == sessions_.end() || found->second.queued + total.second > 4 * 1024 * 1024)
            throw std::runtime_error("transition rejected: dead target or send backpressure");
    }
    auto nextTimers = timers;
    for (const auto& change : result.timers) {
        if (change.delay < 0) nextTimers.erase(change.id);
        else {
            if (sessions_.find(change.connection) == sessions_.end()) throw std::runtime_error("timer target disconnected before commit");
            nextTimers[change.id] = {now + std::chrono::milliseconds(change.delay), change.connection, change.event};
        }
    }
    if (event.type == "closed") {
        for (auto it = nextTimers.begin(); it != nextTimers.end();) {
            if (it->second.connection == event.connection) it = nextTimers.erase(it); else ++it;
        }
    }
    if (nextTimers.size() > 1024) throw std::runtime_error("timer capacity exceeded");
    // 所有替换队列都在无异常提交边界之前分配。
    std::map<uint64_t, std::deque<PendingWrite>> nextSends;
    for (const auto& total : totals) nextSends.emplace(total.first, sessions_.at(total.first).send);
    for (size_t i = 0; i < result.send.size(); ++i) nextSends.at(result.send[i].connection).push_back(std::move(writes[i]));
    for (auto& replacement : nextSends) {
        auto& session = sessions_.at(replacement.first);
        session.send.swap(replacement.second); session.queued += totals.at(replacement.first);
    }
    state.swap(result.state); timers.swap(nextTimers); snapshot_.phase.swap(phase); snapshot_.timers = timers.size();
    committed = true;
    auto late = event.due == TimePoint::max() ? 0.0 : elapsedMs(event.due, event.queuedAt);
    journal_->add("COMMIT", event.connection, version, label + event.type + " command=" + std::to_string(event.command)
        + " sends=" + std::to_string(result.send.size()) + " queueMs=" + std::to_string(elapsedMs(event.queuedAt, now))
        + " callbackMs=" + std::to_string(callbackMs) + " timerLateMs=" + std::to_string(late));
    if (!writes.empty()) signals_->io();
    for (const auto& line : result.logs) journal_->add("BUSINESS", event.connection, version, label + line);
}
void Server::dispatch(PythonHost& python, const QueuedEvent& event, const std::vector<uint64_t>& live,
                      std::string& state, Timers& timers) {
    bool committed = false;
    try {
        if (event.type == "control") { processControl(python, event, state); return; }
        if (event.type != "closed" && std::find(live.begin(), live.end(), event.connection) == live.end()) return;
        auto now = detail::Clock::now();
        auto result = python.dispatch(event, state, python.context(boundConfig_, clock_.millis(now), live));
        auto callbackMs = elapsedMs(now, detail::Clock::now());
        commit(result, event, python.version(), python.phase(result.state), now, callbackMs, state, timers, committed);
    } catch (const std::exception& e) {
        if (committed) {
            fail(std::runtime_error("fatal after committed event=" + std::to_string(event.id) + ": " + e.what()));
            return;
        }
        if (stopping_) return;
        { std::lock_guard<std::mutex> lock(mutex_); snapshot_.error = e.what(); if (event.type == "control") ++snapshot_.completedControls; }
        journal_->add("ERROR", event.connection, python.version(), "event=" + std::to_string(event.id) + " " + e.what());
    }
}
void Server::businessLoop() {
    try {
        PythonHost python(root_, run_ / "revisions");
        std::string state = python.create(python.context(boundConfig_, clock_.millis(), {}));
        Timers timers;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (stopping_) { stateChanged_.notify_all(); return; }
            snapshot_.version = python.version(); snapshot_.status = "running";
            ready_ = true;
            stateChanged_.notify_all();
        }
        if (stopping_) return;
        while (!stopping_) {
            QueuedEvent event; std::vector<uint64_t> live;
            if (!nextEvent(timers, event, live)) break;
            dispatch(python, event, live, state, timers);
        }
    } catch (const std::exception& e) { fail(e); }
}
}
