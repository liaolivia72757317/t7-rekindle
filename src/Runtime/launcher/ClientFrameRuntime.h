#pragma once
#include "GraphicsSettings.h"
#include "AudioSettings.h"
#include "ClientWorld.h"
#include "TitleSettings.h"

namespace t7r {
// Owns the shared frame hook and remote pages. Mailboxes have peer lifetimes;
// no settings category owns movement, world sampling or another category.
class ClientFrameRuntime final {
public:
    void install(HANDLE process, uint32_t imageBase, DWORD threadId, const DashAttackProfile& dash = {},
                 const MovementAnimationProfile& animation = {},
                 const std::vector<CombatProfile>& profiles = {});
    void clear(); // Called after the owned process has exited.
    GraphicsSettings& graphics() { return graphics_; }
    AudioSettings& audio() { return audio_; }
    ClientWorld& world() { return world_; }
    TitleSettings& titles() { return titles_; }
private:
    HANDLE process_ = nullptr;
    GraphicsSettings graphics_;
    AudioSettings audio_;
    ClientWorld world_;
    TitleSettings titles_;
};
}
