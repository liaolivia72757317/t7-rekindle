#include "RunDirectory.h"
#include <algorithm>
#include <atomic>
#include <memory>

namespace t7 {
namespace {
std::atomic<uint64_t> sequence{0};

bool reparsePoint(const fs::path& path) {
    auto attributes = GetFileAttributesW(path.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES) throw std::runtime_error(errorText("retention attributes"));
    return (attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
}

class FileLock final {
public:
    explicit FileLock(const fs::path& path, bool wait = false) {
        for (unsigned attempt = 0; ; ++attempt) {
            handle = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE,
                wait ? 0 : FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, OPEN_ALWAYS,
                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
            if (handle != INVALID_HANDLE_VALUE) return;
            auto error = GetLastError();
            if (error != ERROR_SHARING_VIOLATION && error != ERROR_LOCK_VIOLATION)
                throw std::runtime_error("retention lock failed: " + std::to_string(error));
            if (!wait) return;
            if (attempt == 499) throw std::runtime_error("retention lock timed out");
            Sleep(10);
        }
    }
    ~FileLock() { if (handle != INVALID_HANDLE_VALUE) CloseHandle(handle); }
    FileLock(const FileLock&) = delete;
    FileLock& operator=(const FileLock&) = delete;
    HANDLE handle = INVALID_HANDLE_VALUE;
};

bool runName(const std::wstring& name) {
    unsigned separators = 0;
    bool digit = false;
    for (auto value : name) {
        if (value >= L'0' && value <= L'9') digit = true;
        else if (value == L'-' && digit) { ++separators; digit = false; }
        else return false;
    }
    return digit && separators == 2;
}

uintmax_t directoryBytes(const fs::path& root) {
    uintmax_t bytes = 0;
    for (const auto& entry : fs::recursive_directory_iterator(root)) {
        if (reparsePoint(entry.path())) throw std::runtime_error("retention directory contains a reparse point");
        if (entry.is_regular_file()) bytes += entry.file_size();
    }
    return bytes;
}
}

RunDirectory::RunDirectory(const fs::path& dataRoot) {
    fs::create_directories(dataRoot);
    if (reparsePoint(dataRoot)) throw std::runtime_error("session data root must not be a reparse point");
    const auto root = fs::canonical(dataRoot);
    FileLock guard(root / L".retention.lock", true);
    do {
        const auto id = std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64())
            + L"-" + std::to_wstring(++sequence);
        path_ = root / id;
    } while (!fs::create_directory(path_));
    lease_ = CreateFileW((path_ / L".active").c_str(), GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (lease_ == INVALID_HANDLE_VALUE) throw std::runtime_error(errorText("session data lease"));
}

RunDirectory::~RunDirectory() {
    if (lease_ != INVALID_HANDLE_VALUE) CloseHandle(lease_);
}

void RunDirectory::finish() {
    fs::last_write_time(path_, fs::file_time_type::clock::now());
    if (lease_ != INVALID_HANDLE_VALUE) { CloseHandle(lease_); lease_ = INVALID_HANDLE_VALUE; }
    prune(path_.parent_path());
}

void RunDirectory::prune(const fs::path& dataRoot, size_t maxRuns, uintmax_t maxBytes) {
    if (!maxRuns || !maxBytes) throw std::runtime_error("history retention limits must be positive");
    if (reparsePoint(dataRoot)) throw std::runtime_error("session data root must not be a reparse point");
    const auto root = fs::canonical(dataRoot);
    FileLock guard(root / L".retention.lock", true);
    struct Candidate {
        fs::path path;
        fs::file_time_type time;
        uintmax_t bytes;
        std::unique_ptr<FileLock> lease;
    };
    std::vector<Candidate> ended;
    uintmax_t total = 0;
    for (const auto& entry : fs::directory_iterator(root)) {
        if (!runName(entry.path().filename().wstring()) || reparsePoint(entry.path()) || !entry.is_directory()) continue;
        auto time = entry.last_write_time();
        auto lease = std::make_unique<FileLock>(entry.path() / L".active");
        if (lease->handle == INVALID_HANDLE_VALUE) continue;
        auto bytes = directoryBytes(entry.path());
        total += bytes;
        ended.push_back({entry.path(), time, bytes, std::move(lease)});
    }
    std::sort(ended.begin(), ended.end(), [](const Candidate& left, const Candidate& right) {
        return left.time == right.time ? left.path < right.path : left.time < right.time;
    });
    size_t remaining = ended.size();
    for (auto& run : ended) {
        if (remaining <= maxRuns && total <= maxBytes) break;
        // Only immediate, owned run directories may be removed. Never follow
        // junctions, even if an old data directory has been edited externally.
        if (reparsePoint(run.path) || fs::canonical(run.path).parent_path() != root)
            throw std::runtime_error("retention path escaped the session data root");
        run.lease.reset();
        fs::remove_all(run.path);
        total -= run.bytes;
        --remaining;
    }
}
}
