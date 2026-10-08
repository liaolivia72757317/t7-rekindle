#pragma once

#include <stdint.h>

#if defined(_WIN32) && defined(T7_NATIVE_BRIDGE_BUILD)
#define T7NB_CALL __cdecl
#define T7NB_EXPORT
#elif defined(_WIN32)
#define T7NB_CALL __cdecl
#define T7NB_EXPORT __declspec(dllimport)
#else
#define T7NB_CALL
#define T7NB_EXPORT
#endif

#define T7NB_ABI_VERSION 1u

typedef struct T7NativeSessionOpaque* T7NativeSessionHandle;

enum T7NativeStatus : int32_t {
    T7NB_OK = 0,
    T7NB_INVALID_ARGUMENT = 1,
    T7NB_INVALID_ABI = 2,
    T7NB_INVALID_HANDLE = 3,
    T7NB_BUSY = 4,
    T7NB_NOT_FOUND = 5,
    T7NB_BUFFER_TOO_SMALL = 6,
    T7NB_CANCELLED = 7,
    T7NB_FAILED = 8,
    T7NB_NOT_READY = 9,
    T7NB_INTERNAL_ERROR = 10
};

enum T7NativeErrorCode : uint32_t {
    T7NB_ERROR_NONE = 0,
    T7NB_ERROR_OPERATION = 1000,
    T7NB_ERROR_CANCELLED = 1001,
    T7NB_ERROR_CLEANUP = 1002,
    T7NB_ERROR_CLIENT_EXIT = 1003
};

enum T7NativeOperationKind : uint32_t {
    T7NB_OPERATION_NONE = 0,
    T7NB_OPERATION_CHECK = 1,
    T7NB_OPERATION_START = 2,
    T7NB_OPERATION_STOP = 3
};

enum T7NativeOperationStatus : uint32_t {
    T7NB_OPERATION_UNKNOWN = 0,
    T7NB_OPERATION_QUEUED = 1,
    T7NB_OPERATION_RUNNING = 2,
    T7NB_OPERATION_SUCCEEDED = 3,
    T7NB_OPERATION_CANCELLED = 4,
    T7NB_OPERATION_FAILED = 5
};

enum T7NativeSessionState : uint32_t {
    T7NB_STATE_IDLE = 0,
    T7NB_STATE_CHECKING = 1,
    T7NB_STATE_STARTING_RUNTIME = 2,
    T7NB_STATE_STARTING_CLIENT = 3,
    T7NB_STATE_ADAPTING_CLIENT = 4,
    T7NB_STATE_RUNNING = 5,
    T7NB_STATE_CANCELLING = 6,
    T7NB_STATE_STOPPING_CLIENT = 7,
    T7NB_STATE_STOPPING_RUNTIME = 8,
    T7NB_STATE_FAILED_CLEANING = 9,
    T7NB_STATE_FAILED = 10
};

#pragma pack(push, 8)
enum T7NativeAudioState : uint32_t {
    T7NB_AUDIO_UNAVAILABLE = 0, T7NB_AUDIO_READY = 1, T7NB_AUDIO_APPLYING = 2,
    T7NB_AUDIO_FAILED = 3, T7NB_AUDIO_CONFLICT = 4
};
typedef struct T7NativeAudioValues {
    uint32_t musicMuted;
    float musicVolume;
    uint32_t effectsMuted;
    float effectsVolume;
} T7NativeAudioValues;
typedef struct T7NativeAudioSnapshot {
    uint32_t abiVersion, structSize;
    uint64_t revision;
    uint32_t state;
    T7NativeAudioValues values;
    uint32_t reserved;
} T7NativeAudioSnapshot;
enum T7NativeGraphicsState : uint32_t {
    T7NB_GRAPHICS_UNAVAILABLE = 0, T7NB_GRAPHICS_READY = 1, T7NB_GRAPHICS_APPLYING = 2,
    T7NB_GRAPHICS_FAILED = 3, T7NB_GRAPHICS_CONFLICT = 4
};
typedef struct T7NativeGraphicsValues {
    uint32_t width, height, fullScreen, quality, verticalSync, fog, viewDistance, ragDoll, frameLimit, swoosh;
} T7NativeGraphicsValues;
typedef struct T7NativeGraphicsSnapshot {
    uint32_t abiVersion, structSize;
    uint64_t revision;
    uint32_t state;
    T7NativeGraphicsValues values;
    uint32_t reserved;
} T7NativeGraphicsSnapshot;
typedef struct T7NativeStartArgs {
    uint32_t abiVersion;
    uint32_t structSize;
    const uint8_t* clientDirectory;
    uint32_t clientDirectoryLength;
    const uint8_t* playerName;
    uint32_t playerNameLength;
} T7NativeStartArgs;

enum T7NativeStartFlags : uint32_t {
    T7NB_START_SKIP_STARTUP_ANIMATION = 1u
};

typedef struct T7NativeStartOptions {
    T7NativeStartArgs start;
    uint32_t flags;
    uint32_t reserved;
} T7NativeStartOptions;

typedef struct T7NativePath {
    uint32_t abiVersion;
    uint32_t structSize;
    const uint8_t* data;
    uint32_t length;
} T7NativePath;

typedef struct T7NativeCreateArgs {
    uint32_t abiVersion;
    uint32_t structSize;
    const uint8_t* packageRoot;
    uint32_t packageRootLength;
    uint32_t flags;
} T7NativeCreateArgs;

typedef struct T7NativeSnapshot {
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
} T7NativeSnapshot;

typedef struct T7NativeOperation {
    uint32_t abiVersion;
    uint32_t structSize;
    uint64_t operationId;
    uint32_t kind;
    uint32_t status;
    uint32_t errorCode;
    uint32_t reserved;
} T7NativeOperation;
#pragma pack(pop)

// All UTF-8 lengths are byte counts and exclude a terminator.  Output
// buffers use the same convention; a BufferTooSmall result leaves a log
// cursor unconsumed and reports the required byte count.
// T7NativeSnapshot.flags bit 0 is cleanupComplete.  FailedCleaning keeps the
// bit clear and accepts only a Stop retry until all owned resources are gone.

static_assert(sizeof(T7NativePath) == 24, "T7NativePath ABI changed");
static_assert(sizeof(T7NativeStartArgs) == 40, "T7NativeStartArgs ABI changed");
static_assert(sizeof(T7NativeStartOptions) == 48, "T7NativeStartOptions ABI changed");
static_assert(sizeof(T7NativeCreateArgs) == 24, "T7NativeCreateArgs ABI changed");
static_assert(sizeof(T7NativeSnapshot) == 120, "T7NativeSnapshot ABI changed");
static_assert(sizeof(T7NativeOperation) == 32, "T7NativeOperation ABI changed");
static_assert(sizeof(T7NativeGraphicsSnapshot) == 64, "T7NativeGraphicsSnapshot ABI changed");
static_assert(sizeof(T7NativeAudioSnapshot) == 40, "T7NativeAudioSnapshot ABI changed");

extern "C" {
// identifier is the 16-byte D3D9 DeviceIdentifier; all zero selects the system default.
// Copied into subsequent Check/Start commands; does not change a running device.
T7NB_EXPORT int32_t T7NB_CALL t7_native_set_output_device(T7NativeSessionHandle session, const uint8_t* identifier, uint32_t length);
T7NB_EXPORT int32_t T7NB_CALL t7_native_get_audio(T7NativeSessionHandle session, T7NativeAudioSnapshot* snapshot);
T7NB_EXPORT int32_t T7NB_CALL t7_native_apply_audio(T7NativeSessionHandle session, const T7NativeAudioSnapshot* settings);
// Apply acknowledges a queued request. Poll until state leaves APPLYING for readback.
// revision must match the last read; CONFLICT never overwrites newer game settings.
T7NB_EXPORT int32_t T7NB_CALL t7_native_get_graphics(T7NativeSessionHandle session, T7NativeGraphicsSnapshot* snapshot);
T7NB_EXPORT int32_t T7NB_CALL t7_native_apply_graphics(T7NativeSessionHandle session, const T7NativeGraphicsSnapshot* settings);
T7NB_EXPORT int32_t T7NB_CALL t7_native_get_abi(uint32_t* version, uint32_t* snapshotSize);
// Create starts one native lifecycle worker.  Release only requests cleanup;
// callers must stop using the opaque handle after it returns.
T7NB_EXPORT int32_t T7NB_CALL t7_native_create(const T7NativeCreateArgs* args, T7NativeSessionHandle* session);
T7NB_EXPORT int32_t T7NB_CALL t7_native_release(T7NativeSessionHandle session);
T7NB_EXPORT int32_t T7NB_CALL t7_native_submit_check(T7NativeSessionHandle session, const T7NativePath* clientPath, uint64_t* operationId);
T7NB_EXPORT int32_t T7NB_CALL t7_native_submit_start(T7NativeSessionHandle session, const T7NativePath* clientPath, uint64_t* operationId);
T7NB_EXPORT int32_t T7NB_CALL t7_native_submit_start_named(T7NativeSessionHandle session, const T7NativeStartArgs* args, uint64_t* operationId);
T7NB_EXPORT int32_t T7NB_CALL t7_native_submit_start_options(T7NativeSessionHandle session, const T7NativeStartOptions* args, uint64_t* operationId);
T7NB_EXPORT int32_t T7NB_CALL t7_native_submit_stop(T7NativeSessionHandle session, uint64_t* operationId);
T7NB_EXPORT int32_t T7NB_CALL t7_native_cancel(T7NativeSessionHandle session, uint64_t operationId);
T7NB_EXPORT int32_t T7NB_CALL t7_native_get_snapshot(T7NativeSessionHandle session, T7NativeSnapshot* snapshot);
T7NB_EXPORT int32_t T7NB_CALL t7_native_get_operation(T7NativeSessionHandle session, uint64_t operationId, T7NativeOperation* operation);
T7NB_EXPORT int32_t T7NB_CALL t7_native_get_error(T7NativeSessionHandle session, uint64_t operationId, char* buffer, uint32_t capacity, uint32_t* required, uint32_t* errorCode);
T7NB_EXPORT int32_t T7NB_CALL t7_native_read_logs(T7NativeSessionHandle session, uint64_t* cursor, uint8_t* buffer, uint32_t capacity, uint32_t* required, uint32_t* flags);
}
