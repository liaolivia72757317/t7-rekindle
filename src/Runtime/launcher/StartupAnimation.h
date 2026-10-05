#pragma once
#include <string>
#include <string_view>

namespace t7 {
std::string startupAnimationResourcePath();
bool isStartupAnimationResourcePath(std::string_view path);
std::string skipStartupAnimation(std::string_view source);
}
