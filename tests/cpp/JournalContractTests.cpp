#include "../../tests/reference/native-server/Journal.h"
#include "../../tests/reference/native-server/RunDirectory.h"
#include <algorithm>
#include <chrono>
#include <thread>

namespace {
bool verifyLevels(const t7r::fs::path& root) {
    auto filter = std::make_shared<t7r::DiagnosticLogFilter>();
    t7r::Journal journal(root, 64 * 1024 * 1024, 160, filter);
    for (uint32_t minimum = 0; minimum <= 3; ++minimum) {
        filter->minimum.store(static_cast<t7r::DiagnosticLogLevel>(minimum));
        for (uint32_t level = 0; level <= 3; ++level) {
            const auto before = journal.lines().size();
            journal.add(static_cast<t7r::DiagnosticLogLevel>(level), "BUSINESS", 1, "fixture", "matrix");
            if (journal.lines().size() != before + (level >= minimum ? 1u : 0u)) return false;
        }
    }
    journal.stop();
    std::ifstream index(root / "frames-1.jsonl");
    std::string line;
    unsigned count = 0;
    while (std::getline(index, line)) {
        if (line.find("\"reason\":\"matrix\"") == std::string::npos) return false;
        ++count;
    }
    return count == 16 && journal.lines().size() == 10;
}

bool verifyFinalRecords(const t7r::fs::path& root) {
    t7r::Journal journal(root);
    t7r::fs::create_directory(root / "frames-1.bin");
    journal.add(t7r::DiagnosticLogLevel::Error, "ERROR", 42, "synthetic", "fixture business failure");
    const auto original = journal.lines().front();
    journal.stop();
    journal.stop();
    const auto lines = journal.lines();
    return lines.front() == original
        && original.find("  ERROR  [Journal] #42 synthetic fixture business failure") == 19
        && std::any_of(lines.begin(), lines.end(), [](const std::string& line) {
            return line.find("  ERROR  [Journal] LOG FAILURE:") == 19;
        })
        && std::any_of(lines.begin(), lines.end(), [](const std::string& line) {
            return line.find("  WARN  [Journal] WIRE COVERAGE INCOMPLETE:") == 19;
        });
}

bool verifyWriteFailure(const t7r::fs::path& root) {
    t7r::Journal journal(root);
    t7r::fs::create_directory(root / "frames-1.bin");
    for (unsigned i = 0; i < 20000; ++i) {
        journal.add(t7r::DiagnosticLogLevel::Info, "TEST", i, "synthetic", "queued");
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

bool verifyRetention(const t7r::fs::path& data) {
    t7r::RunDirectory active(data);
    std::ofstream(active.path() / "active.bin", std::ios::binary) << std::string(1024, 'a');
    std::vector<t7r::fs::path> paths;
    for (unsigned i = 0; i < 20; ++i) {
        t7r::RunDirectory ended(data);
        t7r::fs::create_directory(ended.path() / "wire");
        t7r::fs::create_directory(ended.path() / "revisions");
        std::ofstream(ended.path() / "wire/frame.bin", std::ios::binary) << std::string(20, 'w');
        std::ofstream(ended.path() / "revisions/source.py", std::ios::binary) << std::string(20, 'r');
        paths.push_back(ended.path());
        ended.finish();
        Sleep(1);
    }
    auto count = std::count_if(paths.begin(), paths.end(), [](const t7r::fs::path& path) { return t7r::fs::exists(path); });
    if (count != 16 || t7r::fs::exists(paths.front()) || !t7r::fs::exists(paths.back())) return false;
    t7r::RunDirectory::prune(data, 16, 80);
    count = std::count_if(paths.begin(), paths.end(), [](const t7r::fs::path& path) { return t7r::fs::exists(path); });
    if (count != 2 || !t7r::fs::exists(active.path() / "active.bin")) return false;
    // A released lease is collectible even if the process never called finish().
    t7r::fs::path abandoned;
    {
        t7r::RunDirectory run(data);
        abandoned = run.path();
        std::ofstream(abandoned / "large.bin", std::ios::binary) << std::string(100, 'x');
    }
    t7r::RunDirectory::prune(data, 16, 80);
    return !t7r::fs::exists(abandoned) && t7r::fs::exists(active.path());
}
}

bool verifyJournalContracts(const t7r::fs::path& fixtureRoot) {
    return verifyLevels(fixtureRoot / "journal-levels")
        && verifyWriteFailure(fixtureRoot / "journal-failure")
        && verifyFinalRecords(fixtureRoot / "journal-final-records")
        && verifyRetention(fixtureRoot / "history-retention");
}
