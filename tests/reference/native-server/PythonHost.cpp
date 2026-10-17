#include "PythonHost.h"
#include "PythonContext.h"
#include <memory>
#include <mutex>
#include <cmath>
#include <limits>
#include <chrono>

namespace t7r {
using namespace python;
namespace {
std::mutex ownerMutex;
bool ownerActive = false;

void releaseOwner() noexcept {
    std::lock_guard<std::mutex> lock(ownerMutex);
    ownerActive = false;
}

class OwnerLease final {
public:
    OwnerLease() = default;
    OwnerLease(const OwnerLease&) = delete;
    OwnerLease& operator=(const OwnerLease&) = delete;
    ~OwnerLease() {
        if (active_) {
            if (Py_IsInitialized()) Py_FinalizeEx();
            releaseOwner();
        }
    }
    void transfer() noexcept { active_ = false; }
private:
    bool active_ = true;
};

void acquireOwner() {
    std::lock_guard<std::mutex> lock(ownerMutex);
    if (ownerActive || Py_IsInitialized())
        throw std::runtime_error("embedded Python owner already active");
    ownerActive = true;
}

DashAttackProfile readDash(PyObject* value) {
    auto integer = [&](const char* key, long minimum, long maximum) {
        auto item = field(value, key);
        if (!PyLong_CheckExact(item)) throw std::runtime_error(std::string("invalid dash integer: ") + key);
        const auto number = PyLong_AsLong(item);
        if (PyErr_Occurred()) throw std::runtime_error(error());
        if (number < minimum || number > maximum) throw std::runtime_error(std::string("dash integer out of range: ") + key);
        return static_cast<int32_t>(number);
    };
    DashAttackProfile profile;
    auto enabled = field(value, "enabled");
    if (!PyBool_Check(enabled)) throw std::runtime_error("invalid dash enabled flag");
    profile.enabled = enabled == Py_True;
    profile.weaponId = integer("weaponId", 0, INT32_MAX);
    profile.weaponCategory = integer("weaponCategory", -1, 32767);
    profile.durationMs = static_cast<uint32_t>(integer("durationMs", 0, 65535));
    profile.moveMs = static_cast<uint32_t>(integer("moveMs", 0, 65535));
    profile.speedMilli = static_cast<uint32_t>(integer("speedMilli", 0, INT32_MAX));
    profile.contactStartMs = static_cast<uint32_t>(integer("contactStartMs", 0, 65535));
    profile.contactEndMs = static_cast<uint32_t>(integer("contactEndMs", 0, 65535));
    profile.reboundMs = static_cast<uint32_t>(integer("reboundMs", 0, 65535));
    profile.durationSource = string(field(value, "durationSource"));
    profile.animationPath = string(field(value, "animationPath"));
    if (profile.durationSource != "table" && profile.durationSource != "animation" && profile.durationSource != "unavailable")
        throw std::runtime_error("invalid dash duration source");
    if (profile.enabled && (profile.weaponId == 0 || profile.weaponCategory < 0 || profile.durationSource == "unavailable"
        || profile.durationMs == 0 || profile.reboundMs == 0 || profile.moveMs > profile.durationMs
        || profile.contactStartMs > profile.contactEndMs || profile.contactEndMs > profile.durationMs))
        throw std::runtime_error("invalid dash timeline");
    return profile;
}
MovementAnimationProfile readMovementAnimation(PyObject* value) {
    MovementAnimationProfile profile;
    auto enabled = field(value, "enabled");
    if (!PyBool_Check(enabled)) throw std::runtime_error("invalid movement animation enabled flag");
    profile.enabled = enabled == Py_True;
    auto weapon = field(value, "weaponId");
    if (!PyLong_CheckExact(weapon)) throw std::runtime_error("invalid movement animation weapon");
    const auto weaponId = PyLong_AsLong(weapon);
    if (PyErr_Occurred()) throw std::runtime_error(error());
    if (weaponId <= 0 || weaponId > INT32_MAX) throw std::runtime_error("invalid movement animation weapon");
    profile.weaponId = static_cast<int32_t>(weaponId);
    auto speed = [&](const char* key) {
        auto item = field(value, key);
        if (!PyFloat_CheckExact(item)) throw std::runtime_error("invalid movement animation coordinate");
        const auto number = PyFloat_AsDouble(item);
        if (!std::isfinite(number) || number < 0 || number > std::numeric_limits<float>::max()
            || (profile.enabled && number <= 0)) throw std::runtime_error("invalid movement animation coordinate");
        return static_cast<float>(number);
    };
    profile.forwardSlow = speed("forwardSlow"); profile.lateralSlow = speed("lateralSlow");
    profile.backwardSlow = speed("backwardSlow"); profile.forwardSprint = speed("forwardSprint");
    if (profile.enabled && (profile.forwardSprint <= profile.forwardSlow || profile.forwardSprint <= profile.lateralSlow))
        throw std::runtime_error("invalid movement animation gait order");
    return profile;
}
Object eventValue(const Event& event) {
    if (!event.serialized.empty()) return checked(PyUnicode_FromString(event.serialized.c_str()));
    auto dict = checked(PyDict_New());
    put(dict.get(), "type", PyUnicode_FromString(event.type.c_str()));
    put(dict.get(), "eventId", PyLong_FromUnsignedLongLong(event.id));
    put(dict.get(), "role", PyUnicode_FromString(event.role.c_str()));
    put(dict.get(), "name", PyUnicode_FromString(event.name.c_str()));
    put(dict.get(), "connection", PyLong_FromUnsignedLongLong(event.connection));
    put(dict.get(), "sequence", PyLong_FromUnsignedLongLong(event.sequence));
    put(dict.get(), "serverTimeMs", PyLong_FromUnsignedLongLong(event.serverTime));
    put(dict.get(), "command", PyLong_FromLong(event.command));
    put(dict.get(), "body", PyBytes_FromStringAndSize(reinterpret_cast<const char*>(event.body.data()), static_cast<Py_ssize_t>(event.body.size())));
    return dict;
}
PyObject* tupleField(PyObject* tuple, Py_ssize_t index) {
    auto value = PyTuple_GetItem(tuple, index);
    if (!value) throw std::runtime_error("missing Python tuple field");
    return value;
}
}
PythonHost::PythonHost(const fs::path& root, const fs::path& cache) {
    acquireOwner();
    // Any C++/filesystem/Python allocation failure before the normal import
    // catch must release the process-wide owner as well.
    OwnerLease ownerLease;
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.write_bytecode = 0; config.site_import = 0; config.parse_argv = 0;
    config.use_environment = 0; config.user_site_directory = 0; config.install_signal_handlers = 0;
    config.module_search_paths_set = 1;
    auto home = root / "python";
    auto business = (fs::is_directory(root / "Business") ? root / "Business" : root);
    PyStatus status = PyConfig_SetString(&config, &config.home, home.c_str());
    for (auto path : {home / "python314.zip", home, home / "Lib", business / "runtime"}) {
        if (!PyStatus_Exception(status)) status = PyWideStringList_Append(&config.module_search_paths, path.c_str());
    }
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    std::string detail = PyStatus_Exception(status) ? (status.err_msg ? status.err_msg : "initialization error") : "";
    PyConfig_Clear(&config);
    if (!detail.empty()) {
        if (Py_IsInitialized()) Py_FinalizeEx();
        throw std::runtime_error(detail);
    }
    try {
        auto module = checked(PyImport_ImportModule("host_runtime"));
        module_ = module.release();
        auto cls = checked(PyObject_GetAttrString(module_, "Runtime"));
        // Development and packaged layouts share the same contract: Python
        // business sources live below Business/.  The fallback keeps the
        // isolated host useful for focused native tests that provide a small
        // scripts directory directly.
        auto scripts = business / "scripts";
        auto scriptText = checked(PyUnicode_FromWideChar(scripts.c_str(), -1));
        auto cacheText = checked(PyUnicode_FromWideChar(cache.c_str(), -1));
        auto args = checked(PyTuple_Pack(2, scriptText.get(), cacheText.get()));
        runtime_ = checked(PyObject_CallObject(cls.get(), args.get())).release();
    } catch (...) {
        Py_XDECREF(runtime_); Py_XDECREF(module_); Py_FinalizeEx(); throw;
    }
    ownerLease.transfer();
}
PythonHost::~PythonHost() {
    context_.reset();
    if (runtime_) { Object closed(PyObject_CallMethod(runtime_, "close", nullptr)); PyErr_Clear(); }
    Py_XDECREF(runtime_); Py_XDECREF(module_); Py_FinalizeEx();
    releaseOwner();
}
std::string PythonHost::encode(PyObject* object) {
    auto result = checked(PyObject_CallMethod(module_, "encode", "O", object)); return string(result.get());
}
std::string PythonHost::context(const Config& config, uint64_t now, const std::vector<uint64_t>& connections,
                                const WorldSnapshot& world) {
    PythonContext context(config);
    PublishedWorld publication{1, world};
    return encode(context.make(now, connections, &publication, true).get());
}
std::string PythonHost::initializeContext(const Config& config, uint64_t now) {
    auto context = std::make_unique<PythonContext>(config);
    // Configuration is immutable for the lifetime of this server.
    auto initial = encode(context->make(now, {}, nullptr, false).get());
    context_ = std::move(context);
    return initial;
}
std::string PythonHost::create(const std::string& context) {
    auto value = checked(PyObject_CallMethod(runtime_, "create", "s", context.c_str())); return string(value.get());
}
ResourceCheck PythonHost::checkResources(const fs::path& directory, const std::string& playerName) {
    auto path = checked(PyUnicode_FromWideChar(directory.c_str(), -1));
    auto result = checked(PyObject_CallMethod(runtime_, "checkResources", "Os", path.get(), playerName.c_str()));
    ResourceCheck resources;
    resources.dashAttack = readDash(field(result.get(), "dashAttack"));
    resources.movementAnimation = readMovementAnimation(field(result.get(), "movementAnimation"));
    auto profiles = field(result.get(), "combatProfiles");
    if (!PyList_CheckExact(profiles) || PyList_Size(profiles) < 1 || PyList_Size(profiles) > 2048)
        throw std::runtime_error("invalid combat profile list");
    for (Py_ssize_t i = 0; i < PyList_Size(profiles); ++i) {
        auto item = PyList_GetItem(profiles, i);
        auto hero = field(item, "heroId");
        if (!PyLong_CheckExact(hero)) throw std::runtime_error("invalid combat profile hero");
        const auto heroId = PyLong_AsLong(hero);
        if (PyErr_Occurred() || heroId <= 0) throw std::runtime_error("invalid combat profile hero");
        resources.combatProfiles.push_back({static_cast<int32_t>(heroId),
            readMovementAnimation(field(item, "movementAnimation")), readDash(field(item, "dashAttack"))});
    }
    auto diagnostics = field(result.get(), "diagnostics");
    if (!PyList_Check(diagnostics)) throw std::runtime_error("invalid resource diagnostics list");
    for (Py_ssize_t i = 0; i < PyList_Size(diagnostics); ++i) {
        auto item = PyList_GetItem(diagnostics, i);
        resources.diagnostics.push_back({parseDiagnosticLevel(string(field(item, "level"))), string(field(item, "message"))});
    }
    return resources;
}
std::vector<std::string> PythonHost::closeRevision() {
    auto result = checked(PyObject_CallMethod(runtime_, "close", nullptr));
    return diagnostics();
}
std::string PythonHost::version() {
    auto value = checked(PyObject_CallMethod(runtime_, "version", nullptr)); return string(value.get());
}
std::vector<std::string> PythonHost::diagnostics() {
    auto value = checked(PyObject_CallMethod(runtime_, "diagnostics", nullptr));
    if (!PyList_Check(value.get())) throw std::runtime_error("invalid diagnostics list");
    std::vector<std::string> result;
    auto count = PyList_Size(value.get());
    if (count < 0) throw std::runtime_error(error());
    for (Py_ssize_t i = 0; i < count; ++i) result.push_back(string(PyList_GetItem(value.get(), i)));
    return result;
}
std::string PythonHost::phase(const std::string& state) {
    auto value = checked(PyObject_CallMethod(runtime_, "phase", "s", state.c_str())); return string(value.get());
}
std::string PythonHost::prepare() {
    auto value = checked(PyObject_CallMethod(runtime_, "prepare", nullptr)); return string(value.get());
}
std::string PythonHost::switchVersion(const std::string& state, bool rollback) {
    auto value = checked(PyObject_CallMethod(runtime_, "switch", "si", state.c_str(), rollback ? 1 : 0)); return string(value.get());
}
Transition PythonHost::dispatch(const Event& event, const std::string& state, const std::string& context) {
    auto object = eventValue(event);
    auto serialized = event.serialized.empty() ? encode(object.get()) : event.serialized;
    auto value = checked(PyObject_CallMethod(runtime_, "dispatch", "sss", serialized.c_str(), state.c_str(), context.c_str()));
    return readTransition(value.get());
}
Transition PythonHost::dispatchNative(const Event& event, const std::string& state, uint64_t now,
                                      const std::vector<uint64_t>& connections, const PublishedWorld* world) {
    if (!context_) throw std::runtime_error("Python context not initialized");
    const auto started = std::chrono::steady_clock::now();
    auto context = context_->make(now, connections, world, event.type == "world");
    const auto prepared = std::chrono::steady_clock::now();
    auto object = eventValue(event);
    auto value = checked(PyObject_CallMethod(runtime_, "dispatchNative", "OsO", object.get(), state.c_str(), context.get()));
    if (!PyTuple_Check(value.get()) || (PyTuple_Size(value.get()) != 6 && PyTuple_Size(value.get()) != 7))
        throw std::runtime_error("invalid native transition");
    const auto returned = std::chrono::steady_clock::now();
    auto result = readTransition(value.get());
    result.timing.contextMs = std::chrono::duration<double, std::milli>(prepared - started).count();
    result.timing.resultMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - returned).count();
    result.timing.contextRebuilt = context_->rebuilt(); result.timing.worldVersion = context_->version();
    if (!context_->observation().empty()) result.logs.push_back({DiagnosticLogLevel::Debug, context_->observation()});
    return result;
}
Transition PythonHost::readTransition(PyObject* value) {
    if (!PyTuple_Check(value) || (PyTuple_Size(value) != 4 && PyTuple_Size(value) != 6 && PyTuple_Size(value) != 7)) throw std::runtime_error("invalid bridge transition");
    Transition result; result.state = string(tupleField(value, 0));
    auto sends = tupleField(value, 1);
    if (!PyList_Check(sends)) throw std::runtime_error("invalid transition send list");
    auto sendCount = PyList_Size(sends);
    if (sendCount < 0) throw std::runtime_error(error());
    for (Py_ssize_t i = 0; i < sendCount; ++i) {
        auto item = PyList_GetItem(sends, i);
        if (!item || !PyDict_Check(item)) throw std::runtime_error("invalid transition send item");
        Outgoing message{};
        message.connection = PyLong_AsUnsignedLongLong(field(item, "connection"));
        message.command = static_cast<uint16_t>(PyLong_AsLong(field(item, "command")));
        auto bytes = field(item, "body");
        if (!PyBytes_Check(bytes)) throw std::runtime_error("invalid transition send body");
        auto pointer = reinterpret_cast<const unsigned char*>(PyBytes_AsString(bytes));
        if (!pointer) throw std::runtime_error(error());
        auto length = PyBytes_Size(bytes);
        if (length < 0) throw std::runtime_error(error());
        message.body.assign(pointer, pointer + length);
        message.reason = string(field(item, "reason")); result.send.push_back(std::move(message));
    }
    auto timers = tupleField(value, 2);
    if (!PyList_Check(timers)) throw std::runtime_error("invalid transition timer list");
    auto timerCount = PyList_Size(timers);
    if (timerCount < 0) throw std::runtime_error(error());
    for (Py_ssize_t i = 0; i < timerCount; ++i) {
        auto item = PyList_GetItem(timers, i);
        if (!item || !PyTuple_Check(item) || PyTuple_Size(item) != 4)
            throw std::runtime_error("invalid transition timer item");
        result.timers.push_back({string(tupleField(item, 0)), PyLong_AsLongLong(tupleField(item, 1)),
            string(tupleField(item, 2)), PyLong_AsUnsignedLongLong(tupleField(item, 3))});
    }
    auto logs = tupleField(value, 3);
    if (!PyList_Check(logs)) throw std::runtime_error("invalid transition log list");
    auto logCount = PyList_Size(logs);
    if (logCount < 0) throw std::runtime_error(error());
    for (Py_ssize_t i = 0; i < logCount; ++i) {
        auto item = PyList_GetItem(logs, i);
        if (!item || !PyDict_Check(item)) throw std::runtime_error("invalid transition log item");
        result.logs.push_back({parseDiagnosticLevel(string(field(item, "level"))), string(field(item, "message"))});
    }
    if (PyTuple_Size(value) >= 6) {
        result.phase = string(tupleField(value, 4));
        auto timing = tupleField(value, 5);
        auto duration = [&](const char* name) {
            auto number = PyFloat_AsDouble(field(timing, name));
            if (PyErr_Occurred()) throw std::runtime_error(error());
            if (!std::isfinite(number) || number < 0) throw std::runtime_error("invalid dispatch timing");
            return number;
        };
        result.timing.decodeMs = duration("decodeMs"); result.timing.handlerMs = duration("handlerMs");
        result.timing.validationMs = duration("validationMs"); result.timing.encodeMs = duration("encodeMs");
    }
    if (PyTuple_Size(value) == 7 && tupleField(value, 6) != Py_None) {
        auto request = tupleField(value, 6);
        result.titleRequest.requestId = PyLong_AsUnsignedLongLong(field(request, "requestId"));
        result.titleRequest.epoch = PyLong_AsUnsignedLongLong(field(request, "epoch"));
        result.titleRequest.connection = PyLong_AsUnsignedLongLong(field(request, "connection"));
        result.titleRequest.titleId = static_cast<uint32_t>(PyLong_AsUnsignedLong(field(request, "titleId")));
    }
    if (PyErr_Occurred()) throw std::runtime_error(error());
    return result;
}
}
