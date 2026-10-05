#include "../../src/Runtime/launcher/MovementOverlay.h"
#include "../../src/Runtime/launcher/MovementHookCode.h"
#include "../../src/Runtime/launcher/MovementResources.h"
#include "../../src/Runtime/launcher/StartupAnimation.h"
#include "../../src/Runtime/launcher/DebugClient.h"
#include "StartupAnimationFixture.h"
#include <atomic>
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
constexpr uint32_t BASE = 0x400000, IMAGE_SIZE = 0xA6000, HEADER_SIZE = 0x200;
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
void word(t7::Bytes& bytes, uint32_t value) {
    for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<unsigned char>(value >> shift));
}
uint32_t readWord(HANDLE process, uint32_t address) {
    const auto bytes = t7::readClientMemory(process, address, 4);
    uint32_t result = 0; std::memcpy(&result, bytes.data(), 4); return result;
}
std::string readPath(HANDLE process, uint32_t object) {
    const auto bytes = t7::readClientMemory(process, readWord(process, object), readWord(process, object + 0x10));
    return {bytes.begin(), bytes.end()};
}
std::string gbk(const wchar_t* value) {
    const auto count = WideCharToMultiByte(936, 0, value, -1, nullptr, 0, nullptr, nullptr);
    std::string result(static_cast<size_t>(count), '\0');
    require(count && WideCharToMultiByte(936, 0, value, -1, result.data(), count, nullptr, nullptr) == count, "fixture encoding");
    result.pop_back(); return result;
}
struct Input {
    std::string path, source;
    uint32_t object = 0, buffer = 0, result = 0;
};
struct Fixture {
    std::vector<Input> inputs;
    uint32_t completed = 0;

    void write(const t7::fs::path& path, bool malformed, bool malformedLogin) {
        using Resource = t7::MovementResource;
        inputs = {
            {"..\\DATA\\BTREE\\T7_RUNTIME_MOVEMENT.BTREE", "<BTree Version=\"4\"/>"},
            {t7::movementResourcePath(Resource::Router), "<BTree Version=\"4\"/>"},
            {t7::movementOfflineTreePath(), "original tree remains untouched"},
            {t7::movementResourcePath(Resource::Birth), gbk(L"<BTree Version=\"4\"><Node ID=\"1\">"
                L"<Node ID=\"2\"><Node Type=\"ACTION\" ID=\"3\" Name=\"设置步兵转向\"/></Node></Node></BTree>")},
            {t7::movementResourcePath(Resource::Infantry), gbk(L"<EntSheet><Header><GeServerMovable><BTree Value=\"*.*\"/>"
                L"</GeServerMovable></Header><Entity Name=\"武将\"><GeServerMovable><BTree Value=\"online\"/>"
                L"</GeServerMovable><Collision Keep=\"1\"/></Entity><Entity Name=\"女武将\"><GeServerMovable>"
                L"<BTree Value=\"online\"/></GeServerMovable><Camera Keep=\"1\"/></Entity></EntSheet>")},
        };
        inputs.push_back({t7::startupAnimationResourcePath(), malformedLogin ? "<BTree>" : startupAnimationFixture()});
        auto alternatePath = t7::startupAnimationResourcePath();
        alternatePath.replace(0, 14, "..\\DATA\\BTREE\\");
        inputs.push_back({alternatePath, startupAnimationFixture()});
        inputs.push_back({gbk(L"../data/btree/流程_新手教学_片头动画.btree"), "tutorial movie remains untouched"});
        if (malformed) inputs[0].source = "<BTree>";
        t7::Bytes image(HEADER_SIZE + IMAGE_SIZE - 0x1000, 0);
        IMAGE_DOS_HEADER dos{}; dos.e_magic = IMAGE_DOS_SIGNATURE; dos.e_lfanew = 0x80;
        std::memcpy(image.data(), &dos, sizeof(dos));
        IMAGE_NT_HEADERS32 nt{}; nt.Signature = IMAGE_NT_SIGNATURE;
        nt.FileHeader.Machine = IMAGE_FILE_MACHINE_I386; nt.FileHeader.NumberOfSections = 1;
        nt.FileHeader.SizeOfOptionalHeader = sizeof(nt.OptionalHeader);
        nt.FileHeader.Characteristics = IMAGE_FILE_EXECUTABLE_IMAGE | IMAGE_FILE_32BIT_MACHINE | IMAGE_FILE_RELOCS_STRIPPED;
        nt.OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR32_MAGIC;
        nt.OptionalHeader.ImageBase = BASE; nt.OptionalHeader.AddressOfEntryPoint = 0x1000;
        nt.OptionalHeader.BaseOfCode = 0x1000; nt.OptionalHeader.SizeOfCode = IMAGE_SIZE - 0x1000;
        nt.OptionalHeader.SectionAlignment = 0x1000; nt.OptionalHeader.FileAlignment = HEADER_SIZE;
        nt.OptionalHeader.MajorOperatingSystemVersion = 6; nt.OptionalHeader.MajorSubsystemVersion = 6;
        nt.OptionalHeader.SizeOfImage = IMAGE_SIZE; nt.OptionalHeader.SizeOfHeaders = HEADER_SIZE;
        nt.OptionalHeader.Subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI;
        nt.OptionalHeader.SizeOfStackReserve = 0x100000; nt.OptionalHeader.SizeOfStackCommit = 0x1000;
        nt.OptionalHeader.SizeOfHeapReserve = 0x100000; nt.OptionalHeader.SizeOfHeapCommit = 0x1000;
        nt.OptionalHeader.NumberOfRvaAndSizes = IMAGE_NUMBEROF_DIRECTORY_ENTRIES;
        std::memcpy(image.data() + dos.e_lfanew, &nt, sizeof(nt));
        IMAGE_SECTION_HEADER section{}; std::memcpy(section.Name, ".fixture", 8);
        section.VirtualAddress = 0x1000; section.Misc.VirtualSize = IMAGE_SIZE - 0x1000;
        section.PointerToRawData = HEADER_SIZE; section.SizeOfRawData = IMAGE_SIZE - 0x1000;
        section.Characteristics = IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE;
        std::memcpy(image.data() + dos.e_lfanew + sizeof(nt), &section, sizeof(section));
        auto put = [&](uint32_t rva, const t7::Bytes& data) {
            require(rva >= 0x1000 && rva + data.size() <= IMAGE_SIZE, "fixture extent");
            std::memcpy(image.data() + HEADER_SIZE + rva - 0x1000, data.data(), data.size());
        };
        for (size_t i = 0; i < t7::MOVEMENT_HOOK_SITES.size(); ++i) {
            const auto& site = t7::MOVEMENT_HOOK_SITES[i];
            t7::Bytes body(site.original.begin(), site.original.end());
            if (i == 0) body.insert(body.end(), {0x8B,0x45,0x08,0x8B,0xE5,0x5D,0xC2,0x08,0});
            if (i == 2) body.push_back(0x58); // consume the original push esi
            if (i != 0) body.push_back(0xC3);
            put(site.rva, body);
        }
        t7::Bytes code{0xBD}; word(code, BASE + 0x2000); // fixed synthetic XML loader frame
        auto call = [&](uint32_t rva) { code.push_back(0xE8); word(code, rva - (0x1000 + static_cast<uint32_t>(code.size()) + 4)); };
        auto store = [&](unsigned char modrm, uint32_t address) { code.insert(code.end(), {0x89, modrm}); word(code, address); };
        for (size_t i = 0; i < inputs.size(); ++i) {
            auto& input = inputs[i];
            input.object = BASE + 0x3000 + static_cast<uint32_t>(i) * 0x100;
            input.buffer = BASE + 0x4000 + static_cast<uint32_t>(i) * 0x3000;
            input.result = BASE + 0x2100 + static_cast<uint32_t>(i) * 24;
            t7::Bytes object(24, 0);
            const auto pathData = input.object + 24, length = static_cast<uint32_t>(input.path.size());
            std::memcpy(object.data(), &pathData, 4); std::memcpy(object.data() + 16, &length, 4); std::memcpy(object.data() + 20, &length, 4);
            object.insert(object.end(), input.path.begin(), input.path.end()); object.push_back(0);
            put(input.object - BASE, object);
            t7::Bytes buffer(input.source.begin(), input.source.end());
            buffer.resize(input.source.size() + t7::MOVEMENT_XML_RESERVE + 1, 0);
            buffer.insert(buffer.end(), 16, 0xA5); put(input.buffer - BASE, buffer);

            code.insert(code.end(), {0xC7,0x45,0x08}); word(code, input.object);
            code.push_back(0xB8); word(code, static_cast<uint32_t>(input.source.size()));
            code.push_back(0x68); word(code, 0x246); code.push_back(0x9D);
            call(t7::MOVEMENT_HOOK_SITES[1].rva);
            store(0x0D, input.result); store(0x3D, input.result + 4);
            code.insert(code.end(), {0x9C,0x5A}); store(0x15, input.result + 8);
            code.push_back(0xBE); word(code, input.buffer); call(t7::MOVEMENT_HOOK_SITES[2].rva);
            store(0x05, input.result + 12);
            for (unsigned flag = 0; flag < 2; ++flag) {
                code.insert(code.end(), {0x6A, static_cast<unsigned char>(flag), 0x68}); word(code, input.object);
                call(t7::MOVEMENT_HOOK_SITES[0].rva); store(0x05, input.result + 16 + flag * 4);
            }
        }
        completed = BASE + 0x1000 + static_cast<uint32_t>(code.size());
        code.insert(code.end(), {0xCC,0xEB,0xFE}); put(0x1000, code);
        std::ofstream output(path, std::ios::binary);
        output.write(reinterpret_cast<const char*>(image.data()), static_cast<std::streamsize>(image.size()));
        require(output.good(), "synthetic x86 fixture write");
    }

    void verify(HANDLE process, bool skipStartupAnimation) const {
        for (const auto& input : inputs) {
            const auto resource = t7::movementResourceForPath(input.path);
            const auto startupAnimation = skipStartupAnimation && t7::isStartupAnimationResourcePath(input.path);
            const auto reserved = resource == t7::MovementResource::None && !startupAnimation ? 0 : t7::MOVEMENT_XML_RESERVE;
            require(readWord(process, input.result) == input.source.size() + reserved + 1, "XML reserve or saved ECX is wrong");
            require(readWord(process, input.result + 4) == input.source.size(), "original XML read length changed");
            require((readWord(process, input.result + 8) & 0xCD5) == (0x246 & 0xCD5), "hook changed arithmetic flags");
            require(readWord(process, input.result + 12) == input.buffer, "parse argument/stack was not preserved");
            const auto expected = startupAnimation ? t7::skipStartupAnimation(input.source)
                : t7::transformMovementResource(resource, input.source);
            auto actual = t7::readClientMemory(process, input.buffer, expected.size() + 1);
            require(actual.back() == 0 && std::string(actual.begin(), actual.end() - 1) == expected, "runtime XML transform mismatch");
            const auto guard = t7::readClientMemory(process, input.buffer + static_cast<uint32_t>(input.source.size() + t7::MOVEMENT_XML_RESERVE + 1), 16);
            require(guard == t7::Bytes(16, 0xA5), "XML transform overflowed its pool allocation");
            const auto routed = readPath(process, readWord(process, input.result + 16));
            require(routed == (resource == t7::MovementResource::Router ? t7::movementOfflineTreePath() : input.path),
                    "resource alias changed an unrelated path or was consumed after one request");
            require(readWord(process, input.result + 20) == input.object, "non-read-only resource open was redirected");
        }
    }
};
void run(const t7::fs::path& path, bool malformed, bool skipStartupAnimation = false, bool malformedLogin = false) {
    Fixture fixture; fixture.write(path, malformed, malformedLogin);
    t7::MovementOverlay overlay;
    t7::DebugClient client;
    std::atomic<bool> completed{false};
    try {
        client.start(path, t7::fileHash(path), {}, [&](HANDLE process, HANDLE) {
            overlay.install(process, BASE, {}, skipStartupAnimation);
        }, {}, {}, {},
            [&](DWORD thread, uintptr_t address) {
                if (address == fixture.completed) { completed.store(true); return true; }
                return overlay.handleBreakpoint(thread, address);
            });
        const auto deadline = GetTickCount64() + 10000;
        while (!completed.load() && WaitForSingleObject(client.process(), 0) == WAIT_TIMEOUT && GetTickCount64() < deadline) Sleep(10);
        if (malformed || (skipStartupAnimation && malformedLogin)) {
            require(WaitForSingleObject(client.process(), 5000) == WAIT_OBJECT_0 && !completed.load(), "malformed XML was executed");
            std::string error;
            try { client.check(); } catch (const std::exception& failure) { error = failure.what(); }
            require(error.find("client resource XML structure mismatch") != std::string::npos, "resource failure was not propagated");
        } else {
            client.check(); require(completed.load(), "synthetic movement hooks did not complete");
            fixture.verify(client.process(), skipStartupAnimation);
        }
        client.stop(); overlay.rollback();
    } catch (...) { client.stop(); overlay.rollback(); throw; }
    require(!overlay.installed(), "exited movement client retained remote ownership");
}
}

bool verifyMovementHookExecution() {
    const auto directory = t7::fs::temp_directory_path() / (L"t7-movement-" + std::to_wstring(GetCurrentProcessId())
        + L"-" + std::to_wstring(GetTickCount64()));
    try {
        require(t7::fs::create_directory(directory), "fresh movement fixture directory");
        run(directory / "MovementFixture.exe", false);
        run(directory / "MalformedFixture.exe", true);
        run(directory / "SkipStartupFixture.exe", false, true);
        run(directory / "MalformedStartupFixture.exe", false, true, true);
        run(directory / "UnchangedStartupFixture.exe", false, false, true);
        require(t7::fs::canonical(directory).parent_path() == t7::fs::canonical(t7::fs::temp_directory_path()), "fixture cleanup boundary");
        t7::fs::remove_all(directory);
        std::cout << "Synthetic x86 movement/startup hooks, XML pool ownership, option isolation and failure cleanup passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Movement hook execution: " << error.what() << '\n'; return false;
    }
}
