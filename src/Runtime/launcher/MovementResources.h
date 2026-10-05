#pragma once
#include <string>
#include <string_view>

namespace t7 {
enum class MovementResource { None, Infantry, Router, Birth };
inline constexpr size_t MOVEMENT_XML_RESERVE = 4096;
inline constexpr size_t MOVEMENT_XML_LIMIT = 4 * 1024 * 1024;

std::string movementResourcePath(MovementResource resource);
std::string movementOfflineTreePath();
MovementResource movementResourceForPath(std::string_view path);
std::string transformMovementResource(MovementResource resource, std::string_view source);
}
