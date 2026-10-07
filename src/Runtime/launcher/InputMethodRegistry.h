#pragma once
#include "../core/Common.h"
#include <functional>

namespace t7 {
enum class InputMethodScope { User, Machine };
enum class InputMethodView { X86, X64 };
struct InputMethodRegistration {
    fs::path module;
    std::string source;
};
struct InputMethodRegistryReader {
    std::function<std::vector<std::wstring>(InputMethodScope, InputMethodView, const std::wstring&)> subkeys;
    std::function<std::wstring(InputMethodScope, InputMethodView, const std::wstring&, const std::wstring&)> value;
};
std::vector<InputMethodRegistration> readInputMethodRegistrations(
    const InputMethodRegistryReader& reader, const fs::path& windowsDirectory);
std::vector<InputMethodRegistration> registeredInputMethods();
}
