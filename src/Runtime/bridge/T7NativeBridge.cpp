#include "T7NativeBridge.h"
#include "Session.h"
#include "../core/Common.h"
#include <algorithm>
#include <cstring>
#include <memory>

struct T7NativeSessionOpaque {
    std::shared_ptr<t7::bridge::Session> value;
};

namespace {
using t7::bridge::Session;

bool validHeader(uint32_t version, uint32_t size, uint32_t expected) {
    return version == T7NB_ABI_VERSION && size >= expected;
}

int32_t copyUtf8(const uint8_t* data, uint32_t length, std::string& result) {
    if (!data || length == 0 || length > 32768) return T7NB_INVALID_ARGUMENT;
    if (std::find_if(data, data + length, [](uint8_t value) {
            return value == 0 || value == static_cast<uint8_t>('\r') || value == static_cast<uint8_t>('\n');
        }) != data + length) return T7NB_INVALID_ARGUMENT;
    try {
        result.assign(reinterpret_cast<const char*>(data), length);
        (void)t7::wide(result); // validate UTF-8 before an async copy is made
        return T7NB_OK;
    } catch (...) { return T7NB_INVALID_ARGUMENT; }
}

int32_t copyPath(const T7NativePath* path, std::string& result, bool allowEmpty) {
    if (!path) return allowEmpty ? T7NB_OK : T7NB_INVALID_ARGUMENT;
    if (!validHeader(path->abiVersion, path->structSize, sizeof(T7NativePath))) return T7NB_INVALID_ABI;
    if (path->length == 0 && allowEmpty) { result.clear(); return T7NB_OK; }
    return copyUtf8(path->data, path->length, result);
}

int32_t checkSession(T7NativeSessionHandle handle, std::shared_ptr<Session>& result) {
    if (!handle || !handle->value) return T7NB_INVALID_HANDLE;
    result = handle->value;
    return T7NB_OK;
}

template<class F>
int32_t guard(F&& action) noexcept {
    try { return action(); }
    catch (...) { return T7NB_INTERNAL_ERROR; }
}

int32_t submitStart(T7NativeSessionHandle handle, const T7NativeStartArgs* args,
                    uint64_t* operationId, bool skipStartupAnimation) {
    if (!operationId) return T7NB_INVALID_ARGUMENT;
    *operationId = 0;
    if (!args) return T7NB_INVALID_ARGUMENT;
    if (!validHeader(args->abiVersion, args->structSize, sizeof(T7NativeStartArgs))) return T7NB_INVALID_ABI;
    std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7NB_OK) return status;
    std::string directory, name;
    status = copyUtf8(args->clientDirectory, args->clientDirectoryLength, directory); if (status != T7NB_OK) return status;
    status = copyUtf8(args->playerName, args->playerNameLength, name); if (status != T7NB_OK) return status;
    return session->submit(T7NB_OPERATION_START, std::move(directory), *operationId, std::move(name), skipStartupAnimation);
}
}

extern "C" int32_t T7NB_CALL t7_native_get_abi(uint32_t* version, uint32_t* snapshotSize) {
    if (!version || !snapshotSize) return T7NB_INVALID_ARGUMENT;
    *version = T7NB_ABI_VERSION; *snapshotSize = sizeof(T7NativeSnapshot); return T7NB_OK;
}

extern "C" int32_t T7NB_CALL t7_native_create(const T7NativeCreateArgs* args, T7NativeSessionHandle* session) {
    return guard([&]() -> int32_t {
        if (!args || !session) return T7NB_INVALID_ARGUMENT;
        *session = nullptr;
        if (!validHeader(args->abiVersion, args->structSize, sizeof(T7NativeCreateArgs))) return T7NB_INVALID_ABI;
        std::string root;
        auto status = copyUtf8(args->packageRoot, args->packageRootLength, root);
        if (status != T7NB_OK) return status;
        auto opaque = std::make_unique<T7NativeSessionOpaque>();
        opaque->value = Session::create(std::move(root));
        *session = opaque.release();
        return T7NB_OK;
    });
}

extern "C" int32_t T7NB_CALL t7_native_release(T7NativeSessionHandle session) {
    return guard([&]() -> int32_t {
        if (!session || !session->value) return T7NB_INVALID_HANDLE;
        session->value->requestClose();
        delete session;
        return T7NB_OK;
    });
}

extern "C" int32_t T7NB_CALL t7_native_submit_check(T7NativeSessionHandle handle, const T7NativePath* path, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7NB_INVALID_ARGUMENT;
        *operationId = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7NB_OK) return status;
        std::string value; status = copyPath(path, value, false); if (status != T7NB_OK) return status;
        return session->submit(T7NB_OPERATION_CHECK, std::move(value), *operationId);
    });
}

extern "C" int32_t T7NB_CALL t7_native_submit_start(T7NativeSessionHandle handle, const T7NativePath* path, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7NB_INVALID_ARGUMENT;
        *operationId = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7NB_OK) return status;
        std::string value; status = copyPath(path, value, false); if (status != T7NB_OK) return status;
        return session->submit(T7NB_OPERATION_START, std::move(value), *operationId);
    });
}

extern "C" int32_t T7NB_CALL t7_native_submit_stop(T7NativeSessionHandle handle, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7NB_INVALID_ARGUMENT;
        *operationId = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7NB_OK) return status;
        return session->submit(T7NB_OPERATION_STOP, {}, *operationId);
    });
}

extern "C" int32_t T7NB_CALL t7_native_submit_start_named(T7NativeSessionHandle handle, const T7NativeStartArgs* args, uint64_t* operationId) {
    return guard([&] { return submitStart(handle, args, operationId, false); });
}

extern "C" int32_t T7NB_CALL t7_native_submit_start_options(T7NativeSessionHandle handle, const T7NativeStartOptions* args, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7NB_INVALID_ARGUMENT;
        *operationId = 0;
        if (!args) return T7NB_INVALID_ARGUMENT;
        if (!validHeader(args->start.abiVersion, args->start.structSize, sizeof(T7NativeStartOptions))) return T7NB_INVALID_ABI;
        if (args->reserved || (args->flags & ~T7NB_START_SKIP_STARTUP_ANIMATION)) return T7NB_INVALID_ARGUMENT;
        return submitStart(handle, &args->start, operationId, (args->flags & T7NB_START_SKIP_STARTUP_ANIMATION) != 0);
    });
}

extern "C" int32_t T7NB_CALL t7_native_cancel(T7NativeSessionHandle handle, uint64_t operationId) {
    return guard([&]() -> int32_t {
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7NB_OK) return status;
        return session->cancel(operationId);
    });
}

extern "C" int32_t T7NB_CALL t7_native_get_snapshot(T7NativeSessionHandle handle, T7NativeSnapshot* output) {
    return guard([&]() -> int32_t {
        if (!output) return T7NB_INVALID_ARGUMENT;
        if (!validHeader(output->abiVersion, output->structSize, sizeof(T7NativeSnapshot))) return T7NB_INVALID_ABI;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7NB_OK) return status;
        t7::bridge::Snapshot snapshot; status = session->snapshot(snapshot); if (status != T7NB_OK) return status;
        T7NativeSnapshot value{}; value.abiVersion = T7NB_ABI_VERSION; value.structSize = sizeof(value);
        value.state = snapshot.state; value.operation = snapshot.operation; value.operationId = snapshot.operationId;
        value.revision = snapshot.revision; value.loginPort = snapshot.ports[0]; value.logicPort = snapshot.ports[1];
        value.instancePort = snapshot.ports[2]; value.errorCode = snapshot.errorCode; value.flags = snapshot.flags;
        value.logCursor = snapshot.logCursor;
        strncpy_s(value.phase, sizeof(value.phase), snapshot.phase.c_str(), sizeof(value.phase) - 1);
        std::memcpy(output, &value, sizeof(value));
        return T7NB_OK;
    });
}

extern "C" int32_t T7NB_CALL t7_native_get_operation(T7NativeSessionHandle handle, uint64_t operationId, T7NativeOperation* output) {
    return guard([&]() -> int32_t {
        if (!output) return T7NB_INVALID_ARGUMENT;
        if (!validHeader(output->abiVersion, output->structSize, sizeof(T7NativeOperation))) return T7NB_INVALID_ABI;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7NB_OK) return status;
        t7::bridge::Operation operation; status = session->operation(operationId, operation); if (status != T7NB_OK) return status;
        output->abiVersion = T7NB_ABI_VERSION; output->structSize = sizeof(*output); output->operationId = operation.id;
        output->kind = operation.kind; output->status = operation.status; output->errorCode = operation.errorCode; output->reserved = 0;
        return T7NB_OK;
    });
}

extern "C" int32_t T7NB_CALL t7_native_get_error(T7NativeSessionHandle handle, uint64_t operationId, char* buffer,
                                                    uint32_t capacity, uint32_t* required, uint32_t* errorCode) {
    return guard([&]() -> int32_t {
        if (!required || !errorCode || (capacity && !buffer)) return T7NB_INVALID_ARGUMENT;
        *required = 0; *errorCode = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7NB_OK) return status;
        std::string text; status = session->error(operationId, text, *errorCode); if (status != T7NB_OK) return status;
        *required = static_cast<uint32_t>(text.size());
        if (*required > capacity) return T7NB_BUFFER_TOO_SMALL;
        if (*required) std::memcpy(buffer, text.data(), *required);
        return T7NB_OK;
    });
}

extern "C" int32_t T7NB_CALL t7_native_read_logs(T7NativeSessionHandle handle, uint64_t* cursor, uint8_t* buffer,
                                                   uint32_t capacity, uint32_t* required, uint32_t* flags) {
    return guard([&]() -> int32_t {
        if (!cursor || !required || !flags) return T7NB_INVALID_ARGUMENT;
        *required = 0; *flags = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7NB_OK) return status;
        return session->readLogs(*cursor, buffer, capacity, *required, *flags);
    });
}
