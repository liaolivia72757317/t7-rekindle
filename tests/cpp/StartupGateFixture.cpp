#include "StartupGateFixture.h"
#include "../../src/Runtime/launcher/ClientImports.h"
#include <algorithm>
#include <cstring>
#include <fstream>

namespace startupFixture {
namespace {
constexpr uint32_t IMAGE_SIZE = 0x243A000, HEADER_SIZE = 0x200;
constexpr uint32_t SLEEP = BASE + 0x7000, EXIT = SLEEP + 4, CREATE_THREAD = SLEEP + 8;
constexpr uint32_t CLOSE_HANDLE = SLEEP + 12, GET_ACP = SLEEP + 16;
constexpr uint32_t ALIGNED_MALLOC = BASE + 0x7100, ALIGNED_FREE = ALIGNED_MALLOC + 4;
constexpr uint32_t MALLOC = ALIGNED_MALLOC + 8, FREE = ALIGNED_MALLOC + 12;
constexpr uint32_t WORKER = BASE + 0x1500, INITIALIZE = BASE + 0x89090;
constexpr uint32_t ALLOCATOR = BASE + 0x1C8E2BC, ALLOCATOR_VTABLE = BASE + 0x173432C;
class Code {
public:
    t7::Bytes bytes;
    explicit Code(uint32_t address) : address_(address) {}
    void emit(std::initializer_list<unsigned char> values) { bytes.insert(bytes.end(), values); }
    void word(uint32_t value) {
        for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<unsigned char>(value >> shift));
    }
    void call(uint32_t target) { emit({0xE8}); word(target - (address_ + static_cast<uint32_t>(bytes.size()) + 4)); }
    void indirect(uint32_t slot) { emit({0xFF,0x15}); word(slot); }
    void store(uint32_t address) { emit({0xA3}); word(address); }
    size_t branch(unsigned char condition = 0) {
        if (condition) emit({0x0F,condition}); else emit({0xE9});
        const auto field = bytes.size(); word(0); return field;
    }
    void bind(size_t field, size_t target) {
        const auto value = static_cast<uint32_t>(target) - static_cast<uint32_t>(field + 4);
        std::memcpy(bytes.data() + field, &value, 4);
    }
    void bind(size_t field) { bind(field, bytes.size()); }
private:
    uint32_t address_;
};
void manifest(const t7::fs::path& path, const std::string& codePage) {
    if (codePage.empty()) return;
    std::string xml = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<assembly xmlns=\"urn:schemas-microsoft-com:asm.v1\" manifestVersion=\"1.0\">"
        "<assemblyIdentity name=\"T7.StartupFixture\" version=\"1.0.0.0\" type=\"win32\" processorArchitecture=\"x86\"/>"
        "<application xmlns=\"urn:schemas-microsoft-com:asm.v3\"><windowsSettings>"
        "<activeCodePage xmlns=\"http://schemas.microsoft.com/SMI/2019/WindowsSettings\">" + codePage +
        "</activeCodePage></windowsSettings></application></assembly>";
    const auto update = BeginUpdateResourceW(path.c_str(), FALSE);
    require(update != nullptr, "startup fixture manifest open");
    if (!UpdateResourceW(update, RT_MANIFEST, MAKEINTRESOURCEW(1), 0, xml.data(), static_cast<DWORD>(xml.size()))) {
        require(EndUpdateResourceW(update, TRUE), "startup fixture manifest abort");
        throw std::runtime_error("startup fixture manifest write");
    }
    require(EndUpdateResourceW(update, FALSE), "startup fixture manifest commit");
}
}
void require(bool condition, const char* message) { if (!condition) throw std::runtime_error(message); }
uint32_t readWord(HANDLE process, uint32_t address) {
    const auto bytes = t7::readClientMemory(process, address, 4);
    uint32_t value = 0; std::memcpy(&value, bytes.data(), 4); return value;
}
void writeWord(HANDLE process, uint32_t address, uint32_t value) {
    t7::Bytes bytes(4); std::memcpy(bytes.data(), &value, 4); t7::writeClientMemory(process, address, bytes);
}
t7::MemoryPatch initializationPatch() {
    t7::Bytes image(0x3B54000, 0); size_t offset = 0x1000;
    for (const auto* name : {"TerSafe", "TerSafe", "GeTssAntiService", "GeTssAntiService", "GeTssAntiService",
                             "GeTssAntiService", "GeTssAntiService", "QQPCfix.dll"}) {
        std::copy_n(name, std::strlen(name), image.begin() + offset); offset += 32;
    }
    const auto patches = t7::clientMemoryPatches(image);
    const auto patch = std::find_if(patches.begin(), patches.end(), [](const auto& item) { return item.rva == 0x890B1; });
    require(patch != patches.end(), "startup fixture initialization patch");
    return *patch;
}
t7::fs::path create(const t7::fs::path& root, const std::string& name, const std::string& codePage) {
    t7::Bytes memory(IMAGE_SIZE, 0);
    const auto put = [&](uint32_t address, const t7::Bytes& bytes) {
        require(address >= BASE && address - BASE + bytes.size() <= memory.size(), "startup fixture extent");
        std::memcpy(memory.data() + address - BASE, bytes.data(), bytes.size());
    };
    const auto word = [&](uint32_t address, uint32_t value) { std::memcpy(memory.data() + address - BASE, &value, 4); };
    const auto imports = t7::writeClientImports(memory, 0x6000, 0x1000, {
        {"kernel32.dll", SLEEP - BASE, {"Sleep", "ExitProcess", "CreateThread", "CloseHandle", "GetACP"}},
        {"msvcrt.dll", ALIGNED_MALLOC - BASE, {"_aligned_malloc", "_aligned_free", "malloc", "free"}}
    });
    word(SERVICE, NETWORK); word(NETWORK, VTABLE); word(NETWORK + 0x1C, 10000);
    word(ALLOCATOR, ALLOCATOR_VTABLE); word(ALLOCATOR + 4, 16);
    word(ALLOCATOR_VTABLE + 4, BASE + 0x7F990); word(ALLOCATOR_VTABLE + 8, BASE + 0x7F9D0);
    word(BASE + 0x16F67F8, BASE + 0x1900); word(BASE + 0x16F67F4, BASE + 0x1920);
    word(BASE + 0x16F6A90, BASE + 0x1800);

    Code dialog(BASE + 0x1800);
    dialog.emit({0xF0,0xFF,0x05}); dialog.word(CONTROL + DialogCalls);
    dialog.emit({0xB8}); dialog.word(1); dialog.emit({0xC2,0x10,0}); put(BASE + 0x1800, dialog.bytes);
    Code loader(BASE + 0x89540);
    loader.emit({0xA1}); loader.word(CONTROL + InitializationSuccess); loader.emit({0xC3}); put(BASE + 0x89540, loader.bytes);
    Code initialize(INITIALIZE); initialize.emit({0x56,0x8B,0xF1}); initialize.bytes.resize(0x21, 0x90);
    const auto patch = initializationPatch();
    initialize.bytes.insert(initialize.bytes.end(), patch.expected.begin(), patch.expected.end());
    initialize.emit({0xC7,0x46,0x1C}); initialize.word(10001);
    initialize.emit({0xB0,0x01,0x5E,0xC3}); put(INITIALIZE, initialize.bytes);

    Code selector(SELECTOR);
    selector.emit({0x55,0x8B,0xEC,0x6A,0xFF,0x68,0x78,0x7F,0x6E,0x01,0x64,0xA1,0,0,0,0});
    selector.emit({0x53,0x56,0x57,0x8B,0x81,0x90,0,0,0,0x2B,0x81,0x8C,0,0,0,0x33,0xD2,0xBE}); selector.word(12);
    selector.emit({0xF7,0xF6,0x8B,0xF0,0x8B,0x41,0x1C,0x33,0xD2,0xF7,0xF6});
    selector.emit({0xF0,0xFF,0x05}); selector.word(CONTROL + Selections);
    selector.emit({0x5F,0x5E,0x5B,0x8B,0xE5,0x5D,0xB8}); selector.word(1);
    selector.emit({0xC2,0x08,0}); put(SELECTOR, selector.bytes);

    Code worker(WORKER);
    worker.emit({0x83,0x7C,0x24,0x04,0}); const auto primary = worker.branch(0x84);
    const auto peerWait = worker.bytes.size();
    worker.emit({0x83,0x3D}); worker.word(CONTROL + PeerReady); worker.emit({0}); const auto peerGo = worker.branch(0x85);
    worker.emit({0x6A,0x05}); worker.indirect(SLEEP); worker.bind(worker.branch(), peerWait);
    worker.bind(primary); worker.bind(peerGo);
    worker.emit({0x6A,0,0x6A,0,0xB9}); worker.word(NETWORK); worker.call(SELECTOR);
    worker.emit({0x33,0xC0,0xC2,0x04,0}); put(WORKER, worker.bytes);

    Code main(BASE + 0x1000);
    main.indirect(GET_ACP); main.store(CONTROL + CodePage);
    main.emit({0x89,0x25}); main.word(CONTROL + StackBefore);
    main.emit({0xBE}); main.word(0x11223344); main.emit({0xB9}); main.word(NETWORK); main.call(INITIALIZE);
    main.emit({0x0F,0xB6,0xC0}); main.store(CONTROL + InitializationResult);
    main.emit({0x81,0xFE}); main.word(0x11223344); main.emit({0x0F,0x94,0xC0,0x0F,0xB6,0xC0}); main.store(CONTROL + RegisterValid);
    main.emit({0x3B,0x25}); main.word(CONTROL + StackBefore);
    main.emit({0x0F,0x94,0xC0,0x0F,0xB6,0xC0}); main.store(CONTROL + StackValid);
    main.emit({0xFF,0x35}); main.word(CONTROL + Delay); main.indirect(SLEEP);
    main.emit({0x83,0x3D}); main.word(CONTROL + Peer); main.emit({0}); const auto noPeer = main.branch(0x84);
    main.emit({0x6A,0,0x6A,0,0x6A,1,0x68}); main.word(WORKER); main.emit({0x6A,0,0x6A,0}); main.indirect(CREATE_THREAD);
    main.emit({0x85,0xC0}); const auto threadCreated = main.branch(0x85);
    main.emit({0xC7,0x05}); main.word(CONTROL + Error); main.word(1); main.bind(threadCreated);
    main.emit({0x50}); main.indirect(CLOSE_HANDLE); main.bind(noPeer);
    main.emit({0x6A,0}); main.call(WORKER);
    const auto wait = main.bytes.size();
    main.emit({0x83,0x3D}); main.word(CONTROL + Exit); main.emit({0}); const auto done = main.branch(0x85);
    main.emit({0x6A,0x05}); main.indirect(SLEEP); main.bind(main.branch(), wait); main.bind(done);
    main.emit({0x6A,0}); main.indirect(EXIT); put(BASE + 0x1000, main.bytes);

    Code allocate(BASE + 0x7F990);
    allocate.emit({0x83,0x3D}); allocate.word(CONTROL + AllocationFailure); allocate.emit({0}); const auto failed = allocate.branch(0x85);
    allocate.emit({0x6A,0x10,0xFF,0x74,0x24,0x08}); allocate.indirect(ALIGNED_MALLOC);
    allocate.emit({0x83,0xC4,0x08,0xC2,0x04,0}); allocate.bind(failed); allocate.emit({0x33,0xC0,0xC2,0x04,0});
    require(allocate.bytes.size() <= 0x40, "startup fixture allocator overlap"); put(BASE + 0x7F990, allocate.bytes);
    Code free(BASE + 0x7F9D0); free.emit({0xFF,0x74,0x24,0x04}); free.indirect(ALIGNED_FREE);
    free.emit({0x83,0xC4,0x04,0xC2,0x08,0}); put(BASE + 0x7F9D0, free.bytes);
    Code stringAllocate(BASE + 0x1900); stringAllocate.emit({0xFF,0x25}); stringAllocate.word(MALLOC); put(BASE + 0x1900, stringAllocate.bytes);
    Code stringFree(BASE + 0x1920); stringFree.emit({0xFF,0x25}); stringFree.word(FREE); put(BASE + 0x1920, stringFree.bytes);

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
    const auto path = root / (name + ".exe");
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char*>(header.data()), header.size());
    output.write(reinterpret_cast<const char*>(memory.data() + 0x1000), memory.size() - 0x1000);
    require(output.good(), "startup fixture write"); output.close();
    manifest(path, codePage);
    return path;
}
}
