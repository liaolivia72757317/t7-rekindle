#include "../../src/Runtime/bridge/T7NativeBridge.h"
#include "../../src/Runtime/launcher/MovementOverlay.h"
#include <cassert>
#include <cstddef>

bool verifyModuleLookup();
bool verifyAp32();
bool verifyClientCode();
bool verifyClientImports();
bool verifyClientAdaptation();
bool verifyRemoteImage();
bool verifyDebugClient();
bool verifyEndpointStorage();
bool verifyMovementResources();
bool verifyStartupAnimation();
int runDebugClientFixture();

int main() {
    const auto fixture = runDebugClientFixture();
    if (fixture >= 0) return fixture;
    assert(sizeof(T7NativePath) == 24);
    assert(sizeof(T7NativeStartArgs) == 40);
    assert(sizeof(T7NativeStartOptions) == 48);
    assert(offsetof(T7NativeStartOptions, flags) == 40);
    assert(T7NB_START_SKIP_STARTUP_ANIMATION == 1u);
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
    return verifyModuleLookup() && verifyAp32() && verifyClientCode() && verifyClientImports()
        && verifyClientAdaptation() && verifyRemoteImage() && verifyDebugClient() && verifyMovementResources()
        && verifyStartupAnimation() && verifyEndpointStorage() ? 0 : 1;
}
