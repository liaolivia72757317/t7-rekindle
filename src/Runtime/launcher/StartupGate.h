#pragma once
#include "../core/Common.h"
#include <map>
#include <mutex>

namespace t7 {
class StartupGate final {
public:
    static constexpr uint32_t SELECTOR_RVA = 0x000892B0;
    static constexpr uint32_t SERVICE_RVA = 0x024396CC;
    static constexpr uint32_t VTABLE_RVA = 0x01734E64;

    StartupGate() = default;
    StartupGate(const StartupGate&) = delete;
    StartupGate& operator=(const StartupGate&) = delete;
    ~StartupGate();

    void install(HANDLE process, uint32_t imageBase);
    bool handleBreakpoint(DWORD threadId, uintptr_t address);
    uint32_t object() const;
    void release();
    // The debugger must be joined and the owned process terminated first.
    void clear();

private:
    enum class State { Empty, Armed, Holding, Released, Failed };
    mutable std::mutex mutex_;
    HANDLE process_ = nullptr;
    DWORD pid_ = 0;
    uint32_t imageBase_ = 0, object_ = 0;
    State state_ = State::Empty;
    std::map<DWORD, HANDLE> threads_;

    void verifyObject(uint32_t object) const;
};
}
