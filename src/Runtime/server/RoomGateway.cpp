#include "RoomGateway.h"
#include <winhttp.h>
#include <wincrypt.h>
#pragma comment(lib, "crypt32.lib")
#include <array>
#include <algorithm>
#include <fstream>
#include <set>
#include <sstream>
#include <utility>

namespace t7 {
namespace {
using Clock = std::chrono::steady_clock;

struct Handle {
    HINTERNET value = nullptr;
    ~Handle() { if (value) WinHttpCloseHandle(value); }
};

// Context and operation buffers outlive the final HANDLE_CLOSING callbacks.
struct AsyncChannel {
    explicit AsyncChannel(std::condition_variable& eventWake) : wake(eventWake) {}
    std::condition_variable& wake;
    std::mutex mutex;
    std::condition_variable changed;
    std::vector<HINTERNET> handles;
    size_t closed = 0;
    bool sentRequest = false, headers = false, wrote = false, read = false, failed = false;
    DWORD readBytes = 0;
    WINHTTP_WEB_SOCKET_BUFFER_TYPE readType{};
    std::array<unsigned char, 8192> buffer{};

    static void CALLBACK callback(HINTERNET, DWORD_PTR context, DWORD status, void* info, DWORD length) noexcept {
        if (!context) return;
        auto& self = *reinterpret_cast<AsyncChannel*>(context);
        std::lock_guard<std::mutex> lock(self.mutex);
        switch (status) {
        case WINHTTP_CALLBACK_STATUS_SENDREQUEST_COMPLETE: self.sentRequest = true; break;
        case WINHTTP_CALLBACK_STATUS_HEADERS_AVAILABLE: self.headers = true; break;
        case WINHTTP_CALLBACK_STATUS_WRITE_COMPLETE: self.wrote = true; break;
        case WINHTTP_CALLBACK_STATUS_READ_COMPLETE:
            if (!info || length != sizeof(WINHTTP_WEB_SOCKET_STATUS)) { self.failed = true; break; }
            self.readBytes = static_cast<WINHTTP_WEB_SOCKET_STATUS*>(info)->dwBytesTransferred;
            self.readType = static_cast<WINHTTP_WEB_SOCKET_STATUS*>(info)->eBufferType;
            self.read = true;
            break;
        case WINHTTP_CALLBACK_STATUS_REQUEST_ERROR: self.failed = true; break;
        case WINHTTP_CALLBACK_STATUS_HANDLE_CLOSING: ++self.closed; break;
        }
        self.changed.notify_all();
        self.wake.notify_all();
    }

    void attach(HINTERNET handle) {
        if (!handle) throw std::runtime_error("room gateway handle unavailable");
        DWORD_PTR context = reinterpret_cast<DWORD_PTR>(this);
        if (!WinHttpSetOption(handle, WINHTTP_OPTION_CONTEXT_VALUE, &context, sizeof(context))) {
            WinHttpCloseHandle(handle);
            throw std::runtime_error("room gateway context unavailable");
        }
        if (WinHttpSetStatusCallback(handle, callback, WINHTTP_CALLBACK_FLAG_ALL_COMPLETIONS
                | WINHTTP_CALLBACK_FLAG_HANDLES, 0) == WINHTTP_INVALID_STATUS_CALLBACK) {
            WinHttpCloseHandle(handle);
            throw std::runtime_error("room gateway callback unavailable");
        }
        handles.push_back(handle);
    }

    ~AsyncChannel() {
        for (auto handle : handles) WinHttpCloseHandle(handle);
        std::unique_lock<std::mutex> lock(mutex);
        changed.wait(lock, [this] { return closed == handles.size(); });
    }

    void wait(bool& ready, const std::function<bool()>& cancelled) {
        const auto deadline = Clock::now() + std::chrono::seconds(8);
        std::unique_lock<std::mutex> lock(mutex);
        while (!ready && !failed && !cancelled() && Clock::now() < deadline)
            changed.wait_for(lock, std::chrono::milliseconds(20));
        if (!ready || failed || cancelled()) throw std::runtime_error("room gateway handshake failed");
    }
};

void checked(bool result) {
    if (!result) throw std::runtime_error("room gateway HTTP operation failed");
}
}

std::shared_ptr<const RoomGatewaySettings> RoomGateway::load(const fs::path& path) {
    if (!fs::exists(path)) return {};
    const auto bytes = readFile(path, 4096);
    if (std::find(bytes.begin(), bytes.end(), 0) != bytes.end()) throw std::runtime_error("invalid custom-room.ini");
    std::istringstream input(std::string(bytes.begin(), bytes.end()));
    auto settings = std::make_shared<RoomGatewaySettings>();
    std::set<std::string> keys;
    std::string line;
    while (std::getline(input, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty() || line.front() == '#' || line == "[custom-room]") continue;
        const auto equal = line.find('=');
        if (equal == std::string::npos) throw std::runtime_error("invalid custom-room.ini line");
        const auto key = line.substr(0, equal), value = line.substr(equal + 1);
        if (!keys.insert(key).second) throw std::runtime_error("duplicate custom-room.ini field");
        if (key == "accountId") settings->accountId = value;
        else if (key == "serverHost") settings->host = value;
        else if (key == "secure" && (value == "true" || value == "false")) settings->secure = value == "true";
        else if (key == "protectedTicket") {
            DWORD length = 0;
            if (!CryptStringToBinaryA(value.c_str(), static_cast<DWORD>(value.size()), CRYPT_STRING_BASE64, nullptr, &length, nullptr, nullptr))
                throw std::runtime_error("invalid protected room ticket");
            Bytes bytesTicket(length);
            if (!CryptStringToBinaryA(value.c_str(), static_cast<DWORD>(value.size()), CRYPT_STRING_BASE64, bytesTicket.data(), &length, nullptr, nullptr))
                throw std::runtime_error("invalid protected room ticket");
            DATA_BLOB encrypted{length, bytesTicket.data()}, decrypted{};
            if (!CryptUnprotectData(&encrypted, nullptr, nullptr, nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN, &decrypted))
                throw std::runtime_error("room ticket belongs to another Windows account");
            settings->ticket.assign(reinterpret_cast<const char*>(decrypted.pbData), decrypted.cbData);
            SecureZeroMemory(decrypted.pbData, decrypted.cbData); LocalFree(decrypted.pbData);
        }
        else if (key == "serverPort" && !value.empty() && value.size() <= 5
                && value.find_first_not_of("0123456789") == std::string::npos) {
            const auto port = std::stoul(value);
            if (!port || port > 65535) throw std::runtime_error("invalid custom-room server port");
            settings->port = static_cast<unsigned short>(port);
        } else throw std::runtime_error("invalid custom-room.ini field");
    }
    for (const auto* key : {"serverHost", "serverPort", "secure", "accountId"})
        if (!keys.count(key)) throw std::runtime_error("incomplete room gateway configuration");
    if (settings->secure != (keys.count("protectedTicket") != 0))
        throw std::runtime_error("room gateway ticket does not match transport mode");
    Config config; config.roomGateway = settings; validateConfig(config);
    return settings;
}

RoomGateway::RoomGateway(const RoomGatewaySettings& settings, Receiver receiver, Failure failure)
    : settings_(settings), receiver_(std::move(receiver)), failure_(std::move(failure)), worker_(&RoomGateway::run, this) {}
RoomGateway::~RoomGateway() {
    requestStop();
    if (worker_.joinable()) worker_.join();
}
void RoomGateway::requestStop() {
    std::lock_guard<std::mutex> lock(mutex_);
    stopping_ = true;
    latencyMs_ = UINT32_MAX;
    changed_.notify_all();
}
RoomGateway::Batch RoomGateway::prepare(const std::vector<GatewayEffect>& effects) {
    Batch batch{*this, std::unique_lock<std::mutex>(mutex_), sends_, {}};
    if (stopping_) throw std::runtime_error("room gateway is closed");
    for (const auto& effect : effects) {
        if (effect.op == "ack") {
            if (!batch.acknowledge.empty() || !active_.count(effect.requestId))
                throw std::runtime_error("room gateway correlation mismatch");
            batch.acknowledge = effect.requestId;
        } else if (effect.op != "send") throw std::runtime_error("room gateway correlation mismatch");
    }
    const auto retained = active_.size() - (batch.acknowledge.empty() ? 0 : 1);
    for (const auto& effect : effects) {
        if (effect.op == "send") {
            if (effect.requestId.empty() || effect.request.empty() || effect.request.size() > 4096
                    || batch.sends.size() + retained >= 8)
                throw std::runtime_error("room gateway send capacity exceeded");
            if (active_.count(effect.requestId) || std::any_of(batch.sends.begin(), batch.sends.end(),
                    [&](const GatewayEffect& queued) { return queued.requestId == effect.requestId; }))
                throw std::runtime_error("room gateway correlation mismatch");
            batch.sends.push_back(effect);
        }
    }
    return batch;
}
void RoomGateway::Batch::commit() noexcept {
    owner.sends_.swap(sends);
    if (!acknowledge.empty()) {
        const auto pending = owner.active_.find(acknowledge);
        if (pending->second.latencyProbe) {
            const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - pending->second.sentAt).count();
            owner.latencyMs_ = static_cast<uint32_t>(std::clamp<int64_t>(elapsed, 0, 60000));
        }
        owner.active_.erase(pending);
    }
    owner.changed_.notify_all();
}

void RoomGateway::run() {
    const auto cancelled = [this] { std::lock_guard<std::mutex> lock(mutex_); return stopping_; };
    try {
        {
            std::unique_lock<std::mutex> lock(mutex_);
            changed_.wait(lock, [this] { return stopping_ || !sends_.empty(); });
            if (stopping_) return;
        }
        Handle session{WinHttpOpen(L"T7-Rekindle", WINHTTP_ACCESS_TYPE_NO_PROXY,
                                  WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, WINHTTP_FLAG_ASYNC)};
        checked(session.value != nullptr);
        checked(WinHttpSetTimeouts(session.value, 8000, 8000, 8000, 8000));
        const std::wstring host(settings_.host.begin(), settings_.host.end());
        Handle connection{WinHttpConnect(session.value, host.c_str(), settings_.port, 0)};
        checked(connection.value != nullptr);
        std::string outgoing;
        Bytes message;
        AsyncChannel channel(changed_);
        // Reserve before registering callbacks, so cleanup never loses a handle.
        channel.handles.reserve(2);
        auto request = WinHttpOpenRequest(connection.value, L"GET", L"/ws", nullptr,
                                         WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, settings_.secure ? WINHTTP_FLAG_SECURE : 0);
        channel.attach(request);
        DWORD disabled = WINHTTP_DISABLE_REDIRECTS | WINHTTP_DISABLE_COOKIES | WINHTTP_DISABLE_AUTHENTICATION;
        checked(WinHttpSetOption(request, WINHTTP_OPTION_DISABLE_FEATURE, &disabled, sizeof(disabled)));
        checked(WinHttpSetOption(request, WINHTTP_OPTION_UPGRADE_TO_WEB_SOCKET, nullptr, 0));
        std::wstring authorization;
        if (!settings_.ticket.empty()) {
            authorization = L"Authorization: Bearer " + std::wstring(settings_.ticket.begin(), settings_.ticket.end()) + L"\r\n";
        }
        checked(WinHttpSendRequest(request, authorization.empty() ? WINHTTP_NO_ADDITIONAL_HEADERS : authorization.c_str(),
                                  static_cast<DWORD>(authorization.size()),
                                  WINHTTP_NO_REQUEST_DATA, 0, 0, reinterpret_cast<DWORD_PTR>(&channel)));
        channel.wait(channel.sentRequest, cancelled);
        checked(WinHttpReceiveResponse(request, nullptr));
        channel.wait(channel.headers, cancelled);
        DWORD status = 0, size = sizeof(status);
        checked(WinHttpQueryHeaders(request, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                    WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX));
        checked(status == 101);
        auto socket = WinHttpWebSocketCompleteUpgrade(request, reinterpret_cast<DWORD_PTR>(&channel));
        channel.attach(socket);

        bool receivePending = false, sendPending = false;
        size_t fragments = 0, frames = 0;
        auto window = Clock::now();
        while (!cancelled()) {
            bool read = false, wrote = false, failed = false;
            DWORD count = 0;
            WINHTTP_WEB_SOCKET_BUFFER_TYPE type{};
            {
                std::lock_guard<std::mutex> lock(channel.mutex);
                read = std::exchange(channel.read, false);
                wrote = std::exchange(channel.wrote, false);
                failed = channel.failed;
                count = channel.readBytes; type = channel.readType;
            }
            if (failed) throw std::runtime_error("room gateway I/O failed");
            if (wrote) { sendPending = false; outgoing.clear(); }
            if (read) {
                receivePending = false;
                if (Clock::now() - window >= std::chrono::seconds(1)) { window = Clock::now(); frames = 0; }
                if (++frames > RoomGatewayFramesPerSecond || ++fragments > 32 || count > channel.buffer.size()
                        || message.size() + count > 65536
                        || (type != WINHTTP_WEB_SOCKET_UTF8_FRAGMENT_BUFFER_TYPE
                            && type != WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE))
                    throw std::runtime_error("invalid room gateway message");
                message.insert(message.end(), channel.buffer.begin(), channel.buffer.begin() + count);
                if (type == WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE) {
                    wide(std::string(message.begin(), message.end()));
                    if (!receiver_(std::move(message))) throw std::runtime_error("room gateway inbound backpressure");
                    message.clear(); fragments = 0;
                }
            }
            if (!receivePending) {
                checked(WinHttpWebSocketReceive(socket, channel.buffer.data(), static_cast<DWORD>(channel.buffer.size()),
                                               nullptr, nullptr) == NO_ERROR);
                receivePending = true;
            }
            {
                std::unique_lock<std::mutex> lock(mutex_);
                const auto now = Clock::now();
                if (std::any_of(active_.begin(), active_.end(),
                        [&](const auto& request) { return now - request.second.sentAt >= std::chrono::seconds(8); }))
                    throw std::runtime_error("room gateway reply timeout");
                if (!stopping_ && !sendPending && !sends_.empty()) {
                    auto effect = std::move(sends_.front()); sends_.pop_front();
                    active_.emplace(effect.requestId, PendingRequest{now,
                        effect.request.find("\"type\":\"ping\"") != std::string::npos});
                    outgoing = std::move(effect.request);
                    sendPending = true;
                    lock.unlock();
                    checked(WinHttpWebSocketSend(socket, WINHTTP_WEB_SOCKET_UTF8_MESSAGE_BUFFER_TYPE,
                                                outgoing.data(), static_cast<DWORD>(outgoing.size())) == NO_ERROR);
                } else changed_.wait_for(lock, std::chrono::milliseconds(20));
            }
        }
    } catch (...) {
        const bool notify = !cancelled();
        requestStop();
        if (notify) failure_();
    }
}
}
