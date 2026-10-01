#include "../../src/Runtime/server/Journal.h"
#include "../../src/Runtime/server/RunDirectory.h"
#include <algorithm>
#include <chrono>
#include <thread>

namespace {
bool verifyWriteFailure(const t7::fs::path& root) {
    t7::Journal journal(root);
    t7::fs::create_directory(root / "frames-1.bin");
    for (unsigned i = 0; i < 20000; ++i) {
        journal.add("TEST", i, "synthetic", "queued");
        if (i % 100 == 0) {
            const auto lines = journal.lines();
            if (std::any_of(lines.begin(), lines.end(), [](const std::string& line) {
                return line.find("LOG FAILURE:") != std::string::npos;
            })) break;
        }
    }
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (std::chrono::steady_clock::now() < deadline) {
        const auto lines = journal.lines();
        const auto failures = std::count_if(lines.begin(), lines.end(), [](const std::string& line) {
            return line.find("LOG FAILURE:") != std::string::npos;
        });
        if (failures) return failures == 1 && lines.size() <= 1000;
        // Later diagnostics may rotate the original error out of the window.
        if (std::any_of(lines.begin(), lines.end(), [](const std::string& line) {
            return line.find("WIRE COVERAGE INCOMPLETE") != std::string::npos;
        })) return lines.size() <= 1000;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    return false;
}

bool verifyRetention(const t7::fs::path& data) {
    t7::RunDirectory active(data);
    std::ofstream(active.path() / "active.bin", std::ios::binary) << std::string(1024, 'a');
    std::vector<t7::fs::path> paths;
    for (unsigned i = 0; i < 20; ++i) {
        t7::RunDirectory ended(data);
        t7::fs::create_directory(ended.path() / "wire");
        t7::fs::create_directory(ended.path() / "revisions");
        std::ofstream(ended.path() / "wire/frame.bin", std::ios::binary) << std::string(20, 'w');
        std::ofstream(ended.path() / "revisions/source.py", std::ios::binary) << std::string(20, 'r');
        paths.push_back(ended.path());
        ended.finish();
        Sleep(1);
    }
    auto count = std::count_if(paths.begin(), paths.end(), [](const t7::fs::path& path) { return t7::fs::exists(path); });
    if (count != 16 || t7::fs::exists(paths.front()) || !t7::fs::exists(paths.back())) return false;
    t7::RunDirectory::prune(data, 16, 80);
    count = std::count_if(paths.begin(), paths.end(), [](const t7::fs::path& path) { return t7::fs::exists(path); });
    if (count != 2 || !t7::fs::exists(active.path() / "active.bin")) return false;
    // A released lease is collectible even if the process never called finish().
    t7::fs::path abandoned;
    {
        t7::RunDirectory run(data);
        abandoned = run.path();
        std::ofstream(abandoned / "large.bin", std::ios::binary) << std::string(100, 'x');
    }
    t7::RunDirectory::prune(data, 16, 80);
    return !t7::fs::exists(abandoned) && t7::fs::exists(active.path());
}
}

bool verifyJournalContracts(const t7::fs::path& fixtureRoot) {
    return verifyWriteFailure(fixtureRoot / "journal-failure")
        && verifyRetention(fixtureRoot / "history-retention");
}
