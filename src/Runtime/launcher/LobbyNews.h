#pragma once
#include "../core/Common.h"
#include <atomic>
#include <thread>
#include <string_view>

namespace t7 {
// The client copies browser addresses into a 128-byte shared-memory field.
struct LobbyNewsHooks {
    Bytes bytes;
    uint32_t constructor = 0, navigation = 0, initialize = 0;
};
LobbyNewsHooks buildLobbyNewsHooks(uint32_t remoteBase, uint32_t imageBase, const std::string& address);

class LobbyNews {
    SOCKET listener_ = INVALID_SOCKET;
    std::thread worker_;
    std::atomic<bool> stopping_{true};
    bool winsock_ = false;
    std::string response_, address_;
    void serve() noexcept;
public:
    ~LobbyNews();
    void start(const fs::path& page);
    void install(HANDLE process, uint32_t imageBase);
    void installHelper(HANDLE process, uint32_t imageBase);
    void stop() noexcept;
    const std::string& address() const { return address_; }
};
}
