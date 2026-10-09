#include "../../src/Runtime/launcher/ClientWindowMonitor.h"
#include <iostream>

namespace {
void require(bool result, const char* message) { if (!result) throw std::runtime_error(message); }

class WindowFixture final {
public:
    HWND window = nullptr;
    explicit WindowFixture(const wchar_t* name = L"T7.WindowMonitorFixture", HWND owner = nullptr,
                           DWORD style = WS_OVERLAPPEDWINDOW) {
        window = CreateWindowExW(WS_EX_NOACTIVATE, name, L"Window monitor fixture", style,
            -10000, -10000, 100, 100, owner, nullptr, GetModuleHandleW(nullptr), nullptr);
        require(window != nullptr, "window fixture creation");
    }
    ~WindowFixture() {
        if (window && !DestroyWindow(window)) std::cerr << "window fixture cleanup failed\n";
    }
    void destroy() {
        require(DestroyWindow(window), "window fixture destruction");
        window = nullptr;
    }
};
}

bool verifyClientWindowMonitor() {
    const auto instance = GetModuleHandleW(nullptr);
    WNDCLASSW type{}; type.lpfnWndProc = DefWindowProcW; type.hInstance = instance;
    type.lpszClassName = L"T7.WindowMonitorFixture";
    if (!RegisterClassW(&type)) { std::cerr << "window fixture class registration failed\n"; return false; }
    bool valid = true;
    try {
        t7::ClientWindowMonitor monitor;
        const auto pid = GetCurrentProcessId();
        require(!monitor.observed() && !monitor.closed(100000), "startup without a window triggered cleanup");
        require(!monitor.observe(nullptr, pid), "null game window accepted");
        WindowFixture main;
        require(!monitor.observe(main.window, pid + 1) && !monitor.observed(), "unowned window armed cleanup");
        WindowFixture child(L"T7.WindowMonitorFixture", main.window, WS_CHILD);
        require(!monitor.observe(child.window, pid), "child window armed cleanup");
        child.destroy();
        require(monitor.observe(main.window, pid) && monitor.observed(), "main game window was not observed");
        require(!monitor.closed(0), "existing game window triggered cleanup");
        ShowWindow(main.window, SW_SHOWMINNOACTIVE);
        require(IsIconic(main.window) && !monitor.closed(10000), "minimized game triggered cleanup");
        ShowWindow(main.window, SW_HIDE);
        require(!IsWindowVisible(main.window) && !monitor.closed(20000), "hidden game triggered cleanup");
        require(SetWindowLongPtrW(main.window, GWL_STYLE, WS_POPUP) != 0, "fullscreen fixture style change");
        require(!monitor.closed(30000), "fullscreen style change triggered cleanup");
        WindowFixture auxiliary(L"STATIC");
        WindowFixture owned(L"T7.WindowMonitorFixture", auxiliary.window);
        main.destroy();
        require(!monitor.closed(40000) && !monitor.closed(40999), "window recreation grace period was skipped");
        {
            WindowFixture replacement;
            require(!monitor.closed(41000), "replacement game window did not cancel cleanup");
            require(!monitor.closed(42000), "replacement game window was lost");
            replacement.destroy();
        }
        require(!monitor.closed(43000) && !monitor.closed(43999), "replacement close reused an old deadline");
        require(monitor.closed(44000), "closed game retained a running session because of helper windows");
        monitor = {};
        require(!monitor.observed() && !monitor.closed(50000), "a new session retained window-close state");
        WindowFixture next;
        require(monitor.observe(next.window, pid), "next session did not bind its own game window");
        next.destroy();
        require(!monitor.closed(0) && monitor.closed(1000), "zero timestamp broke window-close grace period");
        std::cout << "Game window ownership, startup, minimize/hide, recreation and close detection passed\n";
    } catch (const std::exception& error) {
        std::cerr << "Game window monitor test failure: " << error.what() << '\n'; valid = false;
    }
    if (!UnregisterClassW(type.lpszClassName, instance)) {
        std::cerr << "window fixture class cleanup failed\n"; valid = false;
    }
    return valid;
}
