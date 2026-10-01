#pragma once
#include "../core/Common.h"
#include <exception>

namespace t7 {
inline void stopOwnedProcess(HANDLE process, DWORD waitMs = 10000) {
    auto state = WaitForSingleObject(process, 0);
    if (state == WAIT_OBJECT_0) return;
    if (state == WAIT_FAILED) throw std::runtime_error(errorText("owned client initial wait"));
    if (state != WAIT_TIMEOUT) throw std::runtime_error("unexpected owned client wait state=" + std::to_string(state));
    DWORD terminateError = TerminateProcess(process, 1) ? ERROR_SUCCESS : GetLastError();
    // An already-exiting process can reject termination; the handle decides completion.
    state = WaitForSingleObject(process, waitMs);
    if (state == WAIT_OBJECT_0) return;
    DWORD waitError = state == WAIT_FAILED ? GetLastError() : ERROR_TIMEOUT;
    throw std::runtime_error("owned client cleanup unconfirmed; terminateError=" + std::to_string(terminateError)
        + "; waitResult=" + std::to_string(state) + "; waitError=" + std::to_string(waitError));
}

inline std::string exceptionMessage(std::exception_ptr error) {
    try { std::rethrow_exception(error); }
    catch (const std::exception& e) { return e.what(); }
    catch (...) { return "non-standard exception"; }
}

template<class Cleanup>
[[noreturn]] void cleanupAndRethrow(std::exception_ptr primary, Cleanup cleanup) {
    try { cleanup(); }
    catch (...) {
        auto secondary = std::current_exception();
        throw std::runtime_error("PRIMARY ERROR: " + exceptionMessage(primary) + "; CLEANUP ERROR: " + exceptionMessage(secondary));
    }
    std::rethrow_exception(primary);
}
}
