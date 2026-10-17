#pragma once
#include <stdint.h>

#define T7R_RUNTIME_ABI_VERSION 1
#define T7R_BATTLE_CONFIG_ABI_VERSION 2
#define T7R_BATTLE_MAX_ACTORS 60u

#ifdef __cplusplus
extern "C" {
#endif

uint32_t t7r_runtime_abi_version(void);

/* Gateway handles are process-local IDs, not pointers. Release joins all workers.
 * Buffers are borrowed for the duration of a call only. Calls on one handle are
 * serialized internally. Snapshot fields are fixed-width, naturally aligned.
 * This is an additive, private ABI; existing NativeBridge ABI 1 is unchanged. */
enum T7RGatewayStatus {
    T7RGW_OK = 0, T7RGW_INVALID_ARGUMENT = 1, T7RGW_INVALID_HANDLE = 2,
    T7RGW_INVALID_STATE = 3, T7RGW_BUFFER_TOO_SMALL = 4, T7RGW_EMPTY = 5,
    T7RGW_STALE = 6, T7RGW_INTERNAL_ERROR = 7,
    /* Request not committed: retry the exact request, bounded by the caller. */
    T7RGW_BUSY = 8
};
typedef struct T7RGatewaySnapshot {
    uint32_t abiVersion, structSize;
    uint64_t epoch;
    uint32_t state; /* 0 stopped, 1 running, 2 failed */
    uint16_t loginPort, logicPort, instancePort, reserved;
    uint32_t activeConnections, pendingRequests, connectionErrors;
    uint32_t rulesVersion, resourceVersion, mapId, reserved2;
} T7RGatewaySnapshot;

int32_t t7r_gateway_create(const uint8_t* resourceJson, uint32_t length, uint64_t* handle);
/* Reads original resources and maintains an independent writable cache in-process.
 * UTF-8 JSON: clientDirectory, cacheDirectory, rulesVersion, resourceVersion,
 * clientVersion. Error buffer capacity >= 4096; errorLength excludes terminator.
 * On failure handle/cacheHit are zero. Success starts no worker or listener. */
int32_t t7r_gateway_create_local(const uint8_t* requestJson, uint32_t length,
    uint64_t* handle, uint32_t* cacheHit, uint8_t* error, uint32_t capacity, uint32_t* errorLength);
int32_t t7r_gateway_start(uint64_t handle, uint64_t epoch);
int32_t t7r_gateway_stop(uint64_t handle);
int32_t t7r_gateway_release(uint64_t handle);
int32_t t7r_gateway_snapshot(uint64_t handle, T7RGatewaySnapshot* output);
/* Read-only copy of the validated bundle owned by this gateway. null/0 is a
 * size probe returning BUFFER_TOO_SMALL; required excludes a terminator. */
int32_t t7r_gateway_resource_json(uint64_t handle, uint8_t* buffer, uint32_t capacity, uint32_t* required);
/* Size probe: null/0 buffer returns BUFFER_TOO_SMALL without dequeueing.
 * required excludes a terminator. EMPTY means no request. Payload is UTF-8 JSON.
 * Requests are at most 4096 bytes; responses are at most 1 MiB. */
int32_t t7r_gateway_read_request(uint64_t handle, uint8_t* buffer, uint32_t capacity, uint32_t* required);
int32_t t7r_gateway_complete(uint64_t handle, const uint8_t* responseJson, uint32_t length);
int32_t t7r_gateway_error(uint64_t handle, uint8_t* buffer, uint32_t capacity, uint32_t* required);

typedef struct T7RGatewayTitleRequest {
    uint32_t abiVersion, structSize;
    uint64_t requestId, epoch;
    uint32_t titleId, reserved;
} T7RGatewayTitleRequest;
/* Title ownership comes from the platform; Rust encodes USER_INFO responses and
 * holds one pending equip request until publication, timeout or disconnect. */
int32_t t7r_gateway_publish_titles(uint64_t handle, uint64_t epoch, const uint8_t* json,
    uint32_t length, uint64_t requestId, uint32_t success);
int32_t t7r_gateway_title_request(uint64_t handle, T7RGatewayTitleRequest* output);

/* Private match ingress, scoped to one running gateway handle. The battle
 * epoch increases on every rematch; stop joins its owner before reuse.
 * This ingress does not switch the production launcher path. */
typedef struct T7RBattleEquipment {
    uint32_t weaponId;
    uint8_t slot, reserved[3];
} T7RBattleEquipment;
typedef struct T7RBattleMember {
    uint32_t actorId;
    uint16_t clientRid;
    uint8_t camp, kind; /* 1 human, 2 bot */
    uint32_t heroId, weaponId, equipmentCount;
    T7RBattleEquipment equipment[4];
} T7RBattleMember;
typedef struct T7RBattleConfig {
    uint32_t abiVersion, structSize;
    uint64_t epoch, startedAtMs, durationMs;
    uint32_t killTarget, seed;
    uint64_t sourceId;
    uint32_t memberCount, reserved;
    T7RBattleMember members[T7R_BATTLE_MAX_ACTORS];
} T7RBattleConfig;

int32_t t7r_gateway_battle_start(uint64_t handle, const T7RBattleConfig* config);
/* Stage a match for instance loading; GAME activation starts its clock. */
int32_t t7r_gateway_battle_start_staged(uint64_t handle, const T7RBattleConfig* config);
/* Monotonic match-relative clock (startedAtMs + elapsed); staged matches
 * return T7RGW_EMPTY until GAME. Used to anchor native GetTickCount64 evidence. */
int32_t t7r_gateway_battle_clock(uint64_t handle, uint64_t* clockMs);
/* Map match timestamps to original-client instance elapsed milliseconds:
 * wireAt = matchAt + offsetMs. The first native instance sample binds this
 * value; identical retries are idempotent, a changed value is rejected. */
int32_t t7r_gateway_battle_bind_client_clock(uint64_t handle, uint64_t epoch, int64_t offsetMs);
/* Original-server timestamps use native GetTickCount64, not instance elapsed
 * time: wireServerAt = matchAt + offsetMs. Bound from the same first sample. */
int32_t t7r_gateway_battle_bind_server_clock(uint64_t handle, uint64_t epoch, int64_t offsetMs);
/* kind 6 WorldSample, kind 11 PlayerIntent, or kind 14 ContactBatch (v1/v2/v3),
 * versioned BattleFrame payloads.
 * All buffers are borrowed for the duration of the call. */
int32_t t7r_gateway_battle_submit(uint64_t handle, uint32_t kind, const uint8_t* payload, uint32_t length);
/* Native evidence uses the bound source and match clock. worldBatchId must be
 * the most recently accepted WorldSample; contact batches advance only when
 * the native contactBatchId advances. Input is copied before return. */
typedef struct T7RBattleMotion {
    uint32_t abiVersion, structSize;
    uint64_t epoch, sourceId, batchId, worldBatchId, sampledAtMs;
    uint32_t actorId;
    uint16_t clientRid;
    uint8_t reserved[2];
    uint64_t entity;
    double position[3], heading;
    uint64_t attemptedForwardAtMs; /* Zero means no observed forward attempt. */
} T7RBattleMotion;
typedef struct T7RBattleContact {
    uint32_t actorId, targetId;
    uint16_t clientRid, targetClientRid; /* Target RID zero for scene geometry. */
    uint64_t sourceEntity, targetEntity;
    uint16_t sequence;
    uint8_t targetIntent; /* 0 none; 1-4 directional attack, 5-8 guard, 9 shield, 10 dash */
    uint8_t targetPhase; /* 0 idle, 1 prepare, 2 hold, 3 strike, 4 recovery,
                          * 5 weapon change, 6 parried, 7 terrain, 8 parry success, 9 hurt */
    uint64_t phaseAtMs;
    double targetPhaseAtMs, occurredAtMs, along;
    uint32_t defenceWeaponId;
    uint8_t region; /* 0 scene, 1 head, 2 torso, 3 legs */
    int8_t bodyIndex, material;
    uint8_t flags; /* bit 0 velocityReady, 1 airborne, 2 targetAirborne */
    double sourcePosition[3], targetPosition[3], sourceVelocity[3], targetVelocity[3];
    double weaponVelocity[3], position[3], normal[3], direction[3];
    double sourceHeading, targetHeading;
} T7RBattleContact;
typedef struct T7RBattleContacts {
    uint32_t abiVersion, structSize;
    uint64_t epoch, sourceId, batchId, worldBatchId, throughAtMs;
    uint32_t dropped, count;
    const T7RBattleContact* events;
} T7RBattleContacts;
int32_t t7r_gateway_battle_submit_motion(uint64_t handle, const T7RBattleMotion* sample);
int32_t t7r_gateway_battle_submit_contacts(uint64_t handle, const T7RBattleContacts* sample);
/* Projects and enqueues one complete authoritative output batch. EMPTY means
 * none is pending; failures retain the batch for retry or match shutdown. */
int32_t t7r_gateway_battle_dispatch_next(uint64_t handle);
/* Outputs 1 only after this match has sent logout and confirmed room exit or
 * instance disconnect. No staged or prior-match state is inherited. */
int32_t t7r_gateway_battle_returned_to_room(uint64_t handle, uint32_t* returned);
/* Outputs 1 only before MATCH is requested, instance is bound, or GAME begins.
 * The launcher may refresh its staged configuration only in this window. */
int32_t t7r_gateway_battle_preparation_open(uint64_t handle, uint32_t* open);
int32_t t7r_gateway_battle_reserve_rearm(uint64_t handle, uint32_t* reserved);
int32_t t7r_gateway_battle_stop(uint64_t handle);
/* Private local map selection; no active battle may remain attached. */
int32_t t7r_gateway_select_map(uint64_t handle, uint32_t mapId);

#ifdef __cplusplus
}
#endif
