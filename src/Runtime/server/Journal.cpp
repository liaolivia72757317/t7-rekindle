#include "Journal.h"
#include <sstream>
#include <chrono>

namespace t7 {
Journal::Journal(const fs::path& root, size_t segmentLimit, unsigned maxSegments)
    : root_(root), segmentLimit_(segmentLimit), maxSegments_(maxSegments) {
    if (!segmentLimit_ || !maxSegments_) throw std::runtime_error("journal retention limits must be positive");
    std::error_code error;
    if (!fs::create_directory(root_, error) || error)
        throw std::runtime_error(error ? "journal directory creation failed: " + error.message()
                                       : "journal directory already exists");
    thread_ = std::thread(&Journal::run, this);
}
Journal::~Journal() {
    { std::lock_guard<std::mutex> lock(mutex_); stopping_ = true; }
    changed_.notify_all(); if (thread_.joinable()) thread_.join();
}
void Journal::add(std::string direction, uint64_t connection, std::string version, std::string reason,
                  Bytes bytes, bool capture) {
    const auto wireLength = bytes.size();
    if (!capture) Bytes{}.swap(bytes);
    std::lock_guard<std::mutex> lock(mutex_);
    if (stopping_) {
        ++dropped_;
        return;
    }
    auto timestamp = utcNow();
    auto monotonicUs = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
    lines_.push_back(timestamp + " " + direction + " #" + std::to_string(connection) + " " + version.substr(0, 12) + " " + reason);
    while (lines_.size() > 1000) lines_.pop_front();
    size_t size = bytes.size() + reason.size() + 256;
    if (failed_ || queuedBytes_ + size > 8 * 1024 * 1024) {
        ++dropped_;
        if (dropped_ == 1) {
            lines_.push_back("WIRE COVERAGE INCOMPLETE: log queue/write failure");
            while (lines_.size() > 1000) lines_.pop_front();
        }
        return;
    }
    queuedBytes_ += size;
    pending_.push_back({timestamp, std::move(direction), std::move(version), std::move(reason),
                       connection, std::move(bytes), monotonicUs, wireLength, capture});
    changed_.notify_one();
}
std::vector<std::string> Journal::lines() {
    std::lock_guard<std::mutex> lock(mutex_); return {lines_.begin(), lines_.end()};
}
void Journal::run() {
    uint64_t recordId = 0, retained = 0, offset = 0;
    unsigned segment = 0;
    std::ofstream raw, index;
    while (true) {
        Record record;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            changed_.wait(lock, [this] { return stopping_ || !pending_.empty(); });
            if (pending_.empty() && stopping_) break;
            record = std::move(pending_.front()); pending_.pop_front();
            queuedBytes_ -= record.bytes.size() + record.reason.size() + 256;
        }
        try {
            if (!segment || retained + record.bytes.size() + 2048 > segmentLimit_) {
                raw.close(); index.close(); ++segment; retained = offset = 0;
                auto stem = "frames-" + std::to_string(segment);
                raw.open(root_ / (stem + ".bin"), std::ios::binary | std::ios::out);
                index.open(root_ / (stem + ".jsonl"), std::ios::out);
                if (!raw || !index) throw std::runtime_error("wire segment open failed");
                if (segment > maxSegments_) {
                    auto oldest = "frames-" + std::to_string(segment - maxSegments_);
                    fs::remove(root_ / (oldest + ".bin")); fs::remove(root_ / (oldest + ".jsonl"));
                }
            }
            auto stem = "frames-" + std::to_string(segment) + ".bin";
            std::ostringstream line;
            line << "{\"recordId\":" << ++recordId << ",\"timestamp\":" << jsonString(record.timestamp)
                << ",\"monotonicUs\":" << record.monotonicUs
                << ",\"direction\":" << jsonString(record.direction) << ",\"connectionId\":" << record.connection
                << ",\"scriptVersion\":" << jsonString(record.version) << ",\"reason\":" << jsonString(record.reason)
                << ",\"captured\":" << (record.captured ? "true" : "false")
                << ",\"rawFile\":" << (record.captured ? jsonString(stem) : "null")
                << ",\"offset\":" << (record.captured ? std::to_string(offset) : "null")
                << ",\"wireLength\":" << record.wireLength
                << ",\"wireSha256\":" << (record.captured ? jsonString(sha256(record.bytes)) : "null") << "}\n";
            if (!record.bytes.empty()) raw.write(reinterpret_cast<const char*>(record.bytes.data()), static_cast<std::streamsize>(record.bytes.size()));
            raw.flush(); index << line.str(); index.flush();
            if (!raw || !index) throw std::runtime_error("wire write failed");
            offset += record.bytes.size(); retained += record.bytes.size() + line.str().size();
        } catch (const std::exception& e) {
            std::lock_guard<std::mutex> lock(mutex_);
            failed_ = true;
            dropped_ += pending_.size() + 1;
            pending_.clear(); queuedBytes_ = 0;
            lines_.push_back(std::string("LOG FAILURE: ") + e.what());
            lines_.push_back("WIRE COVERAGE INCOMPLETE: pending records discarded after write failure");
            while (lines_.size() > 1000) lines_.pop_front();
            return;
        }
    }
}
}
