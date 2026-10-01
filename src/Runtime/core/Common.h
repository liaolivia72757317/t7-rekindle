#pragma once
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <filesystem>
#include <string>
#include <vector>
#include <stdexcept>
#include <cstdint>

namespace t7 {
using Bytes = std::vector<unsigned char>;
namespace fs = std::filesystem;
struct Config {
    std::string bindAddress = "127.0.0.1", advertisedAddress = "127.0.0.1";
    std::string playerName = u8"吃我一记流星锤";
    // All three ports are zero before listener binding.  Server replaces them
    // with the actual loopback ports returned by getsockname().
    unsigned short ports[3] = {0, 0, 0};
    fs::path clientRoot;
    fs::path clientDirectory;
    // The embedded business layer accepts local movement reports only after
    // the launcher has installed the memory-only client overlay.
    bool runtimeMovement = true;
    // Raw socket bytes are diagnostic-sensitive and stay disabled unless a
    // local fixture explicitly opts in. Journal metadata is always kept.
    bool captureWire = false;
};
std::wstring wide(const std::string& text);
std::string utf8(const std::wstring& text);
fs::path executableDirectory();
std::string sha256(const Bytes& data);
std::string fileHash(const fs::path& path);
Bytes readFile(const fs::path& path, size_t maximum = 64 * 1024 * 1024);
void validateConfig(const Config& config);
Config readConfig(const fs::path& path);
void writeConfig(const fs::path& path, const Config& config);
std::string utcNow();
std::string jsonString(const std::string& value);
std::string errorText(const char* operation);
}
