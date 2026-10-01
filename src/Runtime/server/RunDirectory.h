#pragma once
#include "../core/Common.h"

namespace t7 {
class RunDirectory final {
public:
    explicit RunDirectory(const fs::path& dataRoot);
    ~RunDirectory();
    RunDirectory(const RunDirectory&) = delete;
    RunDirectory& operator=(const RunDirectory&) = delete;
    const fs::path& path() const { return path_; }
    void finish();
    static void prune(const fs::path& dataRoot, size_t maxRuns = 16,
                      uintmax_t maxBytes = 10ULL * 1024 * 1024 * 1024);
private:
    fs::path path_;
    HANDLE lease_ = INVALID_HANDLE_VALUE;
};
}
