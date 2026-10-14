#include "LobbyNews.h"
#include "RemoteImage.h"

namespace t7 {
LobbyNews::~LobbyNews() { stop(); }
void LobbyNews::start(const fs::path& page) {
    stop();
    const auto body = readFile(page, 256 * 1024);
    if (body.empty()) throw std::runtime_error("lobby news page is empty");
    response_ = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\n"
        "Connection: close\r\nContent-Length: " + std::to_string(body.size()) + "\r\n\r\n";
    response_.append(body.begin(), body.end());
    WSADATA data{};
    if (WSAStartup(MAKEWORD(2, 2), &data)) throw std::runtime_error("lobby news Winsock initialization failed");
    winsock_ = true;
    try {
        listener_ = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listener_ == INVALID_SOCKET) throw std::runtime_error("lobby news socket failed");
        BOOL exclusive = TRUE;
        if (setsockopt(listener_, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<const char*>(&exclusive), sizeof(exclusive)))
            throw std::runtime_error("lobby news exclusive binding failed");
        sockaddr_in endpoint{}; endpoint.sin_family = AF_INET; endpoint.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(listener_, reinterpret_cast<const sockaddr*>(&endpoint), sizeof(endpoint)) || listen(listener_, 4))
            throw std::runtime_error("lobby news listener failed");
        int size = sizeof(endpoint);
        if (getsockname(listener_, reinterpret_cast<sockaddr*>(&endpoint), &size))
            throw std::runtime_error("lobby news port unavailable");
        address_ = "http://127.0.0.1:" + std::to_string(ntohs(endpoint.sin_port)) + "/";
        stopping_ = false;
        worker_ = std::thread([this] { serve(); });
    } catch (...) { stop(); throw; }
}
void LobbyNews::serve() noexcept {
    while (!stopping_) {
        fd_set ready; FD_ZERO(&ready); FD_SET(listener_, &ready);
        timeval timeout{0, 200000};
        if (select(0, &ready, nullptr, nullptr, &timeout) <= 0) continue;
        const auto client = accept(listener_, nullptr, nullptr);
        if (client == INVALID_SOCKET) continue;
        DWORD deadline = 1000;
        setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&deadline), sizeof(deadline));
        setsockopt(client, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&deadline), sizeof(deadline));
        char buffer[2048]{};
        size_t received = 0;
        while (received < sizeof(buffer) - 1 && !stopping_) {
            const int count = recv(client, buffer + received, static_cast<int>(sizeof(buffer) - 1 - received), 0);
            if (count <= 0) break;
            received += static_cast<size_t>(count);
            if (std::string_view(buffer, received).find("\r\n\r\n") != std::string_view::npos) break;
        }
        const std::string_view request(buffer, received);
        const bool page = request.find("GET / HTTP/1.") == 0 || request.find("GET /index.html HTTP/1.") == 0;
        const std::string_view response = page ? std::string_view(response_)
            : std::string_view("HTTP/1.1 404 Not Found\r\nConnection: close\r\nContent-Length: 0\r\n\r\n");
        size_t sent = 0;
        while (!stopping_ && sent < response.size()) {
            const int count = send(client, response.data() + sent, static_cast<int>(response.size() - sent), 0);
            if (count <= 0) break;
            sent += static_cast<size_t>(count);
        }
        closesocket(client);
    }
}
void LobbyNews::install(HANDLE process, uint32_t imageBase) {
    if (address_.empty()) throw std::runtime_error("lobby news service is not running");
    const Bytes constructor{0xE8,0xAE,0xCB,0xC1,0xFF}, navigation{0x55,0x8B,0xEC,0x53,0x8B,0xD9}, initialize{0xE8,0x55,0xEF,0xFF,0xFF};
    if (readClientMemory(process, imageBase + 0x3EF79D, constructor.size()) != constructor
        || readClientMemory(process, imageBase + 0x3EF870, navigation.size()) != navigation
        || readClientMemory(process, imageBase + 0x3FDC06, initialize.size()) != initialize)
        throw std::runtime_error("lobby news client signature mismatch");
    auto memory = VirtualAllocEx(process, nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!memory) throw std::runtime_error(errorText("lobby news hook allocation"));
    const auto address = reinterpret_cast<uintptr_t>(memory);
    bool referenced = false;
    try {
        if (address > UINT32_MAX - 4096) throw std::runtime_error("lobby news allocation outside x86 range");
        const auto hooks = buildLobbyNewsHooks(static_cast<uint32_t>(address), imageBase, address_);
        writeClientMemory(process, address, hooks.bytes);
        DWORD previous = 0;
        if (!VirtualProtectEx(process, memory, 4096, PAGE_EXECUTE_READ, &previous))
            throw std::runtime_error(errorText("lobby news hook protection"));
        const auto patch = [&](uint32_t rva, const Bytes& expected, uint32_t target, unsigned char opcode) {
            Bytes replacement{opcode};
            const auto offset = target - (imageBase + rva + 5);
            for (unsigned i = 0; i < 4; ++i) replacement.push_back(static_cast<unsigned char>(offset >> (i * 8)));
            replacement.resize(expected.size(), 0x90);
            // The owned process is terminated on launch failure; never free referenced code.
            referenced = true;
            applyRemotePatches(process, imageBase, {{rva, expected, replacement}});
        };
        patch(0x3EF79D, constructor, hooks.constructor, 0xE8);
        patch(0x3EF870, navigation, hooks.navigation, 0xE9);
        patch(0x3FDC06, initialize, hooks.initialize, 0xE8);
    } catch (...) {
        if (!referenced) VirtualFreeEx(process, memory, 0, MEM_RELEASE);
        throw;
    }
    // Remote storage belongs to the client process and is reclaimed with that process.
}
void LobbyNews::installHelper(HANDLE process, uint32_t imageBase) {
    // CEF creates the browser asynchronously; defer URL updates until its callback publishes it.
    const Bytes expected{0x8B,0x85,0x6C,0xFF,0xFF,0xFF};
    if (readClientMemory(process, imageBase + 0x5826, expected.size()) != expected)
        throw std::runtime_error("lobby news helper signature mismatch");
    auto memory = VirtualAllocEx(process, nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!memory) throw std::runtime_error(errorText("lobby news helper allocation"));
    bool referenced = false;
    try {
        const auto remote = reinterpret_cast<uintptr_t>(memory);
        if (remote > UINT32_MAX - 4096) throw std::runtime_error("lobby news helper outside x86 range");
        Bytes code{0x83,0xBB,0xD8,0,0,0,0,0x0F,0x84};
        const auto append = [&](uint32_t value) { for (unsigned i = 0; i < 4; ++i) code.push_back(static_cast<unsigned char>(value >> (i * 8))); };
        append(imageBase + 0x58C4 - static_cast<uint32_t>(remote + 13));
        code.insert(code.end(), expected.begin(), expected.end());
        code.push_back(0xE9); append(imageBase + 0x582C - static_cast<uint32_t>(remote + 24));
        writeClientMemory(process, remote, code);
        DWORD previous = 0;
        if (!VirtualProtectEx(process, memory, 4096, PAGE_EXECUTE_READ, &previous))
            throw std::runtime_error(errorText("lobby news helper protection"));
        Bytes patch{0xE9};
        const auto offset = static_cast<uint32_t>(remote) - (imageBase + 0x582B);
        for (unsigned i = 0; i < 4; ++i) patch.push_back(static_cast<unsigned char>(offset >> (i * 8)));
        patch.push_back(0x90);
        referenced = true;
        applyRemotePatches(process, imageBase, {{0x5826, expected, patch}});
    } catch (...) {
        if (!referenced) VirtualFreeEx(process, memory, 0, MEM_RELEASE);
        throw;
    }
}
void LobbyNews::stop() noexcept {
    stopping_ = true;
    if (worker_.joinable()) worker_.join();
    if (listener_ != INVALID_SOCKET) closesocket(listener_);
    listener_ = INVALID_SOCKET;
    if (winsock_) WSACleanup();
    winsock_ = false; address_.clear(); response_.clear();
}
}
