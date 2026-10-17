#include "AudioPlaybackTrace.h"
#include "ClientImports.h"
#include "RemoteImage.h"
#include <algorithm>
#include <cctype>
#include <cstring>
#include <psapi.h>
#include <sstream>

namespace t7r {
namespace {
constexpr uint32_t CLIENT_BASE = 0x00400000, CLIENT_ENTRY_RVA = 0x01220739;
struct Binding { AudioPlaybackApi api; const char* prefix; };
constexpr Binding BINDINGS[]{
    {AudioPlaybackApi::LoadBank, "?LoadBank@SoundEngine@AK@@YA?AW4AKRESULT@@PBDJAAK@Z"},
    {AudioPlaybackApi::UnloadBank, "?UnloadBank@SoundEngine@AK@@YA?AW4AKRESULT@@PBDPBXPAJ@Z"},
    {AudioPlaybackApi::PostEventName, "?PostEvent@SoundEngine@AK@@YAKPBD"},
    {AudioPlaybackApi::PostEventId, "?PostEvent@SoundEngine@AK@@YAKK"},
    {AudioPlaybackApi::GetId, "?GetIDFromString@SoundEngine@AK@@YAKPBD@Z"},
    {AudioPlaybackApi::RtpcName, "?SetRTPCValue@SoundEngine@AK@@YA?AW4AKRESULT@@PBD"},
    {AudioPlaybackApi::RtpcId, "?SetRTPCValue@SoundEngine@AK@@YA?AW4AKRESULT@@KM"},
    {AudioPlaybackApi::Init, "?Init@SOUNDENGINE_DLL@AK@@"},
};
uint32_t readUInt32(HANDLE process, uint32_t address) {
    const auto bytes = readClientMemory(process, address, 4);
    uint32_t word = 0; std::memcpy(&word, bytes.data(), 4); return word;
}
Bytes wordBytes(uint32_t value) {
    Bytes bytes(4); std::memcpy(bytes.data(), &value, 4); return bytes;
}
std::string lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return text;
}
std::string recordName(const AudioPlaybackRecord& record) {
    return {record.name, std::find(std::begin(record.name), std::end(record.name), '\0')};
}
void verifyTarget(HANDLE process, uint32_t address) {
    MEMORY_BASIC_INFORMATION memory{};
    wchar_t path[32768]{};
    if (!VirtualQueryEx(process, reinterpret_cast<void*>(static_cast<uintptr_t>(address)), &memory, sizeof(memory))
        || memory.Type != MEM_IMAGE || !(memory.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY))
        || !GetMappedFileNameW(process, reinterpret_cast<void*>(static_cast<uintptr_t>(address)), path, static_cast<DWORD>(std::size(path)))
        || lower(utf8(fs::path(path).filename().wstring())) != "aksoundenginedll.dll")
        throw std::runtime_error("audio playback import target is not executable Wwise image code");
}
}

std::string describeAudioPlayback(const AudioPlaybackRecord& r) {
    std::ostringstream out;
    out << "Audio playback call; sequence=" << r.sequence << "; thread=" << r.threadId
        << "; startedTickMs=" << r.startedMs << "; completedTickMs=" << r.completedMs
        << "; durationMs=" << static_cast<uint32_t>(r.completedMs - r.startedMs);
    const auto api = static_cast<AudioPlaybackApi>(r.api);
    const auto name = recordName(r);
    switch (api) {
    case AudioPlaybackApi::LoadBank: case AudioPlaybackApi::UnloadBank:
        out << "; api=" << (api == AudioPlaybackApi::LoadBank ? "LoadBank" : "UnloadBank")
            << "; mode=synchronous; completed=1; bank=" << jsonString(name) << "; akResult=" << r.result;
        if (r.flags & AUDIO_PLAYBACK_BANK_ID) out << "; bankId=" << r.bankId;
        else out << "; bankId=not-observed";
        break;
    case AudioPlaybackApi::PostEventName: case AudioPlaybackApi::PostEventId:
        out << "; api=PostEvent";
        if (api == AudioPlaybackApi::PostEventName) out << "; eventName=" << jsonString(name);
        else out << "; eventId=" << r.arguments[0];
        out << "; gameObjectId=" << r.arguments[1] << "; callbackFlags=" << r.arguments[2]
            << "; callbackPresent=" << (r.arguments[3] != 0) << "; externalSources=" << r.arguments[5]
            << "; playingId=" << r.result << "; accepted=" << (r.result != 0) << "; audible=not-observed";
        break;
    case AudioPlaybackApi::GetId:
        out << "; api=GetIDFromString; name=" << jsonString(name) << "; id=" << r.result; break;
    case AudioPlaybackApi::RtpcName: case AudioPlaybackApi::RtpcId: {
        float value = 0; std::memcpy(&value, &r.arguments[1], sizeof(value));
        out << "; api=SetRTPCValue";
        if (api == AudioPlaybackApi::RtpcName) out << "; name=" << jsonString(name);
        else out << "; id=" << r.arguments[0];
        out << "; value=" << value << "; gameObjectId=" << r.arguments[2] << "; akResult=" << r.result; break;
    }
    case AudioPlaybackApi::Init: out << "; api=Init; akResult=" << r.result; break;
    default: throw std::runtime_error("unknown audio playback trace record API");
    }
    if (r.flags & AUDIO_PLAYBACK_TRUNCATED) out << "; nameTruncated=1";
    out << "; scope=client-imports";
    return out.str();
}

void drainAudioPlayback(HANDLE process, uint32_t queue, uint32_t& dropped, const DiagnosticLogCallback& log) {
    const auto bytes = readClientMemory(process, queue, sizeof(AudioPlaybackQueue));
    AudioPlaybackQueue header{}; std::memcpy(&header, bytes.data(), sizeof(header));
    const auto count = header.written - header.read;
    if (count > AUDIO_PLAYBACK_CAPACITY) throw std::runtime_error("audio playback trace queue bounds mismatch");
    std::vector<AudioPlaybackRecord> records;
    records.reserve(count);
    for (uint32_t i = 0; i < count; ++i) {
        const auto index = (header.read + i) % AUDIO_PLAYBACK_CAPACITY;
        const auto data = readClientMemory(process, queue + sizeof(header) + index * sizeof(AudioPlaybackRecord), sizeof(AudioPlaybackRecord));
        AudioPlaybackRecord record{}; std::memcpy(&record, data.data(), sizeof(record));
        if (record.sequence != header.read + i + 1) throw std::runtime_error("audio playback trace sequence mismatch");
        records.push_back(record);
    }
    if (count) {
        const auto read = header.written;
        SIZE_T actual = 0;
        if (!WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(queue + 4)), &read, sizeof(read), &actual)
            || actual != sizeof(read)) throw std::runtime_error(errorText("audio playback trace consume"));
    }
    for (const auto& record : records) {
        const auto api = static_cast<AudioPlaybackApi>(record.api);
        const bool post = api == AudioPlaybackApi::PostEventName || api == AudioPlaybackApi::PostEventId;
        const bool failure = api != AudioPlaybackApi::GetId && (post ? record.result == 0 : record.result != 1);
        if (log) log(failure ? DiagnosticLogLevel::Warn : DiagnosticLogLevel::Debug, describeAudioPlayback(record));
    }
    if (header.dropped != dropped) {
        if (log) log(DiagnosticLogLevel::Warn, "Audio playback trace coverage incomplete; dropped="
            + std::to_string(header.dropped - dropped) + "; totalDropped=" + std::to_string(header.dropped));
        dropped = header.dropped;
    }
}

void AudioPlaybackTrace::arm(HANDLE process, const DiagnosticLogCallback& log) {
    process_ = process; log_ = log;
    entry_ = CLIENT_BASE + CLIENT_ENTRY_RVA;
    bool touched = false;
    try {
        originalEntry_ = readClientMemory(process_, entry_, 1).front();
        if (originalEntry_ == 0xCC) throw std::runtime_error("audio playback trace entry already has a breakpoint");
        touched = true;
        writeClientMemory(process_, entry_, {0xCC}); armed_ = true;
    } catch (const std::exception& error) {
        if (touched) writeClientMemory(process_, entry_, {originalEntry_});
        if (log_) log_(DiagnosticLogLevel::Warn, "Audio playback trace unavailable before entry; " + std::string(error.what()));
    }
}

void AudioPlaybackTrace::install() {
    struct Import { AudioPlaybackApi api; uint32_t iat, original, replacement; };
    std::vector<Import> imports;
    uint32_t tick = 0;
    for (const auto& module : clientImportModules()) {
        for (size_t i = 0; i < module.symbols.size(); ++i) {
            const auto address = CLIENT_BASE + module.iatRva + static_cast<uint32_t>(i * 4);
            if (lower(module.dll) == "kernel32.dll" && module.symbols[i] == "GetTickCount") tick = readUInt32(process_, address);
            if (module.dll != "aksoundenginedll.dll") continue;
            for (const auto& binding : BINDINGS) {
                if (module.symbols[i].rfind(binding.prefix, 0) != 0) continue;
                const auto original = readUInt32(process_, address);
                verifyTarget(process_, original);
                imports.push_back({binding.api, address, original, 0});
            }
        }
    }
    if (!tick || imports.size() != std::size(BINDINGS)) throw std::runtime_error("audio playback trace import coverage mismatch");
    const auto allocation = VirtualAllocEx(process_, nullptr, AUDIO_PLAYBACK_ALLOCATION_SIZE, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!allocation) throw std::runtime_error(errorText("audio playback trace allocation"));
    const auto address = reinterpret_cast<uintptr_t>(allocation);
    if (address > UINT32_MAX - AUDIO_PLAYBACK_ALLOCATION_SIZE) throw std::runtime_error("audio playback trace allocation outside x86 range");
    allocation_ = static_cast<uint32_t>(address);
    Bytes code;
    for (auto& item : imports) {
        item.replacement = allocation_ + AUDIO_PLAYBACK_CODE_OFFSET + static_cast<uint32_t>(code.size());
        const auto stub = buildAudioPlaybackHook(allocation_, item.original, tick, item.api);
        code.insert(code.end(), stub.begin(), stub.end());
    }
    if (code.size() > AUDIO_PLAYBACK_ALLOCATION_SIZE - AUDIO_PLAYBACK_CODE_OFFSET)
        throw std::runtime_error("audio playback trace code capacity exceeded");
    writeClientMemory(process_, allocation_ + AUDIO_PLAYBACK_CODE_OFFSET, code);
    DWORD previous = 0;
    if (!VirtualProtectEx(process_, reinterpret_cast<void*>(static_cast<uintptr_t>(allocation_ + AUDIO_PLAYBACK_CODE_OFFSET)),
                          AUDIO_PLAYBACK_ALLOCATION_SIZE - AUDIO_PLAYBACK_CODE_OFFSET, PAGE_EXECUTE_READ, &previous))
        throw std::runtime_error(errorText("audio playback trace code protection"));
    // Installation runs at the executable entry debug event, with all client threads suspended.
    // Keep allocations until process exit, including on partial installation/rollback failure.
    try {
        for (const auto& item : imports) writeClientMemory(process_, item.iat, wordBytes(item.replacement));
    } catch (...) {
        for (const auto& item : imports) {
            try { writeClientMemory(process_, item.iat, wordBytes(item.original)); }
            catch (const std::exception& error) {
                if (log_) log_(DiagnosticLogLevel::Warn, "Audio playback trace rollback incomplete; " + std::string(error.what()));
            }
        }
        throw;
    }
    if (log_) log_(DiagnosticLogLevel::Debug, "Audio playback trace installed; imports=" + std::to_string(imports.size())
        + "; scope=client-imports; bankMode=synchronous; engineErrors=existing-debug-output-only"
          "; internalCalls=not-observed; audible=not-observed; settingsMailbox=independent");
}

bool AudioPlaybackTrace::handleBreakpoint(DWORD threadId, uintptr_t address) {
    if (!armed_ || address != entry_) return false;
    HANDLE thread = OpenThread(THREAD_GET_CONTEXT | THREAD_SET_CONTEXT, FALSE, threadId);
    if (!thread) throw std::runtime_error(errorText("audio playback trace entry thread"));
    try {
        WOW64_CONTEXT context{}; context.ContextFlags = WOW64_CONTEXT_CONTROL;
        if (!Wow64GetThreadContext(thread, &context) || context.Eip != entry_ + 1)
            throw std::runtime_error("audio playback trace entry context mismatch");
        writeClientMemory(process_, entry_, {originalEntry_});
        context.Eip = entry_;
        if (!Wow64SetThreadContext(thread, &context)) throw std::runtime_error(errorText("audio playback trace entry restore"));
    } catch (...) { CloseHandle(thread); throw; }
    if (!CloseHandle(thread)) throw std::runtime_error(errorText("audio playback trace entry thread cleanup"));
    armed_ = false;
    try { install(); }
    catch (const std::exception& error) {
        if (log_) log_(DiagnosticLogLevel::Warn, "Audio playback trace coverage incomplete; install=" + std::string(error.what()));
    }
    return true;
}

void AudioPlaybackTrace::poll() {
    if (!allocation_ || failed_) return;
    try { drainAudioPlayback(process_, allocation_, dropped_, log_); }
    catch (const std::exception& error) {
        failed_ = true;
        if (log_) log_(DiagnosticLogLevel::Warn, "Audio playback trace coverage incomplete; collection stopped; " + std::string(error.what()));
    }
}

void AudioPlaybackTrace::debugString(const OUTPUT_DEBUG_STRING_INFO& info) {
    if (!process_ || !info.nDebugStringLength) return;
    try {
        const auto count = std::min<size_t>(info.nDebugStringLength, 1024);
        const auto bytes = readClientMemory(process_, reinterpret_cast<uintptr_t>(info.lpDebugStringData), count * (info.fUnicode ? 2 : 1));
        std::string message;
        if (info.fUnicode) {
            std::wstring value(count, L'\0'); std::memcpy(value.data(), bytes.data(), bytes.size());
            value.resize(value.find(L'\0') == std::wstring::npos ? value.size() : value.find(L'\0'));
            message = utf8(value);
        } else message.assign(bytes.begin(), std::find(bytes.begin(), bytes.end(), 0));
        const auto folded = lower(message);
        if (folded.find("wwise") == std::string::npos && folded.find("ak::") == std::string::npos
            && folded.find("aksound") == std::string::npos && folded.find("ak error") == std::string::npos) return;
        if (log_) log_(DiagnosticLogLevel::Debug, "Audio engine debug output; message=" + jsonString(message)
            + "; truncated=" + std::to_string(info.nDebugStringLength > count));
    } catch (const std::exception& error) {
        if (log_) log_(DiagnosticLogLevel::Warn, "Audio engine debug output unavailable; " + std::string(error.what()));
    }
}
}
