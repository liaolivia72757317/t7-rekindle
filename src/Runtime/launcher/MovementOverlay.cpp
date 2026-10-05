#include "MovementOverlay.h"
#include "MovementHookCode.h"
#include "MovementResources.h"
#include "StartupAnimation.h"
#include "ProcessCleanup.h"
#include "RemoteImage.h"
#include <cstring>
#include <iomanip>
#include <sstream>

namespace t7 {
namespace {
std::string hexValue(uintptr_t value) {
    std::ostringstream output; output << "0x" << std::hex << std::uppercase << value; return output.str();
}
std::string hexBytes(const Bytes& bytes) {
    std::ostringstream output; output << std::hex << std::uppercase << std::setfill('0');
    for (auto byte : bytes) output << std::setw(2) << static_cast<unsigned>(byte);
    return output.str();
}
uint32_t readWord(HANDLE process, uintptr_t address) {
    const auto bytes = readClientMemory(process, address, 4);
    uint32_t value = 0; std::memcpy(&value, bytes.data(), 4); return value;
}
std::string readPath(HANDLE process, uint32_t address) {
    if (!address || address > UINT32_MAX - 24) throw std::runtime_error("movement filename object address invalid");
    const auto length = readWord(process, address + 0x10);
    const auto capacity = readWord(process, address + 0x14);
    if (!length || length > 512 || capacity < length) throw std::runtime_error("movement filename extent invalid");
    const auto data = capacity < 16 ? address : readWord(process, address);
    if (!data || data > UINT32_MAX - length) throw std::runtime_error("movement filename data address invalid");
    const auto bytes = readClientMemory(process, data, length);
    return std::string(bytes.begin(), bytes.end());
}
void writeBuffer(HANDLE process, uintptr_t address, const Bytes& bytes) {
    SIZE_T written = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(address), bytes.data(), bytes.size(), &written)
        || written != bytes.size())
        throw std::runtime_error(errorText("movement XML buffer write"));
    if (readClientMemory(process, address, bytes.size()) != bytes)
        throw std::runtime_error("movement XML buffer readback mismatch");
}
}

MovementOverlay::MovementOverlay(uint32_t features) : features_(features) {}
MovementOverlay::~MovementOverlay() { clear(); }

void MovementOverlay::install(HANDLE process, uintptr_t imageBase,
                              const std::function<void(std::string)>& log, bool skipStartupAnimation) {
    if (!process || !imageBase) throw std::runtime_error("movement overlay requires a client process");
    if (installed()) throw std::runtime_error("movement overlay is already installed");
    if (imageBase > UINT32_MAX - MOVEMENT_HOOK_SITES.back().rva - 5)
        throw std::runtime_error("x86 client image base is outside 32-bit range");
    // Validate every site before writing any code or allocating remote storage.
    for (size_t i = 0; i < MOVEMENT_HOOK_SITES.size(); ++i) {
        const auto& site = MOVEMENT_HOOK_SITES[i];
        const Bytes expected(site.original.begin(), site.original.end());
        const auto observed = readClientMemory(process, imageBase + site.rva, expected.size());
        if (observed != expected)
            throw std::runtime_error(std::string(i == 0 ? "movement resource loader" : "movement XML loader")
                + " signature mismatch at " + hexValue(imageBase + site.rva)
                + "; expected=" + hexBytes(expected) + "; observed=" + hexBytes(observed));
    }
    auto remote = VirtualAllocEx(process, nullptr, MOVEMENT_HOOK_PAGE_SIZE,
                                MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) throw std::runtime_error(errorText("movement overlay allocation"));
    process_ = process; imageBase_ = imageBase; remoteBase_ = reinterpret_cast<uintptr_t>(remote);
    try {
        if (remoteBase_ > UINT32_MAX - MOVEMENT_HOOK_PAGE_SIZE)
            throw std::runtime_error("movement overlay allocation outside x86 range");
        const auto page = buildMovementHooks(static_cast<uint32_t>(remoteBase_), static_cast<uint32_t>(imageBase), skipStartupAnimation);
        writeBuffer(process, remoteBase_, page.bytes);
        DWORD previous = 0;
        if (!VirtualProtectEx(process, remote, page.bytes.size(), PAGE_EXECUTE_READ, &previous)
            || !FlushInstructionCache(process, remote, page.bytes.size()))
            throw std::runtime_error(errorText("movement hook protection/cache"));
        breakpoint_ = page.breakpoint; log_ = log;
        skipStartupAnimation_ = skipStartupAnimation;
        for (size_t i = 0; i < MOVEMENT_HOOK_SITES.size(); ++i) {
            const auto target = static_cast<uint32_t>(imageBase + MOVEMENT_HOOK_SITES[i].rva);
            // Include partial writes in rollback; never free a page still referenced by a detour.
            patchedCount_ = i + 1;
            writeClientMemory(process, target, movementJump(target, page.entries[i]));
        }
        if (log_) log_("Runtime local movement hooks installed; original collision, VFS and disk resources unchanged");
    } catch (...) { cleanupAndRethrow(std::current_exception(), [this] { rollback(); }); }
}

bool MovementOverlay::handleBreakpoint(DWORD threadId, uintptr_t address) {
    if (!installed() || address != breakpoint_) return false;
    HANDLE thread = OpenThread(THREAD_GET_CONTEXT, FALSE, threadId);
    if (!thread) throw std::runtime_error(errorText("movement XML thread"));
    WOW64_CONTEXT context{}; context.ContextFlags = WOW64_CONTEXT_FULL;
    const auto received = Wow64GetThreadContext(thread, &context);
    const auto error = received ? std::string{} : errorText("movement XML context");
    if (!CloseHandle(thread)) throw std::runtime_error(errorText("movement XML thread cleanup"));
    if (!received) throw std::runtime_error(error);
    if (context.Eip != address + 1 || !context.Ebp || context.Ebp > UINT32_MAX - 12
        || !context.Esi || !context.Edi || context.Edi > MOVEMENT_XML_LIMIT
        || context.Esi > UINT32_MAX - context.Edi - MOVEMENT_XML_RESERVE - 1)
        throw std::runtime_error("movement XML frame mismatch");
    const auto path = readPath(process_, readWord(process_, context.Ebp + 8));
    const auto resource = movementResourceForPath(path);
    const auto startupAnimation = skipStartupAnimation_ && isStartupAnimationResourcePath(path);
    if (resource == MovementResource::None && !startupAnimation) throw std::runtime_error("unexpected client XML resource");
    const auto original = readClientMemory(process_, context.Esi, static_cast<size_t>(context.Edi) + 1);
    if (original.back() != 0) throw std::runtime_error("movement XML terminator mismatch");
    const std::string source(original.begin(), original.end() - 1);
    const auto transformed = startupAnimation ? skipStartupAnimation(source) : transformMovementResource(resource, source);
    if (transformed.size() > source.size() + MOVEMENT_XML_RESERVE)
        throw std::runtime_error("movement XML exceeds reserved pool capacity");
    Bytes replacement(transformed.begin(), transformed.end()); replacement.push_back(0);
    // The document's own pool owns this expanded buffer. The original parser may
    // retain or mutate it; no external buffer is freed or reused across documents.
    writeBuffer(process_, context.Esi, replacement);
    if (log_) log_(startupAnimation ? "Startup animation skip branch prepared in memory; original login completion event preserved"
        : "Runtime local movement resource prepared; kind=" + std::to_string(static_cast<int>(resource)));
    return true;
}

void MovementOverlay::rollback() {
    if (!process_) return;
    const auto status = WaitForSingleObject(process_, 0);
    if (status == WAIT_OBJECT_0) {
        // Remote memory is already reclaimed after owned client termination.
        clear(); return;
    }
    if (status != WAIT_TIMEOUT) throw std::runtime_error(errorText("movement rollback process state"));
    // The caller keeps the client stopped while installing or undoing hooks.
    while (patchedCount_) {
        const auto& site = MOVEMENT_HOOK_SITES[patchedCount_ - 1];
        writeClientMemory(process_, imageBase_ + site.rva, Bytes(site.original.begin(), site.original.end()));
        --patchedCount_;
    }
    if (remoteBase_ && !VirtualFreeEx(process_, reinterpret_cast<void*>(remoteBase_), 0, MEM_RELEASE))
        throw std::runtime_error(errorText("movement overlay remote memory release"));
    clear();
}
void MovementOverlay::clear() noexcept {
    process_ = nullptr; imageBase_ = 0; remoteBase_ = 0; breakpoint_ = 0; patchedCount_ = 0; log_ = {};
    skipStartupAnimation_ = false;
}
}
