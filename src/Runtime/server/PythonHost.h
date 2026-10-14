#pragma once
#include "../core/Common.h"
#include "../core/WorldSnapshot.h"
#include "../core/DiagnosticLog.h"
#include <Python.h>
#include "RoomGateway.h"

namespace t7 {
struct Event {
    std::string type, role, name;
    uint64_t id = 0, connection = 0, sequence = 0, serverTime = 0;
    uint16_t command = 0;
    Bytes body;
    std::string serialized;
};
struct Outgoing { uint64_t connection; uint16_t command; Bytes body; std::string reason; };
struct TimerChange { std::string id; int64_t delay; std::string event; uint64_t connection; };
struct Transition {
    std::string state;
    uint32_t playerId = 0;
    std::vector<Outgoing> send;
    std::vector<TimerChange> timers;
    std::vector<DiagnosticMessage> logs;
    std::vector<GatewayEffect> gateway;
};
struct ResourceCheck {
    DashAttackProfile dashAttack;
    MovementAnimationProfile movementAnimation;
    std::vector<DiagnosticMessage> diagnostics;
};
class PythonHost {
public:
    PythonHost(const fs::path& root, const fs::path& cache);
    ~PythonHost();
    PythonHost(const PythonHost&) = delete;
    PythonHost& operator=(const PythonHost&) = delete;
    std::string context(const Config& config, uint64_t now, const std::vector<uint64_t>& connections,
                        const WorldSnapshot& world = {});
    std::string create(const std::string& context);
    ResourceCheck checkResources(const fs::path& directory, const std::string& playerName = "");
    std::vector<std::string> closeRevision();
    Transition dispatch(const Event& event, const std::string& state, const std::string& context);
    std::string prepare();
    std::string switchVersion(const std::string& state, bool rollback);
    std::string version();
    std::vector<std::string> diagnostics();
    std::string phase(const std::string& state);
    uint32_t playerId(const std::string& state);
private:
    PyObject* module_ = nullptr;
    PyObject* runtime_ = nullptr;
    std::string encode(PyObject* object);
};
}
