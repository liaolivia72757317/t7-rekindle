#pragma once
#include "../../../src/Runtime/core/Common.h"
#include "../../../src/Runtime/core/WorldSnapshot.h"
#include "../../../src/Runtime/core/DiagnosticLog.h"
#include <Python.h>
#include "../../../src/Runtime/core/TitleRequest.h"
#include <memory>

namespace t7r {
struct Event {
    std::string type, role, name;
    uint64_t id = 0, connection = 0, sequence = 0, serverTime = 0;
    uint16_t command = 0;
    Bytes body;
    std::string serialized;
};
struct Outgoing { uint64_t connection; uint16_t command; Bytes body; std::string reason; };
struct TimerChange { std::string id; int64_t delay; std::string event; uint64_t connection; };
struct DispatchTiming {
    double contextMs = 0, decodeMs = 0, handlerMs = 0, validationMs = 0, encodeMs = 0, resultMs = 0;
    uint64_t worldVersion = 0;
    bool contextRebuilt = false;
};
struct Transition {
    TitleRequest titleRequest;
    std::string state;
    std::vector<Outgoing> send;
    std::vector<TimerChange> timers;
    std::vector<DiagnosticMessage> logs;
    std::string phase;
    DispatchTiming timing;
};
struct ResourceCheck {
    DashAttackProfile dashAttack;
    MovementAnimationProfile movementAnimation;
    std::vector<CombatProfile> combatProfiles;
    std::vector<DiagnosticMessage> diagnostics;
};
class PythonContext;
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
    std::string initializeContext(const Config& config, uint64_t now = 0);
    Transition dispatchNative(const Event& event, const std::string& state, uint64_t now,
                              const std::vector<uint64_t>& connections, const PublishedWorld* world);
    std::string prepare();
    std::string switchVersion(const std::string& state, bool rollback);
    std::string version();
    std::vector<std::string> diagnostics();
    std::string phase(const std::string& state);
private:
    PyObject* module_ = nullptr;
    PyObject* runtime_ = nullptr;
    std::unique_ptr<PythonContext> context_;
    std::string encode(PyObject* object);
    Transition readTransition(PyObject* value);
};
}
