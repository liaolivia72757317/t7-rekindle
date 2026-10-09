#include "../../src/Runtime/launcher/EndpointStorage.h"
#include "../../src/Runtime/launcher/DebugClient.h"
#include "../../src/Runtime/launcher/ClientImports.h"
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
constexpr uint32_t BASE = 0x400000, IMAGE_SIZE = 0x1C90000, HEADER_SIZE = 0x200;
constexpr uint32_t OBJECT = BASE + 0x1C8E2BC, VTABLE = BASE + 0x173432C;
constexpr uint32_t VECTOR_ALLOCATE = BASE + 0x7F990, VECTOR_FREE = BASE + 0x7F9D0;
constexpr uint32_t STRING_ALLOCATE = BASE + 0x1500, STRING_FREE = BASE + 0x1600;
constexpr uint32_t CONTROL = BASE + 0x3000, BLOCKS = CONTROL + 32;
constexpr uint32_t SLEEP_IAT = BASE + 0x7000, EXIT_IAT = SLEEP_IAT + 4;
constexpr uint32_t ALIGNED_ALLOCATE_IAT = BASE + 0x7100, ALIGNED_FREE_IAT = ALIGNED_ALLOCATE_IAT + 4;
constexpr uint32_t MALLOC_IAT = ALIGNED_ALLOCATE_IAT + 8, FREE_IAT = ALIGNED_ALLOCATE_IAT + 12;
void require(bool result, const char* message) { if (!result) throw std::runtime_error(message); }
class Code {
public:
    t7::Bytes bytes;
    explicit Code(uint32_t address) : address_(address) {}
    void emit(std::initializer_list<unsigned char> data) { bytes.insert(bytes.end(), data); }
    void word(uint32_t value) {
        for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<unsigned char>(value >> shift));
    }
    void call(uint32_t target) { emit({0xE8}); word(target - (address_ + static_cast<uint32_t>(bytes.size()) + 4)); }
    void indirect(uint32_t iat) { emit({0xFF, 0x15}); word(iat); }
    size_t branch(unsigned char condition = 0) {
        if (condition) emit({0x0F, condition}); else emit({0xE9});
        const auto field = bytes.size(); word(0); return field;
    }
    void bind(size_t field, size_t target) {
        const auto displacement = static_cast<uint32_t>(target) - static_cast<uint32_t>(field + 4);
        std::memcpy(bytes.data() + field, &displacement, 4);
    }
    void bind(size_t field) { bind(field, bytes.size()); }
private:
    uint32_t address_;
};
template<class T> void writeValue(HANDLE process, uint32_t address, const T& value) {
    const auto data = reinterpret_cast<const unsigned char*>(&value);
    t7::writeClientMemory(process, address, t7::Bytes(data, data + sizeof(value)));
}
uint32_t readWord(HANDLE process, uint32_t address) {
    const auto bytes = t7::readClientMemory(process, address, 4);
    uint32_t value = 0; std::memcpy(&value, bytes.data(), 4); return value;
}
void fixture(const t7::fs::path& path) {
    t7::Bytes memory(IMAGE_SIZE, 0);
    const auto put = [&](uint32_t address, const t7::Bytes& data) {
        require(address >= BASE && address - BASE + data.size() <= memory.size(), "endpoint fixture code extent");
        std::memcpy(memory.data() + address - BASE, data.data(), data.size());
    };
    const auto word = [&](uint32_t address, uint32_t value) { std::memcpy(memory.data() + address - BASE, &value, 4); };
    // The fixture uses the Windows CRT; no game files or redistributable are needed.
    const auto imports = t7::writeClientImports(memory, 0x6000, 0x1000, {
        {"kernel32.dll", SLEEP_IAT - BASE, {"Sleep", "ExitProcess"}},
        {"msvcrt.dll", ALIGNED_ALLOCATE_IAT - BASE, {"_aligned_malloc", "_aligned_free", "malloc", "free"}}
    });
    word(OBJECT, VTABLE); word(OBJECT + 4, 16);
    word(VTABLE + 4, VECTOR_ALLOCATE); word(VTABLE + 8, VECTOR_FREE);
    word(BASE + 0x16F67F8, STRING_ALLOCATE); word(BASE + 0x16F67F4, STRING_FREE);

    Code fail(BASE + 0x1400);
    fail.emit({0xFF, 0x05}); fail.word(CONTROL + 12); // allocation call count
    fail.emit({0xA1}); fail.word(CONTROL + 12);
    fail.emit({0x3B, 0x05}); fail.word(CONTROL + 8); // injected failure index
    fail.emit({0x0F, 0x94, 0xC0, 0x0F, 0xB6, 0xC0, 0xC3}); put(BASE + 0x1400, fail.bytes);

    Code allocate(VECTOR_ALLOCATE);
    // The client increments allocator accounting before trying the CRT allocation.
    allocate.emit({0x56, 0x8B, 0xF1, 0x8B, 0x54, 0x24, 0x08, 0x01, 0x56, 0x08});
    allocate.call(BASE + 0x1400);
    allocate.emit({0x85, 0xC0}); const auto reject = allocate.branch(0x85);
    allocate.emit({0x6A, 0x10, 0xFF, 0x74, 0x24, 0x0C}); allocate.indirect(ALIGNED_ALLOCATE_IAT);
    allocate.emit({0x83, 0xC4, 0x08});
    const auto done = allocate.bytes.size(); allocate.emit({0x5E, 0xC2, 0x04, 0x00});
    allocate.bind(reject); allocate.emit({0x33, 0xC0}); allocate.bind(allocate.branch(), done);
    require(allocate.bytes.size() <= VECTOR_FREE - VECTOR_ALLOCATE, "endpoint fixture allocator overlaps free");
    put(VECTOR_ALLOCATE, allocate.bytes);

    Code release(VECTOR_FREE);
    release.emit({0x56, 0x8B, 0xF1, 0x8B, 0x44, 0x24, 0x0C, 0x29, 0x46, 0x08,
                  0xFF, 0x74, 0x24, 0x08}); release.indirect(ALIGNED_FREE_IAT);
    release.emit({0x83, 0xC4, 0x04, 0x5E, 0xC2, 0x08, 0x00}); put(VECTOR_FREE, release.bytes);

    Code stringAllocate(STRING_ALLOCATE); stringAllocate.call(BASE + 0x1400);
    stringAllocate.emit({0x85, 0xC0}); const auto rejectString = stringAllocate.branch(0x85);
    stringAllocate.emit({0xFF, 0x74, 0x24, 0x04}); stringAllocate.indirect(MALLOC_IAT);
    stringAllocate.emit({0x83, 0xC4, 0x04, 0x85, 0xC0}); const auto emptyString = stringAllocate.branch(0x84);
    stringAllocate.emit({0xFF, 0x05}); stringAllocate.word(CONTROL + 16);
    stringAllocate.bind(emptyString); stringAllocate.emit({0xC3});
    stringAllocate.bind(rejectString); stringAllocate.emit({0x33, 0xC0, 0xC3}); put(STRING_ALLOCATE, stringAllocate.bytes);
    Code stringFree(STRING_FREE);
    stringFree.emit({0xFF, 0x0D}); stringFree.word(CONTROL + 16);
    stringFree.emit({0xFF, 0x74, 0x24, 0x04}); stringFree.indirect(FREE_IAT);
    stringFree.emit({0x83, 0xC4, 0x04, 0xC3}); put(STRING_FREE, stringFree.bytes);

    Code main(BASE + 0x1000);
    main.emit({0xC7, 0x05}); main.word(CONTROL); main.word(1);
    const auto poll = main.bytes.size();
    main.emit({0x83, 0x3D}); main.word(CONTROL + 4); main.emit({0}); const auto stop = main.branch(0x85);
    main.emit({0x6A, 0x0A}); main.indirect(SLEEP_IAT); main.bind(main.branch(), poll); main.bind(stop);
    main.emit({0x8B, 0x35}); main.word(BLOCKS + 4); main.emit({0x85, 0xF6}); const auto noRecords = main.branch(0x84);
    main.emit({0x8B, 0x36}); // the outer vector owns the records vector
    for (uint32_t i = 0; i < 4; ++i) {
        main.emit({0x81, 0xBE}); main.word(i * 52 + 48); main.word(16);
        const auto inlineString = main.branch(0x82);
        main.emit({0xFF, 0xB6}); main.word(i * 52 + 28); main.call(STRING_FREE);
        main.emit({0x83, 0xC4, 0x04}); main.bind(inlineString);
    }
    main.emit({0x68}); main.word(t7::ENDPOINT_BLOCK_SIZES[0]); main.emit({0x56, 0xB9}); main.word(OBJECT);
    main.call(VECTOR_FREE); main.bind(noRecords);
    for (uint32_t i = 1; i < 3; ++i) {
        main.emit({0xA1}); main.word(BLOCKS + i * 4); main.emit({0x85, 0xC0}); const auto missing = main.branch(0x84);
        main.emit({0x68}); main.word(t7::ENDPOINT_BLOCK_SIZES[i]); main.emit({0x50, 0xB9}); main.word(OBJECT);
        main.call(VECTOR_FREE); main.bind(missing);
    }
    main.emit({0xA1}); main.word(OBJECT + 8); main.emit({0x0B, 0x05}); main.word(CONTROL + 16);
    main.emit({0x0F, 0x95, 0xC0, 0x0F, 0xB6, 0xC0, 0x50}); main.indirect(EXIT_IAT);
    require(main.bytes.size() <= 0x400, "endpoint fixture entry overlaps allocator helpers"); put(BASE + 0x1000, main.bytes);

    t7::Bytes header(HEADER_SIZE, 0);
    IMAGE_DOS_HEADER dos{}; dos.e_magic = IMAGE_DOS_SIGNATURE; dos.e_lfanew = 0x80;
    std::memcpy(header.data(), &dos, sizeof(dos));
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
    nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT] = {imports.rva, imports.size};
    nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IAT] = {imports.iatRva, imports.iatSize};
    std::memcpy(header.data() + dos.e_lfanew, &nt, sizeof(nt));
    IMAGE_SECTION_HEADER section{}; std::memcpy(section.Name, ".fixture", 8);
    section.VirtualAddress = 0x1000; section.Misc.VirtualSize = IMAGE_SIZE - 0x1000;
    section.PointerToRawData = HEADER_SIZE; section.SizeOfRawData = IMAGE_SIZE - 0x1000;
    section.Characteristics = IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE;
    std::memcpy(header.data() + dos.e_lfanew + sizeof(nt), &section, sizeof(section));
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char*>(header.data()), header.size());
    output.write(reinterpret_cast<const char*>(memory.data() + 0x1000), memory.size() - 0x1000);
    require(output.good(), "endpoint fixture write");
}
void run(const t7::fs::path& path, const std::string& hash, const t7::Config& config,
         unsigned parity, uint32_t failure = 0, bool unpublished = false, bool legacy = false) {
    t7::DebugClient client;
    client.start(path, hash, {}, [](HANDLE, HANDLE) {});
    const auto process = client.process();
    const auto deadline = GetTickCount64() + 10000;
    while (!readWord(process, CONTROL) && GetTickCount64() < deadline) { client.check(); Sleep(10); }
    require(readWord(process, CONTROL) == 1, "endpoint fixture startup");
    const auto allocator = t7::clientEndpointAllocator(process, BASE);
    writeValue(process, CONTROL + 8, failure);
    t7::EndpointAddresses addresses{};
    bool failed = false;
    if (legacy) {
        const auto page = VirtualAllocEx(process, nullptr, 8192, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        require(page && reinterpret_cast<uintptr_t>(page) <= UINT32_MAX - 8192, "legacy endpoint page");
        // Keep the missing allocator header unreadable regardless of adjacent mappings.
        DWORD old = 0;
        require(VirtualProtectEx(process, page, 4096, PAGE_NOACCESS, &old), "legacy endpoint guard page");
        addresses.records = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(page)) + 4096;
        addresses.group = addresses.records + 208; addresses.candidates = addresses.records + 348;
    } else {
        try { addresses = t7::allocateEndpointStorage(process, allocator, config, parity); }
        catch (const std::runtime_error& error) {
            if (!failure || std::string(error.what()).find("partial allocations released") == std::string::npos) throw;
            failed = true;
        }
        require(failed == (failure != 0), "endpoint allocation failure was ignored");
        if (failed) require(readWord(process, CONTROL + 12) == failure, "allocation continued after failure");
    }
    if (!failed) {
        const auto layout = t7::makeEndpointLayout(config, addresses, parity);
        require(layout.group[0] == addresses.records && layout.group[1] == addresses.records + 208
                && layout.group[2] == layout.group[1], "endpoint nested vector ownership mismatch");
        writeValue(process, addresses.records, layout.records); writeValue(process, addresses.group, layout.group);
        writeValue(process, addresses.candidates, layout.candidates);
        for (size_t i = 0; i < 4; ++i) {
            const auto descriptor = config.advertisedAddress + ":" + std::to_string(config.ports[1]);
            const auto& record = layout.records[i];
            require(record.port == config.ports[1], "startup and retry endpoints must use the logic port");
            require((addresses.descriptors[i] != 0) == (descriptor.size() > 15), "endpoint SSO ownership mismatch");
            require(record.descriptor.length == descriptor.size(), "endpoint descriptor length mismatch");
            require(record.descriptor.capacity == (descriptor.size() > 15 ? 31u : 15u), "endpoint descriptor capacity mismatch");
            require(descriptor == (addresses.descriptors[i] ? layout.descriptors[i] : record.descriptor.buffer),
                    "endpoint descriptor must match the logic port");
            if (addresses.descriptors[i]) {
                writeValue(process, addresses.descriptors[i], layout.descriptors[i]);
                require(readWord(process, addresses.records + static_cast<uint32_t>(i) * 52 + 28) == addresses.descriptors[i],
                        "endpoint string does not own its allocation");
            }
        }
        if (unpublished) t7::freeEndpointStorage(process, allocator, addresses);
        else writeValue(process, BLOCKS, addresses);
    }
    if (failed || unpublished) {
        require(readWord(process, OBJECT + 8) == 0 && readWord(process, CONTROL + 16) == 0,
                "unpublished endpoint storage leaked or was released twice");
    }
    writeValue(process, CONTROL + 4, uint32_t{1});
    require(WaitForSingleObject(process, 10000) == WAIT_OBJECT_0, "endpoint fixture did not exit");
    client.stop();
    std::string error;
    try { client.check(); } catch (const std::exception& failureError) { error = failureError.what(); }
    if (legacy) require(error.find("unhandled client exception code=3221225477") != std::string::npos
                        && error.find("module=msvcrt.dll") != std::string::npos,
                        "legacy VirtualAlloc/aligned-free mismatch was not reproduced");
    else {
        require(error.empty(), error.c_str());
        DWORD exitCode = 0;
        require(GetExitCodeProcess(process, &exitCode) && exitCode == 0, "client endpoint destruction leaked or crashed");
    }
}
}

bool verifyEndpointStorage() {
    const auto root = t7::fs::temp_directory_path() / (L"t7-endpoint-" + std::to_wstring(GetCurrentProcessId())
                                                    + L"-" + std::to_wstring(GetTickCount64()));
    const auto cleanup = [&] {
        require(t7::fs::canonical(root).parent_path() == t7::fs::canonical(t7::fs::temp_directory_path()), "endpoint fixture cleanup boundary");
        t7::fs::remove_all(root);
    };
    try {
        require(t7::fs::create_directory(root), "fresh endpoint fixture directory");
        const auto path = root / "EndpointFixture.exe";
        fixture(path); const auto hash = t7::fileHash(path);
        t7::Config shortConfig; shortConfig.ports[0] = 1; shortConfig.ports[1] = 2; shortConfig.ports[2] = 3;
        run(path, hash, shortConfig, 0, 0, false, true);
        t7::Config heapConfig = shortConfig;
        heapConfig.advertisedAddress = "192.0.2.123"; heapConfig.ports[0] = 123; heapConfig.ports[1] = 12345;
        t7::Config inlineConfig = heapConfig; inlineConfig.ports[0] = 12345; inlineConfig.ports[1] = 123;
        t7::Config longConfig = heapConfig; longConfig.advertisedAddress = "203.0.113.123";
        for (unsigned parity : {0u, 1u}) {
            run(path, hash, shortConfig, parity);
            run(path, hash, inlineConfig, parity);
            run(path, hash, heapConfig, parity);
            run(path, hash, longConfig, parity);
            run(path, hash, longConfig, parity, 0, true);
        }
        for (uint32_t failedAllocation = 1; failedAllocation <= 7; ++failedAllocation)
            run(path, hash, longConfig, 0, failedAllocation);
        cleanup();
        std::cout << "Logic endpoint routing, allocator pairing, SSO, partial failure, unpublished cleanup and x86 normal exit passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Endpoint storage test failure: " << error.what() << '\n';
        try { if (t7::fs::exists(root)) cleanup(); }
        catch (const std::exception& cleanupError) { std::cerr << "Endpoint fixture cleanup: " << cleanupError.what() << '\n'; }
        return false;
    }
}
