#include "../../src/Runtime/launcher/GraphicsSettings.h"
#include "../../src/Runtime/launcher/DebugClient.h"
#include "../../src/Runtime/launcher/OutputDevice.h"
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
constexpr uint32_t BASE = 0x400000, RECORD = BASE + 0x3000, SERVICE = BASE + 0x3100;
constexpr uint32_t FRAME = BASE + 0x3200, SETTING = BASE + 0x3300, CLIENT = BASE + 0x3400;
constexpr uint32_t VARIANTS = BASE + 0x4000, INDEX = BASE + 0x3500, STATS = BASE + 0x3600;
constexpr uint32_t RENDERER = BASE + 0x6000, WINDOW_STYLE = STATS + 48, RESTORE_COUNT = STATS + 52;
constexpr uint32_t ADAPTER = BASE + 0x8000, ADAPTER_ORDINAL = 257;
const GUID ADAPTER_ID{0x12345678,0x1234,0x5678,{0x9A,0xBC,0x12,0x34,0x56,0x78,0x9A,0xBC}};
void require(bool v, const char* text) { if (!v) throw std::runtime_error(text); }
void word(t7::Bytes& bytes, uint32_t v) { for (unsigned s = 0; s < 32; s += 8) bytes.push_back(static_cast<unsigned char>(v >> s)); }
size_t branch(t7::Bytes& bytes, unsigned char condition) {
    bytes.insert(bytes.end(), {0x0F, condition}); const auto position = bytes.size(); word(bytes, 0); return position;
}
void bind(t7::Bytes& bytes, size_t position) {
    const auto delta = static_cast<uint32_t>(bytes.size() - position - 4);
    std::memcpy(bytes.data() + position, &delta, 4);
}
void writeWord(HANDLE process, uint32_t address, uint32_t value) {
    t7::Bytes data; word(data, value); t7::writeClientMemory(process, address, data);
}
uint32_t readWord(HANDLE process, uint32_t address) {
    const auto bytes = t7::readClientMemory(process, address, 4);
    uint32_t value = 0; std::memcpy(&value, bytes.data(), 4); return value;
}
void fixture(const t7::fs::path& path) {
    t7::Bytes image(0x1200, 0);
    IMAGE_DOS_HEADER dos{}; dos.e_magic = IMAGE_DOS_SIGNATURE; dos.e_lfanew = 0x80;
    std::memcpy(image.data(), &dos, sizeof(dos));
    IMAGE_NT_HEADERS32 nt{}; nt.Signature = IMAGE_NT_SIGNATURE;
    nt.FileHeader.Machine = IMAGE_FILE_MACHINE_I386; nt.FileHeader.NumberOfSections = 1;
    nt.FileHeader.SizeOfOptionalHeader = sizeof(nt.OptionalHeader);
    nt.FileHeader.Characteristics = IMAGE_FILE_EXECUTABLE_IMAGE | IMAGE_FILE_32BIT_MACHINE | IMAGE_FILE_RELOCS_STRIPPED;
    nt.OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR32_MAGIC;
    nt.OptionalHeader.ImageBase = BASE; nt.OptionalHeader.AddressOfEntryPoint = 0x1000;
    nt.OptionalHeader.BaseOfCode = 0x1000; nt.OptionalHeader.SizeOfCode = 0x1000;
    nt.OptionalHeader.SectionAlignment = 0x1000; nt.OptionalHeader.FileAlignment = 0x200;
    nt.OptionalHeader.MajorOperatingSystemVersion = 6; nt.OptionalHeader.MajorSubsystemVersion = 6;
    nt.OptionalHeader.SizeOfImage = 0x2E38000; nt.OptionalHeader.SizeOfHeaders = 0x200;
    nt.OptionalHeader.Subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI;
    nt.OptionalHeader.SizeOfStackReserve = 0x100000; nt.OptionalHeader.SizeOfStackCommit = 0x1000;
    nt.OptionalHeader.SizeOfHeapReserve = 0x100000; nt.OptionalHeader.SizeOfHeapCommit = 0x1000;
    nt.OptionalHeader.NumberOfRvaAndSizes = IMAGE_NUMBEROF_DIRECTORY_ENTRIES;
    std::memcpy(image.data() + dos.e_lfanew, &nt, sizeof(nt));
    IMAGE_SECTION_HEADER section{}; std::memcpy(section.Name, ".fixture", 8);
    section.VirtualAddress = 0x1000; section.Misc.VirtualSize = nt.OptionalHeader.SizeOfImage - 0x1000;
    section.PointerToRawData = 0x200; section.SizeOfRawData = 0x1000;
    section.Characteristics = IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE | IMAGE_SCN_MEM_EXECUTE;
    std::memcpy(image.data() + dos.e_lfanew + sizeof(nt), &section, sizeof(section));
    t7::Bytes code;
    auto imm = [&](unsigned char opcode, uint32_t value) { code.push_back(opcode); word(code, value); };
    code.insert(code.end(), {0x89,0x25}); word(code, STATS + 68);
    for (int i = 0; i < 14; ++i) imm(0x68, 0x1234);
    code.push_back(0xE8); word(code, 0x2364E0 - (0x1000 + static_cast<uint32_t>(code.size()) + 4));
    imm(0xA3, STATS + 60);
    imm(0xB8, ADAPTER);
    code.push_back(0xE8); word(code, 0x33B444 - (0x1000 + static_cast<uint32_t>(code.size()) + 4));
    imm(0xA3, STATS + 64);
    code.insert(code.end(), {0x89,0x25}); word(code, STATS + 72);
    imm(0xBB, 0x11223344); imm(0xBE, 0x22334455); imm(0xBF, 0x33445566); imm(0xBD, 0x44556677);
    code.insert(code.end(), {0x89, 0x25}); word(code, STATS); // original ESP
    const auto loop = static_cast<uint32_t>(code.size());
    code.insert(code.end(), {0xD9, 0xE8}); // FLD1
    imm(0xB8, 0x3F800000); code.insert(code.end(), {0x66, 0x0F, 0x6E, 0xC0}); // XMM0 = 1.0
    imm(0xB9, FRAME); code.push_back(0xE8); word(code, t7::GRAPHICS_TICK_RVA - (0x1000 + static_cast<uint32_t>(code.size()) + 4));
    for (const auto pair : std::array<std::pair<uint32_t, uint32_t>, 5>{{{0x25,4},{0x1D,8},{0x35,12},{0x3D,16},{0x2D,20}}}) {
        code.insert(code.end(), {0x89, static_cast<unsigned char>(pair.first)}); word(code, STATS + pair.second);
    }
    code.insert(code.end(), {0xD9, 0x1D}); word(code, STATS + 24);
    code.insert(code.end(), {0x66, 0x0F, 0x7E, 0x05}); word(code, STATS + 28);
    code.push_back(0xE9); word(code, loop - (static_cast<uint32_t>(code.size()) + 4));
    std::memcpy(image.data() + 0x200, code.data(), code.size());
    std::ofstream output(path, std::ios::binary); output.write(reinterpret_cast<const char*>(image.data()), image.size());
    require(output.good(), "graphics fixture write");
}
void prepare(HANDLE process) {
    t7::writeClientMemory(process, BASE + 0x2364E0, {0x55,0x8B,0xEC,0x8B,0x45,0x08,0x8B,0x45,0x1C,0x5D,0xC2,0x38,0});
    t7::writeClientMemory(process, BASE + 0x33B444, {0x8B,0xD0,0x33,0xC9,0x8B,0x3A,0x8B,0xC1,0xC3});
    writeWord(process, RENDERER + 0x458, ADAPTER_ORDINAL); writeWord(process, RENDERER + 0x468, ADAPTER);
    t7::Bytes identifier(sizeof(GUID)); std::memcpy(identifier.data(), &ADAPTER_ID, sizeof(GUID));
    t7::writeClientMemory(process, ADAPTER + 0x43C, identifier);
    auto tick = t7::GRAPHICS_TICK_ORIGINAL; tick.insert(tick.end(), {0x5E, 0xC3});
    t7::writeClientMemory(process, BASE + t7::GRAPHICS_TICK_RVA, tick);
    for (const auto pair : std::array<std::pair<uint32_t, uint32_t>, 12>{{
        {0x283B714, SERVICE}, {SERVICE, 0x1B355AC}, {SERVICE + 0x28, RECORD}, {RECORD, 0x1B35544},
        {0x1B35544 + 0x50, BASE + 0x1200}, {0x3235AC8, SETTING}, {SETTING, 0x1BA7710},
        {0x31379F0, CLIENT}, {CLIENT, 0x1B6F28C}, {0x28396C8, FRAME}, {FRAME, 0x1B3642C}, {BASE + 0x5000, 0x30323931}
    }}) writeWord(process, pair.first, pair.second);
    t7::writeClientMemory(process, BASE + 0x5000, {'1','9','2','0','x','1','0','8','0',0});
    writeWord(process, BASE + 0x5010, 9); writeWord(process, BASE + 0x5014, 15);
    const std::array<uint32_t, 9> types{7,1,2,1,1,4,1,2,0}; // missing Swoosh uses the game's default
    const std::array<uint32_t, 9> values{BASE+0x5000,1,0,0,0,0x431F0000,1,200,0};
    for (size_t i = 0; i < types.size(); ++i) {
        writeWord(process, VARIANTS + static_cast<uint32_t>(i) * 52 + 8, types[i]);
        writeWord(process, VARIANTS + static_cast<uint32_t>(i) * 52 + 12, values[i]);
    }
    t7::Bytes getter{0xA1}; word(getter, INDEX);
    getter.insert(getter.end(), {0x6B,0xC0,52,0x05}); word(getter, VARIANTS);
    getter.insert(getter.end(), {0xFF,0x05}); word(getter, INDEX);
    getter.insert(getter.end(), {0x83,0x3D}); word(getter, INDEX); getter.insert(getter.end(), {9,0x72,10,0xC7,0x05});
    word(getter, INDEX); word(getter, 0); getter.insert(getter.end(), {0xC2,4,0});
    t7::writeClientMemory(process, BASE + 0x1200, getter);
    t7::Bytes apply{0x55,0x8B,0xEC,0x6A,0xFF,0x58,0xFF,0x05}; word(apply, STATS + 32);
    apply.insert(apply.end(), {0x8B,0x55,0x08,0x83,0x7A,0x04,0x04}); // audio-only map skips rendering
    const auto audioOnly = branch(apply, 0x84);
    apply.insert(apply.end(), {0xC7,0x05}); word(apply, VARIANTS + 7 * 52 + 12); word(apply, 60);
    apply.insert(apply.end(), {0x8B,0x82}); word(apply, 0x534); // windowsMode variant in the sorted map
    apply.insert(apply.end(), {0x83,0xF0,0x01,0xA3}); word(apply, VARIANTS + 52 + 12);
    apply.insert(apply.end(), {0xA2}); word(apply, CLIENT + 0x40);
    // Model a lost fullscreen device: config saving succeeds while the minimized window prevents reset.
    apply.insert(apply.end(), {0xF7,0x05}); word(apply, WINDOW_STYLE); word(apply, WS_MINIMIZE);
    const auto minimized = branch(apply, 0x85);
    apply.insert(apply.end(), {0x83,0x3D}); word(apply, STATS + 56); apply.push_back(0);
    const auto resetFailed = branch(apply, 0x85);
    apply.insert(apply.end(), {0xC1,0xE0,0x02,0xA3}); word(apply, RENDERER + 0x920);
    apply.insert(apply.end(), {0xC7,0x05}); word(apply, WINDOW_STYLE); word(apply, 0x16CA0000);
    bind(apply, minimized); bind(apply, resetFailed); bind(apply, audioOnly);
    apply.insert(apply.end(), {0xD9,0xEE,0x0F,0x57,0xC0,0x8B,0xE5,0x5D,0xC2,4,0});
    t7::writeClientMemory(process, 0xAEA040, apply);
    writeWord(process, 0x3120008, RENDERER); writeWord(process, RENDERER, 0x1B5B188);
    writeWord(process, RENDERER + 0x11C, BASE + 0x7000); writeWord(process, RENDERER + 0x920, 4);
    writeWord(process, CLIENT + 0x24, 1); writeWord(process, CLIENT + 0x40, 1);
    writeWord(process, WINDOW_STYLE, WS_POPUP | WS_MINIMIZE | WS_VISIBLE);
    writeWord(process, 0x1AF6A40, BASE + 0x1400); writeWord(process, 0x1AF6A6C, BASE + 0x1440);
    t7::Bytes getStyle{0xA1}; word(getStyle, WINDOW_STYLE); getStyle.insert(getStyle.end(), {0xC2,8,0});
    t7::writeClientMemory(process, BASE + 0x1400, getStyle);
    t7::Bytes showWindow{0x83,0x7C,0x24,0x08,SW_RESTORE};
    const auto notRestore = branch(showWindow, 0x85);
    showWindow.insert(showWindow.end(), {0xFF,0x05}); word(showWindow, RESTORE_COUNT);
    showWindow.insert(showWindow.end(), {0x81,0x25}); word(showWindow, WINDOW_STYLE); word(showWindow, ~static_cast<uint32_t>(WS_MINIMIZE));
    bind(showWindow, notRestore); showWindow.insert(showWindow.end(), {0xC2,8,0});
    t7::writeClientMemory(process, BASE + 0x1440, showWindow);
    writeWord(process, 0x3224998, BASE + 0x3700); writeWord(process, BASE + 0x3700, 0x1B797B4);
    writeWord(process, 0x28396D0, BASE + 0x3800);
    writeWord(process, BASE + 0x3800, 0x1B7A9F4);
    writeWord(process, SETTING + 0x18, 0x3F400000); writeWord(process, SETTING + 0x20, 0x3F000000);
    for (const auto pair : {std::pair<uint32_t, uint32_t>{t7::AUDIO_MUSIC_RVA, 0x14}, {t7::AUDIO_EFFECTS_RVA, 0x1C}}) {
        t7::Bytes setter{0x55,0x8B,0xEC,0x8A,0x45,0x08,0x88,0x41,static_cast<unsigned char>(pair.second),
            0x8B,0x45,0x0C,0x89,0x41,static_cast<unsigned char>(pair.second+4),0xFF,0x05};
        word(setter, STATS + (pair.second == 0x14 ? 40 : 44));
        setter.insert(setter.end(), {0x0F,0x57,0xC0,0x5D,0xC2,8,0});
        t7::writeClientMemory(process, BASE + pair.first, setter);
    }
}
uint32_t readCompleted(t7::GraphicsSettings& graphics, t7::GraphicsValues& values, bool expectApplied) {
    uint32_t result = 0; bool applied = false;
    const auto deadline = GetTickCount64() + 3000;
    while (!graphics.poll(values, result, applied)) {
        require(GetTickCount64() < deadline, "graphics fixture response timeout"); Sleep(5);
    }
    require(applied == expectApplied, "graphics completion command mismatch"); return result;
}
uint32_t readAudio(t7::AudioSettings& audio, t7::AudioValues& values, bool expectApplied) {
    uint32_t result = 0; bool applied = false;
    const auto deadline = GetTickCount64() + 3000;
    while (!audio.poll(values, result, applied)) {
        require(GetTickCount64() < deadline, "audio fixture response timeout"); Sleep(5);
    }
    require(applied == expectApplied, "audio completion command mismatch"); return result;
}
}
bool verifyGraphicsHookExecution() {
    const auto directory = t7::fs::temp_directory_path() / (L"t7-graphics-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64()));
    t7::DebugClient client; t7::GraphicsSettings graphics;
    try {
        require(t7::fs::create_directory(directory), "graphics fixture directory");
        const auto path = directory / L"GraphicsFixture.exe"; fixture(path);
        const t7::OutputDevice selected{ADAPTER_ORDINAL, ADAPTER_ID, "fixture adapter", true};
        client.start(path, t7::fileHash(path), {}, [&](HANDLE process, HANDLE thread) {
            prepare(process); t7::installOutputDevice(process, BASE, selected);
            graphics.install(process, BASE, GetThreadId(thread));
        });
        t7::GraphicsValues values;
        require(readCompleted(graphics, values, false) == 1 && values.viewDistance == 159 && values.swoosh == 0, "graphics initial snapshot/default");
        require(readWord(client.process(), STATS + 60) == ADAPTER_ORDINAL && readWord(client.process(), STATS + 64) == ADAPTER_ORDINAL,
            "x86 renderer and resolution hooks selected different adapters");
        require(readWord(client.process(), STATS + 68) == readWord(client.process(), STATS + 72), "output device hooks changed ESP");
        t7::verifyOutputDevice(client.process(), BASE, selected);
        writeWord(client.process(), RENDERER + 0x458, 0);
        bool rejected = false;
        try { t7::verifyOutputDevice(client.process(), BASE, selected); } catch (const std::runtime_error&) { rejected = true; }
        require(rejected, "wrong renderer adapter was acknowledged");
        writeWord(client.process(), RENDERER + 0x458, ADAPTER_ORDINAL);
        writeWord(client.process(), ADAPTER + 0x43C, 0);
        rejected = false;
        try { t7::verifyOutputDevice(client.process(), BASE, selected); } catch (const std::runtime_error&) { rejected = true; }
        require(rejected, "wrong renderer identifier was acknowledged");
        writeWord(client.process(), ADAPTER + 0x43C, ADAPTER_ID.Data1);
        require(values.fullScreen == 1, "fullscreen fixture initial state");
        auto change = values; change.frameLimit = 1; change.fullScreen = 0; graphics.apply(change);
        require(readCompleted(graphics, values, true) == 1 && values.frameLimit == 1, "graphics frame-thread application");
        require(values.fullScreen == 0 && readWord(client.process(), RENDERER + 0x920) == 0
            && !(readWord(client.process(), WINDOW_STYLE) & (WS_POPUP | WS_MINIMIZE))
            && readWord(client.process(), RESTORE_COUNT) == 1, "fullscreen-to-window did not restore and reset the actual display");
        require(readWord(client.process(), STATS + 32) == 1, "original apply handler not called exactly once");
        writeWord(client.process(), VARIANTS + 7 * 52 + 12, 200); // concurrent in-game change after the last read
        graphics.apply(change);
        require(readCompleted(graphics, values, true) == 2 && values.frameLimit == 0, "stale settings overwrote game change");
        require(readWord(client.process(), STATS + 32) == 1, "conflict executed the apply handler");
        require(readWord(client.process(), RESTORE_COUNT) == 1, "conflict restored the game window");
        auto& audio = graphics.audio(); t7::AudioValues sound;
        writeWord(client.process(), BASE + 0x3700, 0x1B796EC); // base class during initialization
        require(readAudio(audio, sound, false) == 0, "audio accepted an uninitialized sound engine");
        writeWord(client.process(), BASE + 0x3700, 0x1B797B4);
        require(readAudio(audio, sound, false) == 1 && sound.musicVolume == .75f && sound.effectsVolume == .5f, "audio readback");
        sound.musicMuted = 1; sound.musicVolume = .25f; sound.effectsVolume = 0;
        audio.apply(sound);
        t7::AudioValues actual;
        require(readAudio(audio, actual, true) == 1 && t7::sameAudioValues(actual, sound), "audio setters readback");
        require(readWord(client.process(), STATS + 40) == 1 && readWord(client.process(), STATS + 44) == 1,
            "audio engine setters were not called exactly once");
        require(readWord(client.process(), STATS + 32) == 2 && readWord(client.process(), VARIANTS + 7 * 52 + 12) == 200,
            "audio did not save independently of graphics");
        writeWord(client.process(), SETTING + 0x20, 0x3F800000);
        audio.apply(sound);
        require(readAudio(audio, actual, true) == 2 && actual.effectsVolume == 1
            && readWord(client.process(), STATS + 32) == 2, "stale audio write overwrote game changes");
        require(readWord(client.process(), STATS) == readWord(client.process(), STATS + 4), "frame hook changed ESP");
        for (const auto pair : std::array<std::pair<uint32_t, uint32_t>, 6>{{
            {8,0x11223344},{12,0x22334455},{16,0x33445566},{20,0x44556677},{24,0x3F800000},{28,0x3F800000}
        }}) require(readWord(client.process(), STATS + pair.first) == pair.second, "frame hook changed registers/FPU/SSE");
        require(readWord(client.process(), RESTORE_COUNT) == 1, "audio restored the game window");
        require(readCompleted(graphics, values, false) == 1, "windowed snapshot");
        graphics.apply(change);
        require(readCompleted(graphics, values, true) == 1 && readWord(client.process(), RESTORE_COUNT) == 1,
            "an ordinary windowed settings save restored the game window");
        // The effective renderer mode, not the already-saved config, must be synchronized and acknowledged.
        writeWord(client.process(), RENDERER + 0x920, 4);
        writeWord(client.process(), WINDOW_STYLE, WS_POPUP | WS_VISIBLE);
        require(readCompleted(graphics, values, false) == 1 && values.fullScreen == 1,
            "graphics snapshot trusted config instead of the active fullscreen device");
        writeWord(client.process(), STATS + 56, 1); graphics.apply(change);
        require(readCompleted(graphics, values, true) == 0, "failed display reset was acknowledged as applied");
        require(readCompleted(graphics, values, false) == 1 && values.fullScreen == 1,
            "failed reset hid the effective fullscreen state");
        writeWord(client.process(), RENDERER + 0x11C, 0);
        require(readCompleted(graphics, values, false) == 0, "missing rendering device reported graphics ready");
        client.check(); client.stop(); graphics.clear();
        require(t7::fs::canonical(directory).parent_path() == t7::fs::canonical(t7::fs::temp_directory_path()), "graphics cleanup boundary");
        t7::fs::remove_all(directory);
        std::cout << "x86 output device hooks/readback, graphics display restore/readback, read/apply/conflict, defaults and FPU/SSE preservation passed\n";
        return true;
    } catch (const std::exception& e) {
        try { client.stop(); graphics.clear(); } catch (const std::exception& cleanup) { std::cerr << cleanup.what() << '\n'; }
        std::cerr << e.what() << '\n'; return false;
    }
}
