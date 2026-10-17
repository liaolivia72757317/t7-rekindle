#include "BattleCapture.h"
#include <realtimeapiset.h>

#pragma comment(lib, "mincore.lib")
#include <algorithm>
#include <cwchar>
#include <iomanip>
#include <locale>
#include <sstream>

namespace t7r::bridge {
namespace {
constexpr size_t MAX_QUEUE_BYTES = 32 * 1024 * 1024;
constexpr size_t MAX_QUEUE_BATCHES = 16;

bool inside(const fs::path& candidate, const fs::path& parent) {
    auto child = candidate.begin(), root = parent.begin();
    for (; root != parent.end(); ++root, ++child) {
        if (child == candidate.end() || _wcsicmp(child->c_str(), root->c_str()) != 0) return false;
    }
    return true;
}

void point(std::ostringstream& line, const std::array<float, 3>& value) {
    line << '[' << value[0] << ',' << value[1] << ',' << value[2] << ']';
}

std::string header() {
    std::ostringstream line;
    line << "{\"schemaVersion\":1,\"type\":\"captureStart\",\"sourceId\":1,"
            "\"clock\":\"GetTickCount64\",\"ridMap\":[";
    for (uint32_t rid = 1; rid <= MAX_WORLD_ACTORS; ++rid) {
        if (rid != 1) line << ',';
        line << "{\"actorId\":" << rid << ",\"clientRid\":" << rid << '}';
    }
    line << "]}\n";
    return line.str();
}

std::string batchLine(const WorldSnapshot& world, uint64_t batchId, uint64_t capturedAt) {
    std::ostringstream line;
    line.imbue(std::locale::classic());
    line << std::setprecision(17);
    line << "{\"schemaVersion\":1,\"type\":\"sample\",\"sourceId\":1,\"epoch\":"
         << world.instanceStartedAt << ",\"worldBatchId\":" << batchId
         << ",\"contactBatchId\":" << world.contactBatchId
         << ",\"sampledAtUptimeMs\":" << world.sampledAt
         << ",\"capturedAtUptimeMs\":" << capturedAt
         << ",\"contactThroughAtUptimeMs\":" << world.sampledAt
         << ",\"ready\":" << (world.ready ? "true" : "false")
         << ",\"contactsReady\":" << (world.contactsReady ? "true" : "false")
         << ",\"contactFrames\":" << world.contactFrames
         << ",\"droppedContacts\":" << world.droppedContacts << ",\"actors\":[";
    bool first = true;
    for (size_t i = 0; i < world.actors.size(); ++i) {
        const auto& actor = world.actors[i];
        if (!actor.ready) continue;
        if (!first) line << ',';
        first = false;
        const auto rid = i + 1;
        line << "{\"actorId\":" << rid << ",\"clientRid\":" << rid
             << ",\"entity\":" << actor.entity << ",\"position\":";
        point(line, actor.position);
        line << ",\"heading\":" << actor.heading << ",\"state\":" << actor.state
             << ",\"sequence\":" << actor.sequence << ",\"phaseAtUptimeMs\":" << actor.phaseAt
             << ",\"forwardAttemptAtUptimeMs\":" << actor.forwardAttemptAt
             << ",\"attemptedForward\":" << (actor.forwardAttemptAt ? "true" : "false")
             << ",\"freshForwardAttempt\":" << (actor.forwardAttemptAt
                    && actor.forwardAttemptAt <= world.sampledAt
                    && world.sampledAt - actor.forwardAttemptAt <= 250 ? "true" : "false") << '}';
    }
    line << "],\"contacts\":[";
    first = true;
    for (const auto& event : world.contacts) {
        if (!first) line << ',';
        first = false;
        line << "{\"actorId\":" << event.actorId << ",\"targetId\":" << event.hit.targetId
             << ",\"sourceEntity\":" << event.entity << ",\"targetEntity\":" << event.targetEntity
             << ",\"state\":" << event.state << ",\"sequence\":" << event.sequence
             << ",\"targetState\":" << event.targetState << ",\"targetSequence\":" << event.targetSequence
             << ",\"phaseAtUptimeMs\":" << event.phaseAt << ",\"targetPhaseAtUptimeMs\":" << event.targetPhaseAt
             << ",\"targetObservedAtUptimeMs\":" << event.targetObservedAt
             << ",\"sourceObservedAtUptimeMs\":" << event.sourceObservedAt
             << ",\"occurredAtUptimeMs\":" << event.occurredAt
             << ",\"sweepTime\":" << event.hit.time << ",\"along\":" << event.hit.along
             << ",\"bodyIndex\":" << event.hit.bodyIndex << ",\"region\":" << event.hit.region
             << ",\"defenceWeaponId\":" << event.hit.defenceWeaponId
             << ",\"material\":" << event.hit.contact.material << ",\"position\":";
        point(line, event.hit.contact.position);
        line << ",\"normal\":"; point(line, event.hit.contact.normal);
        line << ",\"direction\":"; point(line, event.hit.contact.direction);
        line << ",\"sourcePosition\":"; point(line, event.sourcePosition);
        line << ",\"targetPosition\":"; point(line, event.targetPosition);
        line << ",\"sourceHeading\":" << event.sourceHeading
             << ",\"targetHeading\":" << event.targetHeading
             << ",\"sourceVelocity\":"; point(line, event.sourceVelocity);
        line << ",\"targetVelocity\":"; point(line, event.targetVelocity);
        line << ",\"weaponVelocity\":"; point(line, event.weaponVelocity);
        line << ",\"velocityReady\":" << (event.velocityReady ? "true" : "false")
             << ",\"airborne\":" << (event.airborne ? "true" : "false")
             << ",\"targetAirborne\":" << (event.targetAirborne ? "true" : "false") << '}';
    }
    line << "]}\n";
    return line.str();
}
}

BattleCapture::BattleCapture(const fs::path& output, const fs::path& clientDirectory, const fs::path& packageRoot) {
    if (!output.is_absolute() || output.filename().empty())
        throw std::runtime_error("battle capture requires an absolute new file path");
    const auto parent = fs::canonical(output.parent_path());
    const auto client = fs::canonical(clientDirectory).parent_path();
    const auto package = fs::canonical(packageRoot);
    if (inside(parent, client) || inside(parent, package))
        throw std::runtime_error("battle capture output must be outside client and package directories");
    file_ = CreateFileW(output.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW,
                        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (file_ == INVALID_HANDLE_VALUE) throw std::runtime_error(errorText("battle capture file creation"));
    try {
        write(header());
        thread_ = std::thread(&BattleCapture::run, this);
    } catch (...) {
        CloseHandle(file_); file_ = INVALID_HANDLE_VALUE;
        throw;
    }
}

BattleCapture::~BattleCapture() {
    try { stop(); } catch (...) { }
}

void BattleCapture::record(const WorldSnapshot& world) {
    const auto bytes = sizeof(WorldSnapshot) + world.actors.size() * sizeof(ActorSnapshot) + world.contacts.size() * sizeof(BladeContactEvent);
    if (world.contacts.size() > 8192 || bytes > MAX_QUEUE_BYTES) {
        coverageLost_.store(true);
        throw std::runtime_error("battle capture batch exceeds capacity");
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (!failure_.empty()) throw std::runtime_error(failure_);
    if (stopping_ || pending_.size() >= MAX_QUEUE_BATCHES || queuedBytes_ > MAX_QUEUE_BYTES - bytes) {
        coverageLost_.store(true);
        throw std::runtime_error("battle capture queue is full or stopped");
    }
    if (world.instanceStartedAt != epoch_) { epoch_ = world.instanceStartedAt; batchId_ = 0; }
    ULONGLONG capturedAt = 0;
    QueryInterruptTimePrecise(&capturedAt);
    pending_.push_back({world, ++batchId_, capturedAt / 10000, bytes});
    queuedBytes_ += bytes;
    changed_.notify_one();
    if (world.droppedContacts) {
        coverageLost_.store(true);
        throw std::runtime_error("native contact journal dropped evidence; capture incomplete");
    }
}

void BattleCapture::disable() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        coverageLost_.store(true);
        stopping_ = true;
        pending_.clear();
        queuedBytes_ = 0;
    }
    changed_.notify_all();
}

void BattleCapture::stop() {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        stopping_ = true;
    }
    changed_.notify_all();
    if (thread_.joinable()) thread_.join();
    if (file_ != INVALID_HANDLE_VALUE) {
        CloseHandle(file_); file_ = INVALID_HANDLE_VALUE;
    }
    if (!failure_.empty()) throw std::runtime_error(failure_);
    if (coverageLost_.load()) throw std::runtime_error("battle capture coverage incomplete");
}

void BattleCapture::write(const std::string& line) {
    DWORD written = 0;
    if (line.size() > MAXDWORD || !WriteFile(file_, line.data(), static_cast<DWORD>(line.size()), &written, nullptr)
        || written != line.size()) throw std::runtime_error(errorText("battle capture write"));
}

void BattleCapture::run() noexcept {
    try {
        while (true) {
            Batch batch;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                changed_.wait(lock, [this] { return stopping_ || !pending_.empty(); });
                if (pending_.empty()) break;
                batch = std::move(pending_.front()); pending_.pop_front();
                queuedBytes_ -= batch.bytes;
            }
            write(batchLine(batch.world, batch.worldBatchId, batch.capturedAt));
        }
        write(coverageLost_.load()
            ? "{\"schemaVersion\":1,\"type\":\"captureEnd\",\"complete\":false}\n"
            : "{\"schemaVersion\":1,\"type\":\"captureEnd\",\"complete\":true}\n");
        if (!FlushFileBuffers(file_)) throw std::runtime_error(errorText("battle capture flush"));
    } catch (const std::exception& error) {
        std::lock_guard<std::mutex> lock(mutex_);
        failure_ = error.what(); pending_.clear(); queuedBytes_ = 0;
    }
}

}
