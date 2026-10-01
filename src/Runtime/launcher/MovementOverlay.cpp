#include "MovementOverlay.h"

#include <cstring>
#include <sstream>
#include <vector>

namespace t7 {
namespace {

constexpr uint32_t OVERLAY_MAGIC = 0x544D3754; // T7MT
constexpr uint32_t OVERLAY_VERSION = 1;
constexpr size_t PATCH_SIZE = 5;
constexpr size_t PAGE_SIZE = 4096;
constexpr size_t STATE_OFFSET = 0x000;
constexpr size_t REPLACEMENT_STRING_OFFSET = 0x180;
constexpr size_t REPLACEMENT_DATA_OFFSET = 0x1A0;
constexpr size_t TRAMPOLINE_OFFSET = 0x300;
constexpr size_t HOOK_OFFSET = 0x380;

#pragma pack(push, 1)
struct RemoteOverlayState {
    uint32_t magic;
    uint32_t version;
    uint32_t features;
    uint32_t imageBase;
    uint32_t resourceOpenRva;
    uint32_t entitySheetLoadRva;
    uint32_t offlineMoveActionRva;
    uint32_t offlineJumpActionRva;
    uint32_t jumpCrouchExecutorRva;
    uint32_t localHeroResourceId;
    int32_t gravityMilli;
    float walkSpeed;
    float runSpeed;
    float maximumSpeed;
    float acceleration;
    uint32_t routeBudget;
    uint32_t routeState;
    uint32_t replacementString;
    uint32_t replacementData;
    uint32_t replacementLength;
    uint32_t trampoline;
    uint32_t hook;
    unsigned char onlinePath[64];
    uint32_t onlineLength;
};
#pragma pack(pop)

static_assert(sizeof(RemoteOverlayState) < REPLACEMENT_STRING_OFFSET,
              "movement overlay state overlaps string storage");

std::string hexValue(uintptr_t value) {
    std::ostringstream output;
    output << "0x" << std::hex << std::uppercase << value;
    return output.str();
}

void readRemote(HANDLE process, uintptr_t address, void* destination, size_t size) {
    SIZE_T actual = 0;
    if (!ReadProcessMemory(process, reinterpret_cast<void*>(address), destination, size, &actual)
        || actual != size) {
        throw std::runtime_error("movement overlay read failed at " + hexValue(address)
            + " error=" + std::to_string(GetLastError()));
    }
}

void writeRemote(HANDLE process, uintptr_t address, const void* source, size_t size) {
    SIZE_T actual = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(address), source, size, &actual)
        || actual != size) {
        throw std::runtime_error("movement overlay write failed at " + hexValue(address)
            + " error=" + std::to_string(GetLastError()));
    }
}

void append32(std::vector<unsigned char>& bytes, uint32_t value) {
    bytes.push_back(static_cast<unsigned char>(value));
    bytes.push_back(static_cast<unsigned char>(value >> 8));
    bytes.push_back(static_cast<unsigned char>(value >> 16));
    bytes.push_back(static_cast<unsigned char>(value >> 24));
}

void put32(std::vector<unsigned char>& bytes, size_t offset, uint32_t value) {
    if (offset + sizeof(uint32_t) > bytes.size())
        throw std::runtime_error("movement overlay code overflow");
    memcpy(bytes.data() + offset, &value, sizeof(value));
}

int32_t relativeJump(uintptr_t source, uintptr_t destination) {
    const auto displacement = static_cast<int64_t>(destination)
        - static_cast<int64_t>(source + PATCH_SIZE);
    if (displacement < INT32_MIN || displacement > INT32_MAX)
        throw std::runtime_error("movement overlay jump is outside rel32 range");
    return static_cast<int32_t>(displacement);
}

std::vector<unsigned char> encodedPath(const wchar_t* path) {
    auto count = WideCharToMultiByte(CP_ACP, WC_NO_BEST_FIT_CHARS, path, -1,
                                     nullptr, 0, nullptr, nullptr);
    if (count <= 1) throw std::runtime_error("movement overlay path encoding failed");
    std::vector<unsigned char> result(static_cast<size_t>(count));
    if (WideCharToMultiByte(CP_ACP, WC_NO_BEST_FIT_CHARS, path, -1,
                            reinterpret_cast<char*>(result.data()), count,
                            nullptr, nullptr) != count)
        throw std::runtime_error("movement overlay path encoding failed");
    result.pop_back();
    return result;
}

std::vector<unsigned char> buildPage(uintptr_t remoteBase,
                                     const RemoteOverlayState& state,
                                     const std::vector<unsigned char>& replacement,
                                     const unsigned char* original) {
    std::vector<unsigned char> page(PAGE_SIZE, 0);
    memcpy(page.data() + STATE_OFFSET, &state, sizeof(state));

    // The client uses the MSVC x86 basic_string layout: inline storage at 0,
    // size at 0x10 and capacity at 0x14. The replacement path is external.
    const auto replacementObject = static_cast<uint32_t>(remoteBase + REPLACEMENT_STRING_OFFSET);
    const auto replacementBuffer = static_cast<uint32_t>(remoteBase + REPLACEMENT_DATA_OFFSET);
    memcpy(page.data() + REPLACEMENT_STRING_OFFSET, &replacementBuffer, sizeof(replacementBuffer));
    const auto replacementLength = static_cast<uint32_t>(replacement.size());
    memcpy(page.data() + REPLACEMENT_STRING_OFFSET + 0x10, &replacementLength, sizeof(replacementLength));
    memcpy(page.data() + REPLACEMENT_STRING_OFFSET + 0x14, &replacementLength, sizeof(replacementLength));
    memcpy(page.data() + REPLACEMENT_DATA_OFFSET, replacement.data(), replacement.size());
    page[REPLACEMENT_DATA_OFFSET + replacement.size()] = 0;

    std::vector<unsigned char> hook;
    hook.push_back(0x60); // pushad
    hook.push_back(0xBB); append32(hook, static_cast<uint32_t>(remoteBase + STATE_OFFSET)); // mov ebx,state
    hook.push_back(0x83); hook.push_back(0x7B);
    hook.push_back(static_cast<unsigned char>(offsetof(RemoteOverlayState, routeBudget)));
    hook.push_back(0x00); // cmp dword ptr [ebx+routeBudget],0
    const auto budgetJump = hook.size(); hook.push_back(0x0F); hook.push_back(0x84); const auto budgetDisp = hook.size(); append32(hook, 0);
    hook.push_back(0x8B); hook.push_back(0x54); hook.push_back(0x24); hook.push_back(0x24); // mov edx,[esp+24h]
    hook.push_back(0x85); hook.push_back(0xD2); // test edx,edx
    const auto nullJump = hook.size(); hook.push_back(0x0F); hook.push_back(0x84); const auto nullDisp = hook.size(); append32(hook, 0);
    hook.push_back(0x8B); hook.push_back(0x44); hook.push_back(0x24); hook.push_back(0x28); // mov eax,[esp+28h]
    hook.push_back(0x85); hook.push_back(0xC0); // test eax,eax
    const auto flagJump = hook.size(); hook.push_back(0x0F); hook.push_back(0x85); const auto flagDisp = hook.size(); append32(hook, 0);
    hook.push_back(0x8B); hook.push_back(0x42); hook.push_back(0x10); // size
    hook.push_back(0x3D); append32(hook, state.onlineLength);
    const auto lengthJump = hook.size(); hook.push_back(0x0F); hook.push_back(0x85); const auto lengthDisp = hook.size(); append32(hook, 0);
    hook.push_back(0x8B); hook.push_back(0x72); hook.push_back(0x14); // capacity
    hook.push_back(0x83); hook.push_back(0xFE); hook.push_back(0x0F);
    const auto inlineJump = hook.size(); hook.push_back(0x0F); hook.push_back(0x86); const auto inlineDisp = hook.size(); append32(hook, 0);
    hook.push_back(0x8B); hook.push_back(0x3A); // external data pointer
    hook.push_back(0xE9); const auto externalJump = hook.size(); append32(hook, 0);
    // For the short-string case the inline data begins at the object address.
    const auto inlineData = hook.size();
    hook.push_back(0x8B); hook.push_back(0xFA); // mov edi,edx
    const auto compareData = hook.size();
    hook.push_back(0xBE); append32(hook, static_cast<uint32_t>(remoteBase + STATE_OFFSET + offsetof(RemoteOverlayState, onlinePath)));
    hook.push_back(0xB9); append32(hook, state.onlineLength);
    hook.push_back(0xF3); hook.push_back(0xA6); // repe cmpsb
    const auto compareSkip = hook.size(); hook.push_back(0x0F); hook.push_back(0x85); const auto compareDisp = hook.size(); append32(hook, 0);
    hook.push_back(0xC7); hook.push_back(0x84); hook.push_back(0x24); append32(hook, 0x24); append32(hook, replacementObject);
    hook.push_back(0xC7); hook.push_back(0x83); append32(hook, offsetof(RemoteOverlayState, routeBudget)); append32(hook, 0);
    hook.push_back(0xC7); hook.push_back(0x83); append32(hook, offsetof(RemoteOverlayState, routeState)); append32(hook, 1);
    const auto skip = hook.size();
    hook.push_back(0x61); // popad
    hook.push_back(0xE9); const auto trampolineDisp = hook.size(); append32(hook, 0);

    const auto displacement = [remoteBase](size_t field, size_t destination) {
        const auto source = remoteBase + HOOK_OFFSET + field;
        const auto target = remoteBase + HOOK_OFFSET + destination;
        return static_cast<uint32_t>(static_cast<int64_t>(target)
            - static_cast<int64_t>(source + sizeof(uint32_t)));
    };
    put32(hook, budgetDisp, displacement(budgetDisp, skip));
    put32(hook, nullDisp, displacement(nullDisp, skip));
    put32(hook, flagDisp, displacement(flagDisp, skip));
    put32(hook, lengthDisp, displacement(lengthDisp, skip));
    put32(hook, inlineDisp, displacement(inlineDisp, inlineData));
    put32(hook, externalJump, displacement(externalJump, compareData));
    put32(hook, compareDisp, displacement(compareDisp, skip));
    put32(hook, trampolineDisp, static_cast<uint32_t>(static_cast<int64_t>(
        remoteBase + TRAMPOLINE_OFFSET) - static_cast<int64_t>(
            remoteBase + HOOK_OFFSET + trampolineDisp + sizeof(uint32_t))));
    (void)budgetJump; (void)nullJump; (void)flagJump; (void)lengthJump; (void)inlineJump;
    (void)compareData; (void)compareSkip;
    if (hook.size() > PAGE_SIZE - HOOK_OFFSET)
        throw std::runtime_error("movement overlay hook exceeds remote page");
    memcpy(page.data() + HOOK_OFFSET, hook.data(), hook.size());

    std::vector<unsigned char> trampoline(original, original + PATCH_SIZE);
    trampoline.push_back(0xE9);
    const auto returnAddress = static_cast<uintptr_t>(state.imageBase) + state.resourceOpenRva + PATCH_SIZE;
    const auto returnDisp = static_cast<uint32_t>(static_cast<int64_t>(returnAddress)
        - static_cast<int64_t>(remoteBase + TRAMPOLINE_OFFSET + PATCH_SIZE + sizeof(uint32_t)));
    append32(trampoline, returnDisp);
    memcpy(page.data() + TRAMPOLINE_OFFSET, trampoline.data(), trampoline.size());
    return page;
}

} // namespace

MovementOverlay::MovementOverlay(uint32_t features) : features_(features) {}
MovementOverlay::~MovementOverlay() { clear(); }

void MovementOverlay::install(HANDLE process, uintptr_t imageBase,
                              const std::function<void(std::string)>& log) {
    if (!process || !imageBase) throw std::runtime_error("movement overlay requires a client process");
    if (installed()) throw std::runtime_error("movement overlay is already installed");
    if (imageBase > UINT32_MAX) throw std::runtime_error("x86 client image base is outside 32-bit range");

    const auto target = imageBase + MovementOverlayInfo::kResourceOpenRva;
    unsigned char observed[PATCH_SIZE]{};
    readRemote(process, target, observed, sizeof(observed));
    static constexpr unsigned char expected[PATCH_SIZE] = {0x55, 0x8B, 0xEC, 0x6A, 0xFF};
    if (memcmp(observed, expected, sizeof(expected)) != 0)
        throw std::runtime_error("movement resource loader signature mismatch at " + hexValue(target));

    auto remote = VirtualAllocEx(process, nullptr, PAGE_SIZE,
                                  MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!remote) throw std::runtime_error(errorText("movement overlay allocation"));
    const auto remoteAddress = reinterpret_cast<uintptr_t>(remote);
    bool patchWritten = false;
    try {
        if (remoteAddress > UINT32_MAX) throw std::runtime_error("movement overlay allocation outside x86 range");
        const auto online = encodedPath(L"../Data/EntSheet/步兵.esf");
        const auto replacement = encodedPath(L"../Data/EntSheet/步兵_离线.esf");
        if (online.size() >= 64 || replacement.size() >= PAGE_SIZE - REPLACEMENT_DATA_OFFSET - 1)
            throw std::runtime_error("movement overlay path is too long");

        RemoteOverlayState state{};
        state.magic = OVERLAY_MAGIC; state.version = OVERLAY_VERSION; state.features = features_;
        state.imageBase = static_cast<uint32_t>(imageBase);
        state.resourceOpenRva = static_cast<uint32_t>(MovementOverlayInfo::kResourceOpenRva);
        state.entitySheetLoadRva = static_cast<uint32_t>(MovementOverlayInfo::kEntitySheetLoadRva);
        state.offlineMoveActionRva = static_cast<uint32_t>(MovementOverlayInfo::kOfflineMoveActionRva);
        state.offlineJumpActionRva = static_cast<uint32_t>(MovementOverlayInfo::kOfflineJumpActionRva);
        state.jumpCrouchExecutorRva = static_cast<uint32_t>(MovementOverlayInfo::kJumpCrouchExecutorRva);
        state.localHeroResourceId = MovementOverlayInfo::kLocalHeroResourceId;
        state.gravityMilli = MovementOverlayInfo::kGravityMilli;
        state.walkSpeed = MovementOverlayInfo::kWalkSpeed; state.runSpeed = MovementOverlayInfo::kRunSpeed;
        state.maximumSpeed = MovementOverlayInfo::kMaximumSpeed; state.acceleration = MovementOverlayInfo::kAcceleration;
        state.routeBudget = 1; state.replacementString = static_cast<uint32_t>(remoteAddress + REPLACEMENT_STRING_OFFSET);
        state.replacementData = static_cast<uint32_t>(remoteAddress + REPLACEMENT_DATA_OFFSET);
        state.replacementLength = static_cast<uint32_t>(replacement.size());
        state.trampoline = static_cast<uint32_t>(remoteAddress + TRAMPOLINE_OFFSET);
        state.hook = static_cast<uint32_t>(remoteAddress + HOOK_OFFSET);
        state.onlineLength = static_cast<uint32_t>(online.size());
        memcpy(state.onlinePath, online.data(), state.onlineLength);

        auto page = buildPage(remoteAddress, state, replacement, observed);
        writeRemote(process, remoteAddress, page.data(), page.size());
        RemoteOverlayState checked{}; readRemote(process, remoteAddress, &checked, sizeof(checked));
        if (checked.magic != OVERLAY_MAGIC || checked.version != OVERLAY_VERSION
            || checked.features != features_ || checked.routeBudget != 1)
            throw std::runtime_error("movement overlay descriptor readback mismatch");

        unsigned char patch[PATCH_SIZE] = {0xE9};
        const auto displacement = relativeJump(target, remoteAddress + HOOK_OFFSET);
        memcpy(patch + 1, &displacement, sizeof(displacement));
        DWORD oldProtection = 0;
        if (!VirtualProtectEx(process, reinterpret_cast<void*>(target), PATCH_SIZE,
                              PAGE_EXECUTE_READWRITE, &oldProtection))
            throw std::runtime_error(errorText("movement overlay target protection"));
        try {
            writeRemote(process, target, patch, sizeof(patch));
            patchWritten = true;
            if (!FlushInstructionCache(process, reinterpret_cast<void*>(target), PATCH_SIZE))
                throw std::runtime_error(errorText("movement overlay instruction flush"));
        } catch (...) {
            DWORD ignored = 0;
            VirtualProtectEx(process, reinterpret_cast<void*>(target), PATCH_SIZE, oldProtection, &ignored);
            throw;
        }
        DWORD ignored = 0;
        if (!VirtualProtectEx(process, reinterpret_cast<void*>(target), PATCH_SIZE, oldProtection, &ignored))
            throw std::runtime_error(errorText("movement overlay target protection restore"));
        unsigned char patched[PATCH_SIZE]{}; readRemote(process, target, patched, sizeof(patched));
        if (memcmp(patch, patched, sizeof(patch)) != 0)
            throw std::runtime_error("movement overlay target readback mismatch");

        process_ = process; imageBase_ = imageBase; remoteBase_ = remoteAddress; target_ = target;
        memcpy(original_, observed, sizeof(original_)); targetPatched_ = true;
        if (log) log("Runtime movement overlay installed in client memory; original EntSheet and VFS remain unchanged");
    } catch (...) {
        if (patchWritten) {
            DWORD protection = 0;
            if (VirtualProtectEx(process, reinterpret_cast<void*>(target), PATCH_SIZE,
                                 PAGE_EXECUTE_READWRITE, &protection)) {
                SIZE_T restored = 0;
                WriteProcessMemory(process, reinterpret_cast<void*>(target), observed,
                                   PATCH_SIZE, &restored);
                FlushInstructionCache(process, reinterpret_cast<void*>(target), PATCH_SIZE);
                DWORD ignored = 0;
                VirtualProtectEx(process, reinterpret_cast<void*>(target), PATCH_SIZE, protection, &ignored);
            }
        }
        VirtualFreeEx(process, remote, 0, MEM_RELEASE);
        throw;
    }
}

void MovementOverlay::rollback() {
    if (!process_) return;
    if (WaitForSingleObject(process_, 0) == WAIT_OBJECT_0) {
        // The operating system reclaims the remote page with the exited
        // process.  Avoid a second cross-process free against a torn-down
        // address space and always clear the local ownership record.
        clear();
        return;
    }
    if (targetPatched_ && target_) {
        DWORD oldProtection = 0;
        if (!VirtualProtectEx(process_, reinterpret_cast<void*>(target_), PATCH_SIZE,
                              PAGE_EXECUTE_READWRITE, &oldProtection))
            throw std::runtime_error(errorText("movement overlay rollback protection"));
        writeRemote(process_, target_, original_, sizeof(original_));
        if (!FlushInstructionCache(process_, reinterpret_cast<void*>(target_), PATCH_SIZE))
            throw std::runtime_error(errorText("movement overlay rollback flush"));
        DWORD ignored = 0;
        if (!VirtualProtectEx(process_, reinterpret_cast<void*>(target_), PATCH_SIZE, oldProtection, &ignored))
            throw std::runtime_error(errorText("movement overlay rollback protection restore"));
    }
    if (remoteBase_ && !VirtualFreeEx(process_, reinterpret_cast<void*>(remoteBase_), 0, MEM_RELEASE))
        throw std::runtime_error(errorText("movement overlay remote memory release"));
    clear();
}

void MovementOverlay::clear() noexcept {
    process_ = nullptr; imageBase_ = 0; remoteBase_ = 0; target_ = 0; targetPatched_ = false;
    memset(original_, 0, sizeof(original_));
}

} // namespace t7
