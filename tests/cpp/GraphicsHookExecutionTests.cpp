#include "../../src/Runtime/launcher/ClientFrameRuntime.h"
#include "../../src/Runtime/launcher/DebugClient.h"
#include "../../src/Runtime/launcher/OutputDevice.h"
#include "WorldHookFixture.h"
#include "GuardDebugFixture.h"
#include "TerrainHookFixture.h"
#include "BladeHookFixture.h"
#include "SprintHookFixture.h"
#include "MovementAnimationFixture.h"
#include "CombatProfileFixture.h"
#include "MovementPoseFixture.h"
#include "MovementCommandFixture.h"
#include "JumpPhysicsFixture.h"
#include "JumpControlFixture.h"
#include "WeaponTrailHookFixture.h"
#include "ChatInputHookFixture.h"
#include "RoundEndHookFixture.h"
#include "TitleHookFixture.h"
#include "../../src/Runtime/launcher/ChatInputHookCode.h"
#include "../../src/Runtime/launcher/WeaponTrailHookCode.h"
#include <algorithm>
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
constexpr uint32_t BASE = 0x400000, RECORD = BASE + 0x3000, SERVICE = BASE + 0x3100;
constexpr uint32_t FRAME = BASE + 0x3200, SETTING = BASE + 0x3300, CLIENT = BASE + 0x3400;
constexpr uint32_t VARIANTS = BASE + 0x4000, INDEX = BASE + 0x3500, STATS = BASE + 0x3600;
constexpr uint32_t RENDERER = BASE + 0x6000, WINDOW_STYLE = STATS + 48, RESTORE_COUNT = STATS + 52;
constexpr uint32_t ADAPTER = BASE + 0x8000, ADAPTER_ORDINAL = 257;
constexpr uint32_t BANK_COUNT = STATS + 80;
const GUID ADAPTER_ID{0x12345678,0x1234,0x5678,{0x9A,0xBC,0x12,0x34,0x56,0x78,0x9A,0xBC}};
void require(bool v, const char* text) { if (!v) throw std::runtime_error(text); }
void word(t7r::Bytes& bytes, uint32_t v) { for (unsigned s = 0; s < 32; s += 8) bytes.push_back(static_cast<unsigned char>(v >> s)); }
size_t branch(t7r::Bytes& bytes, unsigned char condition) {
    bytes.insert(bytes.end(), {0x0F, condition}); const auto position = bytes.size(); word(bytes, 0); return position;
}
void bind(t7r::Bytes& bytes, size_t position) {
    const auto delta = static_cast<uint32_t>(bytes.size() - position - 4);
    std::memcpy(bytes.data() + position, &delta, 4);
}
void writeWord(HANDLE process, uint32_t address, uint32_t value) {
    t7r::Bytes data; word(data, value); t7r::writeClientMemory(process, address, data);
}
uint32_t readWord(HANDLE process, uint32_t address) {
    const auto bytes = t7r::readClientMemory(process, address, 4);
    uint32_t value = 0; std::memcpy(&value, bytes.data(), 4); return value;
}
void fixture(const t7r::fs::path& path) {
    t7r::Bytes image(0x1200, 0);
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
    nt.OptionalHeader.SizeOfImage = 0x2E41000; nt.OptionalHeader.SizeOfHeaders = 0x200;
    nt.OptionalHeader.Subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI;
    nt.OptionalHeader.SizeOfStackReserve = 0x100000; nt.OptionalHeader.SizeOfStackCommit = 0x1000;
    nt.OptionalHeader.SizeOfHeapReserve = 0x100000; nt.OptionalHeader.SizeOfHeapCommit = 0x1000;
    nt.OptionalHeader.NumberOfRvaAndSizes = IMAGE_NUMBEROF_DIRECTORY_ENTRIES;
    nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT] = {0x1800, 40};
    std::memcpy(image.data() + dos.e_lfanew, &nt, sizeof(nt));
    IMAGE_SECTION_HEADER section{}; std::memcpy(section.Name, ".fixture", 8);
    section.VirtualAddress = 0x1000; section.Misc.VirtualSize = nt.OptionalHeader.SizeOfImage - 0x1000;
    section.PointerToRawData = 0x200; section.SizeOfRawData = 0x1000;
    section.Characteristics = IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE | IMAGE_SCN_MEM_EXECUTE;
    std::memcpy(image.data() + dos.e_lfanew + sizeof(nt), &section, sizeof(section));
    IMAGE_IMPORT_DESCRIPTOR imports{};
    imports.OriginalFirstThunk = 0x1830; imports.Name = 0x1840; imports.FirstThunk = 0x1900;
    std::memcpy(image.data() + 0xA00, &imports, sizeof(imports));
    const uint32_t importNames[]{0x1860, 0x1870, 0};
    std::memcpy(image.data() + 0xA30, importNames, sizeof(importNames));
    std::memcpy(image.data() + 0xB00, importNames, sizeof(importNames));
    std::memcpy(image.data() + 0xA40, "KERNEL32.dll", 13);
    std::memcpy(image.data() + 0xA62, "GetTickCount", 13);
    std::memcpy(image.data() + 0xA72, "Sleep", 6);
    t7r::Bytes code;
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
    code.insert(code.end(), {0x6A,10,0xFF,0x15}); word(code, BASE + 0x1904);
    imm(0xB8, guardDebugFixture::DISPATCH); code.insert(code.end(), {0xFF,0xD0});
    imm(0xB8, jumpPhysicsFixture::DISPATCH); code.insert(code.end(), {0xFF,0xD0});
    imm(0xB8, jumpControlFixture::DISPATCH); code.insert(code.end(), {0xFF,0xD0});
    code.insert(code.end(), {0x83,0x3D}); word(code, worldFixture::FRAME_GATE); code.push_back(1);
    const auto paused = branch(code, 0x84);
    imm(0xB8, roundEndFixture::DISPATCH); code.insert(code.end(), {0xFF,0xD0});
    code.insert(code.end(), {0xD9, 0xE8}); // FLD1
    imm(0xB8, 0x3F800000); code.insert(code.end(), {0x66, 0x0F, 0x6E, 0xC0}); // XMM0 = 1.0
    imm(0xB9, FRAME); code.push_back(0xE8); word(code, t7r::GRAPHICS_TICK_RVA - (0x1000 + static_cast<uint32_t>(code.size()) + 4));
    for (const auto pair : std::array<std::pair<uint32_t, uint32_t>, 5>{{{0x25,4},{0x1D,8},{0x35,12},{0x3D,16},{0x2D,20}}}) {
        code.insert(code.end(), {0x89, static_cast<unsigned char>(pair.first)}); word(code, STATS + pair.second);
    }
    code.insert(code.end(), {0xD9, 0x1D}); word(code, STATS + 24);
    code.insert(code.end(), {0x66, 0x0F, 0x7E, 0x05}); word(code, STATS + 28);
    imm(0xB8, movementCommandFixture::DISPATCH); code.insert(code.end(), {0xFF,0xD0});
    imm(0xB8, movementAnimationFixture::DISPATCH); code.insert(code.end(), {0xFF,0xD0});
    imm(0xB8, movementPoseFixture::DISPATCH); code.insert(code.end(), {0xFF,0xD0});
    imm(0xB8, movementPoseFixture::defenseExit::DISPATCH); code.insert(code.end(), {0xFF,0xD0});
    code.insert(code.end(), {0x83,0x3D}); word(code, worldFixture::FRAME_GATE); code.push_back(2);
    const auto running = branch(code, 0x85);
    code.insert(code.end(), {0xC7,0x05}); word(code, worldFixture::FRAME_GATE); word(code, 1);
    bind(code, running); bind(code, paused);
    code.push_back(0xE9); word(code, loop - (static_cast<uint32_t>(code.size()) + 4));
    std::memcpy(image.data() + 0x200, code.data(), code.size());
    std::ofstream output(path, std::ios::binary); output.write(reinterpret_cast<const char*>(image.data()), image.size());
    require(output.good(), "graphics fixture write");
}
void prepare(HANDLE process) {
    worldFixture::prepare(process);
    terrainFixture::prepare(process);
    sprintFixture::prepare(process);
    lightShieldRunFixture::prepare(process);
    movementAnimationFixture::prepare(process);
    movementPoseFixture::prepare(process);
    movementCommandFixture::prepare(process);
    jumpPhysicsFixture::prepare(process);
    jumpControlFixture::prepare(process);
    chatInputFixture::prepare(process);
    roundEndFixture::prepare(process);
    titleFixture::prepare(process);
    t7r::writeClientMemory(process, BASE + 0x2364E0, {0x55,0x8B,0xEC,0x8B,0x45,0x08,0x8B,0x45,0x1C,0x5D,0xC2,0x38,0});
    t7r::writeClientMemory(process, BASE + 0x33B444, {0x8B,0xD0,0x33,0xC9,0x8B,0x3A,0x8B,0xC1,0xC3});
    writeWord(process, RENDERER + 0x458, ADAPTER_ORDINAL); writeWord(process, RENDERER + 0x468, ADAPTER);
    t7r::Bytes identifier(sizeof(GUID)); std::memcpy(identifier.data(), &ADAPTER_ID, sizeof(GUID));
    t7r::writeClientMemory(process, ADAPTER + 0x43C, identifier);
    auto tick = t7r::GRAPHICS_TICK_ORIGINAL; tick.insert(tick.end(), {0x5E, 0xC3});
    t7r::writeClientMemory(process, BASE + t7r::GRAPHICS_TICK_RVA, tick);
    for (const auto pair : std::array<std::pair<uint32_t, uint32_t>, 12>{{
        {0x283B714, SERVICE}, {SERVICE, 0x1B355AC}, {SERVICE + 0x28, RECORD}, {RECORD, 0x1B35544},
        {0x1B35544 + 0x50, BASE + 0x1200}, {0x3235AC8, SETTING}, {SETTING, 0x1BA7710},
        {0x31379F0, CLIENT}, {CLIENT, 0x1B6F28C}, {0x28396C8, FRAME}, {FRAME, 0x1B3642C}, {BASE + 0x5000, 0x30323931}
    }}) writeWord(process, pair.first, pair.second);
    t7r::writeClientMemory(process, BASE + 0x5000, {'1','9','2','0','x','1','0','8','0',0});
    writeWord(process, BASE + 0x5010, 9); writeWord(process, BASE + 0x5014, 15);
    const std::array<uint32_t, 9> types{7,1,2,1,1,4,1,2,0}; // missing Swoosh uses the game's default
    const std::array<uint32_t, 9> values{BASE+0x5000,1,0,0,0,0x431F0000,1,200,0};
    for (size_t i = 0; i < types.size(); ++i) {
        writeWord(process, VARIANTS + static_cast<uint32_t>(i) * 52 + 8, types[i]);
        writeWord(process, VARIANTS + static_cast<uint32_t>(i) * 52 + 12, values[i]);
    }
    t7r::Bytes getter{0xA1}; word(getter, INDEX);
    getter.insert(getter.end(), {0x6B,0xC0,52,0x05}); word(getter, VARIANTS);
    getter.insert(getter.end(), {0xFF,0x05}); word(getter, INDEX);
    getter.insert(getter.end(), {0x83,0x3D}); word(getter, INDEX); getter.insert(getter.end(), {9,0x72,10,0xC7,0x05});
    word(getter, INDEX); word(getter, 0); getter.insert(getter.end(), {0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x1200, getter);
    t7r::Bytes apply{0x55,0x8B,0xEC,0x6A,0xFF,0x58,0xFF,0x05}; word(apply, STATS + 32);
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
    t7r::writeClientMemory(process, 0xAEA040, apply);
    writeWord(process, 0x3120008, RENDERER); writeWord(process, RENDERER, 0x1B5B188);
    writeWord(process, RENDERER + 0x11C, BASE + 0x7000); writeWord(process, RENDERER + 0x920, 4);
    writeWord(process, CLIENT + 0x24, 1); writeWord(process, CLIENT + 0x40, 1);
    writeWord(process, WINDOW_STYLE, WS_POPUP | WS_MINIMIZE | WS_VISIBLE);
    writeWord(process, 0x1AF6A40, BASE + 0x1400); writeWord(process, 0x1AF6A6C, BASE + 0x1440);
    t7r::Bytes getStyle{0xA1}; word(getStyle, WINDOW_STYLE); getStyle.insert(getStyle.end(), {0xC2,8,0});
    t7r::writeClientMemory(process, BASE + 0x1400, getStyle);
    t7r::Bytes showWindow{0x83,0x7C,0x24,0x08,SW_RESTORE};
    const auto notRestore = branch(showWindow, 0x85);
    showWindow.insert(showWindow.end(), {0xFF,0x05}); word(showWindow, RESTORE_COUNT);
    showWindow.insert(showWindow.end(), {0x81,0x25}); word(showWindow, WINDOW_STYLE); word(showWindow, ~static_cast<uint32_t>(WS_MINIMIZE));
    bind(showWindow, notRestore); showWindow.insert(showWindow.end(), {0xC2,8,0});
    t7r::writeClientMemory(process, BASE + 0x1440, showWindow);
    writeWord(process, 0x3224998, BASE + 0x3700); writeWord(process, BASE + 0x3700, 0x1B797B4);
    writeWord(process, 0x28396D0, BASE + 0x3800);
    writeWord(process, BASE + 0x3800, 0x1B7A9F4);
    writeWord(process, SETTING + 0x18, 0x3F400000); writeWord(process, SETTING + 0x20, 0x3F000000);
    for (const auto pair : {std::pair<uint32_t, uint32_t>{t7r::AUDIO_MUSIC_RVA, 0x14}, {t7r::AUDIO_EFFECTS_RVA, 0x1C}}) {
        t7r::Bytes setter{0x55,0x8B,0xEC,0x8A,0x45,0x08,0x88,0x41,static_cast<unsigned char>(pair.second),
            0x8B,0x45,0x0C,0x89,0x41,static_cast<unsigned char>(pair.second+4),0xFF,0x05};
        word(setter, STATS + (pair.second == 0x14 ? 40 : 44));
        setter.insert(setter.end(), {0x0F,0x57,0xC0,0x5D,0xC2,8,0});
        t7r::writeClientMemory(process, BASE + pair.first, setter);
    }
    writeWord(process, BASE + 0x16F6084, BASE + 0x1500);
    t7r::Bytes bank{0xFF,0x05}; word(bank, BANK_COUNT);
    bank.insert(bank.end(), {0xB8,1,0,0,0,0xC3});
    t7r::writeClientMemory(process, BASE + 0x1500, bank);
}
uint32_t readCompleted(t7r::GraphicsSettings& graphics, t7r::GraphicsValues& values, bool expectApplied) {
    uint32_t result = 0; bool applied = false;
    const auto deadline = GetTickCount64() + 3000;
    while (!graphics.poll(values, result, applied)) {
        require(GetTickCount64() < deadline, "graphics fixture response timeout"); Sleep(5);
    }
    require(applied == expectApplied, "graphics completion command mismatch"); return result;
}
uint32_t readAudio(t7r::AudioSettings& audio, t7r::AudioValues& values, bool expectApplied) {
    uint32_t result = 0; bool applied = false;
    const auto deadline = GetTickCount64() + 3000;
    while (!audio.poll(values, result, applied)) {
        require(GetTickCount64() < deadline, "audio fixture response timeout"); Sleep(5);
    }
    require(applied == expectApplied, "audio completion command mismatch"); return result;
}
}
bool verifyGraphicsHookExecution(bool combatOnly, bool titlesOnly, bool guardOnly) {
    const auto directory = t7r::fs::temp_directory_path() / (L"t7r-graphics-" + std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64()));
    t7r::DebugClient client; t7r::ClientFrameRuntime runtime; t7r::GuardDebugDraw guardDebug;
    auto& graphics = runtime.graphics();
    try {
        require(t7r::fs::create_directory(directory), "graphics fixture directory");
        const auto path = directory / L"GraphicsFixture.exe"; fixture(path);
        const t7r::OutputDevice selected{ADAPTER_ORDINAL, ADAPTER_ID, "fixture adapter", true};
        client.start(path, t7r::fileHash(path), {}, [&](HANDLE process, HANDLE thread) {
            prepare(process); bladeFixture::prepare(process); weaponTrailFixture::prepare(process);
            guardDebugFixture::prepare(process); guardDebug.install(process, BASE);
            t7r::installOutputDevice(process, BASE, selected);
            const auto page = t7r::buildWorldHook(0x10000000, BASE, GetThreadId(thread), sprintFixture::profile());
            const auto sweep = t7r::buildBladeSweepHook(0x10000000, BASE);
            require(std::equal(sweep.begin(), sweep.end(), page.begin() + t7r::BLADE_SWEEP_CODE_OFFSET),
                "weapon trail overwrote blade sweep code or constants");
            t7r::writeClientMemory(process, BASE + 0x3D1200, {0xCC});
            bool rejected = false;
            try { t7r::verifyWorldFunctions(process, BASE); } catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "world accepted an unknown position getter");
            t7r::writeClientMemory(process, BASE + 0x3D1200, {0x55});
            t7r::writeClientMemory(process, BASE + 0x64C9D0, {0xCC});
            rejected = false;
            try { t7r::verifyWorldFunctions(process, BASE); } catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "world accepted an unknown life-state getter");
            t7r::writeClientMemory(process, BASE + 0x64C9D0, {0x83});
            t7r::writeClientMemory(process, BASE + movementCommandFixture::HOOK_RVA, {0xCC});
            rejected = false;
            try { t7r::verifyWorldFunctions(process, BASE); } catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "movement command accepted an unknown native executor");
            t7r::writeClientMemory(process, BASE + movementCommandFixture::HOOK_RVA, {0x8B});
            t7r::writeClientMemory(process, BASE + jumpControlFixture::COMMAND_CALL_RVA, {0xCC});
            rejected = false;
            try { t7r::verifyWorldFunctions(process, BASE); } catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "jump input accepted an unknown native command dispatcher");
            t7r::writeClientMemory(process, BASE + jumpControlFixture::COMMAND_CALL_RVA, {0x8B});
            t7r::writeClientMemory(process, BASE + jumpControlFixture::SEND_HEADING_RVA, {0xCC});
            rejected = false;
            try { t7r::verifyWorldFunctions(process, BASE); } catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "landing heading accepted an unknown native sender");
            t7r::writeClientMemory(process, BASE + jumpControlFixture::SEND_HEADING_RVA, {0x55});
            for (const auto& anchor : std::vector<std::pair<uint32_t, uint32_t>>{
                {0x3FF3D0,0x55}, {0x3FF3FB,0x80}, {0x4B2C70,0x80}, {0x4B450D,0x80}
            }) {
                t7r::writeClientMemory(process, BASE + anchor.first, {0xCC});
                rejected = false;
                try { t7r::verifyChatInputFunctions(process, BASE); } catch (const std::runtime_error&) { rejected = true; }
                require(rejected, "chat input accepted an unknown focus function");
                t7r::writeClientMemory(process, BASE + anchor.first, {static_cast<unsigned char>(anchor.second)});
            }
            for (const auto& anchor : std::array<std::pair<uint32_t, uint32_t>, 4>{{
                {0x6EC2D0,0x55}, {0x3735A0,0x56}, {0x373630,0x56}, {0x373A60,0x55}
            }}) {
                t7r::writeClientMemory(process, BASE + anchor.first, {0xCC});
                rejected = false;
                try { t7r::verifyWeaponTrailFunctions(process, BASE); } catch (const std::runtime_error&) { rejected = true; }
                require(rejected, "weapon trail accepted an unknown native function");
                t7r::writeClientMemory(process, BASE + anchor.first, {static_cast<unsigned char>(anchor.second)});
            }
            t7r::writeClientMemory(process, BASE + t7r::MOVEMENT_ANIMATION_RVA, {0xCC});
            rejected = false;
            try { runtime.install(process, BASE, GetThreadId(thread), sprintFixture::profile(), movementAnimationFixture::profile()); }
            catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "animation hook accepted an unknown native writer");
            t7r::writeClientMemory(process, BASE + t7r::MOVEMENT_ANIMATION_RVA, {0xF3});
            t7r::writeClientMemory(process, BASE + t7r::MOVEMENT_POSE_RVA, {0xCC});
            rejected = false;
            try { runtime.install(process, BASE, GetThreadId(thread), sprintFixture::profile(), movementAnimationFixture::profile()); }
            catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "movement pose hook accepted an unknown native writer");
            t7r::writeClientMemory(process, BASE + t7r::MOVEMENT_POSE_RVA, {0xFF});
            for (const auto& anchor : std::vector<std::pair<uint32_t, unsigned char>>{
                {t7r::DEFENSE_EXIT_RVA,static_cast<unsigned char>(0xFF)},
                {movementPoseFixture::defenseExit::VARIABLE_RVA,static_cast<unsigned char>(0x55)}
            }) {
                t7r::writeClientMemory(process, BASE + anchor.first, {0xCC});
                rejected = false;
                try { runtime.install(process, BASE, GetThreadId(thread), sprintFixture::profile(), movementAnimationFixture::profile()); }
                catch (const std::runtime_error&) { rejected = true; }
                require(rejected, "defense exit accepted an unknown native event or variable writer");
                t7r::writeClientMemory(process, BASE + anchor.first, {anchor.second});
                require(t7r::readClientMemory(process, BASE + t7r::DEFENSE_EXIT_RVA, t7r::DEFENSE_EXIT_ORIGINAL.size())
                    == t7r::DEFENSE_EXIT_ORIGINAL, "failed defense exit installation changed the native event handler");
            }
            runtime.install(process, BASE, GetThreadId(thread), sprintFixture::profile(), movementAnimationFixture::profile());
        }, [](t7r::DiagnosticLogLevel, const std::string& line) {
            if (line.find("exception") != std::string::npos || line.find("fault") != std::string::npos
                || line.find("violation") != std::string::npos) std::cerr << line << '\n';
        });
        if (!combatOnly && !titlesOnly) guardDebugFixture::verify(client.process(), guardDebug);
        if (guardOnly) {
            client.check(); client.stop(); runtime.clear(); guardDebug.clear();
            require(t7r::fs::canonical(directory).parent_path() == t7r::fs::canonical(t7r::fs::temp_directory_path()), "guard fixture cleanup boundary");
            t7r::fs::remove_all(directory);
            std::cout << "x86 guard debug camera, live toggle, state/stream restore, lost device and draw failure passed\n";
            return true;
        }
        t7r::GraphicsValues values;
        require(readCompleted(graphics, values, false) == 1 && values.viewDistance == 159 && values.swoosh == 0, "graphics initial snapshot/default");
        if (!combatOnly) titleFixture::verify(client.process(), runtime.titles());
        if (titlesOnly) {
            require(readWord(client.process(), STATS) == readWord(client.process(), STATS + 4), "title frame hook changed ESP");
            for (const auto pair : std::array<std::pair<uint32_t, uint32_t>, 6>{{
                {8,0x11223344},{12,0x22334455},{16,0x33445566},{20,0x44556677},{24,0x3F800000},{28,0x3F800000}
            }}) require(readWord(client.process(), STATS + pair.first) == pair.second, "title frame hook changed registers/FPU/SSE");
            client.check(); client.stop(); runtime.clear();
            require(t7r::fs::canonical(directory).parent_path() == t7r::fs::canonical(t7r::fs::temp_directory_path()), "title fixture cleanup boundary");
            t7r::fs::remove_all(directory);
            std::cout << "x86 title description updates, original refresh and register preservation passed\n";
            return true;
        }
        if (combatOnly) {
            const auto entry = BASE + t7r::GRAPHICS_TICK_RVA + 5 + readWord(client.process(), BASE + t7r::GRAPHICS_TICK_RVA + 1);
            combatProfileFixture::verify(client.process(), readWord(client.process(), entry + 10) - t7r::WORLD_CODE_OFFSET);
            client.check(); client.stop(); runtime.clear();
            require(t7r::fs::canonical(directory).parent_path() == t7r::fs::canonical(t7r::fs::temp_directory_path()), "combat fixture cleanup boundary");
            t7r::fs::remove_all(directory);
            std::cout << "x86 hero/weapon-selected movement and dash profiles passed\n";
            return true;
        }
        require(readWord(client.process(), STATS + 60) == ADAPTER_ORDINAL && readWord(client.process(), STATS + 64) == ADAPTER_ORDINAL,
            "x86 renderer and resolution hooks selected different adapters");
        require(readWord(client.process(), STATS + 68) == readWord(client.process(), STATS + 72), "output device hooks changed ESP");
        t7r::verifyOutputDevice(client.process(), BASE, selected);
        writeWord(client.process(), RENDERER + 0x458, 0);
        bool rejected = false;
        try { t7r::verifyOutputDevice(client.process(), BASE, selected); } catch (const std::runtime_error&) { rejected = true; }
        require(rejected, "wrong renderer adapter was acknowledged");
        writeWord(client.process(), RENDERER + 0x458, ADAPTER_ORDINAL);
        writeWord(client.process(), ADAPTER + 0x43C, 0);
        rejected = false;
        try { t7r::verifyOutputDevice(client.process(), BASE, selected); } catch (const std::runtime_error&) { rejected = true; }
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
        auto& audio = runtime.audio(); t7r::AudioValues sound;
        writeWord(client.process(), BASE + 0x3700, 0x1B796EC); // base class during initialization
        require(readAudio(audio, sound, false) == 0, "audio accepted an uninitialized sound engine");
        writeWord(client.process(), BASE + 0x3700, 0x1B797B4);
        require(readAudio(audio, sound, false) == 1 && sound.musicVolume == .75f && sound.effectsVolume == .5f, "audio readback");
        require(readWord(client.process(), BANK_COUNT) == 0, "settings frame hook loaded banks outside the native lifecycle");
        sound.musicMuted = 1; sound.musicVolume = .25f; sound.effectsVolume = 0;
        audio.apply(sound);
        t7r::AudioValues actual;
        require(readAudio(audio, actual, true) == 1 && t7r::sameAudioValues(actual, sound), "audio setters readback");
        require(readWord(client.process(), STATS + 40) == 1 && readWord(client.process(), STATS + 44) == 1,
            "audio engine setters were not called exactly once");
        require(readWord(client.process(), STATS + 32) == 2 && readWord(client.process(), VARIANTS + 7 * 52 + 12) == 200,
            "audio did not save independently of graphics");
        writeWord(client.process(), SETTING + 0x20, 0x3F800000);
        audio.apply(sound);
        require(readAudio(audio, actual, true) == 2 && actual.effectsVolume == 1
            && readWord(client.process(), STATS + 32) == 2, "stale audio write overwrote game changes");
        require(readWord(client.process(), BANK_COUNT) == 0, "audio settings application introduced extra Bank loads");
        const auto graphicsEntry = BASE + t7r::GRAPHICS_TICK_RVA + 5
            + readWord(client.process(), BASE + t7r::GRAPHICS_TICK_RVA + 1);
        const auto worldMailbox = readWord(client.process(), graphicsEntry + 10) - t7r::WORLD_CODE_OFFSET;
        jumpControlFixture::verify(client.process());
        std::cout << "x86 jump locks heading/movement and rejects repeated takeoff until landing passed\n";
        jumpControlFixture::verifyCrouchAnimation(client.process());
        std::cout << "x86 dash attacks reject jump/crouch input and crouch animation while preserving stand-up passed\n";
        jumpControlFixture::verifyLandingHeading(client.process());
        std::cout << "x86 landing resyncs a stationary camera once before restoring held or released movement passed\n";
        jumpPhysicsFixture::verify(client.process());
        std::cout << "x86 airborne heading preserves vertical velocity and native physics clock passed\n";
        movementAnimationFixture::verifyTurn(client.process());
        std::cout << "x86 stationary speed restores native turn animation and idle transitions passed\n";
        worldFixture::verify(client.process(), runtime.world(), worldMailbox);
        terrainFixture::verify(client.process(), runtime.world());
        try { movementAnimationFixture::verify(client.process(), worldMailbox); }
        catch (const std::exception& error) { throw std::runtime_error(std::string("movement animation: ") + error.what()); }
        std::cout << "x86 movement animation gait/cache, defense release and unchanged physical speeds/timer passed\n";
        movementPoseFixture::verify(client.process());
        std::cout << "x86 defense exit restores locomotion selection without speed or animation events passed\n";
        movementPoseFixture::defenseExit::verify(client.process());
        std::cout << "x86 defense exit preserves native layer fade without intermediate Idle and clears pose immediately passed\n";
        movementCommandFixture::verify(client.process(), runtime.world(), worldMailbox);
        std::cout << "x86 queued A/D commands preserve running animation and native movement state passed\n";
        bladeFixture::verify(client.process(), runtime.world(), worldMailbox);
        weaponTrailFixture::verify(client.process());
        try { sprintFixture::verify(client.process()); }
        catch (const std::exception& error) { throw std::runtime_error(std::string("sprint: ") + error.what()); }
        try { chatInputFixture::verify(client.process(), runtime.world()); }
        catch (const std::exception& error) { throw std::runtime_error(std::string("chat input: ") + error.what()); }
        roundEndFixture::verify(client.process(), runtime.world(), worldMailbox);
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
        combatProfileFixture::verify(client.process(), worldMailbox);
        std::cout << "x86 hero/weapon-selected movement and dash profiles, unknown-key isolation passed\n";
        writeWord(client.process(), RENDERER + 0x11C, 0);
        require(readCompleted(graphics, values, false) == 0, "missing rendering device reported graphics ready");
        client.check(); client.stop(); runtime.clear();
        require(t7r::fs::canonical(directory).parent_path() == t7r::fs::canonical(t7r::fs::temp_directory_path()), "graphics cleanup boundary");
        t7r::fs::remove_all(directory);
        std::cout << "x86 output device, graphics/audio/world, weapon trail, sprint/chat input, round settlement, and FPU/SSE preservation passed\n";
        return true;
    } catch (const std::exception& e) {
        try { client.stop(); runtime.clear(); } catch (const std::exception& cleanup) { std::cerr << cleanup.what() << '\n'; }
        std::cerr << e.what() << '\n'; return false;
    }
}
