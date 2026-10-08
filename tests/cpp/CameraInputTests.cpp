#include "../../src/Runtime/launcher/DebugClient.h"
#include <algorithm>
#include <atomic>
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
constexpr uint32_t BASE = 0x400000, WHEEL_RVA = 0x4CBAE4;
constexpr uint32_t CAMERA = BASE + 0x2100, RESULT = BASE + 0x2200;
constexpr uint32_t STACK = BASE + 0x2000, UI_CALLS = STACK + 4, ZOOM = STACK + 8;
struct Input { unsigned char blocked, active, uiHandled; int direction; };
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
void word(t7::Bytes& bytes, uint32_t value) {
    for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<unsigned char>(value >> shift));
}
t7::MemoryPatch wheelPatch() {
    t7::Bytes image(0x3B54000, 0); size_t offset = 0x1000;
    for (const auto* name : {"TerSafe", "TerSafe", "GeTssAntiService", "GeTssAntiService", "GeTssAntiService",
                             "GeTssAntiService", "GeTssAntiService", "QQPCfix.dll"}) {
        std::copy_n(name, std::strlen(name), image.begin() + offset); offset += 32;
    }
    const auto patches = t7::clientMemoryPatches(image);
    const auto found = std::find_if(patches.begin(), patches.end(), [](const auto& patch) { return patch.rva == WHEEL_RVA; });
    require(found != patches.end(), "wheel dispatch does not honor the camera input gate");
    return *found;
}
uint32_t createFixture(const t7::fs::path& path, const t7::MemoryPatch& patch, const std::vector<Input>& inputs) {
    constexpr uint32_t HEADER = 0x200, RAW_SIZE = 0x4CB000;
    t7::Bytes image(HEADER + RAW_SIZE, 0);
    IMAGE_DOS_HEADER dos{}; dos.e_magic = IMAGE_DOS_SIGNATURE; dos.e_lfanew = 0x80;
    std::memcpy(image.data(), &dos, sizeof(dos));
    IMAGE_NT_HEADERS32 nt{}; nt.Signature = IMAGE_NT_SIGNATURE;
    nt.FileHeader.Machine = IMAGE_FILE_MACHINE_I386; nt.FileHeader.NumberOfSections = 1;
    nt.FileHeader.SizeOfOptionalHeader = sizeof(nt.OptionalHeader);
    nt.FileHeader.Characteristics = IMAGE_FILE_EXECUTABLE_IMAGE | IMAGE_FILE_32BIT_MACHINE | IMAGE_FILE_RELOCS_STRIPPED;
    nt.OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR32_MAGIC;
    nt.OptionalHeader.ImageBase = BASE; nt.OptionalHeader.AddressOfEntryPoint = 0x1000;
    nt.OptionalHeader.BaseOfCode = 0x1000; nt.OptionalHeader.SizeOfCode = RAW_SIZE;
    nt.OptionalHeader.SectionAlignment = 0x1000; nt.OptionalHeader.FileAlignment = 0x200;
    nt.OptionalHeader.MajorOperatingSystemVersion = 6; nt.OptionalHeader.MajorSubsystemVersion = 6;
    nt.OptionalHeader.SizeOfImage = 0x2E36000; nt.OptionalHeader.SizeOfHeaders = HEADER;
    nt.OptionalHeader.Subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI;
    nt.OptionalHeader.SizeOfStackReserve = 0x100000; nt.OptionalHeader.SizeOfStackCommit = 0x1000;
    nt.OptionalHeader.SizeOfHeapReserve = 0x100000; nt.OptionalHeader.SizeOfHeapCommit = 0x1000;
    nt.OptionalHeader.NumberOfRvaAndSizes = IMAGE_NUMBEROF_DIRECTORY_ENTRIES;
    std::memcpy(image.data() + dos.e_lfanew, &nt, sizeof(nt));
    IMAGE_SECTION_HEADER section{}; std::memcpy(section.Name, ".fixture", 8);
    section.VirtualAddress = 0x1000; section.Misc.VirtualSize = nt.OptionalHeader.SizeOfImage - 0x1000;
    section.PointerToRawData = HEADER; section.SizeOfRawData = RAW_SIZE;
    section.Characteristics = IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE | IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE;
    std::memcpy(image.data() + dos.e_lfanew + sizeof(nt), &section, sizeof(section));
    const auto put = [&](uint32_t rva, const t7::Bytes& bytes) {
        const auto raw = HEADER + rva - 0x1000;
        require(raw + bytes.size() <= image.size(), "camera fixture extent");
        std::copy(bytes.begin(), bytes.end(), image.begin() + raw);
    };
    // The existing predicate checks operator activation and camera blocking.
    put(0x4B2C70, {0x80,0x79,0x61,1,0x75,9,0x80,0x79,0x60,0,0x75,3,0xB0,1,0xC3,0x32,0xC0,0xC3});
    auto dispatch = patch.expected;
    dispatch.insert(dispatch.end(), {0x0F,0xB6,0x45,0xB3,0xC3});
    put(WHEEL_RVA, dispatch);
    t7::Bytes ui{0xFF,0x05}; word(ui, UI_CALLS); ui.push_back(0xC3); put(0x1F00, ui);
    t7::Bytes code{0xC7,0x05}; word(code, 0x3235368); word(code, CAMERA);
    code.push_back(0xBD); word(code, STACK + 0x80); // synthetic caller frame, blocked byte at +0x33
    code.push_back(0xBB); word(code, 0x11223344);
    code.push_back(0xBE); word(code, 0x22334455);
    code.push_back(0xBF); word(code, 0x33445566);
    const auto store = [&](unsigned char modrm, uint32_t address) { code.insert(code.end(), {0x89,modrm}); word(code, address); };
    const auto call = [&](uint32_t rva) { code.push_back(0xE8); word(code, rva - (0x1000 + static_cast<uint32_t>(code.size()) + 4)); };
    store(0x25, STACK);
    for (size_t index = 0; index < inputs.size(); ++index) {
        const auto& input = inputs[index];
        code.insert(code.end(), {0x66,0xC7,0x05}); word(code, CAMERA + 0x60);
        code.insert(code.end(), {input.blocked,input.active,0xB0,input.uiHandled});
        call(0x1F00); // UI receives the wheel before game-camera filtering.
        call(WHEEL_RVA);
        const auto result = RESULT + static_cast<uint32_t>(index) * 8;
        store(0x05, result);
        code.insert(code.end(), {0x85,0xC0,0x75,6,0xFF,static_cast<unsigned char>(input.direction > 0 ? 0x05 : 0x0D)});
        word(code, ZOOM);
        code.push_back(0xA1); word(code, ZOOM); store(0x05, result + 4);
    }
    store(0x1D, STACK + 0x10); store(0x35, STACK + 0x14); store(0x3D, STACK + 0x18);
    store(0x2D, STACK + 0x1C); store(0x25, STACK + 0x20);
    const auto complete = BASE + 0x1000 + static_cast<uint32_t>(code.size());
    code.insert(code.end(), {0xCC,0xEB,0xFE});
    require(code.size() < 0xF00, "camera fixture code overlaps UI callback"); put(0x1000, code);
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char*>(image.data()), static_cast<std::streamsize>(image.size()));
    require(output.good(), "camera fixture write");
    return complete;
}
void execute(const t7::fs::path& path, const t7::MemoryPatch& patch, const std::vector<Input>& inputs,
             uint32_t complete, bool adapted) {
    std::atomic<bool> finished{false}; t7::DebugClient client;
    client.start(path, t7::fileHash(path), {}, [&](HANDLE process, HANDLE) {
        if (adapted) t7::applyRemotePatches(process, BASE, {patch});
    }, {}, {}, {}, [&](DWORD, uintptr_t address) {
        if (address != complete) return false;
        finished.store(true); return true;
    });
    const auto deadline = GetTickCount64() + 10000;
    while (!finished.load() && GetTickCount64() < deadline) { client.check(); Sleep(2); }
    require(finished.load(), "camera fixture did not complete");
    const auto read = [&](uint32_t address) {
        const auto bytes = t7::readClientMemory(client.process(), address, 4);
        uint32_t value = 0; std::memcpy(&value, bytes.data(), 4); return value;
    };
    int zoom = 0;
    for (size_t index = 0; index < inputs.size(); ++index) {
        const auto& input = inputs[index];
        const bool blocked = (!input.active || input.blocked) && (adapted || input.uiHandled);
        require(read(RESULT + static_cast<uint32_t>(index) * 8) == static_cast<uint32_t>(blocked), "wheel input gate result mismatch");
        if (!blocked) zoom += input.direction;
        require(read(RESULT + static_cast<uint32_t>(index) * 8 + 4) == static_cast<uint32_t>(zoom),
                "menu wheel leaked into camera zoom or remained blocked after closing");
    }
    require(read(UI_CALLS) == inputs.size(), "camera filtering swallowed UI wheel events");
    require(read(STACK) == read(STACK + 0x20) && read(STACK + 0x1C) == STACK + 0x80,
            "wheel dispatch changed the stack/frame");
    require(read(STACK + 0x10) == 0x11223344 && read(STACK + 0x14) == 0x22334455
            && read(STACK + 0x18) == 0x33445566, "wheel dispatch changed nonvolatile registers");
    client.stop(); client.check();
}
}
bool verifyCameraInput() {
    const auto root = t7::fs::temp_directory_path() / (L"t7-camera-" + std::to_wstring(GetCurrentProcessId())
                                                    + L"-" + std::to_wstring(GetTickCount64()));
    const auto cleanup = [&] {
        require(t7::fs::canonical(root).parent_path() == t7::fs::canonical(t7::fs::temp_directory_path()),
                "camera fixture cleanup boundary");
        t7::fs::remove_all(root);
    };
    try {
        const auto patch = wheelPatch();
        require(patch.expected.size() == 27 && patch.replacement.size() == 27
            && std::all_of(patch.replacement.begin(), patch.replacement.begin() + 4, [](auto byte) { return byte == 0x90; })
            && std::equal(patch.expected.begin() + 4, patch.expected.end(), patch.replacement.begin() + 4),
            "wheel adaptation changed more than the skipped input gate");
        require(t7::fs::create_directory(root), "fresh camera fixture directory");
        std::vector<Input> inputs;
        const t7::Bytes flags{0,1};
        for (auto blocked : flags) for (auto active : flags)
            for (auto handled : flags) for (int direction : {1,-1})
                inputs.push_back({blocked,active,handled,direction});
        for (auto active : t7::Bytes{1,0,1,0,1}) inputs.push_back({0,active,0,1});
        const auto path = root / "camera.exe";
        const auto complete = createFixture(path, patch, inputs);
        const auto diskHash = t7::fileHash(path);
        execute(path, patch, inputs, complete, false);
        execute(path, patch, inputs, complete, true);
        require(t7::fileHash(path) == diskHash, "camera adaptation changed the disk fixture");
        cleanup();
        std::cout << "Camera wheel input gate, UI delivery, reopen/close and x86 calling convention cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Camera input test failure: " << error.what() << '\n';
        try { if (t7::fs::exists(root)) cleanup(); }
        catch (const std::exception& cleanupError) { std::cerr << "Camera fixture cleanup: " << cleanupError.what() << '\n'; }
        return false;
    }
}
