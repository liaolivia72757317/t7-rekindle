#pragma once

#include "../core/Common.h"
#include <functional>
#include <string>

namespace t7 {

enum class MovementFeature : uint32_t {
    Terrain = 1u << 0,
    Gravity = 1u << 1,
    Jump = 1u << 2,
    Crouch = 1u << 3,
};

constexpr uint32_t operator|(MovementFeature left, MovementFeature right) {
    return static_cast<uint32_t>(left) | static_cast<uint32_t>(right);
}

struct MovementOverlayInfo {
    static constexpr uint32_t kImageBase = 0x00400000;
    static constexpr uintptr_t kResourceOpenRva = 0x00071590;
    static constexpr uintptr_t kEntitySheetLoadRva = 0x00275A70;
    static constexpr uintptr_t kOfflineMoveActionRva = 0x00469EE0;
    static constexpr uintptr_t kOfflineJumpActionRva = 0x00469EA0;
    static constexpr uintptr_t kJumpCrouchExecutorRva = 0x0075D080;
    static constexpr uint32_t kLocalHeroResourceId = 110001;
    static constexpr int32_t kGravityMilli = -10000;
    static constexpr float kWalkSpeed = 5.0f;
    static constexpr float kRunSpeed = 10.0f;
    static constexpr float kMaximumSpeed = 25.0f;
    static constexpr float kAcceleration = 0.0f;
};

class MovementOverlay final {
public:
    explicit MovementOverlay(uint32_t features =
        static_cast<uint32_t>(MovementFeature::Terrain)
        | static_cast<uint32_t>(MovementFeature::Gravity)
        | static_cast<uint32_t>(MovementFeature::Jump)
        | static_cast<uint32_t>(MovementFeature::Crouch));
    MovementOverlay(const MovementOverlay&) = delete;
    MovementOverlay& operator=(const MovementOverlay&) = delete;
    ~MovementOverlay();

    // Install before first resume under the owned debugger. Resource edits use
    // the client's XML memory pool; its VFS, collision assets and disk stay intact.
    void install(HANDLE process, uintptr_t imageBase,
                 const std::function<void(std::string)>& log = {}, bool skipStartupAnimation = false);
    void rollback();
    bool handleBreakpoint(DWORD threadId, uintptr_t address);

    bool installed() const noexcept { return process_ != nullptr && remoteBase_ != 0; }
    uint32_t features() const noexcept { return features_; }
    uintptr_t remoteBase() const noexcept { return remoteBase_; }

private:
    HANDLE process_ = nullptr;
    uintptr_t imageBase_ = 0;
    uintptr_t remoteBase_ = 0;
    uintptr_t breakpoint_ = 0;
    uint32_t features_ = 0;
    size_t patchedCount_ = 0;
    bool skipStartupAnimation_ = false;
    std::function<void(std::string)> log_;

    void clear() noexcept;
};

} // namespace t7
