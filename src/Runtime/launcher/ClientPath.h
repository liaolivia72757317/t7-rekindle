#pragma once
#include "../core/Common.h"

namespace t7 {
inline fs::path resolveLauncherPath(const fs::path& launcherDirectory, const fs::path& selected) {
    if (selected.has_root_path() && !selected.is_absolute())
        throw std::runtime_error("客户端目录请使用完整绝对路径，或相对于启动器的路径。");
    const auto root = selected.empty() ? launcherDirectory : selected.is_absolute() ? selected : launcherDirectory / selected;
    auto normalized = fs::absolute(root).lexically_normal().make_preferred();
    if (!normalized.has_filename() && normalized.has_relative_path()) normalized = normalized.parent_path();
    return normalized;
}

inline fs::path resolveClientRoot(const fs::path& launcherDirectory, const Config& config) {
    return resolveLauncherPath(launcherDirectory, config.clientRoot);
}

inline fs::path resolveClientDirectory(const fs::path& launcherDirectory, const Config& config) {
    if (!config.clientDirectory.empty()) return resolveLauncherPath(launcherDirectory, config.clientDirectory);
    return (resolveClientRoot(launcherDirectory, config) / "Bin").make_preferred();
}

inline fs::path directoryFromClientExecutable(const fs::path& image) {
    if (_wcsicmp(image.filename().c_str(), L"TieJiClient.exe") != 0 || !fs::is_regular_file(image))
        throw std::runtime_error("请选择游戏客户端 TieJiClient.exe 文件。");
    return fs::absolute(image).lexically_normal().parent_path().make_preferred();
}

inline void validateClientDirectory(const fs::path& directory) {
    if (!fs::is_directory(directory))
        throw std::runtime_error("游戏目录不存在：" + directory.u8string());
    for (const auto* name : {"TieJiClient.exe", "ProtocalHandler.dll"}) {
        if (!fs::is_regular_file(directory / name))
            throw std::runtime_error("游戏目录缺少文件 " + std::string(name) + "；请通过浏览选择 TieJiClient.exe：" + directory.u8string());
    }
    const auto root = directory.parent_path();
    for (const auto* name : {"Data", "vfs"}) {
        if (!fs::is_directory(root / name))
            throw std::runtime_error("客户端目录缺少资源目录 " + std::string(name) + "：" + root.u8string());
    }
}

inline void validateClientRoot(const fs::path& root) { validateClientDirectory(root / "Bin"); }
}
