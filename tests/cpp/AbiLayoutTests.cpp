#include <type_traits>
#include "../../src/Runtime/bridge/T7RNativeBridge.h"
#include "../../src/Runtime/launcher/MovementOverlay.h"
#include <cassert>
#include <cstddef>

bool verifyGraphicsSettings();
bool verifyAudioDiagnostics();
bool verifyAudioPlaybackTrace();
bool verifyAudioResourcePaths();
bool verifyOutputDevice();
bool verifyModuleLookup();
bool verifyAp32();
bool verifyClientCode();
bool verifyClientImports();
bool verifyClientAdaptation();
bool verifyCameraInput();
bool verifyDefenseAssist();
bool verifyPingDisplay();
bool verifyRemoteImage();
bool verifyDebugClient();
bool verifyClientWindowMonitor();
bool verifyInputMethodRegistry();
bool verifyInputMethodDebugClient();
bool verifyEndpointStorage();
bool verifyStartupGate();
bool verifyMovementResources();
bool verifyStartupAnimation();
int runDebugClientFixture();
int runInputMethodDebugFixture();

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--client-adaptation-tests") return verifyClientAdaptation() ? 0 : 1;
    if (argc == 2 && std::string(argv[1]) == "--ping-tests") return verifyClientAdaptation() && verifyPingDisplay() ? 0 : 1;
    if (argc == 2 && std::string(argv[1]) == "--audio-playback-tests") return verifyAudioPlaybackTrace() ? 0 : 1;
    if (argc == 2 && std::string(argv[1]) == "--audio-path-tests") return verifyAudioResourcePaths() ? 0 : 1;
    if (argc == 2 && std::string(argv[1]) == "--audio-settings-tests") return verifyGraphicsSettings() ? 0 : 1;
    const auto inputMethodFixture = runInputMethodDebugFixture();
    if (inputMethodFixture >= 0) return inputMethodFixture;
    const auto fixture = runDebugClientFixture();
    if (fixture >= 0) return fixture;
    assert(sizeof(T7RNativePath) == 24);
    assert(sizeof(T7RNativeStartArgs) == 40);
    assert(sizeof(T7RNativeStartOptions) == 48);
    assert(offsetof(T7RNativeStartOptions, flags) == 40);
    assert(T7RNB_START_SKIP_STARTUP_ANIMATION == 1u);
    assert(sizeof(T7RNativeCreateArgs) == 24);
    assert(sizeof(T7RNativeSnapshot) == 120);
    assert(sizeof(T7RNativeOperation) == 32);
    assert(offsetof(T7RNativePath, data) == 8);
    assert(offsetof(T7RNativeSnapshot, phase) == 56);
    assert(offsetof(T7RNativeOperation, operationId) == 8);
    static_assert(std::is_default_constructible_v<t7r::MovementOverlay>);
    static_assert(!std::is_constructible_v<t7r::MovementOverlay, uint32_t>);
    assert(t7r::MovementOverlayInfo::kImageBase == 0x00400000u);
    assert(t7r::MovementOverlayInfo::kResourceOpenRva == 0x00071590u);
    assert(t7r::MovementOverlayInfo::kEntitySheetLoadRva == 0x00275A70u);
    assert(t7r::MovementOverlayInfo::kLocalHeroResourceId == 110001u);
    assert(t7r::MovementOverlayInfo::kGravityMilli == -10000);
    return verifyOutputDevice() && verifyGraphicsSettings() && verifyAudioDiagnostics() && verifyAudioPlaybackTrace() && verifyAudioResourcePaths() && verifyModuleLookup() && verifyAp32() && verifyClientCode() && verifyClientImports()
        && verifyClientAdaptation() && verifyCameraInput() && verifyDefenseAssist() && verifyPingDisplay() && verifyRemoteImage() && verifyInputMethodRegistry() && verifyInputMethodDebugClient()
        && verifyDebugClient() && verifyClientWindowMonitor() && verifyMovementResources()
        && verifyStartupAnimation() && verifyEndpointStorage() && verifyStartupGate() ? 0 : 1;
}
