#include "../../src/Runtime/launcher/InputMethodRegistry.h"
#include <algorithm>
#include <cwctype>
#include <iostream>
#include <map>
#include <tuple>

namespace {
using Scope = t7::InputMethodScope;
using View = t7::InputMethodView;
using Key = std::tuple<Scope, View, std::wstring>;
struct RegistryFixture {
    std::map<Key, std::vector<std::wstring>> keys;
    std::map<std::pair<Key, std::wstring>, std::wstring> values;
    t7::InputMethodRegistryReader reader() const {
        return {
            [this](Scope scope, View view, const std::wstring& path) {
                const auto found = keys.find({scope, view, path});
                return found == keys.end() ? std::vector<std::wstring>{} : found->second;
            },
            [this](Scope scope, View view, const std::wstring& path, const std::wstring& name) {
                const auto found = values.find({{scope, view, path}, name});
                return found == values.end() ? std::wstring{} : found->second;
            }
        };
    }
};
void require(bool result, const char* message) { if (!result) throw std::runtime_error(message); }
class EnvironmentScope {
public:
    EnvironmentScope(const wchar_t* name, const wchar_t* value) : name_(name) {
        wchar_t previous[32768]{};
        SetLastError(ERROR_SUCCESS);
        const auto length = GetEnvironmentVariableW(name_.c_str(), previous, static_cast<DWORD>(std::size(previous)));
        const auto error = GetLastError();
        require(length < std::size(previous) && (length || error == ERROR_SUCCESS || error == ERROR_ENVVAR_NOT_FOUND),
                "environment fixture read failed");
        existed_ = length || error != ERROR_ENVVAR_NOT_FOUND;
        previous_.assign(previous, length);
        require(SetEnvironmentVariableW(name_.c_str(), value) != 0, "environment fixture setup failed");
    }
    ~EnvironmentScope() {
        if (!SetEnvironmentVariableW(name_.c_str(), existed_ ? previous_.c_str() : nullptr)) std::terminate();
    }
    EnvironmentScope(const EnvironmentScope&) = delete;
    EnvironmentScope& operator=(const EnvironmentScope&) = delete;
private:
    std::wstring name_, previous_;
    bool existed_ = false;
};
std::wstring normalized(const t7::fs::path& path) {
    auto text = path.lexically_normal().wstring();
    std::transform(text.begin(), text.end(), text.begin(), [](wchar_t value) { return static_cast<wchar_t>(std::towlower(value)); });
    return text;
}
bool contains(const std::vector<t7::InputMethodRegistration>& entries, const t7::fs::path& path, const std::string& source) {
    return std::any_of(entries.begin(), entries.end(), [&](const auto& entry) {
        return normalized(entry.module) == normalized(path) && entry.source == source;
    });
}
void verifyEnvironmentViews(const t7::fs::path& windows) {
    const auto root = windows.parent_path() / "t7-fixture-programs";
    const auto nativePrograms = root / "Native Apps", wowPrograms = root / "WOW Apps";
    const auto nativeCommon = root / "Native Shared", wowCommon = root / "WOW Shared";
    const EnvironmentScope programFiles(L"ProgramFiles", nativePrograms.c_str());
    const EnvironmentScope programFilesX86(L"ProgramFiles(x86)", wowPrograms.c_str());
    const EnvironmentScope commonFiles(L"CommonProgramFiles", nativeCommon.c_str());
    const EnvironmentScope commonFilesX86(L"CommonProgramFiles(x86)", wowCommon.c_str());
    const EnvironmentScope programW6432(L"ProgramW6432", nativePrograms.c_str());
    const EnvironmentScope commonW6432(L"CommonProgramW6432", nativeCommon.c_str());
    const EnvironmentScope processor(L"PROCESSOR_ARCHITECTURE", L"AMD64");
    const EnvironmentScope processorW6432(L"PROCESSOR_ARCHITEW6432", nullptr);
    const EnvironmentScope ordinary(L"T7_IME_FIXTURE_ROOT", root.c_str());
    const EnvironmentScope missing(L"T7_IME_FIXTURE_MISSING", nullptr);
    struct PathCase {
        std::wstring registered;
        t7::fs::path x86, x64;
    };
    const PathCase cases[] = {
        {L"%ProgramFiles%\\IME\\Fixture.dll", wowPrograms / "IME/Fixture.dll", nativePrograms / "IME/Fixture.dll"},
        {L"  \"%pRoGrAmFiLeS%\\IME\\Mixed.dll\"  ", wowPrograms / "IME/Mixed.dll", nativePrograms / "IME/Mixed.dll"},
        {L"%CommonProgramFiles%\\IME\\Fixture.dll", wowCommon / "IME/Fixture.dll", nativeCommon / "IME/Fixture.dll"},
        {L"%cOmMoNpRoGrAmFiLeS%\\IME\\Mixed.dll", wowCommon / "IME/Mixed.dll", nativeCommon / "IME/Mixed.dll"},
        {L"%ProgramFiles(x86)%\\Explicit.dll", wowPrograms / "Explicit.dll", wowPrograms / "Explicit.dll"},
        {L"%CommonProgramFiles(x86)%\\Explicit.dll", wowCommon / "Explicit.dll", wowCommon / "Explicit.dll"},
        {L"%ProgramW6432%\\Native.dll", nativePrograms / "Native.dll", nativePrograms / "Native.dll"},
        {L"%CommonProgramW6432%\\Native.dll", nativeCommon / "Native.dll", nativeCommon / "Native.dll"},
        {L"%ProgramFiles%\\%PROCESSOR_ARCHITECTURE%\\Fixture.dll",
            wowPrograms / "x86/Fixture.dll", nativePrograms / "AMD64/Fixture.dll"},
        {L"%ProgramFiles%\\%PROCESSOR_ARCHITEW6432%\\Fixture.dll",
            wowPrograms / "AMD64/Fixture.dll", nativePrograms / "%PROCESSOR_ARCHITEW6432%/Fixture.dll"},
        {L"%T7_IME_FIXTURE_ROOT%\\Ordinary.dll", root / "Ordinary.dll", root / "Ordinary.dll"},
        {L"%T7_IME_FIXTURE_ROOT%\\%T7_IME_FIXTURE_MISSING%\\Unknown.dll",
            root / "%T7_IME_FIXTURE_MISSING%/Unknown.dll", root / "%T7_IME_FIXTURE_MISSING%/Unknown.dll"},
        {(nativePrograms / "Literal.dll").wstring(), nativePrograms / "Literal.dll", nativePrograms / "Literal.dll"}
    };
    const std::wstring tips = L"SOFTWARE\\Microsoft\\CTF\\TIP";
    const std::wstring server = L"SOFTWARE\\Classes\\CLSID\\{environment-tsf}\\InprocServer32";
    RegistryFixture fixture;
    for (const auto view : {View::X86, View::X64})
        fixture.keys[{Scope::Machine, view, tips}] = {L"{environment-tsf}"};
    for (const auto& test : cases) {
        for (const auto view : {View::X86, View::X64})
            fixture.values[{{Scope::Machine, view, server}, L""}] = test.registered;
        const auto entries = t7::readInputMethodRegistrations(fixture.reader(), windows);
        require(entries.size() == 2, "environment fixture registration count");
        require(contains(entries, test.x86, "TSF/system/x86"), "x86 environment expansion used the wrong view");
        require(contains(entries, test.x64, "TSF/system/x64"), "x64 environment expansion changed");
    }
    wchar_t unchanged[32768];
    const auto length = GetEnvironmentVariableW(L"ProgramFiles", unchanged, static_cast<DWORD>(std::size(unchanged)));
    require(length && length < std::size(unchanged) && std::wstring(unchanged, length) == nativePrograms.wstring(),
            "registry discovery changed the launcher environment");
}
}

bool verifyInputMethodRegistry() {
    try {
        const auto windows = t7::fs::temp_directory_path() / "t7-fixture-windows";
        const std::wstring layouts = L"SYSTEM\\CurrentControlSet\\Control\\Keyboard Layouts";
        const std::wstring tips = L"SOFTWARE\\Microsoft\\CTF\\TIP";
        const std::wstring classes = L"SOFTWARE\\Classes\\CLSID\\";
        RegistryFixture fixture;
        for (const auto view : {View::X86, View::X64}) {
            fixture.keys[{Scope::Machine, view, layouts}] = {L"keyboard-only", L"fixture-ime"};
            fixture.values[{{Scope::Machine, view, layouts + L"\\fixture-ime"}, L"Ime File"}] = L"Fixture.ime";
        }
        fixture.keys[{Scope::Machine, View::X86, tips}] = {L"{fixture-tsf}", L"{missing-server}"};
        fixture.values[{{Scope::Machine, View::X86, classes + L"{fixture-tsf}\\InprocServer32"}, L""}]
            = (windows / "System32/IME/Fixture/FixtureTsf.dll").wstring();
        fixture.keys[{Scope::User, View::X64, tips}] = {L"{user-tsf}"};
        fixture.values[{{Scope::User, View::X64, classes + L"{user-tsf}\\InprocServer32"}, L""}]
            = L"\"" + (windows / "UserIme/UserTsf.dll").wstring() + L"\"";
        const auto registrations = t7::readInputMethodRegistrations(fixture.reader(), windows);
        require(registrations.size() == 4, "IMM and TSF registration discovery");
        require(contains(registrations, windows / "SysWOW64/Fixture.ime", "IMM/system/x86"), "x86 IMM system directory");
        require(contains(registrations, windows / "System32/Fixture.ime", "IMM/system/x64"), "x64 IMM system directory");
        require(contains(registrations, windows / "SysWOW64/IME/Fixture/FixtureTsf.dll", "TSF/system/x86"),
                "WOW64 TSF System32 redirection");
        require(contains(registrations, windows / "UserIme/UserTsf.dll", "TSF/user/x64"), "user TSF quoted module path");
        fixture.values[{{Scope::User, View::X86, classes + L"{fixture-tsf}\\InprocServer32"}, L""}]
            = (windows / "UserIme/Override.dll").wstring();
        const auto overridden = t7::readInputMethodRegistrations(fixture.reader(), windows);
        require(contains(overridden, windows / "UserIme/Override.dll", "TSF/system/x86"), "per-user COM registration precedence");
        require(!contains(overridden, windows / "SysWOW64/IME/Fixture/FixtureTsf.dll", "TSF/system/x86"),
                "shadowed COM server remained eligible");
        fixture.values[{{Scope::User, View::X86, classes + L"{fixture-tsf}\\InprocServer32"}, L""}]
            = (windows / "System32Extra/Fixture.dll").wstring();
        require(contains(t7::readInputMethodRegistrations(fixture.reader(), windows),
                         windows / "System32Extra/Fixture.dll", "TSF/system/x86"), "WOW64 directory boundary");
        wchar_t systemRoot[32768];
        const auto length = GetEnvironmentVariableW(L"SystemRoot", systemRoot, static_cast<DWORD>(std::size(systemRoot)));
        require(length && length < std::size(systemRoot), "Windows environment fixture");
        fixture.values[{{Scope::User, View::X86, classes + L"{fixture-tsf}\\InprocServer32"}, L""}]
            = L"  \"%SystemRoot%\\System32\\IME\\Fixture\\Env.dll\"  ";
        require(contains(t7::readInputMethodRegistrations(fixture.reader(), systemRoot),
                         t7::fs::path(systemRoot) / "SysWOW64/IME/Fixture/Env.dll", "TSF/system/x86"),
                "environment expansion, whitespace and WOW64 path normalization");
        verifyEnvironmentViews(windows);
        RegistryFixture empty;
        require(t7::readInputMethodRegistrations(empty.reader(), windows).empty(), "missing registrations enabled compatibility");
        const auto reader = fixture.reader();
        auto broken = reader;
        size_t reads = 0;
        broken.value = [&](Scope scope, View view, const std::wstring& path, const std::wstring& name) {
            if (++reads == 3) throw std::runtime_error("synthetic registry read failure");
            return reader.value(scope, view, path, name);
        };
        bool rejected = false;
        try { t7::readInputMethodRegistrations(broken, windows); }
        catch (const std::runtime_error&) { rejected = true; }
        require(rejected, "registry failure returned a partial snapshot");
        std::cout << "IMM/TSF registration and architecture cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Input method registry test failure: " << error.what() << '\n';
        return false;
    }
}
