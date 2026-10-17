#include "T7RNativeBridge.h"
#include "Session.h"
#include "../core/Common.h"
#include "../launcher/CombatProfileHookCode.h"
#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <set>

struct T7RNativeSessionOpaque {
    std::shared_ptr<t7r::bridge::Session> value;
};

namespace {
using t7r::bridge::Session;

bool validHeader(uint32_t version, uint32_t size, uint32_t expected) {
    return version == T7RNB_ABI_VERSION && size >= expected;
}

int32_t copyUtf8(const uint8_t* data, uint32_t length, std::string& result) {
    if (!data || length == 0 || length > 32768) return T7RNB_INVALID_ARGUMENT;
    if (std::find_if(data, data + length, [](uint8_t value) {
            return value == 0 || value == static_cast<uint8_t>('\r') || value == static_cast<uint8_t>('\n');
        }) != data + length) return T7RNB_INVALID_ARGUMENT;
    try {
        result.assign(reinterpret_cast<const char*>(data), length);
        (void)t7r::wide(result); // validate UTF-8 before an async copy is made
        return T7RNB_OK;
    } catch (...) { return T7RNB_INVALID_ARGUMENT; }
}

int32_t copyPath(const T7RNativePath* path, std::string& result, bool allowEmpty) {
    if (!path) return allowEmpty ? T7RNB_OK : T7RNB_INVALID_ARGUMENT;
    if (!validHeader(path->abiVersion, path->structSize, sizeof(T7RNativePath))) return T7RNB_INVALID_ABI;
    if (path->length == 0 && allowEmpty) { result.clear(); return T7RNB_OK; }
    return copyUtf8(path->data, path->length, result);
}

int32_t checkSession(T7RNativeSessionHandle handle, std::shared_ptr<Session>& result) {
    if (!handle || !handle->value) return T7RNB_INVALID_HANDLE;
    result = handle->value;
    return T7RNB_OK;
}

template<class F>
int32_t guard(F&& action) noexcept {
    try { return action(); }
    catch (...) { return T7RNB_INTERNAL_ERROR; }
}

int32_t submitStart(T7RNativeSessionHandle handle, const T7RNativeStartArgs* args,
                    uint64_t* operationId, bool skipStartupAnimation,
                    std::array<uint16_t, 3> gatewayPorts = {},
                    std::vector<t7r::CombatProfile> profiles = {}) {
    if (!operationId) return T7RNB_INVALID_ARGUMENT;
    *operationId = 0;
    if (!args) return T7RNB_INVALID_ARGUMENT;
    if (!validHeader(args->abiVersion, args->structSize, sizeof(T7RNativeStartArgs))) return T7RNB_INVALID_ABI;
    std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
    std::string directory, name;
    status = copyUtf8(args->clientDirectory, args->clientDirectoryLength, directory); if (status != T7RNB_OK) return status;
    status = copyUtf8(args->playerName, args->playerNameLength, name); if (status != T7RNB_OK) return status;
    return session->submit(T7RNB_OPERATION_START, std::move(directory), *operationId,
                           std::move(name), skipStartupAnimation, gatewayPorts, std::move(profiles));
}
}

extern "C" int32_t T7RNB_CALL t7r_native_get_abi(uint32_t* version, uint32_t* snapshotSize) {
    if (!version || !snapshotSize) return T7RNB_INVALID_ARGUMENT;
    *version = T7RNB_ABI_VERSION; *snapshotSize = sizeof(T7RNativeSnapshot); return T7RNB_OK;
}
extern "C" int32_t T7RNB_CALL t7r_native_publish_titles(T7RNativeSessionHandle handle, uint64_t epoch,
        const T7RNativePath* json, uint64_t requestId, uint32_t success, const uint8_t* descriptions, uint32_t descriptionLength) {
    return guard([&]() -> int32_t {
        if (!epoch || epoch > INT64_MAX || requestId > INT64_MAX || success > 1) return T7RNB_INVALID_ARGUMENT;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        std::string value; status = copyPath(json, value, false); if (status != T7RNB_OK) return status;
        t7r::Bytes text;
        if (descriptionLength) {
            if (!descriptions || descriptionLength > t7r::TITLE_CATALOGUE_MAX_SIZE) return T7RNB_INVALID_ARGUMENT;
            text.assign(descriptions, descriptions + descriptionLength);
            if (!t7r::validTitleCatalogue(text)) return T7RNB_INVALID_ARGUMENT;
        }
        return session->publishTitles(epoch, std::move(value), requestId, success != 0, std::move(text));
    });
}
extern "C" int32_t T7RNB_CALL t7r_native_get_title_request(T7RNativeSessionHandle handle, T7RNativeTitleRequest* request) {
    return guard([&]() -> int32_t {
        if (!request) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(request->abiVersion, request->structSize, sizeof(*request))) return T7RNB_INVALID_ABI;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        t7r::TitleRequest value; status = session->titleRequest(value); if (status != T7RNB_OK) return status;
        request->requestId = value.requestId; request->epoch = value.epoch;
        request->titleId = value.titleId; request->reserved = 0;
        return T7RNB_OK;
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_set_output_device(T7RNativeSessionHandle handle, const uint8_t* identifier, uint32_t length) {
    return guard([&]() -> int32_t {
        if (!identifier || length != sizeof(GUID)) return T7RNB_INVALID_ARGUMENT;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        GUID value{}; std::memcpy(&value, identifier, sizeof(value));
        return session->setOutputDevice(value);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_set_log_level(T7RNativeSessionHandle handle, uint32_t level) {
    return guard([&]() -> int32_t {
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        return session->setLogLevel(level);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_get_graphics(T7RNativeSessionHandle handle, T7RNativeGraphicsSnapshot* output) {
    return guard([&]() -> int32_t {
        if (!output) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(output->abiVersion, output->structSize, sizeof(*output))) return T7RNB_INVALID_ABI;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        t7r::bridge::GraphicsSnapshot snapshot; status = session->graphics(snapshot); if (status != T7RNB_OK) return status;
        T7RNativeGraphicsSnapshot value{}; value.abiVersion = T7RNB_ABI_VERSION; value.structSize = sizeof(value);
        value.revision = snapshot.revision; value.state = snapshot.state;
        static_assert(sizeof(value.values) == sizeof(snapshot.values), "graphics value layout");
        std::memcpy(&value.values, &snapshot.values, sizeof(value.values));
        *output = value; return T7RNB_OK;
    });
}
extern "C" int32_t T7RNB_CALL t7r_native_apply_graphics(T7RNativeSessionHandle handle, const T7RNativeGraphicsSnapshot* input) {
    return guard([&]() -> int32_t {
        if (!input) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(input->abiVersion, input->structSize, sizeof(*input))) return T7RNB_INVALID_ABI;
        if (input->reserved || input->state != T7RNB_GRAPHICS_READY) return T7RNB_INVALID_ARGUMENT;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        t7r::GraphicsValues values; std::memcpy(&values, &input->values, sizeof(values));
        return session->applyGraphics(input->revision, values);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_get_audio(T7RNativeSessionHandle handle, T7RNativeAudioSnapshot* output) {
    return guard([&]() -> int32_t {
        if (!output) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(output->abiVersion, output->structSize, sizeof(*output))) return T7RNB_INVALID_ABI;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        t7r::bridge::AudioSnapshot snapshot; status = session->audio(snapshot); if (status != T7RNB_OK) return status;
        T7RNativeAudioSnapshot value{}; value.abiVersion = T7RNB_ABI_VERSION; value.structSize = sizeof(value);
        value.revision = snapshot.revision; value.state = snapshot.state;
        static_assert(sizeof(value.values) == sizeof(snapshot.values), "audio value layout");
        std::memcpy(&value.values, &snapshot.values, sizeof(value.values));
        *output = value; return T7RNB_OK;
    });
}
extern "C" int32_t T7RNB_CALL t7r_native_apply_audio(T7RNativeSessionHandle handle, const T7RNativeAudioSnapshot* input) {
    return guard([&]() -> int32_t {
        if (!input) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(input->abiVersion, input->structSize, sizeof(*input))) return T7RNB_INVALID_ABI;
        if (input->reserved || input->state != T7RNB_AUDIO_READY) return T7RNB_INVALID_ARGUMENT;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        t7r::AudioValues values; std::memcpy(&values, &input->values, sizeof(values));
        return session->applyAudio(input->revision, values);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_create(const T7RNativeCreateArgs* args, T7RNativeSessionHandle* session) {
    return guard([&]() -> int32_t {
        if (!args || !session) return T7RNB_INVALID_ARGUMENT;
        *session = nullptr;
        if (!validHeader(args->abiVersion, args->structSize, sizeof(T7RNativeCreateArgs))) return T7RNB_INVALID_ABI;
        std::string root;
        auto status = copyUtf8(args->packageRoot, args->packageRootLength, root);
        if (status != T7RNB_OK) return status;
        auto opaque = std::make_unique<T7RNativeSessionOpaque>();
        opaque->value = Session::create(std::move(root));
        *session = opaque.release();
        return T7RNB_OK;
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_release(T7RNativeSessionHandle session) {
    return guard([&]() -> int32_t {
        if (!session || !session->value) return T7RNB_INVALID_HANDLE;
        session->value->requestClose();
        delete session;
        return T7RNB_OK;
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_submit_check(T7RNativeSessionHandle handle, const T7RNativePath* path, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7RNB_INVALID_ARGUMENT;
        *operationId = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        std::string value; status = copyPath(path, value, false); if (status != T7RNB_OK) return status;
        return session->submit(T7RNB_OPERATION_CHECK, std::move(value), *operationId);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_submit_start(T7RNativeSessionHandle handle, const T7RNativePath* path, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7RNB_INVALID_ARGUMENT;
        *operationId = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        std::string value; status = copyPath(path, value, false); if (status != T7RNB_OK) return status;
        return session->submit(T7RNB_OPERATION_START, std::move(value), *operationId);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_submit_stop(T7RNativeSessionHandle handle, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7RNB_INVALID_ARGUMENT;
        *operationId = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        return session->submit(T7RNB_OPERATION_STOP, {}, *operationId);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_attach_battle(T7RNativeSessionHandle handle,
                                                          const T7RNativeBattleAttachArgs* args) {
    return guard([&]() -> int32_t {
        if (!args || !args->config || !args->gatewayHandle) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(args->abiVersion, args->structSize, sizeof(*args))
            || args->config->abiVersion != T7R_BATTLE_CONFIG_ABI_VERSION
            || args->config->structSize != sizeof(T7RBattleConfig)) return T7RNB_INVALID_ABI;
        std::shared_ptr<Session> session;
        auto status = checkSession(handle, session);
        if (status != T7RNB_OK) return status;
        std::string path;
        status = copyPath(&args->rustLibrary, path, false);
        if (status != T7RNB_OK) return status;
        return session->attachBattle(*args->config, std::filesystem::path(t7r::wide(path)), args->gatewayHandle);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_detach_battle(T7RNativeSessionHandle handle) {
    return guard([&]() -> int32_t {
        std::shared_ptr<Session> session;
        const auto status = checkSession(handle, session);
        return status == T7RNB_OK ? session->detachBattle() : status;
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_submit_start_named(T7RNativeSessionHandle handle, const T7RNativeStartArgs* args, uint64_t* operationId) {
    return guard([&] { return submitStart(handle, args, operationId, false); });
}

extern "C" int32_t T7RNB_CALL t7r_native_submit_start_options(T7RNativeSessionHandle handle, const T7RNativeStartOptions* args, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7RNB_INVALID_ARGUMENT;
        *operationId = 0;
        if (!args) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(args->start.abiVersion, args->start.structSize, sizeof(T7RNativeStartOptions))) return T7RNB_INVALID_ABI;
        if (args->reserved || (args->flags & ~T7RNB_START_SKIP_STARTUP_ANIMATION)) return T7RNB_INVALID_ARGUMENT;
        return submitStart(handle, &args->start, operationId, (args->flags & T7RNB_START_SKIP_STARTUP_ANIMATION) != 0);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_submit_start_gateway(T7RNativeSessionHandle handle,
        const T7RNativeGatewayStartOptions* args, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7RNB_INVALID_ARGUMENT;
        *operationId = 0;
        if (!args) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(args->start.start.abiVersion, args->start.start.structSize, sizeof(*args)))
            return T7RNB_INVALID_ABI;
        if (args->start.reserved || args->reserved
            || (args->start.flags & ~T7RNB_START_SKIP_STARTUP_ANIMATION)
            || !args->loginPort || !args->logicPort || !args->instancePort
            || args->loginPort == args->logicPort || args->loginPort == args->instancePort
            || args->logicPort == args->instancePort)
            return T7RNB_INVALID_ARGUMENT;
        return submitStart(handle, &args->start.start, operationId,
            (args->start.flags & T7RNB_START_SKIP_STARTUP_ANIMATION) != 0,
            {args->loginPort, args->logicPort, args->instancePort});
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_submit_start_gateway_profiles(T7RNativeSessionHandle handle,
        const T7RNativeGatewayProfileStartOptions* args, uint64_t* operationId) {
    return guard([&]() -> int32_t {
        if (!operationId) return T7RNB_INVALID_ARGUMENT;
        *operationId = 0;
        if (!args) return T7RNB_INVALID_ARGUMENT;
        const auto& gateway = args->gateway;
        if (!validHeader(gateway.start.start.abiVersion, gateway.start.start.structSize, sizeof(*args)))
            return T7RNB_INVALID_ABI;
        if (gateway.start.reserved || gateway.reserved || args->reserved
            || (gateway.start.flags & ~T7RNB_START_SKIP_STARTUP_ANIMATION)
            || !gateway.loginPort || !gateway.logicPort || !gateway.instancePort
            || gateway.loginPort == gateway.logicPort || gateway.loginPort == gateway.instancePort
            || gateway.logicPort == gateway.instancePort || !args->profiles
            || !args->profileCount || args->profileCount > t7r::MAX_COMBAT_PROFILES)
            return T7RNB_INVALID_ARGUMENT;
        std::vector<t7r::CombatProfile> profiles;
        profiles.reserve(args->profileCount);
        std::set<std::pair<int32_t, int32_t>> keys;
        for (uint32_t index = 0; index < args->profileCount; ++index) {
            const auto& input = args->profiles[index];
            if (input.heroId <= 0 || input.weaponId <= 0
                || !keys.emplace(input.heroId, input.weaponId).second
                || !std::isfinite(input.forwardSlow) || !std::isfinite(input.lateralSlow)
                || !std::isfinite(input.backwardSlow) || !std::isfinite(input.forwardSprint)
                || input.forwardSlow <= 0 || input.lateralSlow <= 0 || input.backwardSlow <= 0
                || input.forwardSprint <= input.forwardSlow || input.forwardSprint <= input.lateralSlow
                || (input.dashDurationMs == 0 && (input.dashMoveMs || input.dashSpeedMilli))
                || (input.dashDurationMs && (!input.dashMoveMs || !input.dashSpeedMilli
                    || input.dashMoveMs > input.dashDurationMs || input.dashMoveMs > 65535)))
                return T7RNB_INVALID_ARGUMENT;
            t7r::CombatProfile profile;
            profile.heroId = input.heroId;
            profile.movement.enabled = true;
            profile.movement.weaponId = input.weaponId;
            profile.movement.forwardSlow = input.forwardSlow;
            profile.movement.lateralSlow = input.lateralSlow;
            profile.movement.backwardSlow = input.backwardSlow;
            profile.movement.forwardSprint = input.forwardSprint;
            profile.dash.enabled = input.dashDurationMs != 0;
            profile.dash.weaponId = input.weaponId;
            profile.dash.durationMs = input.dashDurationMs;
            profile.dash.moveMs = input.dashMoveMs;
            profile.dash.speedMilli = input.dashSpeedMilli;
            profiles.push_back(std::move(profile));
        }
        return submitStart(handle, &gateway.start.start, operationId,
            (gateway.start.flags & T7RNB_START_SKIP_STARTUP_ANIMATION) != 0,
            {gateway.loginPort, gateway.logicPort, gateway.instancePort}, std::move(profiles));
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_cancel(T7RNativeSessionHandle handle, uint64_t operationId) {
    return guard([&]() -> int32_t {
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        return session->cancel(operationId);
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_get_snapshot(T7RNativeSessionHandle handle, T7RNativeSnapshot* output) {
    return guard([&]() -> int32_t {
        if (!output) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(output->abiVersion, output->structSize, sizeof(T7RNativeSnapshot))) return T7RNB_INVALID_ABI;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        t7r::bridge::Snapshot snapshot; status = session->snapshot(snapshot); if (status != T7RNB_OK) return status;
        T7RNativeSnapshot value{}; value.abiVersion = T7RNB_ABI_VERSION; value.structSize = sizeof(value);
        value.state = snapshot.state; value.operation = snapshot.operation; value.operationId = snapshot.operationId;
        value.revision = snapshot.revision; value.loginPort = snapshot.ports[0]; value.logicPort = snapshot.ports[1];
        value.instancePort = snapshot.ports[2]; value.errorCode = snapshot.errorCode; value.flags = snapshot.flags;
        value.logCursor = snapshot.logCursor;
        strncpy_s(value.phase, sizeof(value.phase), snapshot.phase.c_str(), sizeof(value.phase) - 1);
        std::memcpy(output, &value, sizeof(value));
        return T7RNB_OK;
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_get_operation(T7RNativeSessionHandle handle, uint64_t operationId, T7RNativeOperation* output) {
    return guard([&]() -> int32_t {
        if (!output) return T7RNB_INVALID_ARGUMENT;
        if (!validHeader(output->abiVersion, output->structSize, sizeof(T7RNativeOperation))) return T7RNB_INVALID_ABI;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        t7r::bridge::Operation operation; status = session->operation(operationId, operation); if (status != T7RNB_OK) return status;
        output->abiVersion = T7RNB_ABI_VERSION; output->structSize = sizeof(*output); output->operationId = operation.id;
        output->kind = operation.kind; output->status = operation.status; output->errorCode = operation.errorCode; output->reserved = 0;
        return T7RNB_OK;
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_get_error(T7RNativeSessionHandle handle, uint64_t operationId, char* buffer,
                                                    uint32_t capacity, uint32_t* required, uint32_t* errorCode) {
    return guard([&]() -> int32_t {
        if (!required || !errorCode || (capacity && !buffer)) return T7RNB_INVALID_ARGUMENT;
        *required = 0; *errorCode = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        std::string text; status = session->error(operationId, text, *errorCode); if (status != T7RNB_OK) return status;
        *required = static_cast<uint32_t>(text.size());
        if (*required > capacity) return T7RNB_BUFFER_TOO_SMALL;
        if (*required) std::memcpy(buffer, text.data(), *required);
        return T7RNB_OK;
    });
}

extern "C" int32_t T7RNB_CALL t7r_native_read_logs(T7RNativeSessionHandle handle, uint64_t* cursor, uint8_t* buffer,
                                                   uint32_t capacity, uint32_t* required, uint32_t* flags) {
    return guard([&]() -> int32_t {
        if (!cursor || !required || !flags) return T7RNB_INVALID_ARGUMENT;
        *required = 0; *flags = 0;
        std::shared_ptr<Session> session; auto status = checkSession(handle, session); if (status != T7RNB_OK) return status;
        return session->readLogs(*cursor, buffer, capacity, *required, *flags);
    });
}
