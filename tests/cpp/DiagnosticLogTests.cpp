#include "../../src/Runtime/bridge/Session.h"
#include "../../src/Runtime/core/DiagnosticLog.h"
#include "../../tests/reference/native-server/PythonHost.h"
#include <algorithm>
#include <chrono>
#include <fstream>
#include <iostream>
#include <thread>

namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

std::string contents(const t7r::fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    require(static_cast<bool>(input), "diagnostic fixture file is missing");
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

class LocalDataScope {
public:
    explicit LocalDataScope(const t7r::fs::path& root) {
        wchar_t value[32768]{};
        const auto length = GetEnvironmentVariableW(L"LOCALAPPDATA", value, static_cast<DWORD>(std::size(value)));
        require(length < std::size(value), "LOCALAPPDATA exceeds fixture capacity");
        previous_ = value;
        require(SetEnvironmentVariableW(L"LOCALAPPDATA", root.c_str()) != 0, "local data fixture setup failed");
    }
    ~LocalDataScope() {
        if (!SetEnvironmentVariableW(L"LOCALAPPDATA", previous_.empty() ? nullptr : previous_.c_str())) std::terminate();
    }
private:
    std::wstring previous_;
};

std::string checkLogs(const t7r::fs::path& packageRoot, t7r::Bootstrap::TestAdapter adapter, uint32_t status,
                      uint32_t minimumLevel = UINT32_MAX, const std::function<void(t7r::bridge::Session&)>& prepare = {}) {
    auto session = t7r::bridge::Session::createForTest(t7r::utf8(packageRoot.wstring()), std::move(adapter));
    try {
        if (minimumLevel != UINT32_MAX) require(session->setLogLevel(minimumLevel) == T7RNB_OK, "diagnostic level setup failed");
        if (prepare) prepare(*session);
        uint64_t id = 0;
        require(session->submit(T7RNB_OPERATION_CHECK, t7r::utf8((packageRoot / "synthetic-client" / "Bin").wstring()), id) == T7RNB_OK,
            "diagnostic check submission failed");
        t7r::bridge::Operation operation;
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
        do {
            require(session->operation(id, operation) == T7RNB_OK, "diagnostic operation lookup failed");
            if (operation.status >= T7RNB_OPERATION_SUCCEEDED) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        } while (std::chrono::steady_clock::now() < deadline);
        require(operation.status == status, "diagnostic check did not finish");
        uint64_t cursor = 0;
        uint32_t required = 0, flags = 0;
        require(session->readLogs(cursor, nullptr, 0, required, flags) == T7RNB_BUFFER_TOO_SMALL,
            "diagnostic check produced no records");
        std::vector<uint8_t> buffer(required);
        require(session->readLogs(cursor, buffer.data(), required, required, flags) == T7RNB_OK,
            "diagnostic log read failed");
        session->requestClose();
        require(session->setLogLevel(T7RNB_LOG_INFO) == T7RNB_INVALID_HANDLE, "closed session accepted a log level");
        require(session->waitForWorkerForTest(std::chrono::seconds(5)), "diagnostic worker did not stop");
        return {buffer.begin(), buffer.end()};
    } catch (...) {
        session->requestClose();
        session->waitForWorkerForTest(std::chrono::seconds(5));
        throw;
    }
}

void verifyLevels(const t7r::fs::path& packageRoot, const t7r::fs::path& root) {
    for (uint32_t minimum = T7RNB_LOG_DEBUG; minimum <= T7RNB_LOG_ERROR; ++minimum) {
        const auto directory = root / std::to_string(minimum);
        LocalDataScope local(directory);
        t7r::Bootstrap::TestAdapter adapter;
        adapter.check = [](const auto&, const auto&, const t7r::DiagnosticLogCallback& log) {
            for (uint32_t level = T7RNB_LOG_DEBUG; level <= T7RNB_LOG_ERROR; ++level)
                log(static_cast<t7r::DiagnosticLogLevel>(level), "fixture-level-" + std::to_string(level));
        };
        const auto logs = checkLogs(packageRoot, std::move(adapter), T7RNB_OPERATION_SUCCEEDED, minimum);
        const auto disk = contents(directory / "T7-Rekindle/logs/native.log");
        for (uint32_t level = T7RNB_LOG_DEBUG; level <= T7RNB_LOG_ERROR; ++level) {
            const auto marker = "fixture-level-" + std::to_string(level);
            require((logs.find(marker) != std::string::npos) == (level >= minimum), "native memory level filter mismatch");
            require((disk.find(marker) != std::string::npos) == (level >= minimum), "native file level filter mismatch");
        }
        require(logs.find("  WARN  ") != std::string::npos || minimum == T7RNB_LOG_ERROR, "native warning is not canonical");
        require(logs.find("WARNING") == std::string::npos, "native output used legacy severity");
    }
    require(t7r::diagnosticRecordLevel("2026-01-02 03:04:05  WARNING  [fixture] legacy") == t7r::DiagnosticLogLevel::Warn,
        "legacy warning parsing changed");
}

void verifyLiveLevelChanges(const t7r::fs::path& packageRoot, const t7r::fs::path& root) {
    LocalDataScope local(root);
    t7r::bridge::Session* current = nullptr;
    t7r::Bootstrap::TestAdapter adapter;
    adapter.check = [&](const auto&, const auto&, const t7r::DiagnosticLogCallback& log) {
        log(t7r::DiagnosticLogLevel::Debug, "hidden-at-default");
        log(t7r::DiagnosticLogLevel::Info, "retained-before-change");
        require(current->setLogLevel(T7RNB_LOG_DEBUG) == T7RNB_OK, "changing level during check failed");
        log(t7r::DiagnosticLogLevel::Debug, "visible-after-debug");
        require(current->setLogLevel(T7RNB_LOG_ERROR) == T7RNB_OK, "changing level to error failed");
        log(t7r::DiagnosticLogLevel::Warn, "hidden-after-error");
        log(t7r::DiagnosticLogLevel::Error, "visible-error");
    };
    const auto logs = checkLogs(packageRoot, std::move(adapter), T7RNB_OPERATION_SUCCEEDED, UINT32_MAX,
        [&](auto& session) { current = &session; });
    for (const auto& text : {logs, contents(root / "T7-Rekindle/logs/native.log")}) {
        require(text.find("hidden-") == std::string::npos, "live level change leaked filtered records");
        require(text.find("retained-before-change") != std::string::npos && text.find("visible-after-debug") != std::string::npos
            && text.find("visible-error") != std::string::npos, "live level change rewrote or lost logs");
    }
}

void verifyBusinessLevels(const t7r::fs::path& packageRoot, const t7r::fs::path& root) {
    t7r::PythonHost python(packageRoot, root);
    const auto context = python.context(t7r::Config{}, 1, {});
    const auto state = python.create(context);
    t7r::Event event;
    event.type = "operator"; event.name = "diagnostic";
    const auto result = python.dispatch(event, state, context);
    require(result.logs.size() == 1 && result.logs.front().level == t7r::DiagnosticLogLevel::Debug
        && result.logs.front().message.find("diagnostic phase=") == 0, "Python host lost structured severity or message");
}

void verifyNativeWriteFailure(const t7r::fs::path& packageRoot, const t7r::fs::path& root) {
    LocalDataScope local(root);
    t7r::fs::create_directories(root / "T7-Rekindle/logs/native.log");
    t7r::Bootstrap::TestAdapter adapter;
    adapter.check = [](const t7r::fs::path&, const t7r::Config&, const t7r::DiagnosticLogCallback&) {
        throw std::runtime_error("fixture native failure");
    };
    const auto logs = checkLogs(packageRoot, std::move(adapter), T7RNB_OPERATION_FAILED);
    require(logs.find("  ERROR  [NativeBridge] operation failed: fixture native failure") != std::string::npos,
        "native file failure replaced the original error");
    require(logs.find(u8"  ERROR  [NativeBridge] 日志文件写入失败：") != std::string::npos,
        "native file failure has no separate warning");
}

void verifyUtf8Boundary(const t7r::fs::path& packageRoot, const t7r::fs::path& root) {
    LocalDataScope local(root);
    std::string message;
    for (unsigned index = 0; index < 4096; ++index) message += u8"界";
    message += " end-of-full-record";
    t7r::Bootstrap::TestAdapter adapter;
    adapter.check = [message](const t7r::fs::path&, const t7r::Config&, const t7r::DiagnosticLogCallback& log) {
        log(t7r::DiagnosticLogLevel::Info, message);
    };
    const auto logs = checkLogs(packageRoot, std::move(adapter), T7RNB_OPERATION_SUCCEEDED);
    require(!t7r::wide(logs).empty() && logs.find("[truncated]") != std::string::npos,
        "long native log is invalid UTF-8 or lacks a truncation marker");
    require(contents(root / "T7-Rekindle/logs/native.log").find(message) != std::string::npos,
        "limiting in-memory logs truncated the disk record");
    for (const auto& value : {std::string(8192, 'a'), std::string(8193, 'a'), message + "\r\n"}) {
        const auto bounded = t7r::boundedDiagnosticRecord(value);
        require(bounded.size() <= 8192 && !t7r::wide(bounded).empty(), "diagnostic record exceeded its UTF-8 byte limit");
        require(bounded.find_first_of("\r\n") == std::string::npos, "diagnostic record contains ABI line separators");
        if (value.size() == 8192) require(bounded == value, "exact-limit record was unnecessarily truncated");
    }
}

void verifyDailyRotation(const t7r::fs::path& root) {
    t7r::appendNativeLog(root, "day0");
    t7r::appendNativeLog(root, "same-day");
    require(std::distance(t7r::fs::directory_iterator(root), t7r::fs::directory_iterator()) == 1,
        "native log rotated within the same day");
    std::ofstream(root / "unrelated.log") << "keep";
    const auto written = t7r::fs::file_time_type::clock::now() - std::chrono::hours(48);
    for (unsigned day = 1; day <= 9; ++day) {
        t7r::fs::last_write_time(root / "native.log", written + std::chrono::seconds(day));
        t7r::appendNativeLog(root, "day" + std::to_string(day));
    }
    const auto archived = [&root] {
        std::vector<std::string> records;
        for (const auto& entry : t7r::fs::directory_iterator(root))
            if (entry.path().filename() != "native.log" && entry.path().filename() != "unrelated.log")
                records.push_back(contents(entry.path()));
        std::sort(records.begin(), records.end());
        return records;
    };
    const std::vector<std::string> expected{"day2\n", "day3\n", "day4\n", "day5\n", "day6\n", "day7\n", "day8\n"};
    require(contents(root / "native.log") == "day9\n" && archived() == expected
        && contents(root / "unrelated.log") == "keep", "native daily archive retention is incorrect");
    t7r::fs::last_write_time(root / "native.log", written);
    const auto locked = CreateFileW((root / "native.log").c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
        nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    require(locked != INVALID_HANDLE_VALUE, "native archive lock fixture failed");
    bool failed = false;
    try { t7r::appendNativeLog(root, "blocked"); }
    catch (const std::exception&) { failed = true; }
    require(CloseHandle(locked) != 0, "native archive lock release failed");
    require(failed && archived() == expected && contents(root / "native.log") == "day9\n",
        "failed native rotation deleted retained log records");
}

void verifyPythonTraceback(const t7r::fs::path& packageRoot, const t7r::fs::path& cache) {
    t7r::PythonHost python(packageRoot, cache);
    const auto failure = [&python] {
        try { python.phase("invalid-json"); }
        catch (const std::exception& error) { return std::string(error.what()); }
        throw std::runtime_error("invalid JSON did not raise a Python exception");
    };
    const auto detail = failure();
    require(detail.rfind("JSONDecodeError:", 0) == 0
        && detail.find("Traceback (most recent call last)") != std::string::npos
        && detail.find("JSONDecodeError") != std::string::npos && detail.find("host_runtime.py") != std::string::npos,
        "Python exception lost its type or traceback");
    require(PyRun_SimpleString("import traceback\n"
        "def broken_formatter(*args, **kwargs):\n"
        "    raise RuntimeError('fixture formatter failure')\n"
        "traceback.format_exception = broken_formatter\n") == 0, "Python formatting fixture failed");
    const auto fallback = failure();
    require(fallback.find("JSONDecodeError:") != std::string::npos && fallback.find("Expecting value") != std::string::npos,
        "Python formatting failure replaced the original exception");
    require(python.phase("[\"dict\",{}]") == "", "Python formatting failure leaked an active exception");
}
}

bool verifyDiagnosticLogs(const t7r::fs::path& packageRoot) {
    try {
        const auto root = packageRoot / "diagnostic-tests";
        t7r::fs::create_directories(root);
        verifyLevels(packageRoot, root / "levels");
        verifyLiveLevelChanges(packageRoot, root / "live-levels");
        verifyBusinessLevels(packageRoot, root / "business-levels");
        verifyNativeWriteFailure(packageRoot, root / "write-failure");
        verifyUtf8Boundary(packageRoot, root / "utf8");
        verifyDailyRotation(root / "rotation");
        verifyPythonTraceback(packageRoot, root / "python-cache");
        return true;
    } catch (const std::exception& error) {
        std::cerr << "diagnostic logging: " << error.what() << '\n';
        return false;
    }
}
