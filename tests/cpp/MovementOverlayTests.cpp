#include "../../src/Runtime/launcher/MovementOverlay.h"
#include "../../src/Runtime/launcher/MovementHookCode.h"
#include <algorithm>
#include <cstring>
#include <iostream>

namespace {
constexpr unsigned char ENTRY[] = {0x55, 0x8B, 0xEC, 0x6A, 0xFF};
constexpr unsigned char UNREADY_ENTRY[] = {0x90, 0x90, 0x90, 0x90, 0x90};
constexpr size_t PAGE_SIZE = 4096;

bool expect(bool condition, const char* message) {
    if (!condition) std::cerr << message << '\n';
    return condition;
}

t7::Bytes readBytes(HANDLE process, uintptr_t address, size_t length) {
    t7::Bytes bytes(length);
    SIZE_T actual = 0;
    if (!ReadProcessMemory(process, reinterpret_cast<void*>(address), bytes.data(), bytes.size(), &actual)
        || actual != bytes.size()) throw std::runtime_error("overlay fixture read failed");
    return bytes;
}

void writeBytes(HANDLE process, uintptr_t address, const unsigned char* bytes, size_t length) {
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(address), bytes, length, &actual)
        || actual != length) throw std::runtime_error("overlay fixture write failed");
}

uintptr_t jumpTarget(uintptr_t address, const unsigned char* bytes) {
    int32_t displacement = 0;
    memcpy(&displacement, bytes + 1, sizeof(displacement));
    return static_cast<uintptr_t>(static_cast<int64_t>(address) + 5 + displacement);
}

bool verifyOverlayMemory(HANDLE process) {
    auto image = VirtualAllocEx(process, nullptr, t7::MOVEMENT_HOOK_SITES.back().rva + PAGE_SIZE,
                                MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!image) throw std::runtime_error(t7::errorText("overlay fixture allocation"));
    const auto base = reinterpret_cast<uintptr_t>(image);
    const auto target = base + t7::MovementOverlayInfo::kResourceOpenRva;
    t7::MovementOverlay overlay;
    writeBytes(process, target, UNREADY_ENTRY, sizeof(UNREADY_ENTRY));
    std::string failure;
    try { overlay.install(process, base); }
    catch (const std::runtime_error& error) { failure = error.what(); }
    bool valid = expect(failure.find("movement resource loader signature mismatch") != std::string::npos,
                        "unready movement entry was not rejected");
    valid = expect(failure.find("expected=558BEC6AFF") != std::string::npos
                   && failure.find("observed=9090909090") != std::string::npos,
                   "movement signature diagnostic omits expected or observed bytes") && valid;
    valid = expect(!overlay.installed() && !overlay.remoteBase(),
                   "rejected movement entry retained an installation") && valid;
    valid = expect(readBytes(process, target, sizeof(UNREADY_ENTRY))
                   == t7::Bytes(std::begin(UNREADY_ENTRY), std::end(UNREADY_ENTRY)),
                   "rejected movement entry was modified") && valid;
    if (overlay.installed()) overlay.rollback();

    writeBytes(process, target, ENTRY, sizeof(ENTRY));
    for (size_t i = 1; i < t7::MOVEMENT_HOOK_SITES.size(); ++i) {
        const auto& site = t7::MOVEMENT_HOOK_SITES[i];
        writeBytes(process, base + site.rva, UNREADY_ENTRY, sizeof(UNREADY_ENTRY));
        failure.clear();
        try { overlay.install(process, base); }
        catch (const std::runtime_error& error) { failure = error.what(); }
        valid = expect(failure.find("movement XML loader signature mismatch") != std::string::npos
                       && !overlay.installed(), "XML signature mismatch did not reject the entire install") && valid;
        valid = expect(readBytes(process, target, sizeof(ENTRY)) == t7::Bytes(std::begin(ENTRY), std::end(ENTRY)),
                       "a later mismatch changed the first hook") && valid;
        writeBytes(process, base + site.rva, site.original.data(), site.original.size());
    }
    overlay.install(process, base);
    valid = expect(overlay.installed(), "ready movement entry was not installed") && valid;
    const auto remoteBase = overlay.remoteBase();
    const auto patched = readBytes(process, target, sizeof(ENTRY));
    const auto hook = jumpTarget(target, patched.data());
    valid = expect(patched[0] == 0xE9 && hook >= remoteBase && hook < remoteBase + PAGE_SIZE,
                   "movement entry does not jump into its remote page") && valid;

    const auto page = readBytes(process, remoteBase, PAGE_SIZE);
    const auto trampoline = std::search(page.begin(), page.end(), std::begin(ENTRY), std::end(ENTRY));
    const bool trampolineFound = trampoline != page.end()
        && static_cast<size_t>(page.end() - trampoline) >= sizeof(ENTRY) + 5;
    valid = expect(trampolineFound, "movement trampoline omits original instructions") && valid;
    if (trampolineFound) {
        const auto offset = static_cast<size_t>(trampoline - page.begin()) + sizeof(ENTRY);
        valid = expect(page[offset] == 0xE9
                       && jumpTarget(remoteBase + offset, page.data() + offset) == target + sizeof(ENTRY),
                       "movement trampoline returns to the wrong entry offset") && valid;
    }
    overlay.rollback();
    for (const auto& site : t7::MOVEMENT_HOOK_SITES)
        valid = expect(readBytes(process, base + site.rva, site.original.size())
                       == t7::Bytes(site.original.begin(), site.original.end()), "rollback left an XML hook installed") && valid;
    valid = expect(!overlay.installed() && !overlay.remoteBase(),
                   "movement rollback retained an installation") && valid;
    valid = expect(readBytes(process, target, sizeof(ENTRY)) == t7::Bytes(std::begin(ENTRY), std::end(ENTRY)),
                   "movement rollback did not restore the entry") && valid;
    MEMORY_BASIC_INFORMATION region{};
    valid = expect(VirtualQueryEx(process, reinterpret_cast<void*>(remoteBase), &region, sizeof(region))
                   && region.State == MEM_FREE, "movement rollback did not release the remote page") && valid;
    if (!VirtualFreeEx(process, image, 0, MEM_RELEASE))
        throw std::runtime_error(t7::errorText("overlay fixture release"));
    return valid;
}
}

bool verifyMovementOverlay() {
    wchar_t directory[MAX_PATH]{};
    const auto length = GetSystemWow64DirectoryW(directory, static_cast<UINT>(std::size(directory)));
    if (!length || length >= std::size(directory)) return false;
    const auto image = t7::fs::path(directory) / L"cmd.exe";
    std::wstring command = L"\"" + image.wstring() + L"\" /d /c exit 0";
    STARTUPINFOW startup{}; startup.cb = sizeof(startup);
    PROCESS_INFORMATION info{};
    if (!CreateProcessW(image.c_str(), command.data(), nullptr, nullptr, FALSE,
                        CREATE_SUSPENDED | CREATE_NO_WINDOW, nullptr, nullptr, &startup, &info)) {
        std::cerr << t7::errorText("overlay fixture creation") << '\n';
        return false;
    }
    bool valid = false;
    try { valid = verifyOverlayMemory(info.hProcess); }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; }
    // The synthetic x86 process never runs; termination also cleans failed installs.
    if (!TerminateProcess(info.hProcess, 0)) valid = false;
    if (WaitForSingleObject(info.hProcess, 5000) != WAIT_OBJECT_0) valid = false;
    if (!CloseHandle(info.hThread)) valid = false;
    if (!CloseHandle(info.hProcess)) valid = false;
    return valid;
}
