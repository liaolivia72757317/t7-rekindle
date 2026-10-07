#include "DebugClient.h"
#include <algorithm>
#include <cwctype>
#include <cstring>
#include <map>
#include <psapi.h>
#include <sstream>

namespace t7 {
namespace {
std::wstring lower(std::wstring value) {
    std::transform(value.begin(), value.end(), value.begin(), [](wchar_t c) { return static_cast<wchar_t>(std::towlower(c)); });
    return value;
}
std::wstring mappedPath(HANDLE process, uintptr_t address) {
    std::wstring path(32768, L'\0');
    const auto size = GetMappedFileNameW(process, reinterpret_cast<void*>(address), path.data(), static_cast<DWORD>(path.size()));
    if (!size || size >= path.size()) throw std::runtime_error(errorText("debug module identity"));
    path.resize(size); return lower(fs::path(path).lexically_normal().wstring());
}
std::wstring mappedName(HANDLE process, uintptr_t address) {
    return fs::path(mappedPath(process, address)).filename().wstring();
}
std::wstring devicePath(const fs::path& path) {
    const auto normalized = lower(path.lexically_normal().wstring());
    if (normalized.rfind(L"\\\\", 0) == 0) return L"\\device\\mup\\" + normalized.substr(2);
    wchar_t device[32768];
    if (!QueryDosDeviceW(path.root_name().c_str(), device, static_cast<DWORD>(std::size(device))))
        throw std::runtime_error(errorText("input method module path mapping"));
    return lower(device) + normalized.substr(path.root_name().wstring().size());
}
std::string hexAddress(uintptr_t value) {
    std::ostringstream output; output << "0x" << std::hex << std::uppercase << value; return output.str();
}
std::string faultLocation(HANDLE process, uintptr_t address) {
    const auto prefix = "address=" + hexAddress(address);
    MEMORY_BASIC_INFORMATION memory{};
    if (!VirtualQueryEx(process, reinterpret_cast<void*>(address), &memory, sizeof(memory)))
        return prefix + "; module=unavailable; " + errorText("fault address query");
    if (memory.Type != MEM_IMAGE) return prefix + "; module=unmapped";
    try {
        return prefix + "; module=" + utf8(mappedName(process, address))
            + "; rva=" + hexAddress(address - reinterpret_cast<uintptr_t>(memory.AllocationBase));
    } catch (const std::exception& error) { return prefix + "; module=unavailable; " + error.what(); }
}
void logFaultContext(HANDLE process, DWORD tid, const std::function<void(std::string)>& log) {
    HANDLE thread = OpenThread(THREAD_GET_CONTEXT, FALSE, tid);
    if (!thread) throw std::runtime_error(errorText("fault thread context"));
    uintptr_t stackPointer = 0;
    size_t wordSize = 0;
    try {
        BOOL wow64 = FALSE;
        if (!IsWow64Process(process, &wow64)) throw std::runtime_error(errorText("fault process architecture"));
        if (wow64) {
            WOW64_CONTEXT context{}; context.ContextFlags = WOW64_CONTEXT_FULL;
            if (!Wow64GetThreadContext(thread, &context)) throw std::runtime_error(errorText("fault WOW64 context"));
            stackPointer = context.Esp; wordSize = sizeof(DWORD);
            log("Client fault context; arch=x86; EIP=" + hexAddress(context.Eip) + "; ESP=" + hexAddress(context.Esp)
                + "; EBP=" + hexAddress(context.Ebp) + "; EAX=" + hexAddress(context.Eax) + "; EBX=" + hexAddress(context.Ebx)
                + "; ECX=" + hexAddress(context.Ecx) + "; EDX=" + hexAddress(context.Edx)
                + "; ESI=" + hexAddress(context.Esi) + "; EDI=" + hexAddress(context.Edi));
        } else {
            CONTEXT context{}; context.ContextFlags = CONTEXT_FULL;
            if (!GetThreadContext(thread, &context)) throw std::runtime_error(errorText("fault native context"));
            stackPointer = context.Rsp; wordSize = sizeof(DWORD64);
            log("Client fault context; arch=x64; RIP=" + hexAddress(context.Rip) + "; RSP=" + hexAddress(context.Rsp)
                + "; RBP=" + hexAddress(context.Rbp) + "; RAX=" + hexAddress(context.Rax)
                + "; RCX=" + hexAddress(context.Rcx) + "; RDX=" + hexAddress(context.Rdx));
        }
    } catch (...) { CloseHandle(thread); throw; }
    if (!CloseHandle(thread)) throw std::runtime_error(errorText("fault context thread cleanup"));
    MEMORY_BASIC_INFORMATION memory{};
    if (!VirtualQueryEx(process, reinterpret_cast<void*>(stackPointer), &memory, sizeof(memory)))
        throw std::runtime_error(errorText("fault stack region"));
    const auto available = memory.RegionSize - (stackPointer - reinterpret_cast<uintptr_t>(memory.BaseAddress));
    const auto bytes = readClientMemory(process, stackPointer, std::min<size_t>(32 * wordSize, available));
    // Image-address candidates are not an unwound stack; omit arbitrary stack contents.
    for (size_t offset = 0; offset + wordSize <= bytes.size(); offset += wordSize) {
        uintptr_t address = 0; std::memcpy(&address, bytes.data() + offset, wordSize);
        MEMORY_BASIC_INFORMATION candidate{};
        if (VirtualQueryEx(process, reinterpret_cast<void*>(address), &candidate, sizeof(candidate)) && candidate.Type == MEM_IMAGE)
            log("Client fault stack candidate; slot=" + std::to_string(offset / wordSize) + "; " + faultLocation(process, address));
    }
}
std::wstring executablePath(HANDLE process) {
    std::wstring path(32768, L'\0'); DWORD length = static_cast<DWORD>(path.size());
    if (!QueryFullProcessImageNameW(process, 0, path.data(), &length) || length >= path.size())
        throw std::runtime_error(errorText("debug process image path"));
    path.resize(length); return lower(fs::path(path).lexically_normal().wstring());
}
std::string handleHash(HANDLE file) {
    LARGE_INTEGER size{}, zero{};
    if (!file || !GetFileSizeEx(file, &size) || size.QuadPart <= 0 || size.QuadPart > 64 * 1024 * 1024
        || !SetFilePointerEx(file, zero, nullptr, FILE_BEGIN))
        throw std::runtime_error(errorText("debug image file identity"));
    Bytes bytes(static_cast<size_t>(size.QuadPart)); DWORD actual = 0;
    if (!ReadFile(file, bytes.data(), static_cast<DWORD>(bytes.size()), &actual, nullptr) || actual != bytes.size())
        throw std::runtime_error(errorText("debug image file read"));
    return sha256(bytes);
}
bool forbidden(const std::wstring& name) {
    return name == L"tiejiclientbase.dll" || name == L"tersafe.dll" || name == L"tenslx.dll"
        || name == L"gbspy.dll" || name == L"qqpcfix.dll";
}
struct EntryBreakpoint { uint32_t base; const DllEntryRule* rule; bool pending; };
enum class ProcessRole { Main, VerifiedGameChild, CompatibleChild, RejectedChild };
const char* roleName(ProcessRole role) {
    switch (role) {
    case ProcessRole::Main: return "Main";
    case ProcessRole::VerifiedGameChild: return "VerifiedGameChild";
    case ProcessRole::CompatibleChild: return "CompatibleChild";
    default: return "RejectedChild";
    }
}
struct DebugProcess {
    HANDLE process = nullptr;
    DWORD primaryTid = 0;
    ProcessRole role = ProcessRole::RejectedChild;
    bool nativeInitial = false, wow64Initial = false;
    ULONGLONG terminationDeadline = 0;
    std::map<uintptr_t, EntryBreakpoint> entries;
};
void initializeDll(HANDLE process, DWORD tid, uintptr_t entry, EntryBreakpoint& breakpoint) {
    const auto& rule = *breakpoint.rule;
    auto expected = rule.entrySignature; expected[0] = 0xCC;
    if (readClientMemory(process, entry, expected.size()) != expected)
        throw std::runtime_error("DLL entry breakpoint signature changed");
    HANDLE thread = OpenThread(THREAD_GET_CONTEXT | THREAD_SET_CONTEXT, FALSE, tid);
    if (!thread) throw std::runtime_error(errorText("DLL entry thread"));
    try {
        WOW64_CONTEXT context{}; context.ContextFlags = WOW64_CONTEXT_CONTROL;
        if (!Wow64GetThreadContext(thread, &context) || context.Eip != entry + 1)
            throw std::runtime_error("DLL entry instruction pointer mismatch");
        // Relocation-bearing patches are applied here, not at LOAD_DLL.
        applyRemotePatches(process, breakpoint.base, rule.patches(breakpoint.base));
        writeClientMemory(process, entry, rule.entrySignature);
        context.Eip = static_cast<DWORD>(entry);
        if (!Wow64SetThreadContext(thread, &context)) throw std::runtime_error(errorText("DLL entry context restore"));
    } catch (...) { CloseHandle(thread); throw; }
    if (!CloseHandle(thread)) throw std::runtime_error(errorText("DLL entry thread cleanup"));
    breakpoint.pending = false;
}
}

DebugClient::DebugClient(InputMethodProvider inputMethods) : inputMethods_(std::move(inputMethods)) {}
DebugClient::~DebugClient() {
    stopping_.store(true);
    if (worker_.joinable()) worker_.join();
    if (process_) CloseHandle(process_);
}
void DebugClient::fail(const std::string& message, bool cleanup) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (error_.empty()) error_ = message;
    if (cleanup && cleanupError_.empty()) cleanupError_ = message;
    stopping_.store(true);
}
void DebugClient::check() const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!error_.empty()) throw std::runtime_error(error_);
}
void DebugClient::stop() {
    stopping_.store(true);
    if (worker_.joinable()) worker_.join();
    std::lock_guard<std::mutex> lock(mutex_);
    if (!cleanupError_.empty()) throw std::runtime_error(cleanupError_);
}
void DebugClient::start(const fs::path& executable, const std::string& imageHash,
                        const std::vector<DllEntryRule>& rules,
                        const std::function<void(HANDLE, HANDLE)>& prepare,
                        const std::function<void(std::string)>& log,
                        const std::function<bool()>& cancelled,
                        const std::vector<ChildImageRule>& children,
                        const std::function<bool(DWORD, uintptr_t)>& breakpoint,
                        const std::function<void(std::string)>& warning) {
    if (worker_.joinable() || process_) throw std::runtime_error("debug client already started");
    for (const auto& rule : rules) {
        if (rule.name.empty() || !rule.entryRva || rule.entrySignature.empty() || rule.entrySignature[0] == 0xCC || !rule.patches)
            throw std::runtime_error("invalid DLL entry rule");
    }
    for (const auto& child : children) {
        if (!child.executable.is_absolute() || child.sha256.size() != 64)
            throw std::runtime_error("invalid child image rule");
    }
    std::promise<void> ready;
    auto result = ready.get_future();
    worker_ = std::thread([this, executable, imageHash, rules, prepare, log, cancelled, children, breakpoint, warning,
                           ready = std::move(ready)]() mutable {
        HANDLE job = nullptr, primaryThread = nullptr;
        std::map<DWORD, DebugProcess> processes;
        std::map<std::wstring, std::string> registeredModules;
        bool terminating = false, inputMethodCompatible = false;
        ULONGLONG terminationDeadline = 0;
        const auto warn = [&](const std::string& message) {
            if (warning) warning(message); else if (log) log("WARNING: " + message);
        };
        try {
            if (cancelled && cancelled()) throw std::runtime_error("client launch cancelled");
            try {
                std::map<std::wstring, std::string> snapshot;
                for (const auto& registration : inputMethods_())
                    snapshot.emplace(devicePath(registration.module), registration.source);
                registeredModules = std::move(snapshot);
            } catch (const std::exception& error) {
                warn("Input method registry unavailable; compatibility remains disabled; " + std::string(error.what()));
            }
            job = CreateJobObjectW(nullptr, nullptr);
            if (!job) throw std::runtime_error(errorText("debug client Job Object"));
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits)))
                throw std::runtime_error(errorText("debug client Job Object limits"));
            STARTUPINFOW startup{}; startup.cb = sizeof(startup); PROCESS_INFORMATION info{};
            std::wstring command = L"\"" + executable.wstring() + L"\"";
            if (!CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, FALSE,
                                DEBUG_PROCESS | CREATE_SUSPENDED, nullptr, executable.parent_path().c_str(), &startup, &info))
                throw std::runtime_error(errorText("debug client CreateProcessW"));
            process_ = info.hProcess; pid_ = info.dwProcessId; primaryThread = info.hThread;
            processes[pid_].process = process_; processes[pid_].primaryTid = info.dwThreadId;
            processes[pid_].role = ProcessRole::Main;
            if (!AssignProcessToJobObject(job, process_)) throw std::runtime_error(errorText("debug client Job Object assignment"));
            prepare(process_, primaryThread);
            if (cancelled && cancelled()) throw std::runtime_error("client launch cancelled");
            if (ResumeThread(primaryThread) != 1) throw std::runtime_error(errorText("prepared client first resume"));
            ready.set_value();
        } catch (const std::exception& error) {
            fail(error.what()); ready.set_exception(std::current_exception());
        } catch (...) {
            fail("non-standard client preparation error"); ready.set_exception(std::current_exception());
        }
        if (primaryThread && !CloseHandle(primaryThread)) fail(errorText("client primary thread cleanup"), true);
        while (!processes.empty()) {
            for (const auto& process : processes) {
                if (!terminating && process.second.terminationDeadline && GetTickCount64() > process.second.terminationDeadline)
                    fail("compatible child exit event timeout; PID=" + std::to_string(process.first), true);
            }
            if (stopping_.load() && !terminating) {
                terminating = true; terminationDeadline = GetTickCount64() + 10000;
                if (!TerminateJobObject(job, 1)) fail(errorText("debug client job termination"), true);
                if (WaitForSingleObject(process_, 0) == WAIT_TIMEOUT && !TerminateProcess(process_, 1)
                    && GetLastError() != ERROR_ACCESS_DENIED) fail(errorText("debug client process termination"), true);
            }
            if (terminating && GetTickCount64() > terminationDeadline) { fail("debug client exit event timeout", true); break; }
            DEBUG_EVENT event{};
            if (!WaitForDebugEvent(&event, 100)) {
                if (GetLastError() != ERROR_SEM_TIMEOUT) { fail(errorText("client debug event wait"), true); break; }
                continue;
            }
            DWORD disposition = DBG_CONTINUE;
            HANDLE exitedProcess = nullptr;
            HANDLE eventFile = event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT ? event.u.CreateProcessInfo.hFile
                : event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT ? event.u.LoadDll.hFile : nullptr;
            try {
                if (event.dwDebugEventCode == CREATE_PROCESS_DEBUG_EVENT) {
                    auto& state = processes[event.dwProcessId];
                    state.process = event.dwProcessId == pid_ ? process_ : event.u.CreateProcessInfo.hProcess;
                    state.primaryTid = event.dwThreadId;
                    if (!terminating && event.dwProcessId != pid_) {
                        const auto path = executablePath(state.process);
                        const auto found = std::find_if(children.begin(), children.end(), [&](const auto& child) {
                            return lower(child.executable.lexically_normal().wstring()) == path;
                        });
                        const auto filename = fs::path(path).filename().wstring();
                        const auto name = utf8(filename);
                        const bool known = std::any_of(children.begin(), children.end(), [&](const auto& child) {
                            return lower(child.executable.filename().wstring()) == filename;
                        });
                        if (known && (found == children.end() || handleHash(eventFile) != found->sha256))
                            throw std::runtime_error("unverified client child process; stopped before user code: " + name
                                + "; reason=game component identity mismatch; PID=" + std::to_string(event.dwProcessId));
                        if (!known && !inputMethodCompatible)
                            throw std::runtime_error("unverified client child process; stopped before user code: " + name
                                + "; reason=no registered IME module observed; PID=" + std::to_string(event.dwProcessId));
                        BOOL owned = FALSE;
                        if (!IsProcessInJob(state.process, job, &owned) || !owned)
                            throw std::runtime_error("client child process did not inherit the owned job");
                        state.role = known ? ProcessRole::VerifiedGameChild : ProcessRole::CompatibleChild;
                        if (log) log(std::string(known ? "Verified" : "Compatible") + " client child process; PID="
                            + std::to_string(event.dwProcessId) + "; image=" + name + "; role=" + roleName(state.role)
                            + (known ? "; reason=game component rule" : "; reason=registered IME module observed"));
                    } else if (!terminating) {
                        if (handleHash(eventFile) != imageHash) throw std::runtime_error("created client image identity changed");
                        if (log) log("Client process creation observed before user code; PID=" + std::to_string(pid_) + "; role=Main");
                    }
                } else if (event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT) {
                    const auto& state = processes.at(event.dwProcessId);
                    const auto exit = "Client process exit observed; PID=" + std::to_string(event.dwProcessId)
                        + "; code=" + std::to_string(event.u.ExitProcess.dwExitCode) + "; role=" + roleName(state.role);
                    if (!DuplicateHandle(GetCurrentProcess(), state.process,
                                         GetCurrentProcess(), &exitedProcess, SYNCHRONIZE, FALSE, 0))
                        fail(errorText("exiting process wait handle"), true);
                    if (!stopping_.load() && state.role == ProcessRole::CompatibleChild
                        && event.u.ExitProcess.dwExitCode && !state.terminationDeadline)
                        warn(exit + "; game session continues");
                    processes.erase(event.dwProcessId);
                    if (event.dwProcessId == pid_) stopping_.store(true);
                    if (log) log(exit);
                } else if (!stopping_.load() && event.dwDebugEventCode == LOAD_DLL_DEBUG_EVENT) {
                    auto& state = processes.at(event.dwProcessId);
                    const auto process = state.process;
                    const auto base = reinterpret_cast<uintptr_t>(event.u.LoadDll.lpBaseOfDll);
                    const auto path = mappedPath(process, base);
                    const auto name = fs::path(path).filename().wstring();
                    if (log) log("Client module load: " + utf8(name) + "; PID=" + std::to_string(event.dwProcessId)
                                 + "; role=" + roleName(state.role));
                    if (forbidden(name)) throw std::runtime_error("TP module reached loader; stopped before initialization: " + utf8(name));
                    const auto registration = registeredModules.find(path);
                    if (!inputMethodCompatible && registration != registeredModules.end()
                        && (state.role == ProcessRole::Main || state.role == ProcessRole::VerifiedGameChild)) {
                        inputMethodCompatible = true;
                        if (log) log("Input method compatibility enabled; module=" + utf8(name) + "; source=" + registration->second
                            + "; PID=" + std::to_string(event.dwProcessId) + "; role=" + roleName(state.role)
                            + "; reason=registered module load; scope=session");
                    }
                    for (const auto& rule : rules) {
                        if (state.role == ProcessRole::CompatibleChild) break;
                        if (name != lower(rule.name)) continue;
                        if (base > UINT32_MAX - rule.entryRva || handleHash(eventFile) != rule.sha256)
                            throw std::runtime_error("required DLL identity/address mismatch");
                        const auto entry = base + rule.entryRva;
                        if (readClientMemory(process, entry, rule.entrySignature.size()) != rule.entrySignature)
                            throw std::runtime_error("required DLL entry signature mismatch");
                        const auto header = readClientMemory(process, base, 0x400);
                        uint32_t ntOffset = 0; std::memcpy(&ntOffset, header.data() + 0x3C, 4);
                        if (ntOffset > header.size() - sizeof(IMAGE_NT_HEADERS32)) throw std::runtime_error("required DLL header bounds");
                        IMAGE_NT_HEADERS32 nt{}; std::memcpy(&nt, header.data() + ntOffset, sizeof(nt));
                        if (nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC || nt.OptionalHeader.AddressOfEntryPoint != rule.entryRva
                            || nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_TLS].VirtualAddress)
                            throw std::runtime_error("required DLL early initialization layout mismatch");
                        if (!state.entries.emplace(entry, EntryBreakpoint{static_cast<uint32_t>(base), &rule, true}).second)
                            throw std::runtime_error("duplicate required DLL mapping");
                        writeClientMemory(process, entry, {0xCC});
                    }
                } else if (!stopping_.load() && event.dwDebugEventCode == UNLOAD_DLL_DEBUG_EVENT) {
                    auto& entries = processes.at(event.dwProcessId).entries;
                    const auto base = reinterpret_cast<uintptr_t>(event.u.UnloadDll.lpBaseOfDll);
                    for (auto it = entries.begin(); it != entries.end();) {
                        if (it->second.base == base) it = entries.erase(it); else ++it;
                    }
                } else if (!stopping_.load() && event.dwDebugEventCode == EXCEPTION_DEBUG_EVENT) {
                    auto& state = processes.at(event.dwProcessId);
                    const auto& exception = event.u.Exception.ExceptionRecord;
                    const auto address = reinterpret_cast<uintptr_t>(exception.ExceptionAddress);
                    const bool native = exception.ExceptionCode == EXCEPTION_BREAKPOINT;
                    const bool wow64 = exception.ExceptionCode == 0x4000001F;
                    const auto entry = state.entries.find(address);
                    if ((native || wow64) && event.u.Exception.dwFirstChance && entry != state.entries.end() && entry->second.pending) {
                        initializeDll(state.process, event.dwThreadId, address, entry->second);
                        if (log) log("Required DLL adapted before initialization: " + utf8(entry->second.rule->name));
                    } else if ((native || wow64) && event.u.Exception.dwFirstChance && event.dwProcessId == pid_
                               && breakpoint && breakpoint(event.dwThreadId, address)) {
                        disposition = DBG_CONTINUE;
                    } else if ((native || wow64) && event.u.Exception.dwFirstChance && event.dwThreadId == state.primaryTid
                               && mappedName(state.process, address) == L"ntdll.dll" && (native ? !state.nativeInitial : !state.wow64Initial)) {
                        (native ? state.nativeInitial : state.wow64Initial) = true;
                    } else {
                        disposition = DBG_EXCEPTION_NOT_HANDLED;
                        if (log) log("Client exception; firstChance=" + std::to_string(event.u.Exception.dwFirstChance)
                                     + "; code=" + std::to_string(exception.ExceptionCode) + "; address=" + std::to_string(address)
                                     + "; PID=" + std::to_string(event.dwProcessId) + "; role=" + roleName(state.role));
                        if (log && exception.ExceptionCode == EXCEPTION_ACCESS_VIOLATION && exception.NumberParameters >= 2)
                            log("Client access violation; operation=" + std::to_string(exception.ExceptionInformation[0])
                                + "; target=" + std::to_string(exception.ExceptionInformation[1]));
                        if (!event.u.Exception.dwFirstChance) {
                            const auto location = faultLocation(state.process, address);
                            if (log) {
                                log("Client fault location; " + location);
                                try { logFaultContext(state.process, event.dwThreadId, log); }
                                catch (const std::exception& error) { log("Client fault diagnostics incomplete: " + std::string(error.what())); }
                            }
                            const auto failure = "unhandled client exception code=" + std::to_string(exception.ExceptionCode)
                                + "; " + location + "; PID=" + std::to_string(event.dwProcessId) + "; role=" + roleName(state.role);
                            if (state.role != ProcessRole::CompatibleChild) throw std::runtime_error(failure);
                            warn(failure + "; terminating only compatible child; game session continues");
                            if (!TerminateProcess(state.process, exception.ExceptionCode))
                                fail(errorText("compatible child termination"), true);
                            state.terminationDeadline = GetTickCount64() + 10000;
                            disposition = DBG_CONTINUE;
                        }
                    }
                }
            } catch (const std::exception& error) { fail(error.what()); }
            catch (...) { fail("non-standard client debug event error"); }
            if (eventFile && !CloseHandle(eventFile)) fail(errorText("debug event file cleanup"), true);
            // Do not release an offending load/creation event until termination is requested.
            if (stopping_.load() && !terminating) {
                terminating = true; terminationDeadline = GetTickCount64() + 10000;
                if (!TerminateJobObject(job, 1)) fail(errorText("debug event job termination"), true);
                if (!TerminateProcess(process_, 1) && GetLastError() != ERROR_ACCESS_DENIED)
                    fail(errorText("debug event process termination"), true);
            }
            const auto continued = ContinueDebugEvent(event.dwProcessId, event.dwThreadId, disposition);
            if (!continued) fail(errorText("client debug event continuation"), true);
            if (exitedProcess) {
                // Continuing EXIT_PROCESS closes debugger handles before the process finishes exiting.
                if (continued && WaitForSingleObject(exitedProcess, 5000) != WAIT_OBJECT_0)
                    fail("debug client process exit unconfirmed after continuation", true);
                if (!CloseHandle(exitedProcess)) fail(errorText("exiting process wait handle cleanup"), true);
            }
            if (!continued) break;
        }
        if (job && !CloseHandle(job)) fail(errorText("debug client Job Object cleanup"), true);
        if (process_ && WaitForSingleObject(process_, 5000) != WAIT_OBJECT_0) fail("debug client process exit unconfirmed", true);
    });
    result.get();
}
}
