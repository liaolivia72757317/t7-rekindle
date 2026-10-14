#pragma once
#include "../core/Common.h"
#include <array>

namespace t7 {
// Persistent state must stay above the terrain collector/input scratch at 0x600..0x74F.
constexpr uint32_t ROUND_END_CODE_OFFSET = 0x1F000, ROUND_END_DATA_OFFSET = 0x800;
constexpr uint32_t FINISH_OBSERVER_OFFSET = 0x1F400, LEAVE_OBSERVER_OFFSET = 0x1F800;
constexpr uint32_t LOBBY_RETURN_OFFSET = 0x1FC00, GFX_READ_RVA = 0x3F2B10;
constexpr uint32_t FINISH_RESULT_RVA = 0x6A7B73, NATIVE_LEAVE_RVA = 0x79C790;
constexpr uint32_t LOBBY_IDENTITY_RVA = 0x57A51B, LOBBY_IDENTITY_OFFSET = 0x1F300;
inline constexpr std::array<std::pair<uint32_t, uint32_t>, 2> LOBBY_IDENTITY_SITES{{
    {LOBBY_IDENTITY_RVA, LOBBY_IDENTITY_OFFSET}, {0x57C3E1, 0x1F340}
}};
constexpr uint32_t LOBBY_IDENTITY_DATA_OFFSET = 0x820, SOLO_ROOM_UI_OFFSET = 0x1F900;
constexpr uint32_t NETWORK_HUD_RVA = 0x795493, NETWORK_HUD_OFFSET = 0x1F380;
constexpr uint32_t NETWORK_LATENCY_DATA_OFFSET = 0x830;
inline const Bytes NETWORK_HUD_ORIGINAL{0x53,0x8D,0x45,0xD8,0x50};
struct NetworkLatencyMailbox {
    uint32_t measuredMs = UINT32_MAX, displayedMs = UINT32_MAX, reads = 0;
};
static_assert(sizeof(NetworkLatencyMailbox) == 12, "network latency mailbox layout");
struct LobbyIdentityMailbox {
    uint32_t playerId = 0, uiPlayerId = 0;
    uint32_t createReads = 0, loginReads = 0;
};
static_assert(sizeof(LobbyIdentityMailbox) == 16, "lobby identity mailbox layout");
struct RoundEndMailbox {
    uint64_t epoch = 0;
    uint32_t status = 0; // 0 idle, 1 logout requested, 2 native handoff failed.
    uint32_t result = 0; // Set only when this instance's FINISH_GAME is consumed.
    uint32_t lobbyUiError = 0; // Resource incompatibility survives battle epoch changes.
};
static_assert(sizeof(RoundEndMailbox) == 24, "round end mailbox layout");
void verifyRoundEndFunctions(HANDLE process, uint32_t imageBase);
Bytes buildRoundEndHooks(uint32_t address, uint32_t imageBase, bool customLobby = false);
std::vector<std::pair<uint32_t, Bytes>> roundEndOriginals(uint32_t imageBase, bool customLobby = false);
std::vector<std::pair<uint32_t, Bytes>> roundEndJumps(uint32_t address, uint32_t imageBase, bool customLobby = false);
}
