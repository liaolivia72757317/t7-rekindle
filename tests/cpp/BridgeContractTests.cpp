#include "../../src/Runtime/bridge/T7NativeBridge.h"
#include <windows.h>
#include <cassert>
#include <chrono>
#include <cstring>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {
std::string utf8(const std::wstring& value) {
    int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()),
                                     nullptr, 0, nullptr, nullptr);
    assert(length > 0);
    std::string result(static_cast<size_t>(length), '\0');
    assert(WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value.data(), static_cast<int>(value.size()),
                               result.data(), length, nullptr, nullptr) == length);
    return result;
}

std::string currentDirectory() {
    std::wstring buffer(32768, L'\0');
    auto length = GetCurrentDirectoryW(static_cast<DWORD>(buffer.size()), buffer.data());
    assert(length && length < buffer.size());
    buffer.resize(length);
    return utf8(buffer);
}

std::string localTimestamp() {
    SYSTEMTIME time{};
    GetLocalTime(&time);
    char buffer[20];
    sprintf_s(buffer, "%04u-%02u-%02u %02u:%02u:%02u", time.wYear, time.wMonth, time.wDay,
              time.wHour, time.wMinute, time.wSecond);
    return buffer;
}

}

int main() {
    const auto earliestLogTime = localTimestamp();
    uint32_t abi = 0, snapshotSize = 0;
    assert(t7_native_get_abi(&abi, &snapshotSize) == T7NB_OK);
    assert(abi == T7NB_ABI_VERSION && snapshotSize == sizeof(T7NativeSnapshot));
    assert(t7_native_create(nullptr, nullptr) == T7NB_INVALID_ARGUMENT);
    assert(t7_native_release(nullptr) == T7NB_INVALID_HANDLE);

    auto root = currentDirectory();
    T7NativeCreateArgs wrong{T7NB_ABI_VERSION + 1, sizeof(T7NativeCreateArgs),
                             reinterpret_cast<const uint8_t*>(root.data()), static_cast<uint32_t>(root.size()), 0};
    T7NativeSessionHandle session = nullptr;
    assert(t7_native_create(&wrong, &session) == T7NB_INVALID_ABI && !session);
    T7NativeCreateArgs create{T7NB_ABI_VERSION, sizeof(T7NativeCreateArgs),
                              reinterpret_cast<const uint8_t*>(root.data()), static_cast<uint32_t>(root.size()), 0};
    assert(t7_native_create(&create, &session) == T7NB_OK && session);

    T7NativeSnapshot snapshot{T7NB_ABI_VERSION, sizeof(T7NativeSnapshot)};
    assert(t7_native_get_snapshot(session, &snapshot) == T7NB_OK);
    assert(snapshot.state == T7NB_STATE_IDLE && snapshot.flags == 1);
    T7NativeSnapshot shortSnapshot{T7NB_ABI_VERSION, 8};
    assert(t7_native_get_snapshot(session, &shortSnapshot) == T7NB_INVALID_ABI);

    const uint8_t badUtf8[]{0xC3, 0x28};
    T7NativePath bad{T7NB_ABI_VERSION, sizeof(T7NativePath), badUtf8, sizeof(badUtf8)};
    uint64_t ignored = 0;
    assert(t7_native_submit_check(session, &bad, &ignored) == T7NB_INVALID_ARGUMENT);

    const uint8_t badControls[]{'C', ':', '\\', 't', 'm', 'p', '\r'};
    T7NativePath badControlsPath{T7NB_ABI_VERSION, sizeof(T7NativePath), badControls, sizeof(badControls)};
    assert(t7_native_submit_check(session, &badControlsPath, &ignored) == T7NB_INVALID_ARGUMENT);

    auto missing = root + "\\missing-client";
    T7NativePath path{T7NB_ABI_VERSION, sizeof(T7NativePath),
                      reinterpret_cast<const uint8_t*>(missing.data()), static_cast<uint32_t>(missing.size())};
    uint64_t operationId = 0;
    const std::string longName(32, 'A');
    T7NativeStartArgs startArgs{T7NB_ABI_VERSION, sizeof(T7NativeStartArgs), path.data, path.length,
        reinterpret_cast<const uint8_t*>(longName.data()), static_cast<uint32_t>(longName.size())};
    assert(t7_native_submit_start_named(session, &startArgs, &operationId) == T7NB_INVALID_ARGUMENT && operationId == 0);
    const uint8_t emoji[]{0xF0, 0x9F, 0x98, 0x80};
    startArgs.playerName = emoji; startArgs.playerNameLength = sizeof(emoji);
    assert(t7_native_submit_start_named(session, &startArgs, &operationId) == T7NB_INVALID_ARGUMENT);
    startArgs.structSize = 8;
    assert(t7_native_submit_start_named(session, &startArgs, &operationId) == T7NB_INVALID_ABI);
    assert(t7_native_submit_start_named(session, nullptr, &operationId) == T7NB_INVALID_ARGUMENT);
    T7NativeStartOptions options{startArgs, T7NB_START_SKIP_STARTUP_ANIMATION, 0};
    options.start.structSize = sizeof(options);
    operationId = 99;
    assert(t7_native_submit_start_options(session, &options, &operationId) == T7NB_INVALID_ARGUMENT && operationId == 0);
    const uint8_t name[]{'P', 'l', 'a', 'y', 'e', 'r'};
    options.start.playerName = name; options.start.playerNameLength = sizeof(name);
    for (const auto flag : {0u, static_cast<uint32_t>(T7NB_START_SKIP_STARTUP_ANIMATION)}) {
        options.flags = flag;
        assert(t7_native_submit_start_options(nullptr, &options, &operationId) == T7NB_INVALID_HANDLE);
    }
    options.flags = 2;
    assert(t7_native_submit_start_options(session, &options, &operationId) == T7NB_INVALID_ARGUMENT);
    options.flags = T7NB_START_SKIP_STARTUP_ANIMATION; options.reserved = 1;
    assert(t7_native_submit_start_options(session, &options, &operationId) == T7NB_INVALID_ARGUMENT);
    options.reserved = 0; options.start.structSize = sizeof(T7NativeStartArgs);
    assert(t7_native_submit_start_options(session, &options, &operationId) == T7NB_INVALID_ABI);
    options.start.structSize = sizeof(options); options.start.abiVersion = T7NB_ABI_VERSION + 1;
    assert(t7_native_submit_start_options(session, &options, &operationId) == T7NB_INVALID_ABI);
    assert(t7_native_submit_start_options(session, nullptr, &operationId) == T7NB_INVALID_ARGUMENT);
    assert(t7_native_submit_start_options(session, &options, nullptr) == T7NB_INVALID_ARGUMENT);
    assert(t7_native_submit_check(session, &path, &operationId) == T7NB_OK && operationId != 0);
    T7NativeOperation operation{};
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    do {
        operation = {T7NB_ABI_VERSION, sizeof(T7NativeOperation)};
        assert(t7_native_get_operation(session, operationId, &operation) == T7NB_OK);
        if (operation.status >= T7NB_OPERATION_SUCCEEDED) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    } while (std::chrono::steady_clock::now() < deadline);
    assert(operation.status == T7NB_OPERATION_FAILED && operation.errorCode != 0);

    uint32_t required = 0, errorCode = 0;
    assert(t7_native_get_error(session, operationId, nullptr, 0, &required, &errorCode) == T7NB_BUFFER_TOO_SMALL);
    assert(required > 0 && errorCode == operation.errorCode);
    std::vector<char> error(required);
    assert(t7_native_get_error(session, operationId, error.data(), static_cast<uint32_t>(error.size()),
                               &required, &errorCode) == T7NB_OK);
    auto firstError = std::string(error.data(), error.size());
    std::fill(error.begin(), error.end(), '\0');
    assert(t7_native_get_error(session, operationId, error.data(), static_cast<uint32_t>(error.size()),
                               &required, &errorCode) == T7NB_OK);
    assert(firstError == std::string(error.data(), error.size()));

    // The worker remains alive while the session is idle; this catches an
    // empty-queue timeout regression that would otherwise only surface after
    // the first operation completed.
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
    snapshot = {T7NB_ABI_VERSION, sizeof(T7NativeSnapshot)};
    assert(t7_native_get_snapshot(session, &snapshot) == T7NB_OK);
    assert(snapshot.state == T7NB_STATE_FAILED && snapshot.operation == T7NB_OPERATION_NONE
           && snapshot.loginPort == 0 && snapshot.logicPort == 0 && snapshot.instancePort == 0);

    uint64_t cursor = 0;
    uint32_t flags = 0;
    required = 0;
    assert(t7_native_read_logs(session, &cursor, nullptr, 0, &required, &flags) == T7NB_BUFFER_TOO_SMALL);
    assert(cursor == 0 && required > 0);
    std::vector<uint8_t> logs(required);
    assert(t7_native_read_logs(session, &cursor, logs.data(), static_cast<uint32_t>(logs.size()),
                               &required, &flags) == T7NB_OK);
    assert(cursor > 0 && std::string(reinterpret_cast<char*>(logs.data()), logs.size()).find("operation failed") != std::string::npos);
    const auto latestLogTime = localTimestamp();
    std::istringstream logLines(std::string(reinterpret_cast<char*>(logs.data()), logs.size()));
    std::string line;
    while (std::getline(logLines, line)) {
        const auto separator = line.find('\t');
        assert(separator != std::string::npos && line.size() > separator + 22);
        const auto timestamp = line.substr(separator + 1, 19);
        assert(timestamp >= earliestLogTime && timestamp <= latestLogTime);
        assert(line.substr(separator + 20, 2) == "  ");
    }

    // Force the bounded in-memory log window to rotate.  A stale cursor must
    // report a gap, and the flag must remain visible when the first read used
    // a short buffer rather than silently advancing the cursor.
    const auto logDeadline = std::chrono::steady_clock::now() + std::chrono::seconds(15);
    for (int index = 0; index != 530; ++index) {
        uint64_t repeatedId = 0;
        assert(t7_native_submit_check(session, &path, &repeatedId) == T7NB_OK);
        T7NativeOperation repeated{T7NB_ABI_VERSION, sizeof(T7NativeOperation)};
        do {
            repeated = {T7NB_ABI_VERSION, sizeof(T7NativeOperation)};
            assert(t7_native_get_operation(session, repeatedId, &repeated) == T7NB_OK);
            if (repeated.status >= T7NB_OPERATION_SUCCEEDED) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        } while (std::chrono::steady_clock::now() < logDeadline);
        assert(repeated.status == T7NB_OPERATION_FAILED);
        assert(std::chrono::steady_clock::now() < logDeadline);
    }
    uint64_t staleCursor = 1;
    required = 0; flags = 0;
    assert(t7_native_read_logs(session, &staleCursor, nullptr, 0, &required, &flags) == T7NB_BUFFER_TOO_SMALL);
    assert((flags & 1u) != 0 && staleCursor == 1 && required > 0);
    std::vector<uint8_t> rotatedLogs(required);
    assert(t7_native_read_logs(session, &staleCursor, rotatedLogs.data(), static_cast<uint32_t>(rotatedLogs.size()),
                               &required, &flags) == T7NB_OK);
    assert((flags & 1u) != 0 && staleCursor > 1);

    const auto releaseStarted = std::chrono::steady_clock::now();
    assert(t7_native_release(session) == T7NB_OK);
    assert(std::chrono::steady_clock::now() - releaseStarted < std::chrono::milliseconds(100));

    // Release remains non-blocking even when a Start command has already
    // entered the worker.  The worker owns its shared Session reference and
    // completes cleanup after the opaque handle is gone.
    T7NativeSessionHandle active = nullptr;
    assert(t7_native_create(&create, &active) == T7NB_OK && active);
    uint64_t startId = 0;
    assert(t7_native_submit_start(active, &path, &startId) == T7NB_OK && startId != 0);
    const auto activeRelease = std::chrono::steady_clock::now();
    assert(t7_native_release(active) == T7NB_OK);
    assert(std::chrono::steady_clock::now() - activeRelease < std::chrono::milliseconds(100));
    Sleep(250);
    return 0;
}
