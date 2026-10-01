#include "Common.h"
#include <bcrypt.h>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <set>

namespace t7 {
std::wstring wide(const std::string& text) {
    if (text.empty()) return {};
    int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (!size) throw std::runtime_error("invalid UTF-8");
    std::wstring result(size, L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), result.data(), size);
    return result;
}
std::string utf8(const std::wstring& text) {
    if (text.empty()) return {};
    int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    if (!size) throw std::runtime_error("invalid UTF-16");
    std::string result(size, '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), result.data(), size, nullptr, nullptr);
    return result;
}
fs::path executableDirectory() {
    std::wstring path(32768, L'\0');
    DWORD length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (!length || length == path.size()) throw std::runtime_error("executable path unavailable");
    path.resize(length); return fs::path(path).parent_path();
}
std::string sha256(const Bytes& data) {
    unsigned char hash[32];
    NTSTATUS status = BCryptHash(BCRYPT_SHA256_ALG_HANDLE, nullptr, 0,
        const_cast<PUCHAR>(data.data()), static_cast<ULONG>(data.size()), hash, sizeof(hash));
    if (status < 0) throw std::runtime_error("BCryptHash failed");
    std::ostringstream out;
    for (auto byte : hash) out << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(byte);
    return out.str();
}
Bytes readFile(const fs::path& path, size_t maximum) {
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    if (!file) throw std::runtime_error("read failed: " + path.u8string());
    auto length = file.tellg();
    if (length < 0 || static_cast<uint64_t>(length) > maximum) throw std::runtime_error("file size exceeds limit");
    Bytes data(static_cast<size_t>(length)); file.seekg(0);
    if (!data.empty() && !file.read(reinterpret_cast<char*>(data.data()), length)) throw std::runtime_error("short file read");
    return data;
}
std::string fileHash(const fs::path& path) { return sha256(readFile(path)); }
void validateConfig(const Config& config) {
    for (const auto* path : {&config.clientRoot, &config.clientDirectory}) {
        const auto& value = path->native();
        if (value.find_first_of(L"\r\n") != std::wstring::npos || value.find(L'\0') != std::wstring::npos)
            throw std::runtime_error("invalid client directory characters");
    }
    for (const auto& text : {config.bindAddress, config.advertisedAddress}) {
        IN_ADDR address{};
        if (inet_pton(AF_INET, text.c_str(), &address) != 1) throw std::runtime_error("IPv4 literal required");
        uint32_t n = ntohl(address.S_un.S_addr);
        if (n >= 0xE0000000) throw std::runtime_error("TCP endpoint requires a unicast IPv4 address");
    }
    if (config.advertisedAddress == "0.0.0.0")
        throw std::runtime_error("0.0.0.0 is for listening only; publish a reachable server IPv4 address");
    std::set<unsigned short> unique;
    bool dynamic = config.ports[0] == 0 || config.ports[1] == 0 || config.ports[2] == 0;
    for (auto port : config.ports) {
        if (dynamic && port != 0) throw std::runtime_error("dynamic listener configuration must use port zero for all roles");
        if (!dynamic && (!port || !unique.insert(port).second))
            throw std::runtime_error("three distinct ports in 1..65535 required");
    }
}
Config readConfig(const fs::path& path) {
    Config result;
    if (!fs::exists(path)) return result;
    std::ifstream input(path);
    std::string line;
    std::set<std::string> seen;
    while (std::getline(input, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty() || line[0] == '#' || line == "[network]" || line == "[launcher]") continue;
        auto equal = line.find('=');
        if (equal == std::string::npos) throw std::runtime_error("invalid configuration line");
        auto key = line.substr(0, equal), value = line.substr(equal + 1);
        if (!seen.insert(key).second) throw std::runtime_error("duplicate configuration field");
        if (key == "bindAddress") result.bindAddress = value;
        else if (key == "advertisedAddress") result.advertisedAddress = value;
        else if (key == "clientRoot") result.clientRoot = fs::path(wide(value));
        else if (key == "clientDirectory") result.clientDirectory = fs::path(wide(value));
        else {
            int index = key == "loginPort" ? 0 : key == "logicPort" ? 1 : key == "instancePort" ? 2 : -1;
            if (index < 0 || value.empty() || value.find_first_not_of("0123456789") != std::string::npos)
                throw std::runtime_error("invalid configuration field");
            auto port = std::stoul(value);
            if (port > 65535) throw std::runtime_error("port exceeds 65535");
            result.ports[index] = static_cast<unsigned short>(port);
        }
    }
    validateConfig(result); return result;
}
void writeConfig(const fs::path& path, const Config& config) {
    validateConfig(config);
    auto temporary = path; temporary += L".tmp";
    std::ofstream file(temporary, std::ios::trunc);
    file << "[network]\nbindAddress=" << config.bindAddress << "\nadvertisedAddress=" << config.advertisedAddress;
    const char* names[] = {"loginPort", "logicPort", "instancePort"};
    for (int i = 0; i != 3; ++i) file << '\n' << names[i] << '=' << config.ports[i];
    file << '\n';
    if (!config.clientDirectory.empty()) file << "[launcher]\nclientDirectory=" << config.clientDirectory.u8string() << '\n';
    else if (!config.clientRoot.empty()) file << "[launcher]\nclientRoot=" << config.clientRoot.u8string() << '\n';
    file.close();
    if (!file || !MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
        throw std::runtime_error("configuration save failed");
}
std::string utcNow() {
    SYSTEMTIME time{}; GetSystemTime(&time); char buffer[40];
    sprintf_s(buffer, "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ", time.wYear, time.wMonth, time.wDay,
        time.wHour, time.wMinute, time.wSecond, time.wMilliseconds); return buffer;
}
std::string jsonString(const std::string& value) {
    std::ostringstream out; out << '"';
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') out << '\\' << c;
        else if (c < 32) out << "\\u00" << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(c);
        else out << c;
    }
    out << '"'; return out.str();
}
std::string errorText(const char* operation) { return std::string(operation) + " error=" + std::to_string(GetLastError()); }
}
