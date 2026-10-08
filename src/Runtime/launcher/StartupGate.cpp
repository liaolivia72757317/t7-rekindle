#include "StartupGate.h"
#include "RemoteImage.h"
#include "ProcessCleanup.h"
#include <cstring>

namespace t7 {
namespace {
Bytes signature(uint32_t base, bool armed) {
    Bytes bytes{0x55,0x8B,0xEC,0x6A,0xFF,0x68,0,0,0,0,0x64,0xA1,0,0,0,0};
    const auto handler = base + 0x012E7F78;
    std::memcpy(bytes.data() + 6, &handler, sizeof(handler));
    if (armed) bytes[0] = 0xCC;
    return bytes;
}
uint32_t readWord(HANDLE process, uintptr_t address) {
    const auto bytes = readClientMemory(process, address, sizeof(uint32_t));
    uint32_t value = 0; std::memcpy(&value, bytes.data(), sizeof(value)); return value;
}
void closeThread(HANDLE& thread) {
    if (thread && !CloseHandle(thread)) throw std::runtime_error(errorText("startup gate thread cleanup"));
    thread = nullptr;
}
void rewindThread(HANDLE thread, WOW64_CONTEXT context, uint32_t address) {
    context.Eip = address;
    if (!Wow64SetThreadContext(thread, &context)) throw std::runtime_error(errorText("startup gate context restore"));
    WOW64_CONTEXT checked{}; checked.ContextFlags = WOW64_CONTEXT_CONTROL | WOW64_CONTEXT_INTEGER;
    if (!Wow64GetThreadContext(thread, &checked) || checked.Eip != address
        || checked.Esp != context.Esp || checked.Ecx != context.Ecx)
        throw std::runtime_error("startup gate context readback mismatch");
}
void verifyVector(HANDLE process, uint32_t address, uint32_t size) {
    const auto bytes = readClientMemory(process, address, 12);
    uint32_t fields[3]{}; std::memcpy(fields, bytes.data(), sizeof(fields));
    if (!fields[0] || fields[0] > UINT32_MAX - size || fields[1] != fields[0] + size || fields[2] != fields[1])
        throw std::runtime_error("startup gate requires published endpoint vectors");
}
}

StartupGate::~StartupGate() {
    // Never resume a failed startup during stack unwinding; DebugClient owns termination.
    for (const auto& entry : threads_) if (entry.second) CloseHandle(entry.second);
}
void StartupGate::install(HANDLE process, uint32_t imageBase) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (state_ != State::Empty) throw std::runtime_error("startup gate is already installed");
    BOOL wow64 = FALSE;
    if (!process || !imageBase || imageBase > UINT32_MAX - SERVICE_RVA - 4
        || !IsWow64Process(process, &wow64) || !wow64)
        throw std::runtime_error("startup gate requires a supported x86 image");
    const auto expected = signature(imageBase, false);
    if (readClientMemory(process, imageBase + SELECTOR_RVA, expected.size()) != expected)
        throw std::runtime_error("startup selector signature mismatch");
    const auto pid = GetProcessId(process);
    if (!pid) throw std::runtime_error(errorText("startup gate process identity"));
    process_ = process; imageBase_ = imageBase; pid_ = pid;
    try {
        writeClientMemory(process_, imageBase_ + SELECTOR_RVA, {0xCC});
        state_ = State::Armed;
    } catch (...) { state_ = State::Failed; throw; }
}
void StartupGate::verifyObject(uint32_t object) const {
    if (!object || object > UINT32_MAX - 0x98
        || readWord(process_, imageBase_ + SERVICE_RVA) != object
        || readWord(process_, object) != imageBase_ + VTABLE_RVA
        || (object_ && object != object_))
        throw std::runtime_error("startup gate network object mismatch");
}
bool StartupGate::handleBreakpoint(DWORD threadId, uintptr_t address) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!process_ || address != imageBase_ + SELECTOR_RVA) return false;
    HANDLE thread = nullptr;
    try {
        if (state_ == State::Failed) throw std::runtime_error("startup gate has failed");
        const auto expected = signature(imageBase_, state_ != State::Released);
        if (readClientMemory(process_, address, expected.size()) != expected)
            throw std::runtime_error("startup gate breakpoint signature changed");
        thread = OpenThread(THREAD_GET_CONTEXT | THREAD_SET_CONTEXT | THREAD_SUSPEND_RESUME
                            | THREAD_QUERY_INFORMATION, FALSE, threadId);
        if (!thread) throw std::runtime_error(errorText("startup gate thread"));
        if (GetProcessIdOfThread(thread) != pid_) throw std::runtime_error("startup gate thread ownership mismatch");
        WOW64_CONTEXT context{}; context.ContextFlags = WOW64_CONTEXT_CONTROL | WOW64_CONTEXT_INTEGER;
        if (!Wow64GetThreadContext(thread, &context) || context.Eip != address + 1)
            throw std::runtime_error("startup gate instruction pointer mismatch");
        verifyObject(context.Ecx);
        if (state_ == State::Released) {
            // A second thread can have a pending INT3 event when the entry is restored.
            rewindThread(thread, context, static_cast<uint32_t>(address));
            closeThread(thread);
            return true;
        }
        if (!threads_.emplace(threadId, thread).second) throw std::runtime_error("duplicate startup gate thread");
        const auto owned = thread; thread = nullptr;
        const auto previous = Wow64SuspendThread(owned);
        if (previous == static_cast<DWORD>(-1)) throw std::runtime_error(errorText("startup gate thread suspension"));
        if (previous != 0) throw std::runtime_error("startup gate thread was already suspended");
        rewindThread(owned, context, static_cast<uint32_t>(address));
        object_ = context.Ecx; state_ = State::Holding;
        return true;
    } catch (...) {
        state_ = State::Failed;
        cleanupAndRethrow(std::current_exception(), [&] { closeThread(thread); });
    }
}
uint32_t StartupGate::object() const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (state_ == State::Failed) throw std::runtime_error("startup gate has failed");
    return state_ == State::Holding ? object_ : 0;
}
void StartupGate::release() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (state_ != State::Holding || threads_.empty()) throw std::runtime_error("startup gate is not holding a client");
    try {
        verifyObject(object_);
        verifyVector(process_, object_ + 0x8C, 12);
        verifyVector(process_, object_ + 0x78, 56);
        const auto expected = signature(imageBase_, true);
        if (readClientMemory(process_, imageBase_ + SELECTOR_RVA, expected.size()) != expected)
            throw std::runtime_error("startup gate breakpoint signature changed before release");
        writeClientMemory(process_, imageBase_ + SELECTOR_RVA, {0x55});
        state_ = State::Released;
        for (auto& entry : threads_) {
            if (ResumeThread(entry.second) != 1) throw std::runtime_error("startup gate thread resume count mismatch");
            closeThread(entry.second);
        }
        threads_.clear();
    } catch (...) { state_ = State::Failed; throw; }
}
void StartupGate::clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (process_ && WaitForSingleObject(process_, 0) != WAIT_OBJECT_0)
        throw std::runtime_error("startup gate cleanup requires owned client termination");
    for (auto& entry : threads_) closeThread(entry.second);
    threads_.clear();
    process_ = nullptr; pid_ = 0; imageBase_ = 0; object_ = 0; state_ = State::Empty;
}
}
