#include "../../src/Runtime/launcher/LobbyNews.h"
#include "../../src/Runtime/launcher/RemoteImage.h"
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
void require(bool value, const char* text) { if (!value) throw std::runtime_error(text); }
void word(t7::Bytes& bytes, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) bytes.push_back(static_cast<unsigned char>(value >> (i * 8)));
}
std::string fetch(const std::string& address, const std::string& request) {
    const auto socket = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    require(socket != INVALID_SOCKET, "news test socket");
    sockaddr_in endpoint{}; endpoint.sin_family = AF_INET; endpoint.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    const auto port = std::stoi(address.substr(address.find_last_of(':') + 1));
    endpoint.sin_port = htons(static_cast<unsigned short>(port));
    if (connect(socket, reinterpret_cast<const sockaddr*>(&endpoint), sizeof(endpoint))) {
        closesocket(socket); throw std::runtime_error("news test connection");
    }
    DWORD timeout = 2000;
    setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));
    send(socket, request.data(), static_cast<int>(request.size()), 0);
    std::string response; char buffer[2048]; int count = 0;
    while ((count = recv(socket, buffer, sizeof(buffer), 0)) > 0) response.append(buffer, static_cast<size_t>(count));
    closesocket(socket); return response;
}
void fixture(const t7::fs::path& path) {
    constexpr uint32_t size = 0x401000;
    t7::Bytes image(0x200 + size - 0x1000, 0);
    IMAGE_DOS_HEADER dos{}; dos.e_magic = IMAGE_DOS_SIGNATURE; dos.e_lfanew = 0x80;
    IMAGE_NT_HEADERS32 nt{}; nt.Signature = IMAGE_NT_SIGNATURE;
    nt.FileHeader.Machine = IMAGE_FILE_MACHINE_I386; nt.FileHeader.NumberOfSections = 1;
    nt.FileHeader.SizeOfOptionalHeader = sizeof(nt.OptionalHeader);
    nt.FileHeader.Characteristics = IMAGE_FILE_EXECUTABLE_IMAGE | IMAGE_FILE_32BIT_MACHINE | IMAGE_FILE_RELOCS_STRIPPED;
    auto& h = nt.OptionalHeader;
    h.Magic = IMAGE_NT_OPTIONAL_HDR32_MAGIC; h.ImageBase = 0x400000; h.AddressOfEntryPoint = 0x1000;
    h.BaseOfCode = 0x1000; h.SizeOfCode = size - 0x1000; h.SectionAlignment = 0x1000; h.FileAlignment = 0x200;
    h.MajorOperatingSystemVersion = 6; h.MajorSubsystemVersion = 6; h.SizeOfImage = size; h.SizeOfHeaders = 0x200;
    h.Subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI; h.SizeOfStackReserve = 0x100000; h.SizeOfStackCommit = 0x1000;
    h.SizeOfHeapReserve = 0x100000; h.SizeOfHeapCommit = 0x1000; h.NumberOfRvaAndSizes = IMAGE_NUMBEROF_DIRECTORY_ENTRIES;
    std::memcpy(image.data(), &dos, sizeof(dos)); std::memcpy(image.data() + dos.e_lfanew, &nt, sizeof(nt));
    IMAGE_SECTION_HEADER section{}; std::memcpy(section.Name, ".news", 5);
    section.VirtualAddress = 0x1000; section.Misc.VirtualSize = size - 0x1000;
    section.PointerToRawData = 0x200; section.SizeOfRawData = size - 0x1000;
    section.Characteristics = IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE;
    std::memcpy(image.data() + dos.e_lfanew + sizeof(nt), &section, sizeof(section));
    const auto put = [&](uint32_t rva, const t7::Bytes& bytes) {
        std::memcpy(image.data() + 0x200 + rva - 0x1000, bytes.data(), bytes.size());
    };
    put(0x3EF790, {0x55,0x8B,0xEC,0xFF,0x75,0x0C,0xFF,0x75,0x08,0x90,0x90,0x90,0x90,
                  0xE8,0xAE,0xCB,0xC1,0xFF,0x5D,0xC2,0x08,0});
    // Capture assign arguments and the registers/flags observed by the original function.
    put(0xC350, {0x89,0x46,0x08,0x9C,0x58,0x89,0x46,0x0C,0x8B,0x44,0x24,0x04,0x89,0x06,
                0x8B,0x44,0x24,0x08,0x89,0x46,0x04,0xC2,0x08,0});
    put(0x3EF870, {0x55,0x8B,0xEC,0x53,0x8B,0xD9,0x89,0x46,0x14,0x9C,0x58,0x89,0x46,0x18,
                  0x8B,0x45,0x08,0x89,0x46,0x10,0x5B,0x5D,0xC2,0x04,0});
    const char* names[]{"bbsMessage", "bannerMessage", "popupMessage", "payMessage", "actMessage", "bbsMessageExtra", "bbsMessage", "bannerMessage"};
    t7::Bytes code;
    const auto imm = [&](unsigned char op, uint32_t value) { code.push_back(op); word(code, value); };
    const auto call = [&](uint32_t rva) { code.push_back(0xE8); word(code, rva - (0x1000 + static_cast<uint32_t>(code.size()) + 4)); };
    for (uint32_t i = 0; i < 8; ++i) {
        const uint32_t name = 0x402000 + i * 64, object = 0x403000 + i * 64, result = 0x404000 + i * 64;
        const std::string text = names[i]; t7::Bytes value(text.begin(), text.end()); value.push_back(0); put(name - 0x400000, value);
        t7::Bytes state(32, 0); const uint32_t length = static_cast<uint32_t>(text.size()), capacity = i < 6 ? 15 : 32;
        if (i < 6) std::memcpy(state.data() + 4, value.data(), value.size());
        else std::memcpy(state.data() + 4, &name, 4);
        std::memcpy(state.data() + 20, &length, 4); std::memcpy(state.data() + 24, &capacity, 4); put(object - 0x400000, state);
        imm(0xBE, result); imm(0xBF, name); imm(0xB9, 0x407004 + i * 128);
        code.insert(code.end(), {0x89,0x66,0x1C}); // ESP before both calls.
        imm(0x68, 1); imm(0x68, 0x405000); imm(0xB8, 0x12345678); imm(0x68, 0x246); code.push_back(0x9D);
        call(0x3EF790);
        imm(0xB9, object); imm(0x68, 0x405000); imm(0xB8, 0x12345678); imm(0x68, 0x246); code.push_back(0x9D);
        call(0x3EF870); code.insert(code.end(), {0x89,0x66,0x20});
    }
    imm(0xB9, 0x11223344); imm(0xB8, 0x3F800000);
    code.insert(code.end(), {0xD9,0xE8,0x66,0x0F,0x6E,0xC0});
    imm(0xB8, 0x55667700); call(0x3FDC06);
    code.push_back(0xA3); word(code, 0x406010);
    code.insert(code.end(), {0x89,0x0D}); word(code, 0x406014);
    code.insert(code.end(), {0xD9,0x1D}); word(code, 0x406018);
    code.insert(code.end(), {0x66,0x0F,0x7E,0x05}); word(code, 0x40601C);
    code.insert(code.end(), {0xC7,0x05}); word(code, 0x406004); word(code, 0);
    imm(0xB8, 0x11223344); call(0x3FDC06); code.push_back(0xA3); word(code, 0x406020);
    imm(0xBB, 0x408000); call(0x5800);
    code.push_back(0xA1); word(code, 0x406024); code.push_back(0xA3); word(code, 0x406028);
    code.insert(code.end(), {0xC7,0x83,0xD8,0,0,0}); word(code, 1); call(0x5800);
    code.insert(code.end(), {0xC7,0x05}); word(code, 0x406000); word(code, 1); code.insert(code.end(), {0xEB,0xFE}); put(0x1000, code);
    put(0x5800, {0x55,0x8B,0xEC,0x81,0xEC,0x94,0,0,0,0xC7,0x85,0x6C,0xFF,0xFF,0xFF,0x77,0,0,0,
                 0xE9,0x0E,0,0,0});
    put(0x5826, {0x8B,0x85,0x6C,0xFF,0xFF,0xFF,0xA3,0x24,0x60,0x40,0,0xE9,0x8E,0,0,0});
    put(0x58C4, {0xC9,0xC3});
    put(0x5000, {'x',0});
    put(0x3FDC06, {0xE8,0x55,0xEF,0xFF,0xFF,0xC3});
    put(0x6004, {1,0,0,0});
    put(0x3FCB60, {0xA0,0x04,0x60,0x40,0,0xC3});
    put(0x3FF860, {0xFF,0x05,0x08,0x60,0x40,0,0x89,0x0D,0x0C,0x60,0x40,0,
                  0xD9,0xEE,0x66,0x0F,0xEF,0xC0,0x33,0xC0,0xC3});
    std::ofstream output(path, std::ios::binary); output.write(reinterpret_cast<const char*>(image.data()), image.size());
}
void hooks(const t7::fs::path& path, t7::LobbyNews& news) {
    fixture(path);
    STARTUPINFOW startup{}; startup.cb = sizeof(startup); PROCESS_INFORMATION process{};
    require(CreateProcessW(path.c_str(), nullptr, nullptr, nullptr, FALSE, CREATE_SUSPENDED, nullptr, nullptr, &startup, &process), "news x86 fixture start");
    try {
        news.install(process.hProcess, 0x400000);
        news.installHelper(process.hProcess, 0x400000); ResumeThread(process.hThread);
        uint32_t complete = 0;
        for (int i = 0; i < 200 && !complete; ++i) {
            Sleep(10); const auto bytes = t7::readClientMemory(process.hProcess, 0x406000, 4); std::memcpy(&complete, bytes.data(), 4);
        }
        require(complete == 1, "news x86 hooks did not return");
        const auto ready = t7::readClientMemory(process.hProcess, 0x406024, 8);
        require(ready[0] == 0x77 && ready[4] == 0, "news helper updated URL before browser readiness or failed to resume");
        const auto initialized = t7::readClientMemory(process.hProcess, 0x406008, 28);
        uint32_t initialization[7]{}; std::memcpy(initialization, initialized.data(), sizeof(initialization));
        require(initialization[0] == 1 && initialization[1] == 0x11223344, "news initialized after a failed UI load or lost its owner");
        require(initialization[2] == 0x55667701 && initialization[3] == 0x11223344
            && initialization[4] == 0x3F800000 && initialization[5] == 0x3F800000
            && initialization[6] == 0x11223300, "news UI initialization clobbered return value, registers or FPU/SSE");
        for (uint32_t i = 0; i < 8; ++i) {
            const auto bytes = t7::readClientMemory(process.hProcess, 0x404000 + i * 64, 36);
            uint32_t values[9]{}; std::memcpy(values, bytes.data(), sizeof(values));
            const bool sidebar = i == 0 || i == 1 || i >= 6;
            const auto expected = sidebar ? news.address() + (i == 1 || i == 7 ? "#banner" : "#news") : "x";
            for (auto pointer : {values[0], values[4]}) {
                const auto url = t7::readClientMemory(process.hProcess, pointer, expected.size() + 1);
                require(std::string(url.begin(), url.end() - 1) == expected && url.back() == 0, "news URL or unrelated view changed");
            }
            require(values[1] == (sidebar ? expected.size() : 1), "news constructor length");
            const auto revision = t7::readClientMemory(process.hProcess, 0x407000 + i * 128, 4);
            require(revision[0] == (sidebar ? 1 : 0), "news initial URL revision was not published");
            require(values[2] == 0x12345678 && values[5] == 0x12345678, "news hook clobbered EAX");
            require((values[3] & 0xCD5) == (0x246 & 0xCD5) && (values[6] & 0xCD5) == (0x246 & 0xCD5), "news hook clobbered flags");
            require(values[7] == values[8], "news hook stack imbalance");
        }
    } catch (...) {
        TerminateProcess(process.hProcess, 0); WaitForSingleObject(process.hProcess, 2000);
        CloseHandle(process.hThread); CloseHandle(process.hProcess); throw;
    }
    TerminateProcess(process.hProcess, 0); WaitForSingleObject(process.hProcess, 2000);
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
}
}
bool verifyLobbyNews() {
    const auto directory = t7::fs::temp_directory_path() / (L"t7-news-" + std::to_wstring(GetCurrentProcessId()));
    try {
        require(t7::fs::create_directory(directory), "news test directory");
        const auto page = directory / "index.html";
        { std::ofstream file(page); file << "<!doctype html><title>Local bulletin</title>"; }
        t7::LobbyNews news; news.start(page);
        require(fetch(news.address(), "GET / HTTP/1.1\r\nHost: localhost\r\n\r\n").find("Local bulletin") != std::string::npos, "news page not served");
        require(fetch(news.address(), "GET /../private HTTP/1.1\r\nHost: localhost\r\n\r\n").find("404 Not Found") != std::string::npos, "news server exposed another path");
        hooks(directory / "NewsFixture.exe", news);
        news.stop(); require(news.address().empty(), "news stop retained endpoint");
        news.start(page); require(fetch(news.address(), "GET /index.html HTTP/1.1\r\n\r\n").find("HTTP/1.1 200 OK") == 0, "news restart failed"); news.stop();
        t7::fs::remove_all(directory);
        std::cout << "Local lobby news service, x86 sidebar isolation, flags and stack preservation passed\n"; return true;
    } catch (const std::exception& error) {
        t7::fs::remove_all(directory); std::cerr << "Lobby news tests: " << error.what() << '\n'; return false;
    }
}
