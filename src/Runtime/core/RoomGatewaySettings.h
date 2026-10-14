#pragma once
#include <string>
#include <cstddef>

namespace t7 {
inline constexpr size_t RoomGatewayFramesPerSecond = 1024;

struct RoomGatewaySettings {
    unsigned short port = 8090;
    std::string accountId;
    std::string host = "127.0.0.1";
    bool secure = false;
    std::string ticket;
};
}
