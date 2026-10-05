#pragma once
#include "RemoteImage.h"
#include <atomic>
#include <future>
#include <mutex>
#include <thread>

namespace t7 {
struct DllEntryRule {
    std::wstring name;
    std::string sha256;
    uint32_t entryRva;
    Bytes entrySignature;
    std::function<std::vector<MemoryPatch>(uint32_t)> patches;
};
struct ChildImageRule {
    fs::path executable;
    std::string sha256;
};

class DebugClient final {
public:
    ~DebugClient();
    void start(const fs::path& executable, const std::string& imageHash,
               const std::vector<DllEntryRule>& rules,
               const std::function<void(HANDLE, HANDLE)>& prepare,
               const std::function<void(std::string)>& log = {},
               const std::function<bool()>& cancelled = {},
               const std::vector<ChildImageRule>& children = {},
               const std::function<bool(DWORD, uintptr_t)>& breakpoint = {});
    HANDLE process() const noexcept { return process_; }
    DWORD pid() const noexcept { return pid_; }
    void check() const;
    void stop();
private:
    HANDLE process_ = nullptr;
    DWORD pid_ = 0;
    std::thread worker_;
    std::atomic<bool> stopping_{false};
    mutable std::mutex mutex_;
    std::string error_;
    std::string cleanupError_;
    void fail(const std::string& message, bool cleanup = false);
};
}
