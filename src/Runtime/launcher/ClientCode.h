#pragma once
#include "../core/Common.h"
#include <array>
#include <functional>

namespace t7 {
inline constexpr char CLIENT_IMAGE_SHA256[] = "3c205c7efaf1956bc2c458b1073e5273a9fef29e3e5ee18418f93f418eeaa5c8";

struct ClientCodeSection {
    uint32_t rva = 0;
    Bytes bytes;
};
std::array<ClientCodeSection, 2> recoverClientCode(
    const Bytes& image, const std::function<bool()>& cancelled = {});
}
