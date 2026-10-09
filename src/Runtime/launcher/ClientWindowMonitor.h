#pragma once
#include "../core/Common.h"
#include <optional>

namespace t7 {
class ClientWindowMonitor final {
public:
    bool observed() const noexcept { return window_ != nullptr; }

    bool observe(HWND window, DWORD pid) {
        DWORD owner = 0;
        if (!pid || !GetWindowThreadProcessId(window, &owner) || owner != pid
            || GetAncestor(window, GA_ROOT) != window || GetWindow(window, GW_OWNER)) return false;
        wchar_t name[256]{};
        if (!GetClassNameW(window, name, static_cast<int>(std::size(name)))) return false;
        window_ = window; pid_ = pid; className_ = name; missingSince_.reset();
        return true;
    }

    bool closed(ULONGLONG now) {
        if (!observed()) return false;
        if (!matches(window_)) {
            Search search{this};
            if (!EnumWindows(findReplacement, reinterpret_cast<LPARAM>(&search)) && !search.window)
                throw std::runtime_error(errorText("owned game window enumeration"));
            if (search.window) window_ = search.window;
        }
        if (matches(window_)) {
            missingSince_.reset();
            return false;
        }
        // A display-mode change may destroy and recreate the same game window.
        if (!missingSince_) missingSince_ = now;
        return now - *missingSince_ >= CLOSE_GRACE_MS;
    }

private:
    static constexpr ULONGLONG CLOSE_GRACE_MS = 1000;
    struct Search { const ClientWindowMonitor* monitor; HWND window = nullptr; };
    HWND window_ = nullptr;
    DWORD pid_ = 0;
    std::wstring className_;
    std::optional<ULONGLONG> missingSince_;

    bool matches(HWND window) const {
        DWORD owner = 0;
        wchar_t name[256]{};
        // HWND values can be reused; existence alone does not establish ownership.
        return GetWindowThreadProcessId(window, &owner) && owner == pid_
            && GetAncestor(window, GA_ROOT) == window && !GetWindow(window, GW_OWNER)
            && GetClassNameW(window, name, static_cast<int>(std::size(name))) && className_ == name;
    }

    static BOOL CALLBACK findReplacement(HWND window, LPARAM parameter) {
        auto& search = *reinterpret_cast<Search*>(parameter);
        if (!search.monitor->matches(window)) return TRUE;
        search.window = window;
        return FALSE;
    }
};
}
