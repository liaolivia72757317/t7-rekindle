#include "InputMethodRegistry.h"
#include <algorithm>
#include <cwctype>

namespace t7 {
namespace {
using Scope = InputMethodScope;
using View = InputMethodView;
const std::wstring KEYBOARD_LAYOUTS = L"SYSTEM\\CurrentControlSet\\Control\\Keyboard Layouts";
const std::wstring TEXT_SERVICES = L"SOFTWARE\\Microsoft\\CTF\\TIP";

void registryError(const char* operation, LSTATUS status) {
    throw std::runtime_error(std::string("input method registry ") + operation + "; win32=" + std::to_string(status));
}
template<class Read>
auto readKey(Scope scope, View view, const std::wstring& path, Read read) -> decltype(read(HKEY{})) {
    HKEY key = nullptr;
    const auto status = RegOpenKeyExW(scope == Scope::User ? HKEY_CURRENT_USER : HKEY_LOCAL_MACHINE,
        path.c_str(), 0, KEY_READ | (view == View::X86 ? KEY_WOW64_32KEY : KEY_WOW64_64KEY), &key);
    if (status == ERROR_FILE_NOT_FOUND || status == ERROR_PATH_NOT_FOUND) return {};
    if (status != ERROR_SUCCESS) registryError("open", status);
    try {
        auto result = read(key);
        const auto closed = RegCloseKey(key); key = nullptr;
        if (closed != ERROR_SUCCESS) registryError("close", closed);
        return result;
    } catch (...) {
        if (key) RegCloseKey(key);
        throw;
    }
}
std::vector<std::wstring> subkeys(Scope scope, View view, const std::wstring& path) {
    return readKey(scope, view, path, [](HKEY key) {
        std::vector<std::wstring> result;
        for (DWORD index = 0;; ++index) {
            wchar_t name[256]; DWORD length = static_cast<DWORD>(std::size(name));
            const auto status = RegEnumKeyExW(key, index, name, &length, nullptr, nullptr, nullptr, nullptr);
            if (status == ERROR_NO_MORE_ITEMS) break;
            if (status != ERROR_SUCCESS) registryError("enumerate", status);
            result.emplace_back(name, length);
        }
        return result;
    });
}
std::wstring stringValue(Scope scope, View view, const std::wstring& path, const std::wstring& name) {
    return readKey(scope, view, path, [&](HKEY key) -> std::wstring {
        DWORD type = 0, size = 0;
        auto status = RegQueryValueExW(key, name.c_str(), nullptr, &type, nullptr, &size);
        if (status == ERROR_FILE_NOT_FOUND) return {};
        if (status != ERROR_SUCCESS) registryError("value size", status);
        if ((type != REG_SZ && type != REG_EXPAND_SZ) || size % sizeof(wchar_t) || size > 65536)
            registryError("string format", ERROR_INVALID_DATA);
        std::vector<wchar_t> value(size / sizeof(wchar_t) + 1, L'\0');
        status = RegQueryValueExW(key, name.c_str(), nullptr, &type, reinterpret_cast<BYTE*>(value.data()), &size);
        if (status != ERROR_SUCCESS) registryError("value read", status);
        if ((type != REG_SZ && type != REG_EXPAND_SZ) || size % sizeof(wchar_t))
            registryError("string format changed", ERROR_INVALID_DATA);
        return value.data();
    });
}
std::wstring lower(std::wstring value) {
    std::transform(value.begin(), value.end(), value.begin(), [](wchar_t c) { return static_cast<wchar_t>(std::towlower(c)); });
    return value;
}
std::wstring environmentReferencesForView(const std::wstring& text, View view) {
    if (view == View::X64) return text;
    // Mirror WOW64 child-process variables without changing the x64 launcher's environment.
    std::wstring result;
    size_t offset = 0;
    while (offset < text.size()) {
        const auto first = text.find(L'%', offset);
        if (first == std::wstring::npos) break;
        const auto last = text.find(L'%', first + 1);
        if (last == std::wstring::npos) break;
        result.append(text, offset, first - offset);
        const auto token = text.substr(first, last - first + 1);
        const auto name = lower(token);
        if (name == L"%programfiles%") result += L"%ProgramFiles(x86)%";
        else if (name == L"%commonprogramfiles%") result += L"%CommonProgramFiles(x86)%";
        else if (name == L"%processor_architecture%") result += L"x86";
        else if (name == L"%processor_architew6432%") result += L"%PROCESSOR_ARCHITECTURE%";
        else result += token;
        offset = last + 1;
    }
    result.append(text, offset, std::wstring::npos);
    return result;
}
fs::path modulePath(const std::wstring& registered, View view, const fs::path& windows) {
    const auto references = environmentReferencesForView(registered, view);
    const auto size = ExpandEnvironmentStringsW(references.c_str(), nullptr, 0);
    if (!size || size > 32768) throw std::runtime_error(errorText("input method path expansion"));
    std::wstring text(size, L'\0');
    if (ExpandEnvironmentStringsW(references.c_str(), text.data(), size) != size)
        throw std::runtime_error(errorText("input method path expansion"));
    text.resize(size - 1);
    const auto first = text.find_first_not_of(L" \t\r\n");
    if (first == std::wstring::npos) return {};
    text = text.substr(first, text.find_last_not_of(L" \t\r\n") - first + 1);
    if (text.size() >= 2 && text.front() == L'"' && text.back() == L'"') text = text.substr(1, text.size() - 2);
    if (text.empty()) return {};
    if (text.rfind(L"\\\\?\\", 0) == 0) text.erase(0, 4);
    auto path = fs::path(text);
    if (!path.is_absolute()) path = windows / (view == View::X86 ? "SysWOW64" : "System32") / path;
    path = path.lexically_normal();
    // Registry text is not subject to WOW64 filesystem redirection in this x64 launcher.
    const auto system = lower((windows / "System32").lexically_normal().wstring()) + L"\\";
    const auto normalized = lower(path.wstring());
    if (view == View::X86 && normalized.rfind(system, 0) == 0)
        path = windows / "SysWOW64" / path.wstring().substr(system.size());
    return path;
}
}

std::vector<InputMethodRegistration> readInputMethodRegistrations(
    const InputMethodRegistryReader& reader, const fs::path& windowsDirectory) {
    std::vector<InputMethodRegistration> result;
    for (const auto view : {View::X86, View::X64}) {
        const std::string architecture = view == View::X86 ? "/x86" : "/x64";
        const auto add = [&](const std::wstring& value, const std::string& source) {
            if (value.empty()) return;
            const auto module = modulePath(value, view, windowsDirectory);
            if (!module.empty()) result.push_back({module, source + architecture});
        };
        for (const auto& layout : reader.subkeys(Scope::Machine, view, KEYBOARD_LAYOUTS))
            add(reader.value(Scope::Machine, view, KEYBOARD_LAYOUTS + L"\\" + layout, L"Ime File"), "IMM/system");
        for (const auto scope : {Scope::User, Scope::Machine}) {
            for (const auto& clsid : reader.subkeys(scope, view, TEXT_SERVICES)) {
                const auto server = L"SOFTWARE\\Classes\\CLSID\\" + clsid + L"\\InprocServer32";
                auto value = reader.value(Scope::User, view, server, L"");
                if (value.empty()) value = reader.value(Scope::Machine, view, server, L"");
                add(value, scope == Scope::User ? "TSF/user" : "TSF/system");
            }
        }
    }
    return result;
}
std::vector<InputMethodRegistration> registeredInputMethods() {
    wchar_t windows[32768];
    const auto length = GetWindowsDirectoryW(windows, static_cast<UINT>(std::size(windows)));
    if (!length || length >= std::size(windows)) throw std::runtime_error(errorText("input method Windows directory"));
    return readInputMethodRegistrations({subkeys, stringValue}, fs::path(std::wstring(windows, length)));
}
}
