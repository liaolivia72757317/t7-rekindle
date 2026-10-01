#include "PythonHost.h"
#include <memory>
#include <mutex>

namespace t7 {
namespace {
struct Release { void operator()(PyObject* p) const { Py_XDECREF(p); } };
using Object = std::unique_ptr<PyObject, Release>;
std::string error() {
    Object exception(PyErr_GetRaisedException());
    if (!exception) return "Python operation failed";
    Object text(PyObject_Str(exception.get()));
    if (!text) { PyErr_Clear(); return "Python exception formatting failed"; }
    const char* chars = PyUnicode_AsUTF8(text.get());
    return chars ? chars : "Python exception";
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
PyObject* field(PyObject* object, const char* key) {
    if (!object || !PyDict_Check(object)) throw std::runtime_error("Python field container is not a dict");
    PyObject* value = PyDict_GetItemString(object, key);
    if (!value) throw std::runtime_error(std::string("missing field: ") + key);
    return value;
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
std::string PythonHost::context(const Config& config, uint64_t now, const std::vector<uint64_t>& connections) {
    auto dict = checked(PyDict_New());
    put(dict.get(), "nowMs", PyLong_FromUnsignedLongLong(now));
    put(dict.get(), "advertisedAddress", PyUnicode_FromString(config.advertisedAddress.c_str()));
    put(dict.get(), "instancePort", PyLong_FromLong(config.ports[2]));
    put(dict.get(), "playerName", PyUnicode_FromString(config.playerName.c_str()));
    put(dict.get(), "runtimeMovement", PyBool_FromLong(config.runtimeMovement ? 1 : 0));
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
    if (!PyTuple_Check(value.get()) || PyTuple_Size(value.get()) != 4) throw std::runtime_error("invalid bridge transition");
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
    for (Py_ssize_t i = 0; i < logCount; ++i) result.logs.push_back(string(PyList_GetItem(logs, i)));
    if (PyErr_Occurred()) throw std::runtime_error(error());
    return result;
}
}
