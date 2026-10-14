#include "PythonHost.h"
#include <memory>
#include <mutex>
#include <cmath>
#include <limits>

namespace t7 {
namespace {
struct Release { void operator()(PyObject* p) const { Py_XDECREF(p); } };
using Object = std::unique_ptr<PyObject, Release>;
std::string error() {
    Object exception(PyErr_GetRaisedException());
    if (!exception) return "Python operation failed";
    std::string summary = Py_TYPE(exception.get())->tp_name;
    Object text(PyObject_Str(exception.get()));
    if (text) {
        const char* chars = PyUnicode_AsUTF8(text.get());
        if (chars) summary += std::string(": ") + chars;
    }
    PyErr_Clear();
    Object traceback(PyImport_ImportModule("traceback"));
    if (traceback) {
        Object lines(PyObject_CallMethod(traceback.get(), "format_exception", "O", exception.get()));
        Object separator(PyUnicode_FromString(""));
        if (lines && separator) {
            Object formatted(PyUnicode_Join(separator.get(), lines.get()));
            if (formatted) {
                const char* chars = PyUnicode_AsUTF8(formatted.get());
                if (chars) return summary + "\n" + chars;
            }
        }
    }
    PyErr_Clear();
    return summary;
}
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

Object checked(PyObject* p) { if (!p) throw std::runtime_error(error()); return Object(p); }
std::string string(PyObject* p) {
    if (!p) throw std::runtime_error("missing Python string");
    const char* result = PyUnicode_AsUTF8(p);
    if (!result) throw std::runtime_error(error());
    return result;
}
void put(PyObject* dict, const char* key, PyObject* owned) {
    auto value = checked(owned);
    if (PyDict_SetItemString(dict, key, value.get()) < 0) throw std::runtime_error(error());
}
Object terrainValue(const TerrainContact& contact) {
    auto value = checked(PyDict_New());
    put(value.get(), "ready", PyBool_FromLong(contact.ready ? 1 : 0));
    put(value.get(), "hit", PyBool_FromLong(contact.hit ? 1 : 0));
    put(value.get(), "material", PyLong_FromLong(contact.material));
    put(value.get(), "position", Py_BuildValue("[fff]", contact.position[0], contact.position[1], contact.position[2]));
    put(value.get(), "normal", Py_BuildValue("[fff]", contact.normal[0], contact.normal[1], contact.normal[2]));
    put(value.get(), "direction", Py_BuildValue("[fff]", contact.direction[0], contact.direction[1], contact.direction[2]));
    return value;
}
Object bladeValue(const BladeTrace& blade) {
    auto value = checked(PyDict_New());
    put(value.get(), "ready", PyBool_FromLong(blade.ready ? 1 : 0));
    put(value.get(), "spanMs", PyLong_FromUnsignedLong(blade.spanMs));
    put(value.get(), "length", PyFloat_FromDouble(blade.length));
    put(value.get(), "base", Py_BuildValue("[fff]", blade.base[0], blade.base[1], blade.base[2]));
    put(value.get(), "tip", Py_BuildValue("[fff]", blade.tip[0], blade.tip[1], blade.tip[2]));
    auto contacts = checked(PyList_New(0));
    for (const auto& hit : blade.contacts) {
        if (!hit.contact.hit) continue;
        auto contact = terrainValue(hit.contact);
        put(contact.get(), "targetId", PyLong_FromUnsignedLong(hit.targetId));
        put(contact.get(), "time", PyFloat_FromDouble(hit.time));
        put(contact.get(), "along", PyFloat_FromDouble(hit.along));
        if (PyList_Append(contacts.get(), contact.get()) < 0) throw std::runtime_error(error());
    }
    put(value.get(), "contacts", contacts.release());
    return value;
}
Object contactJournal(const WorldSnapshot& world) {
    auto journal = checked(PyDict_New());
    put(journal.get(), "throughAt", PyLong_FromUnsignedLongLong(world.sampledAt));
    put(journal.get(), "frames", PyLong_FromUnsignedLong(world.contactFrames));
    put(journal.get(), "dropped", PyLong_FromUnsignedLong(world.droppedContacts));
    put(journal.get(), "batchId", PyLong_FromUnsignedLong(world.contactBatchId));
    auto events = checked(PyList_New(0));
    for (const auto& event : world.contacts) {
        auto value = terrainValue(event.hit.contact);
        for (const auto& field : std::initializer_list<std::pair<const char*, uint32_t>>{
            {"actorId", event.actorId}, {"targetId", event.hit.targetId}, {"entity", event.entity},
            {"targetEntity", event.targetEntity}, {"state", event.state}, {"seq", event.sequence},
            {"targetState", event.targetState}, {"targetSeq", event.targetSequence}})
            put(value.get(), field.first, PyLong_FromUnsignedLong(field.second));
        put(value.get(), "phaseAt", PyLong_FromUnsignedLongLong(event.phaseAt));
        put(value.get(), "targetPhaseAt", PyLong_FromUnsignedLongLong(event.targetPhaseAt));
        put(value.get(), "occurredAt", PyFloat_FromDouble(event.occurredAt));
        put(value.get(), "along", PyFloat_FromDouble(event.hit.along));
        put(value.get(), "weaponContact", PyBool_FromLong(event.weaponContact));
        put(value.get(), "sourcePosition", Py_BuildValue("[fff]", event.sourcePosition[0], event.sourcePosition[1], event.sourcePosition[2]));
        put(value.get(), "targetPosition", Py_BuildValue("[fff]", event.targetPosition[0], event.targetPosition[1], event.targetPosition[2]));
        put(value.get(), "sourceHeading", PyFloat_FromDouble(event.sourceHeading));
        put(value.get(), "targetHeading", PyFloat_FromDouble(event.targetHeading));
        if (PyList_Append(events.get(), value.get()) < 0) throw std::runtime_error(error());
    }
    put(journal.get(), "events", events.release());
    return journal;
}
PyObject* field(PyObject* object, const char* key) {
    if (!object || !PyDict_Check(object)) throw std::runtime_error("Python field container is not a dict");
    PyObject* value = PyDict_GetItemString(object, key);
    if (!value) throw std::runtime_error(std::string("missing field: ") + key);
    return value;
}
Object dashValue(const DashAttackProfile& profile) {
    auto value = checked(PyDict_New());
    put(value.get(), "enabled", PyBool_FromLong(profile.enabled ? 1 : 0));
    put(value.get(), "weaponId", PyLong_FromLong(profile.weaponId));
    put(value.get(), "weaponCategory", PyLong_FromLong(profile.weaponCategory));
    for (const auto& entry : std::initializer_list<std::pair<const char*, uint32_t>>{
        {"durationMs", profile.durationMs}, {"moveMs", profile.moveMs}, {"speedMilli", profile.speedMilli},
        {"contactStartMs", profile.contactStartMs}, {"contactEndMs", profile.contactEndMs}, {"reboundMs", profile.reboundMs}})
        put(value.get(), entry.first, PyLong_FromUnsignedLong(entry.second));
    put(value.get(), "durationSource", PyUnicode_FromString(profile.durationSource.c_str()));
    put(value.get(), "animationPath", PyUnicode_FromString(profile.animationPath.c_str()));
    return value;
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
    if (runtime_) { Object closed(PyObject_CallMethod(runtime_, "close", nullptr)); PyErr_Clear(); }
    Py_XDECREF(runtime_); Py_XDECREF(module_); Py_FinalizeEx();
    releaseOwner();
}
std::string PythonHost::encode(PyObject* object) {
    auto result = checked(PyObject_CallMethod(module_, "encode", "O", object)); return string(result.get());
}
std::string PythonHost::context(const Config& config, uint64_t now, const std::vector<uint64_t>& connections,
                                const WorldSnapshot& world) {
    auto dict = checked(PyDict_New());
    put(dict.get(), "nowMs", PyLong_FromUnsignedLongLong(now));
    put(dict.get(), "advertisedAddress", PyUnicode_FromString(config.advertisedAddress.c_str()));
    put(dict.get(), "instancePort", PyLong_FromLong(config.ports[2]));
    put(dict.get(), "playerName", PyUnicode_FromString(config.playerName.c_str()));
    put(dict.get(), "runtimeMovement", PyBool_FromLong(config.runtimeMovement ? 1 : 0));
    if (config.roomGateway) {
        auto gateway = checked(Py_BuildValue("{s:s}", "accountId", config.roomGateway->accountId.c_str()));
        put(dict.get(), "roomGateway", gateway.release());
    }
    put(dict.get(), "clientDirectory", PyUnicode_FromWideChar(config.clientDirectory.c_str(), -1));
    put(dict.get(), "dashAttack", dashValue(config.dashAttack).release());
    auto snapshot = checked(PyDict_New());
    put(snapshot.get(), "ready", PyBool_FromLong(world.ready ? 1 : 0));
    put(snapshot.get(), "sampledAt", PyLong_FromUnsignedLongLong(world.sampledAt));
    put(snapshot.get(), "instanceStartedAt", PyLong_FromUnsignedLongLong(world.instanceStartedAt));
    if (world.contactsReady) put(snapshot.get(), "contactJournal", contactJournal(world).release());
    put(snapshot.get(), "player", Py_BuildValue("[fff]", world.player[0], world.player[1], world.player[2]));
    put(snapshot.get(), "bot", Py_BuildValue("[fff]", world.bot[0], world.bot[1], world.bot[2]));
    put(snapshot.get(), "playerHeading", PyFloat_FromDouble(world.playerHeading));
    put(snapshot.get(), "botHeading", PyFloat_FromDouble(world.botHeading));
    auto allActors = checked(PyDict_New());
    auto blades = checked(PyDict_New());
    for (size_t rid = 1; rid <= world.actors.size(); ++rid) {
        const auto& actor = world.actors[rid - 1];
        put(blades.get(), std::to_string(rid).c_str(), bladeValue(actor.blade).release());
        auto entry = checked(PyDict_New());
        put(entry.get(), "ready", PyBool_FromLong(actor.ready ? 1 : 0));
        put(entry.get(), "position", Py_BuildValue("[fff]", actor.position[0], actor.position[1], actor.position[2]));
        put(entry.get(), "heading", PyFloat_FromDouble(actor.heading));
        put(entry.get(), "targetId", PyLong_FromUnsignedLong(actor.targetId));
        put(entry.get(), "entity", PyLong_FromUnsignedLong(actor.entity));
        put(entry.get(), "state", PyLong_FromUnsignedLong(actor.state));
        put(entry.get(), "seq", PyLong_FromUnsignedLong(actor.sequence));
        put(entry.get(), "phaseAt", PyLong_FromUnsignedLongLong(actor.phaseAt));
        auto probes = checked(PyDict_New());
        for (size_t i = 0; i < actor.terrain.size(); ++i)
            put(probes.get(), i == 0 ? "forward" : "target", terrainValue(actor.terrain[i]).release());
        put(entry.get(), "terrain", probes.release());
        auto targets = checked(PyDict_New());
        for (size_t target = 0; target < actor.targetTerrain.size(); ++target)
            if (actor.targetTerrain[target].ready)
                put(targets.get(), std::to_string(target + 1).c_str(), terrainValue(actor.targetTerrain[target]).release());
        put(entry.get(), "targetTerrain", targets.release());
        put(allActors.get(), std::to_string(rid).c_str(), entry.release());
    }
    put(snapshot.get(), "actors", allActors.release());
    put(snapshot.get(), "blades", blades.release());
    put(dict.get(), "botWorld", snapshot.release());
    auto list = checked(PyList_New(0));
    for (auto id : connections) {
        auto value = checked(PyLong_FromUnsignedLongLong(id));
        if (PyList_Append(list.get(), value.get()) < 0) throw std::runtime_error(error());
    }
    put(dict.get(), "connections", list.release()); return encode(dict.get());
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
uint32_t PythonHost::playerId(const std::string& state) {
    auto value = checked(PyObject_CallMethod(runtime_, "playerId", "s", state.c_str()));
    auto result = PyLong_AsUnsignedLong(value.get());
    if (PyErr_Occurred()) throw std::runtime_error(error());
    return static_cast<uint32_t>(result);
}
std::string PythonHost::prepare() {
    auto value = checked(PyObject_CallMethod(runtime_, "prepare", nullptr)); return string(value.get());
}
std::string PythonHost::switchVersion(const std::string& state, bool rollback) {
    auto value = checked(PyObject_CallMethod(runtime_, "switch", "si", state.c_str(), rollback ? 1 : 0)); return string(value.get());
}
Transition PythonHost::dispatch(const Event& event, const std::string& state, const std::string& context) {
    std::string serialized = event.serialized;
    if (serialized.empty()) {
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
        serialized = encode(dict.get());
    }
    auto value = checked(PyObject_CallMethod(runtime_, "dispatch", "sss", serialized.c_str(), state.c_str(), context.c_str()));
    if (!PyTuple_Check(value.get()) || (PyTuple_Size(value.get()) != 4 && PyTuple_Size(value.get()) != 5)) throw std::runtime_error("invalid bridge transition");
    Transition result; result.state = string(tupleField(value.get(), 0));
    auto sends = tupleField(value.get(), 1);
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
    auto timers = tupleField(value.get(), 2);
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
    auto logs = tupleField(value.get(), 3);
    if (!PyList_Check(logs)) throw std::runtime_error("invalid transition log list");
    auto logCount = PyList_Size(logs);
    if (logCount < 0) throw std::runtime_error(error());
    for (Py_ssize_t i = 0; i < logCount; ++i) {
        auto item = PyList_GetItem(logs, i);
        if (!item || !PyDict_Check(item)) throw std::runtime_error("invalid transition log item");
        result.logs.push_back({parseDiagnosticLevel(string(field(item, "level"))), string(field(item, "message"))});
    }
    if (PyTuple_Size(value.get()) == 5) {
        auto effects = tupleField(value.get(), 4);
        if (!PyList_Check(effects) || PyList_Size(effects) > 8) throw std::runtime_error("invalid room gateway effects");
        for (Py_ssize_t i = 0; i < PyList_Size(effects); ++i) {
            auto item = PyList_GetItem(effects, i);
            GatewayEffect effect;
            effect.connection = PyLong_AsUnsignedLongLong(field(item, "connection"));
            effect.op = string(field(item, "op"));
            effect.requestId = string(field(item, "requestId"));
            effect.request = string(field(item, "request"));
            result.gateway.push_back(std::move(effect));
        }
    }
    if (PyErr_Occurred()) throw std::runtime_error(error());
    return result;
}
}
