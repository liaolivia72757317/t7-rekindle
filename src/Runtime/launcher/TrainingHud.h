#pragma once
#include <string>
#include <string_view>

namespace t7r {
std::string trainingHudResourcePath();
bool isTrainingHudResourcePath(std::string_view path);
std::string hideTrainingMatchHud(std::string_view source);
}
