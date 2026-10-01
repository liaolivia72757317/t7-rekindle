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

    // Installs an in-memory detour in the x86 client. The detour changes only
    // the next exact online EntSheet request to the original offline EntSheet
    // path; both files are still read by the client's own VFS.
    void install(HANDLE process, uintptr_t imageBase,
                 const std::function<void(std::string)>& log = {});
    void rollback();

    bool installed() const noexcept { return process_ != nullptr && remoteBase_ != 0; }
    uint32_t features() const noexcept { return features_; }
    uintptr_t remoteBase() const noexcept { return remoteBase_; }

private:
    HANDLE process_ = nullptr;
    uintptr_t imageBase_ = 0;
    uintptr_t remoteBase_ = 0;
    uintptr_t target_ = 0;
    uint32_t features_ = 0;
    unsigned char original_[5]{};
    bool targetPatched_ = false;

    void clear() noexcept;
};

} // namespace t7
