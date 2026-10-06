#pragma once
#include "../core/Common.h"
#include <thread>
#include <mutex>
#include <condition_variable>
#include <deque>
#include <fstream>

namespace t7 {
class Journal {
public:
    explicit Journal(const fs::path& root, size_t segmentLimit = 64 * 1024 * 1024,
                     unsigned maxSegments = 160);
    ~Journal();
    void stop();
    void add(std::string direction, uint64_t connection, std::string version, std::string reason,
             Bytes bytes = {}, bool capture = true);
    std::vector<std::string> lines();
private:
    struct Record {
        std::string timestamp, direction, version, reason;
        uint64_t connection;
        Bytes bytes;
        int64_t monotonicUs;
        size_t wireLength;
        bool captured;
    };
    fs::path root_;
    size_t segmentLimit_;
    unsigned maxSegments_;
    std::mutex mutex_;
    std::condition_variable changed_;
    std::deque<Record> pending_;
    std::deque<std::string> lines_;
    size_t queuedBytes_ = 0;
    uint64_t dropped_ = 0;
    bool stopping_ = false, failed_ = false;
    std::thread thread_;
    void run();
};
}
