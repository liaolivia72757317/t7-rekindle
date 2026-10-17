#pragma once

#include <stdint.h>
#include <stddef.h>

#if defined(_WIN32) && defined(T7R_NATIVE_BRIDGE_BUILD)
#define T7RNB_CALL __cdecl
#define T7RNB_EXPORT
#elif defined(_WIN32)
#define T7RNB_CALL __cdecl
#define T7RNB_EXPORT __declspec(dllimport)
#else
#define T7RNB_CALL
#define T7RNB_EXPORT
#endif

#define T7RNB_ABI_VERSION 1u

typedef struct T7RNativeSessionOpaque* T7RNativeSessionHandle;

enum T7RNativeStatus : int32_t {
    T7RNB_OK = 0,
    T7RNB_INVALID_ARGUMENT = 1,
    T7RNB_INVALID_ABI = 2,
    T7RNB_INVALID_HANDLE = 3,
    T7RNB_BUSY = 4,
    T7RNB_NOT_FOUND = 5,
    T7RNB_BUFFER_TOO_SMALL = 6,
    T7RNB_CANCELLED = 7,
    T7RNB_FAILED = 8,
    T7RNB_NOT_READY = 9,
    T7RNB_INTERNAL_ERROR = 10
};

enum T7RNativeErrorCode : uint32_t {
    T7RNB_ERROR_NONE = 0,
    T7RNB_ERROR_OPERATION = 1000,
    T7RNB_ERROR_CANCELLED = 1001,
    T7RNB_ERROR_CLEANUP = 1002,
    T7RNB_ERROR_CLIENT_EXIT = 1003
};

enum T7RNativeLogLevel : uint32_t {
    T7RNB_LOG_DEBUG = 0, T7RNB_LOG_INFO = 1, T7RNB_LOG_WARN = 2, T7RNB_LOG_ERROR = 3
};

enum T7RNativeOperationKind : uint32_t {
    T7RNB_OPERATION_NONE = 0,
    T7RNB_OPERATION_CHECK = 1,
    T7RNB_OPERATION_START = 2,
    T7RNB_OPERATION_STOP = 3
};

enum T7RNativeOperationStatus : uint32_t {
    T7RNB_OPERATION_UNKNOWN = 0,
    T7RNB_OPERATION_QUEUED = 1,
    T7RNB_OPERATION_RUNNING = 2,
    T7RNB_OPERATION_SUCCEEDED = 3,
    T7RNB_OPERATION_CANCELLED = 4,
    T7RNB_OPERATION_FAILED = 5
};

enum T7RNativeSessionState : uint32_t {
    T7RNB_STATE_IDLE = 0,
    T7RNB_STATE_CHECKING = 1,
    T7RNB_STATE_STARTING_RUNTIME = 2,
    T7RNB_STATE_STARTING_CLIENT = 3,
    T7RNB_STATE_ADAPTING_CLIENT = 4,
    T7RNB_STATE_RUNNING = 5,
    T7RNB_STATE_CANCELLING = 6,
    T7RNB_STATE_STOPPING_CLIENT = 7,
    T7RNB_STATE_STOPPING_RUNTIME = 8,
    T7RNB_STATE_FAILED_CLEANING = 9,
    T7RNB_STATE_FAILED = 10
};

#pragma pack(push, 8)
enum T7RNativeAudioState : uint32_t {
    T7RNB_AUDIO_UNAVAILABLE = 0, T7RNB_AUDIO_READY = 1, T7RNB_AUDIO_APPLYING = 2,
    T7RNB_AUDIO_FAILED = 3, T7RNB_AUDIO_CONFLICT = 4
};
typedef struct T7RNativeAudioValues {
    uint32_t musicMuted;
    float musicVolume;
    uint32_t effectsMuted;
    float effectsVolume;
} T7RNativeAudioValues;
typedef struct T7RNativeAudioSnapshot {
    uint32_t abiVersion, structSize;
    uint64_t revision;
    uint32_t state;
    T7RNativeAudioValues values;
    uint32_t reserved;
} T7RNativeAudioSnapshot;
enum T7RNativeGraphicsState : uint32_t {
    T7RNB_GRAPHICS_UNAVAILABLE = 0, T7RNB_GRAPHICS_READY = 1, T7RNB_GRAPHICS_APPLYING = 2,
    T7RNB_GRAPHICS_FAILED = 3, T7RNB_GRAPHICS_CONFLICT = 4
};
typedef struct T7RNativeGraphicsValues {
    uint32_t width, height, fullScreen, quality, verticalSync, fog, viewDistance, ragDoll, frameLimit, swoosh;
} T7RNativeGraphicsValues;
typedef struct T7RNativeGraphicsSnapshot {
    uint32_t abiVersion, structSize;
    uint64_t revision;
    uint32_t state;
    T7RNativeGraphicsValues values;
    uint32_t reserved;
} T7RNativeGraphicsSnapshot;
typedef struct T7RNativeStartArgs {
    uint32_t abiVersion;
    uint32_t structSize;
    const uint8_t* clientDirectory;
    uint32_t clientDirectoryLength;
    const uint8_t* playerName;
    uint32_t playerNameLength;
} T7RNativeStartArgs;

enum T7RNativeStartFlags : uint32_t {
    T7RNB_START_SKIP_STARTUP_ANIMATION = 1u
};

typedef struct T7RNativeStartOptions {
    T7RNativeStartArgs start;
    uint32_t flags;
    uint32_t reserved;
} T7RNativeStartOptions;

// Private opt-in path. The caller owns three distinct loopback listeners for
// the full client lifetime; NativeBridge never starts an embedded server here.
typedef struct T7RNativeGatewayStartOptions {
    T7RNativeStartOptions start;
    uint16_t loginPort;
    uint16_t logicPort;
    uint16_t instancePort;
    uint16_t reserved;
} T7RNativeGatewayStartOptions;

// Private, process-local copy of the validated Rust resource movement table.
typedef struct T7RNativeCombatProfile {
    int32_t heroId, weaponId;
    float forwardSlow, lateralSlow, backwardSlow, forwardSprint;
    uint32_t dashDurationMs, dashMoveMs, dashSpeedMilli;
} T7RNativeCombatProfile;

typedef struct T7RNativeGatewayProfileStartOptions {
    T7RNativeGatewayStartOptions gateway;
    const T7RNativeCombatProfile* profiles;
    uint32_t profileCount;
    uint32_t reserved;
} T7RNativeGatewayProfileStartOptions;

typedef struct T7RNativePath {
    uint32_t abiVersion;
    uint32_t structSize;
    const uint8_t* data;
    uint32_t length;
} T7RNativePath;

struct T7RBattleConfig;
typedef struct T7RNativeBattleAttachArgs {
    uint32_t abiVersion;
    uint32_t structSize;
    uint64_t gatewayHandle;
    const T7RBattleConfig* config;
    T7RNativePath rustLibrary;
} T7RNativeBattleAttachArgs;

typedef struct T7RNativeTitleRequest {
    uint32_t abiVersion, structSize;
    uint64_t requestId, epoch;
    uint32_t titleId, reserved;
} T7RNativeTitleRequest;

typedef struct T7RNativeCreateArgs {
    uint32_t abiVersion;
    uint32_t structSize;
    const uint8_t* packageRoot;
    uint32_t packageRootLength;
    uint32_t flags;
} T7RNativeCreateArgs;

typedef struct T7RNativeSnapshot {
    uint32_t abiVersion;
    uint32_t structSize;
    uint32_t state;
    uint32_t operation;
    uint64_t operationId;
    uint64_t revision;
    uint16_t loginPort;
    uint16_t logicPort;
    uint16_t instancePort;
    uint16_t reserved;
    uint32_t errorCode;
    uint32_t flags;
    uint64_t logCursor;
    char phase[64];
} T7RNativeSnapshot;

typedef struct T7RNativeOperation {
    uint32_t abiVersion;
    uint32_t structSize;
    uint64_t operationId;
    uint32_t kind;
    uint32_t status;
    uint32_t errorCode;
    uint32_t reserved;
} T7RNativeOperation;
#pragma pack(pop)

// All UTF-8 lengths are byte counts and exclude a terminator.  Output
// buffers use the same convention; a BufferTooSmall result leaves a log
// cursor unconsumed and reports the required byte count.
// T7RNativeSnapshot.flags bit 0 is cleanupComplete.  FailedCleaning keeps the
// bit clear and accepts only a Stop retry until all owned resources are gone.

static_assert(sizeof(T7RNativePath) == 24, "T7RNativePath ABI changed");
static_assert(sizeof(T7RNativeBattleAttachArgs) == 48, "T7RNativeBattleAttachArgs ABI changed");
static_assert(sizeof(T7RNativeStartArgs) == 40, "T7RNativeStartArgs ABI changed");
static_assert(sizeof(T7RNativeStartOptions) == 48, "T7RNativeStartOptions ABI changed");
static_assert(sizeof(T7RNativeGatewayStartOptions) == 56, "T7RNativeGatewayStartOptions ABI changed");
static_assert(sizeof(T7RNativeCombatProfile) == 36, "T7RNativeCombatProfile ABI changed");
static_assert(sizeof(T7RNativeGatewayProfileStartOptions) == 72, "T7RNativeGatewayProfileStartOptions ABI changed");
static_assert(offsetof(T7RNativeGatewayProfileStartOptions, profiles) == 56, "Gateway profile pointer ABI changed");
static_assert(offsetof(T7RNativeGatewayProfileStartOptions, profileCount) == 64, "Gateway profile count ABI changed");
static_assert(sizeof(T7RNativeCreateArgs) == 24, "T7RNativeCreateArgs ABI changed");
static_assert(sizeof(T7RNativeSnapshot) == 120, "T7RNativeSnapshot ABI changed");
static_assert(sizeof(T7RNativeOperation) == 32, "T7RNativeOperation ABI changed");
static_assert(sizeof(T7RNativeGraphicsSnapshot) == 64, "T7RNativeGraphicsSnapshot ABI changed");
static_assert(sizeof(T7RNativeAudioSnapshot) == 40, "T7RNativeAudioSnapshot ABI changed");

extern "C" {
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_publish_titles(T7RNativeSessionHandle session, uint64_t epoch,
    const T7RNativePath* json, uint64_t requestId, uint32_t success, const uint8_t* descriptions, uint32_t descriptionLength);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_get_title_request(T7RNativeSessionHandle session, T7RNativeTitleRequest* request);
// Applies immediately to subsequent diagnostics; journal data is not filtered.
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_set_log_level(T7RNativeSessionHandle session, uint32_t level);
// identifier is the 16-byte D3D9 DeviceIdentifier; all zero selects the system default.
// Copied into subsequent Check/Start commands; does not change a running device.
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_set_output_device(T7RNativeSessionHandle session, const uint8_t* identifier, uint32_t length);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_get_audio(T7RNativeSessionHandle session, T7RNativeAudioSnapshot* snapshot);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_apply_audio(T7RNativeSessionHandle session, const T7RNativeAudioSnapshot* settings);
// Apply acknowledges a queued request. Poll until state leaves APPLYING for readback.
// revision must match the last read; CONFLICT never overwrites newer game settings.
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_get_graphics(T7RNativeSessionHandle session, T7RNativeGraphicsSnapshot* snapshot);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_apply_graphics(T7RNativeSessionHandle session, const T7RNativeGraphicsSnapshot* settings);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_get_abi(uint32_t* version, uint32_t* snapshotSize);
// Create starts one native lifecycle worker.  Release only requests cleanup;
// callers must stop using the opaque handle after it returns.
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_create(const T7RNativeCreateArgs* args, T7RNativeSessionHandle* session);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_release(T7RNativeSessionHandle session);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_submit_check(T7RNativeSessionHandle session, const T7RNativePath* clientPath, uint64_t* operationId);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_submit_start(T7RNativeSessionHandle session, const T7RNativePath* clientPath, uint64_t* operationId);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_submit_start_named(T7RNativeSessionHandle session, const T7RNativeStartArgs* args, uint64_t* operationId);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_submit_start_options(T7RNativeSessionHandle session, const T7RNativeStartOptions* args, uint64_t* operationId);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_submit_start_gateway(T7RNativeSessionHandle session, const T7RNativeGatewayStartOptions* args, uint64_t* operationId);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_submit_start_gateway_profiles(T7RNativeSessionHandle session, const T7RNativeGatewayProfileStartOptions* args, uint64_t* operationId);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_submit_stop(T7RNativeSessionHandle session, uint64_t* operationId);
// Private additive attachment for a battle already started on the same
// process-local Rust gateway handle. The caller owns that match and must
// detach before stopping it; all pointers are borrowed for this call only.
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_attach_battle(T7RNativeSessionHandle session, const T7RNativeBattleAttachArgs* args);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_detach_battle(T7RNativeSessionHandle session);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_cancel(T7RNativeSessionHandle session, uint64_t operationId);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_get_snapshot(T7RNativeSessionHandle session, T7RNativeSnapshot* snapshot);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_get_operation(T7RNativeSessionHandle session, uint64_t operationId, T7RNativeOperation* operation);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_get_error(T7RNativeSessionHandle session, uint64_t operationId, char* buffer, uint32_t capacity, uint32_t* required, uint32_t* errorCode);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_native_read_logs(T7RNativeSessionHandle session, uint64_t* cursor, uint8_t* buffer, uint32_t capacity, uint32_t* required, uint32_t* flags);
}
