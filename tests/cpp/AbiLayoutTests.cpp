#include "../../src/Runtime/bridge/T7NativeBridge.h"
#include "../../src/Runtime/launcher/MovementOverlay.h"
#include <cassert>
#include <cstddef>

int main() {
    assert(sizeof(T7NativePath) == 24);
    assert(sizeof(T7NativeCreateArgs) == 24);
    assert(sizeof(T7NativeSnapshot) == 120);
    assert(sizeof(T7NativeOperation) == 32);
    assert(offsetof(T7NativePath, data) == 8);
    assert(offsetof(T7NativeSnapshot, phase) == 56);
    assert(offsetof(T7NativeOperation, operationId) == 8);
    const auto features = static_cast<uint32_t>(t7::MovementFeature::Terrain)
        | static_cast<uint32_t>(t7::MovementFeature::Gravity)
        | static_cast<uint32_t>(t7::MovementFeature::Jump)
        | static_cast<uint32_t>(t7::MovementFeature::Crouch);
    assert(features == 0x0Fu);
    assert(t7::MovementOverlayInfo::kImageBase == 0x00400000u);
    assert(t7::MovementOverlayInfo::kResourceOpenRva == 0x00071590u);
    assert(t7::MovementOverlayInfo::kEntitySheetLoadRva == 0x00275A70u);
    assert(t7::MovementOverlayInfo::kLocalHeroResourceId == 110001u);
    assert(t7::MovementOverlayInfo::kGravityMilli == -10000);
    return 0;
}
