#pragma once
#include "../core/Common.h"

namespace t7r {
class WorldHookCode;
// Offline movement tuning; not a reconstruction of the server's speed formula.
constexpr float JOG_SPEED = 3.f, RUN_SPEED = 5.f;
constexpr uint32_t RUN_DELAY_MS = 1000;
constexpr uint32_t SPRINT_DATA_OFFSET = 0x100, SPRINT_CODE_OFFSET = 0x3000;
constexpr uint32_t SPRINT_STATES_OFFSET = 0x380;
constexpr uint32_t JUMP_PHYSICS_CODE_OFFSET = 0x3C00, JUMP_PHYSICS_RVA = 0x3D0AAE;
inline const Bytes JUMP_PHYSICS_ORIGINAL{0xFF,0x90,0xD0,0,0,0};
void verifySprintFunctions(HANDLE process, uint32_t imageBase);
Bytes buildSprintHook(uint32_t address, uint32_t imageBase, const DashAttackProfile& dash, bool dynamicProfiles = false);
Bytes buildJumpPhysicsHook(uint32_t address, uint32_t imageBase, uint32_t threadId);
Bytes jumpPhysicsJump(uint32_t address, uint32_t imageBase);
void writeSprintDirections(Bytes& page);
// EAX=GeFightable; preserves registers, sets ZF when the action interrupts running.
void emitRunEligibleState(WorldHookCode& code, uint32_t imageBase);
}
