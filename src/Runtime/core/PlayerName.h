#pragma once
#include "Common.h"
#include <algorithm>

namespace t7 {
inline bool validPlayerName(const std::string& value) {
    try {
        const auto text = wide(value);
        if (text.empty() || text.size() > 31 || std::any_of(text.begin(), text.end(), [](wchar_t ch) {
            return ch < 32 || (ch >= 127 && ch < 160) || ch == L'\u20ac' || (ch >= 0xE000 && ch <= 0xF8FF);
        })) return false;
        BOOL replaced = FALSE;
        const auto length = WideCharToMultiByte(936, WC_NO_BEST_FIT_CHARS, text.data(),
            static_cast<int>(text.size()), nullptr, 0, nullptr, &replaced);
        return length > 0 && length <= 31 && !replaced;
    } catch (const std::exception&) { return false; }
}
}
